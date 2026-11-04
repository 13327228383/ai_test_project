# -*- coding: utf-8 -*-
"""
Intent Validator - P0 防幻觉：Action 类型确定性校验 (v3.0)
===========================================================
从 actions[{type, target_hint, value}] 验证：
  1. action type 白名单校验 — 不在已知类型中 → error
  2. action target_hint 非空校验 — 交互类动作 target_hint 为空 → warning
  3. action value 合理性 — fill 动作 value 为空 → warning

零 AI 调用 — 纯白名单枚举。
"""

import json
import logging

_log = logging.getLogger(__name__)

# ── 已知有效的 action type（可通过 pipeline_config.case_valid_action_types 覆盖）──
_DEFAULT_ACTION_TYPES = {
    "navigate",  # 页面跳转
    "click",  # 点击
    "fill",  # 输入
    "upload",  # 上传文件
    "select_option",  # 下拉选择
    "verify",  # 验证检查
    "hover",  # 悬停
    "scroll",  # 滚动
    "wait",  # 等待
    "press_key",  # 按键
    "check",  # 勾选复选框
    "uncheck",  # 取消勾选
    "double_click",  # 双击
    "right_click",  # 右键
    "drag",  # 拖拽
    "screenshot",  # 截图
}


def _get_valid_action_types() -> set:
    """加载有效 action type 集合。

    来源优先级：
      1. pipeline_config.case_valid_action_types 配置覆盖
      2. MySQL action_library_store 中的非工作流 action id
      3. 默认通用 web 自动化动作兜底
    """
    valid = set(_DEFAULT_ACTION_TYPES)
    try:
        from src.core.pipeline_config import pipeline_config

        custom = getattr(pipeline_config, "case_valid_action_types", None)
        if custom:
            # ★ 支持字符串（逗号分隔）或列表/集合
            if isinstance(custom, str):
                items = [x for t in custom.split(",") if (x := t.strip())]
            elif isinstance(custom, (list, set, tuple)):
                items = list(custom)
            else:
                items = []
            valid |= {str(t).lower() for t in items if str(t).strip()}
    except Exception as _e:
        _log.debug("_get_valid_action_types pipeline_config: %s", _e, exc_info=True)

    try:
        from src.pipeline.utils.action_library_store import action_library_store

        lib = action_library_store.load_library()
        for cat in lib.get("categories", []) if isinstance(lib, dict) else []:
            if not isinstance(cat, dict):
                continue
            for action in cat.get("actions", []):
                if not isinstance(action, dict):
                    continue
                if bool(action.get("is_workflow")):
                    continue
                aid = str(action.get("id", "") or "").strip().lower()
                if aid:
                    valid.add(aid)
    except Exception as _e:
        _log.debug(
            "_get_valid_action_types action_library_store: %s", _e, exc_info=True
        )

    return valid


# type 为这些时 target_hint 不能为空
_TARGET_REQUIRED_TYPES = {
    "navigate",
    "click",
    "fill",
    "upload",
    "select_option",
    "hover",
    "check",
    "uncheck",
    "double_click",
    "right_click",
    "drag",
}

# type 为 fill 时 value 不能为空（可以空字符串表示清空，但不能为 None）
_VALUE_RECOMMENDED_TYPES = {"fill", "select_option", "upload", "press_key"}


def cross_check_actions(cases: list[dict]) -> list[dict]:
    """
    v3.0 确定性校验：验证 actions[] 的 type/target/value 合法性。

    返回: [{"case_id": "...", "reason": "...", "severity": "error|warning", "cross_check": "action"}]
    """
    errors = []

    for c in cases:
        if not isinstance(c, dict):
            continue
        cid = c.get("case_id", "?")

        actions = c.get("actions") or []
        if isinstance(actions, str):
            try:
                actions = json.loads(actions)
            except Exception:
                errors.append(
                    {
                        "case_id": cid,
                        "reason": "actions 字段 JSON 解析失败，无法校验。",
                        "severity": "error",
                        "cross_check": "action",
                    }
                )
                continue

        if not isinstance(actions, list):
            errors.append(
                {
                    "case_id": cid,
                    "reason": f"actions 字段类型错误（期望list，实际{type(actions).__name__}）。",
                    "severity": "error",
                    "cross_check": "action",
                }
            )
            continue

        for ai, act in enumerate(actions):
            if not isinstance(act, dict):
                errors.append(
                    {
                        "case_id": cid,
                        "reason": f"actions[{ai}]不是有效对象。",
                        "severity": "error",
                        "cross_check": "action",
                    }
                )
                continue

            act_type = str(act.get("type", "")).strip().lower()

            # ── 1. type 白名单校验 ──
            if not act_type:
                errors.append(
                    {
                        "case_id": cid,
                        "reason": f"actions[{ai}].type 为空，无法确定操作类型。",
                        "severity": "error",
                        "cross_check": "action",
                    }
                )
                continue

            if act_type not in _get_valid_action_types():
                closest = _find_closest_type(act_type)
                errors.append(
                    {
                        "case_id": cid,
                        "reason": (
                            f"actions[{ai}].type「{act.get('type')}」不在已知操作类型中。"
                            f"有效类型: {', '.join(sorted(_get_valid_action_types()))}。"
                            f"{'建议: ' + closest if closest else ''}"
                        ),
                        "severity": "error",  # ★ 编造 action type → 阻断级
                        "cross_check": "action",
                        "fix_hint": closest,
                    }
                )

            # ── 2. target_hint 非空校验 ──
            if act_type in _TARGET_REQUIRED_TYPES:
                target = str(act.get("target_hint", "")).strip()
                if not target:
                    errors.append(
                        {
                            "case_id": cid,
                            "reason": f"actions[{ai}]（type={act_type}）target_hint 为空，操作缺少目标。",
                            "severity": "error",
                            "cross_check": "action",
                        }
                    )

            # ── 3. value 合理性 ──
            if act_type in _VALUE_RECOMMENDED_TYPES:
                value = act.get("value")
                if value is None and act_type in ("fill", "select_option"):
                    errors.append(
                        {
                            "case_id": cid,
                            "reason": f"actions[{ai}]（type={act_type}）value 为 None，输入/选择值缺失。",
                            "severity": "warning",
                            "cross_check": "action",
                        }
                    )

    return errors


def _find_closest_type(wrong_type: str) -> str:
    """找最接近的有效 action type。"""
    wrong = wrong_type.lower().strip()
    if not wrong:
        return ""
    valid_types = _get_valid_action_types()
    # 前缀匹配
    for valid in sorted(valid_types):
        if valid.startswith(wrong) or wrong.startswith(valid):
            return valid
    # 子串包含
    for valid in sorted(valid_types):
        if wrong in valid or valid in wrong:
            return valid
    return ""
