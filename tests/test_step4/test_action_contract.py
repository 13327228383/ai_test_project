# -*- coding: utf-8 -*-
"""单元测试：统一 action 契约（双执行路径契约统一）。

镜像 src/pipeline/steps/step4/action_contract.py。
- CANONICAL_ACTIONS   生成层可产出的全部 action 白名单
- GUIDED_IMPLEMENTED  引导路径 _exec_one_step 已实现分支
- classify_action / guided_gap / is_canonical  三态分类用于运行时暴露漂移
"""

from src.pipeline.steps.step4 import action_contract as ac


def test_is_canonical_known_actions():
    for name in (
        "click", "double_click", "right_click", "hover",
        "fill", "type_text", "clear", "select_option", "check",
        "uncheck", "set_date", "upload_file", "press_key",
        "key_down", "key_up", "navigate", "wait",
        "verify", "focus_check", "scroll", "wheel", "drag",
        "download_file", "multi_touch",
    ):
        assert ac.is_canonical(name) is True


def test_template_and_reserved_not_canonical():
    # 彻底校准：模板级复合名 / 流程级保留动作不再是"可产出的原子 action"
    assert ac.is_canonical("tab_traverse") is False
    assert ac.is_canonical("login") is False
    assert ac.is_canonical("logout") is False
    assert ac.is_canonical("submit") is False
    assert ac.is_canonical("screenshot") is False
    # 分类语义区分，不应误报为 UNKNOWN 配置错误
    assert ac.classify_action("tab_traverse") == "TEMPLATE"
    assert ac.classify_action("login") == "RESERVED"
    assert ac.classify_action("logout") == "RESERVED"
    assert ac.classify_action("submit") == "RESERVED"
    # ★ 2026-09-27 契约补全：screenshot 是**已实现且被消费**的证据级动作
    #   （_TOOL_PREFIX_MAP 有 browser_take_screenshot → screenshot；
    #    结构化执行器 _is_interactive 集合显式含它），
    #   必须在契约内 → 分类为 RESERVED，不得落 UNKNOWN（会使生成端守卫误剔）。
    assert ac.classify_action("screenshot") == "RESERVED"


def test_guided_implemented_contains_baseline():
    # 引导路径必须能执行全部基础交互（这些都是双路径共用能力）
    for name in (
        "click", "fill", "press_key", "select_option", "check",
        "set_date", "verify", "focus_check", "scroll", "hover",
        "double_click", "right_click", "upload_file",
    ):
        assert ac.classify_action(name) == "GUIDED", name


def test_canonical_implies_guided_no_gap():
    # 契约统一防漂移自证：CANONICAL 全集内每个原子动作都必须能由引导路径执行。
    # 在此基础上新增原子动作时，若漏加引导分支，这里将立即暴露 GAP。
    assert ac.CANONICAL_ACTIONS, "CANONICAL_ACTIONS 不能为空"
    gaps = sorted(
        _a for _a in ac.CANONICAL_ACTIONS if ac.classify_action(_a) != "GUIDED"
    )
    assert gaps == [], "契约存在 GAP（CANONICAL 但引导路径未实现）: %r" % gaps
    # 反向一致性：引导已实现集合不应含契约外或模板/流程级动作
    drift = sorted(
        _a for _a in ac.GUIDED_IMPLEMENTED if ac.classify_action(_a) != "GUIDED"
    )
    assert drift == [], "GUIDED 集合含非契约动作: %r" % drift


def test_former_gaps_now_guided():
    # 契约 GAP 修复：drag / wheel / multi_touch / download_file 已在引导路径补齐，
    # 不再是有潜在漂移的 GAP，而应由引导路径直接执行。
    for name in ("drag", "wheel", "scroll_wheel", "multi_touch",
                 "multi_touch_pinch", "download_file", "download",
                 "drag_offset"):
        assert ac.is_canonical(name) is True
        assert ac.classify_action(name) == "GUIDED", name
        assert ac.guided_gap(name) is False, name

    # 契约外 action 仍应报 UNKNOWN，确保三态分类在 GAP 修复后不失掉否定路径
    assert ac.classify_action("bogus") == "UNKNOWN"


def test_unknown_action():
    assert ac.is_canonical("bogus") is False
    assert ac.classify_action("bogus") == "UNKNOWN"
    assert ac.classify_action("") == "UNKNOWN"
    assert ac.classify_action(None) == "UNKNOWN"


def test_focus_check_is_registered_and_guided():
    # focus_check 是本轮新增：契约登记 + 引导路径已实现 + 结构化路径对称实现
    assert ac.is_canonical("focus_check") is True
    assert ac.classify_action("focus_check") == "GUIDED"
    assert ac.guided_gap("focus_check") is False