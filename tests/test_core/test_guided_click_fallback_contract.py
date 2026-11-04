# -*- coding: utf-8 -*-
"""点击路径契约：已定位到的候选按钮，其点击必须走三级兜底（与按钮类型无关）。

根因（实测 TC-REQ-040-E01 假 PRODUCT_BUG）：
    `_guided_click` 先用 getByRole 找到可见候选（日志有"★ getByRole命中'登录'"），
    但**三级兜底点击块被错误嵌套在 `if _is_upload_btn:` 的 `except` 块内**。
    普通按钮（"登录"不在 `_FILE_UPLOAD_BTN_KW`）→ `_is_upload_btn=False`
    → 整个 if 块跳过 → 定位成功却从未发出 click → 页面无变化 → 假 FAIL。

判据（单一事实源）：三级兜底（普通 click → force click → JS dispatchEvent）
    必须位于 `if _is_upload_btn:` **之外**，与按钮业务语义无关，
    使任何已定位的可见候选都按同一强度递进尝试点击。
"""
from __future__ import annotations

import inspect
import re

from src.agent.explorer_mixins import _action

MOD = inspect.getsource(_action)


def _click_region() -> str:
    """定位 _guided_click 中 getByRole 命中日志之后的代码区。

    取"★ getByRole命中"到"匹配按钮:"（快照路径起点）之间——三级兜底
    必须出现在这一段里，而不是藏在 upload 分支内。
    """
    m = re.search(r"getByRole命中.*?匹配按钮:", MOD, re.S)
    assert m, "未找到 _guided_click 的 getByRole → 快照匹配区间"
    return m.group(0)


def _indent_of_line(text: str, needle: str) -> int:
    for line in text.splitlines():
        if needle in line:
            return len(line) - len(line.lstrip())
    raise AssertionError(f"未找到行: {needle!r}")


def test_fallback_block_exists_between_role_and_snapshot():
    """三级兜底三档必须都出现在 getByRole 命中与快照匹配之间。"""
    region = _click_region()
    for tier in ("兜底1 普通click成功", "兜底2 force click成功", "兜底3 JS click成功"):
        assert tier in region, (
            f"三级兜底缺少 [{tier}]，位于 getByRole 命中之后、快照匹配之前。"
            "普通按钮将无法点击（假 FAIL 根因）。"
        )


def test_fallback_not_nested_under_upload_guard():
    """关键回归护栏：三级兜底不得嵌在 `if _is_upload_btn:` 的 except 内。

    判据 = 兜底块的缩进必须 **小于等于** `if _is_upload_btn:` 的身体缩进，
    即与 `if _is_upload_btn:` 同级（loop-body 层），而非 upload 分支内部。
    """
    region = _click_region()
    upload_indent = _indent_of_line(region, "if _is_upload_btn:")
    fallback_indent = _indent_of_line(region, "_fallback_ok = False")
    assert fallback_indent <= upload_indent, (
        f"三级兜底缩进({fallback_indent}) > `if _is_upload_btn:`({upload_indent}) "
        "—— 仍在上传分支内，普通按钮会整块跳过（假 FAIL 根因）。"
    )


def test_fallback_all_tiers_warn_on_failure():
    """三档 guard 必须显式 level=WARNING（点击失败必须可见，D19 同类）。"""
    region = _click_region()
    n_warn = len(re.findall(r'with guard\([^)]*level=logging\.WARNING\)', region))
    assert n_warn >= 3, (
        f"三级兜底的 guard 只有 {n_warn} 处带 level=WARNING，点击失败会在生产日志中"
        "完全不可见（表层只剩'点了页面没变'的假 FAIL，无归因）。"
    )


def test_fallback_total_failure_logged():
    """三级全灭必须显式 WARNING 归因。"""
    region = _click_region()
    assert "三级兜底全部点击失败" in region, (
        "三级兜底全灭无显式归因 —— 排查时会退化成无证据的猜测。"
    )


def test_no_new_vocabulary_introduced():
    """反补丁护栏：本次修复不得新增业务词表（通用性）。"""
    region = _click_region()
    # 修复只做结构提升，不引入任何新常量/词表定义
    assert not re.search(r"^[ \t]*_[A-Z_]{3,}\s*=\s*[\(\[]", region, re.M), (
        "点击兜底区出现了新的模块级词表/常量定义 —— 属补丁式修复，违反反补丁三禁。"
    )
