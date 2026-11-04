# -*- coding: utf-8 -*-
"""存储断言 — storage_check。

场景：登录后 cookie 含 token；用户偏好持久化到 localStorage；会话状态校验。
缺失后果：会话状态/持久化规则漏测。

实现：
  1. cookie：通过 page.context.cookies() 读取。
  2. localStorage / sessionStorage：通过 page.evaluate() 读取。
  3. operator 支持：exists/not_exists/eq/contains。
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


# storage_type 小写键 → JS 中真实的全局对象名（cookie 走 context.cookies，无需 JS 对象）
_STORAGE_JS_OBJ: dict[str, str] = {
    "cookie": "cookie",
    "localstorage": "localStorage",
    "sessionstorage": "sessionStorage",
}


async def verify_storage(page, check: dict[str, Any]) -> dict[str, Any]:
    """存储断言。

    check 格式：
      {"type": "storage_check",
       "storage_type": "localStorage",                # cookie / localStorage / sessionStorage
       "key": "token",                                 # 键名
       "expected": "abc123",                           # 期望值（exists/not_exists 可不填）
       "operator": "eq"}                               # exists/not_exists/eq/contains
    """
    try:
        # ★ 根因修复(2026-09-21): storage_type 有两重语义——
        #   ① 枚举值（比较时需大小写不敏感）② JS 全局对象名（localStorage/sessionStorage，
        #   必须保留驼峰，否则 page.evaluate 里 localstorage 是未定义标识符 → ReferenceError）。
        #   原实现先 .lower() 再与驼峰常量比较 → 分支①永假，storage_check 100% 判失败；
        #   且把 lower 后的名字直接拼进 JS → 分支②必然抛错。
        #   修法：匹配用小写，求值用规范驼峰名（单一事实源 _STORAGE_JS_OBJ）。
        _raw_storage = (check.get("storage_type") or "localStorage").strip()
        storage_type = _raw_storage.lower()
        key = check.get("key", "")
        expected = check.get("expected", "")
        operator = (check.get("operator") or "exists").strip().lower()

        if not key:
            return {"passed": False, "evidence": "[StorageCheck] 缺少 key 参数"}
        if storage_type not in _STORAGE_JS_OBJ:
            return {
                "passed": False,
                "evidence": "[StorageCheck] 未知 storage_type: %s" % _raw_storage,
            }
        if operator not in ("exists", "not_exists", "eq", "contains"):
            return {
                "passed": False,
                "evidence": "[StorageCheck] 未知 operator: %s" % operator,
            }

        # 读取值
        actual_value = None
        exists = False
        if storage_type == "cookie":
            cookies = await page.context.cookies()
            for c in cookies:
                if c.get("name") == key:
                    actual_value = c.get("value", "")
                    exists = True
                    break
        else:
            # localStorage / sessionStorage
            js = "(() => { const v = %s.getItem('%s'); return v; })()" % (
                _STORAGE_JS_OBJ[storage_type],
                key.replace("'", "\\'"),
            )
            try:
                actual_value = await page.evaluate(js)
                exists = actual_value is not None
            except Exception as e:
                return {
                    "passed": False,
                    "evidence": "[StorageCheck] 读取 %s 失败: %s"
                    % (storage_type, str(e)[:60]),
                }

        # 校验
        if operator == "exists":
            passed = exists
        elif operator == "not_exists":
            passed = not exists
        elif operator == "eq":
            passed = exists and str(actual_value) == str(expected)
        else:  # contains
            passed = exists and str(expected) in str(actual_value)

        return {
            "passed": passed,
            "evidence": "[StorageCheck] %s['%s'] = '%s' exists=%s operator=%s expected='%s' → %s"
            % (
                storage_type,
                key,
                str(actual_value)[:40] if exists else "NULL",
                exists,
                operator,
                str(expected)[:40],
                "PASS" if passed else "FAIL",
            ),
        }
    except Exception as e:
        _log.error("[StorageCheck] 异常: %s", str(e)[:120], exc_info=True)
        return {
            "passed": False,
            "error": "executor_exception",
            "evidence": "[执行器异常] storage_check 崩溃: %s" % str(e)[:80],
        }
