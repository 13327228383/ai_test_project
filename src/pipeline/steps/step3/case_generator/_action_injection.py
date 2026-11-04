# -*- coding: utf-8 -*-
"""Step3 操作/断言关键字注入器 — build_action_injection_prompt。

根据需求文本智能筛选相关操作，只注入用得到的操作和断言，避免浪费 token。

设计原则：
  1. 基础操作（navigate/click/fill/verify/screenshot）始终注入
  2. 根据需求文本匹配场景关键词，注入专用操作
  3. 格式: - id(必填参数): 描述
  4. 无需求文本时不注入（让 AI 自由生成，操作清单已在代码中定义，无需执行时二次匹配）
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)

# ── 操作定义（代码内维护，不依赖 DB） ──
# 每个操作: (id, 必填参数列表, 描述, 场景标签)
_OPERATIONS = [
    # ── 基础操作（始终注入） ──
    ("navigate", ["url"], "导航到指定URL", "base"),
    ("click", ["target_hint"], "点击元素", "base"),
    ("fill", ["target_hint", "value"], "在输入框中填入文字", "base"),
    ("verify", [], "AI判断目标是否达成", "base"),
    ("screenshot", [], "截图保存", "base"),
    # ── 表单类 ──
    ("select_option", ["target_hint", "value"], "选择下拉框选项", "form"),
    ("check", ["target_hint"], "勾选复选框", "form"),
    ("uncheck", ["target_hint"], "取消勾选", "form"),
    ("focus", ["target_hint"], "聚焦元素（触发onFocus）", "form"),
    ("blur", ["target_hint"], "失焦元素（触发onBlur）", "form"),
    (
        "type_text",
        ["target_hint", "value"],
        "逐字输入（触发完整事件链，用于自动补全/实时搜索）",
        "form",
    ),
    ("clear", ["target_hint"], "清空输入框（触发清除事件链）", "form"),
    ("set_date", ["target_hint", "value"], "设置日期选择器值（YYYY-MM-DD）", "form"),
    # ── 复杂交互类 ──
    ("hover", ["target_hint"], "鼠标悬停", "interact"),
    ("double_click", ["target_hint"], "双击元素", "interact"),
    ("right_click", ["target_hint"], "右键点击", "interact"),
    ("middle_click", ["target_hint"], "中键点击（新标签页打开）", "interact"),
    ("drag", ["target_hint", "target_hint2"], "拖拽元素到另一个元素", "interact"),
    ("drag_offset", ["target_hint", "dx", "dy"], "偏移拖拽（滑块/画布）", "interact"),
    ("long_press", ["target_hint"], "长按元素", "interact"),
    ("swipe", ["target_hint"], "滑动手势（移动端）", "interact"),
    (
        "context_menu_click",
        ["target_hint", "menu_item_hint"],
        "右键菜单交互",
        "interact",
    ),
    ("key_down", ["key"], "按下按键（不释放，组合键用）", "interact"),
    ("key_up", ["key"], "释放按键", "interact"),
    ("multi_touch_pinch", ["scale"], "双指缩放手势（移动端）", "interact"),
    # ── 文件类 ──
    ("upload_file", ["target_hint", "file_path"], "上传文件", "file"),
    ("download_file", ["target_hint"], "下载文件", "file"),
    # ── 导航类 ──
    ("scroll", [], "滚动页面", "nav"),
    ("press_key", ["key"], "键盘按键", "nav"),
    ("wait", ["seconds"], "等待", "nav"),
    ("switch_tab", [], "切换标签页", "nav"),
    ("close_tab", [], "关闭当前标签页", "nav"),
    ("page_reload", [], "刷新页面", "nav"),
    ("go_back", [], "浏览器后退", "nav"),
    ("go_forward", [], "浏览器前进", "nav"),
    ("wheel", ["delta_y"], "原生滚轮事件（Canvas/地图）", "nav"),
    # ── 布局类 ──
    ("resize_window", [], "调整视口尺寸（响应式测试）", "layout"),
    # ── iframe 类 ──
    ("switch_to_iframe", ["target_hint"], "进入iframe上下文", "iframe"),
    ("switch_to_default", [], "退出iframe回到主页面", "iframe"),
]

# ── 断言定义 ──
_ASSERTIONS = [
    ("verify", [], "AI判断步骤目标是否达成", "base"),
    ("element_count_check", ["selector", "expected_count"], "元素数量断言", "base"),
    ("element_enabled_check", ["selector"], "元素启用/禁用状态断言", "base"),
    ("element_text_equals_check", ["selector", "expected"], "文本精确匹配断言", "base"),
    ("image_loaded_check", ["selector"], "图片加载完成断言", "base"),
    ("api_verify", [], "检查API状态码和响应", "api"),
    (
        "api_call_count_check",
        ["url_pattern", "expected_count"],
        "API调用次数断言",
        "api",
    ),
    (
        "request_body_check",
        ["url_pattern", "expected_field", "expected_value"],
        "请求体字段值断言",
        "api",
    ),
    ("db_verify", [], "检查数据库记录", "db"),
    ("db_record_count_check", ["table", "expected_count"], "数据库记录数量断言", "db"),
    ("db_record_not_exists_check", ["table", "where"], "数据库记录不存在断言", "db"),
    (
        "verify_style",
        ["selector", "expected"],
        "样式断言（颜色/字体/可见性/CSS类/属性/z-index）",
        "style",
    ),
    ("verify_position", ["selector"], "精确位置/尺寸断言", "style"),
    ("element_checked_check", ["selector"], "复选框勾选状态断言", "form"),
    ("select_value_check", ["selector", "expected"], "下拉框选中值断言", "form"),
    ("verify_toast", ["check_type"], "Toast出现/消失/Loading状态", "feedback"),
    ("verify_refresh", ["expected"], "页面刷新检测", "feedback"),
    ("response_time_check", ["url_pattern", "max_ms"], "API响应速度断言", "perf"),
    # ── 2026-07-06 补充：对称缺失 + 高频场景 ──
    (
        "element_exists_check",
        ["selector", "expected"],
        "元素存在断言（DOM节点存在性）",
        "base",
    ),
    (
        "element_visible_check",
        ["selector", "expected"],
        "元素可见断言（display/visibility/opacity）",
        "style",
    ),
    (
        "element_attribute_check",
        ["selector", "attribute", "expected"],
        "元素属性断言（disabled/readonly/href/src/data-*）",
        "style",
    ),
    ("url_check", ["expected", "operator"], "URL断言（路由跳转校验）", "base"),
    (
        "api_response_body_check",
        ["url_pattern", "expected_field", "expected_value"],
        "响应体字段值断言",
        "api",
    ),
    (
        "db_record_exists_check",
        ["table", "where"],
        "DB记录存在断言（与not_exists对称）",
        "db",
    ),
    (
        "db_field_value_check",
        ["table", "field", "where", "expected", "operator", "tolerance"],
        "DB字段值断言（支持operator+tolerance精度）",
        "db",
    ),
    (
        "db_query_check",
        ["sql", "expected", "operator", "tolerance", "result_field"],
        "DB自定义查询断言（JOIN/SUM/AVG跨表运算）",
        "db",
    ),
    ("console_error_check", ["expected"], "Console错误断言（无JS错误）", "feedback"),
    (
        "dialog_check",
        ["expected_type", "expected_text"],
        "弹窗断言（alert/confirm/prompt文本）",
        "feedback",
    ),
    # ── 2026-07-07 P0/P1/P2 致命遗漏补齐 12 个断言 ──
    (
        "loading_complete_check",
        ["loading_selector", "expected_state", "timeout_ms", "wait_network_idle"],
        "异步加载完成断言（等待loading消失/networkidle，搜索/筛选后必用）",
        "feedback",
    ),
    (
        "db_ui_consistency_check",
        ["ui_selector", "db_table", "db_where", "expected_relation"],
        "DB与UI一致性断言（列表数量 vs DB COUNT）",
        "db",
    ),
    (
        "text_regex_check",
        ["selector", "pattern", "expected"],
        "文本格式正则断言（手机号/邮箱/身份证格式）",
        "base",
    ),
    (
        "list_order_check",
        ["selector", "value_selector", "data_type", "order"],
        "列表排序顺序断言（asc升序/desc降序）",
        "base",
    ),
    (
        "download_check",
        ["expected_filename", "expected_min_size"],
        "文件下载结果断言（文件存在且非空）",
        "feedback",
    ),
    (
        "storage_check",
        ["storage_type", "key", "expected", "operator"],
        "存储断言（cookie/localStorage/sessionStorage）",
        "base",
    ),
    (
        "network_check",
        ["url_pattern", "expected", "expected_count", "operator"],
        "网络请求拦截断言（合规/第三方拦截）",
        "api",
    ),
    (
        "animation_complete_check",
        ["selector", "animation_type", "timeout_ms"],
        "元素动画完成断言（CSS动画/过渡完成后）",
        "style",
    ),
    (
        "pagination_check",
        [
            "current_page_selector",
            "items_selector",
            "expected_page",
            "expected_items_per_page",
        ],
        "分页数据正确性断言",
        "base",
    ),
    (
        "element_attribute_all_check",
        ["selector", "attribute", "expected", "operator"],
        "多元素属性一致性断言（all/none/any）",
        "style",
    ),
    (
        "time_range_check",
        [
            "value_source",
            "selector",
            "db_table",
            "db_field",
            "db_where",
            "start_time",
            "end_time",
        ],
        "时间范围断言（created_at在范围内）",
        "db",
    ),
    (
        "websocket_check",
        ["expected_message", "operator"],
        "WebSocket消息断言（contains/equals/regex）",
        "api",
    ),
    # ── 2026-07-07 P3 补齐 3 个高频遗漏 ──
    (
        "api_header_check",
        ["url_pattern", "header_name", "expected", "operator"],
        "API响应头断言（Content-Type/Authorization/Cache-Control安全合规）",
        "api",
    ),
    (
        "page_load_time_check",
        ["metric", "max_ms"],
        "页面加载性能断言（load/DOMContentLoaded/FCP/TTFB）",
        "perf",
    ),
    (
        "text_contains_check",
        ["selector", "expected", "operator"],
        "元素文本包含断言（contains/not_contains，高频业务规则）",
        "base",
    ),
]

# ── 场景关键词映射 ──
_SCENE_KEYWORDS = {
    "form": [
        "表单",
        "输入",
        "填写",
        "登录",
        "注册",
        "提交",
        "保存",
        "编辑",
        "新增",
        "下拉",
        "选择",
        "勾选",
        "复选",
        "单选",
        "聚焦",
        "失焦",
        "焦点",
        "清空",
        "清除",
        "日期",
        "日历",
        "form",
        "input",
        "login",
        "register",
        "submit",
        "select",
        "checkbox",
        "clear",
        "date",
        "datepicker",
    ],
    "interact": [
        "拖拽",
        "拖动",
        "悬停",
        "双击",
        "右键",
        "长按",
        "滑动",
        "手势",
        "菜单",
        "滑块",
        "验证码",
        "中键",
        "缩放",
        "组合键",
        "按住",
        "drag",
        "hover",
        "double",
        "right click",
        "swipe",
        "long press",
        "middle click",
        "pinch",
        "zoom",
    ],
    "file": [
        "上传",
        "下载",
        "导入",
        "导出",
        "文件",
        "附件",
        "upload",
        "download",
        "import",
        "export",
        "file",
    ],
    "nav": [
        "滚动",
        "翻页",
        "标签页",
        "切换页",
        "键盘",
        "快捷键",
        "等待",
        "加载",
        "刷新",
        "后退",
        "前进",
        "返回",
        "关闭页",
        "滚轮",
        "scroll",
        "tab",
        "keyboard",
        "wait",
        "reload",
        "back",
        "forward",
        "close tab",
        "wheel",
    ],
    "layout": [
        "响应式",
        "布局",
        "窗口",
        "屏幕",
        "适配",
        "移动端",
        "分辨率",
        "responsive",
        "viewport",
        "resize",
        "mobile",
    ],
    "iframe": ["iframe", "嵌套", "frame", "内嵌", "富文本编辑器"],
    "api": [
        "接口",
        "API",
        "请求",
        "响应",
        "状态码",
        "HTTP",
        "调用",
        "请求体",
        "参数",
        "request",
        "body",
        "调用次数",
    ],
    "db": [
        "数据库",
        "记录",
        "表",
        "SQL",
        "存储",
        "持久化",
        "记录数",
        "不存在",
        "database",
        "DB",
        "count",
    ],
    "style": [
        "颜色",
        "字体",
        "样式",
        "位置",
        "坐标",
        "尺寸",
        "大小",
        "可见",
        "隐藏",
        "color",
        "font",
        "style",
        "position",
        "size",
        "visible",
        "display",
    ],
    "feedback": [
        "提示",
        "Toast",
        "消息",
        "弹窗",
        "刷新",
        "加载中",
        "Loading",
        "toast",
        "message",
        "refresh",
        "spinner",
    ],
    "perf": ["性能", "耗时", "延迟", "速度", "响应时间", "performance", "latency"],
}


# ── 断言 action_id -> (params, desc, tag) 元数据（用于识别 DB 中的断言） ──
_ASSERTION_META = {aid: (params, desc, tag) for aid, params, desc, tag in _ASSERTIONS}

# ── 断言分类 ID（与 _ASSERTIONS 的 tag 对齐） ──
_ASSERTION_CATEGORIES = {"api", "db", "style", "feedback", "perf"}

# ── 分类 -> 场景 tag 映射（确保基础操作始终被注入） ──
#   键为 DB 中 action_categories.category_id；值为 scene tag 或 action_id -> scene tag 映射。
_CATEGORY_SCENE_MAP = {
    "mouse": {
        "click": "base",
        "double_click": "interact",
        "right_click": "interact",
        "hover": "interact",
        "drag": "interact",
        "scroll": "interact",
    },
    "form": {
        "fill": "base",
        "select_option": "form",
        "check": "form",
        "uncheck": "form",
        "upload_file": "file",
    },
    "keyboard": {"press_key": "nav"},
    "navigation": {"navigate": "base", "wait": "nav"},
    "verification": {"verify": "base", "screenshot": "base"},
    "advanced": "interact",
    "import_export": "file",
    # 兼容用户可能使用的旧分类 ID
    "interact": "interact",
    "file": "file",
    "layout": "layout",
    "iframe": "iframe",
    "api": "api",
    "db": "db",
    "style": "style",
    "feedback": "feedback",
    "perf": "perf",
}

# ── 通用兜底操作/断言：无 applicable_tags 时也始终保留 ──
_BASE_OPERATION_IDS = {"navigate", "click", "screenshot"}
_BASE_ASSERTION_IDS = {"verify"}


# ── f5: 控件类型映射缓存（从 action_library_store 加载，替代原硬编码常量） ──
_CONTROL_TYPE_MAP_CACHE: dict[str, Any] = None  # type: ignore


def _load_control_type_mappings() -> dict[str, Any]:
    """从 action_library_store 加载控件类型映射（带缓存）。

    返回 {"control_type_tags": {...}, "control_type_preferences": {...}}。
    首次调用加载并缓存；后续调用直接返回缓存。
    """
    global _CONTROL_TYPE_MAP_CACHE
    if _CONTROL_TYPE_MAP_CACHE is not None:
        return _CONTROL_TYPE_MAP_CACHE
    try:
        from src.pipeline.utils.action_library_store import action_library_store

        _CONTROL_TYPE_MAP_CACHE = action_library_store.load_control_type_mappings()
    except Exception as e:
        _log.warning("[ActionInjection] 加载控件类型映射失败: %s", str(e)[:120])
        _CONTROL_TYPE_MAP_CACHE = {
            "control_type_tags": {},
            "control_type_preferences": {},
        }
    return _CONTROL_TYPE_MAP_CACHE


def _get_control_type_tags(control_type: str) -> list[str]:
    """获取指定控件类型的 HTML tag 候选集合。"""
    mappings = _load_control_type_mappings()
    return mappings.get("control_type_tags", {}).get(control_type, [])


def _get_control_type_preferences(control_type: str) -> dict[str, int]:
    """获取指定控件类型的优先 action id 映射（仅用于排序）。"""
    mappings = _load_control_type_mappings()
    return mappings.get("control_type_preferences", {}).get(control_type, {})


def _load_db_library_meta() -> list[dict[str, Any]]:
    """从 MySQL action_library_store 读取完整 action 元数据（含 applicable_tags）。"""
    try:
        from src.pipeline.utils.action_library_store import action_library_store

        lib = action_library_store.load_library()
        result = []
        for cat in lib.get("categories", []) if isinstance(lib, dict) else []:
            if not isinstance(cat, dict):
                continue
            cat_id = str(cat.get("id", "") or "").strip()
            for action in cat.get("actions", []):
                if not isinstance(action, dict):
                    continue
                if bool(action.get("is_workflow")):
                    continue
                action = dict(action)
                action["_category_id"] = cat_id
                result.append(action)
        return result
    except Exception as e:
        _log.debug("[ActionInjection] 加载 action meta 失败: %s", str(e)[:120])
        return []


def _action_relevance(
    control_type: str, action: dict[str, Any], control_tags: set
) -> int:
    """计算 action 与控件类型的相关度（命中标签数 + 优先级加分），仅用于排序。"""
    tags = set(action.get("applicable_tags") or [])
    if "*" in tags or not tags:
        return 0
    base = len(tags & control_tags)
    # ★ f5: 从 action_library_store 加载优先级映射
    prefs = _get_control_type_preferences(control_type)
    bonus = prefs.get(str(action.get("id", "")).strip(), 0)
    return base + bonus


def _is_source_match(
    control_type: str, action: dict[str, Any], control_tags: set
) -> bool:
    """源头判定：action 是否确实适用于当前控件类型。

    规则（从严到宽）：
      1. 空标签 / 通配符 * → 不匹配（由兜底操作覆盖）。
      2. 没有任何标签命中 → 不匹配。
      3. 该控件类型下标记为优先的 action → 直接放行（即使不是严格子集）。
      4. 其余情况要求 action 的所有 applicable_tags 都是当前控件标签集合的子集，
         避免「一个标签到处匹配」的通用操作混入所有控件。
    """
    tags = set(action.get("applicable_tags") or [])
    if not tags or "*" in tags:
        return False
    if not (tags & control_tags):
        return False
    aid = str(action.get("id", "")).strip()
    # ★ f5: 从 action_library_store 加载优先级映射
    prefs = _get_control_type_preferences(control_type)
    if aid in prefs:
        return True
    return tags.issubset(control_tags)


def filter_actions_by_control_type(control_type: str) -> dict[str, Any]:
    """根据控件类型过滤操作库，返回 {operations, assertions, control_type, tags}。

    策略：
      1. 始终保留通用兜底操作（navigate/click/screenshot）和断言（verify）。
      2. 源头控制：仅保留 applicable_tags 与控件类型严格相关的操作/断言，
         不依赖结果数量截断。相关度由标签子集 + 优先级映射决定。
      3. 按 _action_relevance 排序，优先展示最贴近当前控件的操作。

    operations/assertions 每项为 (aid, params, desc, tag) 元组，与 _OPERATIONS/_ASSERTIONS 格式一致。
    """
    # ★ f5: 从 action_library_store 加载控件类型 → 标签映射
    control_tags = set(_get_control_type_tags(control_type))
    meta = _load_db_library_meta()

    base_ops = []
    specific_ops = []
    base_asserts = []
    specific_asserts = []

    for action in meta:
        aid = str(action.get("id", "") or "").strip()
        if not aid:
            continue

        is_base_op = aid in _BASE_OPERATION_IDS
        is_base_assert = aid in _BASE_ASSERTION_IDS
        is_specific = _is_source_match(control_type, action, control_tags)

        if not (is_base_op or is_base_assert or is_specific):
            continue

        desc = str(action.get("desc", "") or action.get("label", "") or "")
        params = [_normalize_param(p) for p in (action.get("params") or [])]

        # tag 用于场景兜底：已知断言用硬编码 tag，否则用 category_id 映射
        if aid in _ASSERTION_META:
            _, _, tag = _ASSERTION_META[aid]
        else:
            cat_id = action.get("_category_id", "")
            tag = _resolve_scene_tag(cat_id, aid)

        item = (aid, params, desc, tag)
        rank = _action_relevance(control_type, action, control_tags)

        if aid in _ASSERTION_META:
            if is_base_assert:
                base_asserts.append(item)
            else:
                specific_asserts.append((item, rank))
        else:
            if is_base_op:
                base_ops.append(item)
            else:
                specific_ops.append((item, rank))

    specific_ops.sort(key=lambda x: -x[1])
    specific_asserts.sort(key=lambda x: -x[1])

    operations = base_ops + [item for item, _ in specific_ops]
    assertions = base_asserts + [item for item, _ in specific_asserts]

    return {
        "control_type": control_type,
        "tags": sorted(control_tags),
        "operations": operations,
        "assertions": assertions,
    }


def build_control_action_prompt(operation: str, expected_feedback: str = "") -> str:
    """根据操作描述识别控件类型，只注入源头判定适用于该控件的操作/断言。

    用于 Step3 生成用例时精确限制 AI 可选择的 action type，避免 54 个操作全量注入。
    数量由 applicable_tags 与控件类型的匹配关系自然决定，不做硬截断。
    """
    if not operation:
        return ""
    try:
        from src.pipeline.steps.step3.case_generator._control_type import (
            detect_control_type,
        )

        control_type = detect_control_type(operation, expected_feedback)
    except Exception as e:
        _log.debug("[ActionInjection] 控件类型识别失败: %s", str(e)[:120])
        return ""

    filtered = filter_actions_by_control_type(control_type)
    operations = filtered["operations"]
    assertions = filtered["assertions"]

    def _format_lines(items):
        lines = []
        for aid, params, desc, _tag in items:
            param_str = ", ".join(params) if params else ""
            if param_str:
                lines.append("- %s(%s): %s" % (aid, param_str, desc))
            else:
                lines.append("- %s: %s" % (aid, desc))
        return lines

    op_lines = _format_lines(operations)
    assert_lines = _format_lines(assertions)

    parts = [
        "## 目标控件与允许操作",
        "识别到操作目标控件类型: %s（候选标签: %s）"
        % (control_type, ", ".join(filtered["tags"])),
        "生成 actions 时只能使用以下 type（禁止编造未列出的 type）：",
    ]
    if op_lines:
        parts.extend(op_lines)
    else:
        parts.append("- （无特定操作，使用通用 navigate/click/verify）")
    if assert_lines:
        parts.append("生成 checks 时只能使用以下断言 type：")
        parts.extend(assert_lines)

    return "\n".join(parts) + "\n"


def _is_word_boundary(text: str, start: int, end: int) -> bool:
    """检查关键词边界，避免中文单字被长词内部命中（如「表单」误匹配「表」）。"""
    if end - start == 1:
        ch = text[start]
        if "\u4e00" <= ch <= "\u9fff":
            if start > 0 and "\u4e00" <= text[start - 1] <= "\u9fff":
                return False
            if end < len(text) and "\u4e00" <= text[end] <= "\u9fff":
                return False
    return True


def _match_scenes(requirement_text: str) -> set:
    """根据需求文本匹配场景标签。"""
    if not requirement_text:
        return set()
    text_lower = requirement_text.lower()
    scenes = set()
    for scene, keywords in _SCENE_KEYWORDS.items():
        for kw in keywords:
            kw_lower = kw.lower()
            idx = text_lower.find(kw_lower)
            while idx != -1:
                if _is_word_boundary(text_lower, idx, idx + len(kw_lower)):
                    scenes.add(scene)
                    break
                idx = text_lower.find(kw_lower, idx + 1)
    return scenes


def _normalize_param(p: str) -> str:
    """去掉参数名后的 * 标记（如 ai_id* → ai_id）。"""
    return p.rstrip("*") if isinstance(p, str) else p


def _resolve_scene_tag(cat_id: str, action_id: str) -> str:
    """根据分类 ID 和操作 ID 解析场景 tag。"""
    mapping = _CATEGORY_SCENE_MAP.get(cat_id)
    if mapping is None:
        return cat_id if cat_id in _SCENE_KEYWORDS else "base"
    if isinstance(mapping, dict):
        return mapping.get(action_id, cat_id if cat_id in _SCENE_KEYWORDS else "base")
    return mapping


def _load_db_library() -> tuple:
    """从 MySQL action_library_store 读取操作库。

    返回: (operations, assertions)
        operations: [(aid, [params...], desc, tag), ...]
        assertions: [(aid, [params...], desc, tag), ...]
    """
    try:
        from src.pipeline.utils.action_library_store import action_library_store

        lib = action_library_store.load_library()
        categories = lib.get("categories", []) if isinstance(lib, dict) else []
        operations = []
        assertions = []
        for cat in categories:
            if not isinstance(cat, dict):
                continue
            cat_id = str(cat.get("id", "") or "").strip()
            is_assertion_cat = cat_id in _ASSERTION_CATEGORIES
            for action in cat.get("actions", []):
                if not isinstance(action, dict):
                    continue
                # 工作流指令不在 Step3 注入（由 Step4 按需识别或单独处理）
                if bool(action.get("is_workflow")):
                    continue
                aid = str(action.get("id", "") or "").strip()
                if not aid:
                    continue
                params = [_normalize_param(p) for p in (action.get("params") or [])]
                desc = str(action.get("desc", "") or action.get("label", "") or "")

                # 断言识别：优先匹配已知断言 action_id，其次按断言分类
                if aid in _ASSERTION_META:
                    _, _, tag = _ASSERTION_META[aid]
                    assertions.append((aid, params, desc, tag))
                elif is_assertion_cat:
                    assertions.append((aid, params, desc, cat_id))
                else:
                    tag = _resolve_scene_tag(cat_id, aid)
                    operations.append((aid, params, desc, tag))
        return operations, assertions
    except Exception as e:
        _log.debug("[ActionInjection] 从 DB 加载操作库失败: %s", str(e)[:120])
        return [], []


def build_action_injection_prompt(requirement_text: str = "") -> str:
    """构建操作/断言清单注入 Prompt 文本。

    根据需求文本智能筛选：
    1. 基础操作（navigate/click/fill/verify/screenshot）始终注入
    2. 需求文本命中场景关键词时，注入对应场景的专用操作
    3. 无需求文本时不注入（让 AI 自由生成，操作清单已在代码中定义，无需执行时二次匹配）
    4. 优先从 MySQL action_library_store 读取用户自定义操作库；DB 失败时回退代码硬编码
    """
    # 无需求文本 → 不注入
    if not requirement_text:
        return ""

    scenes = _match_scenes(requirement_text)

    def _should_include(tag: str) -> bool:
        if tag == "base":
            return True
        return tag in scenes

    # 加载 DB 操作库并与硬编码清单合并：硬编码兜底 + DB 自定义扩展，按 action_id 去重（DB 优先）
    db_operations, db_assertions = _load_db_library()
    operations_source = list(_OPERATIONS)
    assertions_source = list(_ASSERTIONS)
    _seen_op_ids = {aid for aid, _, _, _ in operations_source}
    _seen_assert_ids = {aid for aid, _, _, _ in assertions_source}
    for item in db_operations:
        if item[0] not in _seen_op_ids:
            operations_source.append(item)
            _seen_op_ids.add(item[0])
    for item in db_assertions:
        if item[0] not in _seen_assert_ids:
            assertions_source.append(item)
            _seen_assert_ids.add(item[0])
    source_name = "DB+code" if (db_operations or db_assertions) else "code"

    # 筛选操作
    action_lines = []
    for aid, params, desc, tag in operations_source:
        if not _should_include(tag):
            continue
        param_str = ", ".join(params) if params else ""
        if param_str:
            action_lines.append("- %s(%s): %s" % (aid, param_str, desc))
        else:
            action_lines.append("- %s: %s" % (aid, desc))

    # 筛选断言
    assertion_lines = []
    for aid, params, desc, tag in assertions_source:
        if not _should_include(tag):
            continue
        param_str = ", ".join(params) if params else ""
        if param_str:
            assertion_lines.append("- %s(%s): %s" % (aid, param_str, desc))
        else:
            assertion_lines.append("- %s: %s" % (aid, desc))

    parts = ["## 可用操作与断言"]
    if action_lines:
        parts.append(
            "生成 actions 时用以下 type（括号内为必填参数，target_hint 为元素描述）:"
        )
        parts.extend(action_lines)
    if assertion_lines:
        parts.append("生成 checks 时用以下断言 type:")
        parts.extend(assertion_lines)

    result = "\n".join(parts) + "\n"
    _log.info(
        "[ActionInjection] 来源=%s 需求命中场景=%s，注入 %d 操作 + %d 断言",
        source_name,
        scenes,
        len(action_lines),
        len(assertion_lines),
    )
    return result
