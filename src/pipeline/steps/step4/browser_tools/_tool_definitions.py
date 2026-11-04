# -*- coding: utf-8 -*-
"""
工具 Schema 纯数据定义
======================
- _make_schema 辅助函数
- ALL_TOOLS：所有工具的 JSON Schema 列表
- _TOOL_PREFIX_MAP：工具名前缀 → action 类型映射规则
"""

from typing import Any


def _make_schema(name, description, properties, required_fields):
    """构造工具 schema 的辅助方法。"""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required_fields,
            },
        },
    }


ALL_TOOLS: list[dict[str, Any]] = [
    _make_schema(
        "browser_click",
        "点击页面中的一个元素。通过元素的人类可读描述（如按钮显示的文字）来定位。",
        {
            "target_hint": {
                "type": "string",
                "description": "元素描述。可带前缀 text= / placeholder= / label= / id= / name= 精确定位。",
            }
        },
        ["target_hint"],
    ),
    _make_schema(
        "browser_fill",
        "在输入框中填入文字内容。",
        {
            "target_hint": {"type": "string", "description": "输入框的人类可读描述"},
            "text": {"type": "string", "description": "要填入的文字"},
        },
        ["target_hint", "text"],
    ),
    _make_schema(
        "browser_select",
        "在下拉框中选择一个选项。",
        {
            "target_hint": {"type": "string", "description": "下拉框元素描述"},
            "option_text": {"type": "string", "description": "要选择的选项文字"},
        },
        ["target_hint", "option_text"],
    ),
    _make_schema(
        "browser_check",
        "勾选一个 checkbox 或 radio 元素。不管当前状态如何都会安全地执行勾选操作。",
        {"target_hint": {"type": "string", "description": "checkbox/radio 元素描述"}},
        ["target_hint"],
    ),
    _make_schema(
        "browser_uncheck",
        "取消勾选一个 checkbox 元素。",
        {"target_hint": {"type": "string", "description": "checkbox 元素描述"}},
        ["target_hint"],
    ),
    _make_schema(
        "browser_hover",
        "鼠标悬停在一个元素上。用于触发 hover 效果或展开下拉菜单。",
        {"target_hint": {"type": "string", "description": "要悬停的元素描述"}},
        ["target_hint"],
    ),
    _make_schema(
        "browser_double_click",
        "双击一个元素。",
        {"target_hint": {"type": "string", "description": "要双击的元素描述"}},
        ["target_hint"],
    ),
    _make_schema(
        "browser_right_click",
        "在元素上右键点击。",
        {"target_hint": {"type": "string", "description": "要右键的元素描述"}},
        ["target_hint"],
    ),
    _make_schema(
        "browser_drag",
        "把一个元素拖拽到另一个元素位置。",
        {
            "source_hint": {"type": "string", "description": "被拖拽的源元素描述"},
            "target_hint": {"type": "string", "description": "目标元素描述"},
        },
        ["source_hint", "target_hint"],
    ),
    _make_schema(
        "browser_press_key",
        "按下键盘按键。若提供了 focus_hint，会先把焦点给指定元素再按键。",
        {
            "key": {
                "type": "string",
                "enum": [
                    "Enter",
                    "Escape",
                    "Tab",
                    "Backspace",
                    "Delete",
                    "ArrowUp",
                    "ArrowDown",
                    "ArrowLeft",
                    "ArrowRight",
                    "PageUp",
                    "PageDown",
                    "Home",
                    "End",
                    "Space",
                ],
                "description": "要按下的键名（合法取值已列出）",
            },
            "focus_hint": {"type": "string", "description": "可选，先让焦点元素描述"},
        },
        ["key"],
    ),
    _make_schema(
        "browser_scroll",
        "滚动页面。可指定滚动方向和距离（像素）。",
        {
            "direction": {"type": "string", "enum": ["up", "down"], "default": "down"},
            "amount": {"type": "integer", "default": 500, "description": "滚动像素数"},
        },
        [],
    ),
    _make_schema(
        "browser_wait",
        "等待若干秒。用于等待动画、异步请求等。",
        {"seconds": {"type": "integer", "default": 2, "description": "等待秒数"}},
        [],
    ),
    _make_schema(
        "browser_navigate",
        "导航到指定 URL。",
        {"url": {"type": "string", "description": "完整 URL（包含 http/https）"}},
        ["url"],
    ),
    _make_schema(
        "browser_take_screenshot",
        "对当前页面截图，保存为 PNG 文件。",
        {
            "description": {
                "type": "string",
                "description": "截图的说明文字（如 登录失败时截图）",
            }
        },
        [],
    ),
    _make_schema(
        "browser_upload_file",
        "选择本地文件并上传到 file input。",
        {
            "target_hint": {
                "type": "string",
                "description": "上传元素描述（如上传/选择文件）",
            },
            "file_path": {"type": "string", "description": "要上传的文件的本地路径"},
        },
        ["target_hint", "file_path"],
    ),
    _make_schema(
        "get_page_state",
        "读取当前页面状态：页面 URL、标题以及元素摘要。用于在不确定下一步做什么时查询页面。",
        {},
        [],
    ),
    _make_schema(
        "verify_text_in_page",
        "验证页面是否包含指定文字。用于动作完成后对结果断言。",
        {"expect_text": {"type": "string", "description": "期望出现的文字"}},
        ["expect_text"],
    ),
    _make_schema(
        "verify_element_exists",
        "验证页面上是否存在符合描述的元素。",
        {"target_hint": {"type": "string", "description": "元素描述"}},
        ["target_hint"],
    ),
    _make_schema(
        "verify_no_error_text",
        "验证页面是否没有错误提示文字（如 error、失败、错误、alert、warning）。",
        {
            "not_expected": {
                "type": "string",
                "description": "可选。确认不要出现的特定错误文字",
            }
        },
        [],
    ),
    _make_schema(
        "api_verify",
        "验证最近一次 API 调用。可检查 HTTP 状态码和响应中是否含期望关键字。",
        {
            "expect_status": {
                "type": "integer",
                "default": 200,
                "description": "期望的 HTTP 状态码",
            },
            "expect_keyword": {
                "type": "string",
                "description": "期望在响应中出现的关键字",
            },
        },
        [],
    ),
    _make_schema(
        "finish",
        "所有操作已完成，调用此工具表示任务结束。会把结果摘要、自评和成功标志返回给上层。",
        {
            "result_summary": {
                "type": "string",
                "description": "本次操作结果的简要总结",
            },
            "success": {"type": "boolean", "description": "是否成功完成任务"},
            "evaluation": {
                "type": "string",
                "description": "【自评】分析上一步操作是否按预期完成。说明：1) 页面发生了什么变化 2) 与操作目标的差距 3) 为什么认为成功或失败",
            },
        },
        ["result_summary", "success"],
    ),
    # ★ 2026-07-02 扩展：新增 9 个原子操作工具
    _make_schema(
        "browser_focus",
        "将焦点移至指定元素。用于触发表单 onFocus 校验或焦点样式验证。",
        {"target_hint": {"type": "string", "description": "元素描述"}},
        ["target_hint"],
    ),
    _make_schema(
        "browser_blur",
        "从指定元素移除焦点。用于触发表单 onBlur 校验。",
        {"target_hint": {"type": "string", "description": "元素描述"}},
        ["target_hint"],
    ),
    _make_schema(
        "browser_long_press",
        "长按指定元素。用于移动端右键菜单触发或自定义长按交互。",
        {
            "target_hint": {"type": "string", "description": "元素描述"},
            "duration_ms": {
                "type": "integer",
                "default": 1000,
                "description": "长按持续时间（毫秒）",
            },
        },
        ["target_hint"],
    ),
    _make_schema(
        "browser_switch_tab",
        "切换浏览器标签页。可按索引或 URL 关键词切换。",
        {
            "index": {
                "type": "integer",
                "default": -1,
                "description": "标签页索引（-1 表示最后一个）",
            },
            "url_keyword": {
                "type": "string",
                "description": "可选，按 URL 关键词匹配标签页",
            },
        },
        [],
    ),
    _make_schema(
        "browser_switch_to_iframe",
        "切换到 iframe 上下文。后续操作将在 iframe 内执行。",
        {"target_hint": {"type": "string", "description": "iframe 元素描述"}},
        ["target_hint"],
    ),
    _make_schema(
        "browser_switch_to_default",
        "退出 iframe 上下文，回到父级或顶层页面。",
        {},
        [],
    ),
    _make_schema(
        "browser_download_file",
        "点击触发文件下载的元素，等待下载完成。",
        {
            "target_hint": {"type": "string", "description": "触发下载的元素描述"},
            "save_dir": {"type": "string", "description": "可选，保存目录路径"},
        },
        ["target_hint"],
    ),
    _make_schema(
        "browser_drag_offset",
        "从元素中心按偏移量拖拽。用于滑块验证码、画布拖拽等。",
        {
            "target_hint": {"type": "string", "description": "源元素描述"},
            "dx": {"type": "integer", "description": "X 轴偏移量（像素）"},
            "dy": {"type": "integer", "description": "Y 轴偏移量（像素）"},
        },
        ["target_hint", "dx", "dy"],
    ),
    _make_schema(
        "browser_resize_window",
        "调整浏览器视口尺寸。用于响应式布局测试。",
        {
            "width": {"type": "integer", "default": 1920, "description": "视口宽度"},
            "height": {"type": "integer", "default": 1080, "description": "视口高度"},
        },
        [],
    ),
    _make_schema(
        "browser_swipe",
        "从元素中心执行滑动手势。用于移动端场景。",
        {
            "target_hint": {"type": "string", "description": "元素描述"},
            "direction": {
                "type": "string",
                "enum": ["up", "down", "left", "right"],
                "default": "up",
            },
            "distance": {
                "type": "integer",
                "default": 200,
                "description": "滑动距离（像素）",
            },
        },
        ["target_hint"],
    ),
    _make_schema(
        "browser_context_menu_click",
        "右键点击元素弹出上下文菜单，再点击菜单中的指定项。",
        {
            "target_hint": {"type": "string", "description": "目标元素描述"},
            "menu_item_hint": {"type": "string", "description": "要点击的菜单项文字"},
        },
        ["target_hint", "menu_item_hint"],
    ),
    # ★ 2026-07-02 扩展：新增 5 个断言工具
    _make_schema(
        "verify_style",
        "验证元素的 CSS 样式。支持颜色、字体、可见性、CSS类、属性值、z-index 检查。",
        {
            "check_type": {
                "type": "string",
                "enum": [
                    "color_check",
                    "font_check",
                    "visibility_check",
                    "css_class_check",
                    "attribute_check",
                    "zindex_check",
                ],
                "description": "样式检查类型",
            },
            "selector": {"type": "string", "description": "CSS 选择器"},
            "expected": {"type": "string", "description": "期望值"},
            "property": {
                "type": "string",
                "description": "CSS 属性名（font_check 用）",
            },
            "attribute": {
                "type": "string",
                "description": "HTML 属性名（attribute_check 用）",
            },
            "class_name": {
                "type": "string",
                "description": "CSS 类名（css_class_check 用）",
            },
        },
        ["check_type", "selector"],
    ),
    _make_schema(
        "verify_position",
        "验证元素的精确位置或尺寸。支持容差。",
        {
            "check_type": {
                "type": "string",
                "enum": ["position_check", "exact_size_check"],
                "description": "检查类型",
            },
            "selector": {"type": "string", "description": "CSS 选择器"},
            "x": {"type": "number", "description": "期望 X 坐标（position_check 用）"},
            "y": {"type": "number", "description": "期望 Y 坐标（position_check 用）"},
            "width": {
                "type": "number",
                "description": "期望宽度（exact_size_check 用）",
            },
            "height": {
                "type": "number",
                "description": "期望高度（exact_size_check 用）",
            },
            "tolerance": {
                "type": "number",
                "default": 0,
                "description": "容差（像素）",
            },
        },
        ["check_type", "selector"],
    ),
    _make_schema(
        "verify_toast",
        "验证 Toast 提示或 Loading 状态。支持出现/消失/状态检查。",
        {
            "check_type": {
                "type": "string",
                "enum": [
                    "toast_appear_check",
                    "toast_disappear_check",
                    "loading_state_check",
                ],
                "description": "检查类型",
            },
            "text": {
                "type": "string",
                "description": "可选，Toast 文本内容（toast_appear_check 用）",
            },
            "expected": {
                "type": "string",
                "enum": ["visible", "hidden"],
                "default": "hidden",
                "description": "期望状态（loading_state_check 用）",
            },
            "timeout_ms": {
                "type": "integer",
                "default": 3000,
                "description": "等待超时（毫秒）",
            },
        },
        ["check_type"],
    ),
    _make_schema(
        "verify_refresh",
        "验证页面是否发生了刷新。",
        {
            "expected": {
                "type": "string",
                "enum": ["refreshed", "not_refreshed"],
                "default": "refreshed",
                "description": "期望是否刷新",
            }
        },
        [],
    ),
    _make_schema(
        "verify_response_time",
        "验证 API 响应时间是否在阈值内。",
        {
            "url_pattern": {"type": "string", "description": "API URL 匹配模式"},
            "max_ms": {
                "type": "integer",
                "default": 5000,
                "description": "最大允许耗时（毫秒）",
            },
        },
        ["url_pattern"],
    ),
    # ★ 2026-07-02 扩展第二批：新增原子操作工具
    _make_schema(
        "browser_type_text",
        "逐字符输入文字，模拟真实键盘输入。触发 keydown/input/keyup 完整事件链。用于自动补全、实时搜索、字数统计等场景。",
        {
            "target_hint": {"type": "string", "description": "输入框元素描述"},
            "text": {"type": "string", "description": "要输入的文字"},
            "delay_ms": {
                "type": "integer",
                "default": 50,
                "description": "每字符延迟（毫秒）",
            },
        },
        ["target_hint", "text"],
    ),
    _make_schema(
        "browser_page_reload",
        "刷新当前页面。用于操作后刷新验证数据持久化。",
        {
            "wait_until": {
                "type": "string",
                "enum": ["load", "domcontentloaded", "networkidle"],
                "default": "load",
            }
        },
        [],
    ),
    _make_schema(
        "browser_go_back",
        "浏览器后退一页。用于返回上一页、面包屑导航等场景。",
        {},
        [],
    ),
    _make_schema(
        "browser_go_forward",
        "浏览器前进一页。",
        {},
        [],
    ),
    _make_schema(
        "browser_clear",
        "清空输入框内容。触发完整的清除事件链（focus+selectAll+delete+change）。",
        {"target_hint": {"type": "string", "description": "输入框元素描述"}},
        ["target_hint"],
    ),
    _make_schema(
        "browser_key_down",
        "按下键盘按键（不释放）。用于组合键操作（如按住Shift多选）。",
        {
            "key": {
                "type": "string",
                "description": "按键名（如 Shift/Control/Alt/Meta）",
            }
        },
        ["key"],
    ),
    _make_schema(
        "browser_key_up",
        "释放键盘按键。与 key_down 配合使用。",
        {"key": {"type": "string", "description": "按键名"}},
        ["key"],
    ),
    _make_schema(
        "browser_wheel",
        "模拟原生鼠标滚轮滚动。能触发 Canvas/地图等只响应原生 wheel 事件的场景。",
        {
            "delta_x": {"type": "integer", "default": 0, "description": "水平滚动量"},
            "delta_y": {
                "type": "integer",
                "default": 300,
                "description": "垂直滚动量（正值向下）",
            },
        },
        [],
    ),
    _make_schema(
        "browser_close_tab",
        "关闭当前标签页。如果有多个标签页，关闭后自动切换到剩余的第一个。",
        {},
        [],
    ),
    _make_schema(
        "browser_middle_click",
        "中键点击元素。用于在新标签页打开链接等场景。",
        {"target_hint": {"type": "string", "description": "元素描述"}},
        ["target_hint"],
    ),
    _make_schema(
        "browser_multi_touch_pinch",
        "模拟双指缩放手势。用于移动端地图、图片缩放等场景。",
        {
            "target_hint": {"type": "string", "description": "可选，缩放中心元素描述"},
            "scale": {
                "type": "number",
                "default": 1.5,
                "description": "缩放比例（>1放大，<1缩小）",
            },
        },
        [],
    ),
    _make_schema(
        "browser_set_date",
        "设置日期选择器的值。支持原生 input[type=date] 和自定义日历组件。",
        {
            "target_hint": {"type": "string", "description": "日期选择器元素描述"},
            "value": {"type": "string", "description": "日期值（YYYY-MM-DD 格式）"},
        },
        ["target_hint", "value"],
    ),
    # ★ 2026-07-02 扩展第二批：新增断言工具
    _make_schema(
        "verify_element_count",
        "验证页面上匹配选择器的元素数量。",
        {
            "selector": {"type": "string", "description": "CSS 选择器"},
            "expected_count": {"type": "integer", "description": "期望数量"},
            "operator": {
                "type": "string",
                "enum": ["eq", "gte", "lte", "gt", "lt"],
                "default": "eq",
                "description": "比较操作符",
            },
        },
        ["selector", "expected_count"],
    ),
    _make_schema(
        "verify_element_enabled",
        "验证元素是否启用（非 disabled 状态）。",
        {
            "selector": {"type": "string", "description": "CSS 选择器"},
            "expected": {
                "type": "boolean",
                "default": True,
                "description": "true=应启用，false=应禁用",
            },
        },
        ["selector"],
    ),
    _make_schema(
        "verify_element_checked",
        "验证复选框/单选框的勾选状态。",
        {
            "selector": {"type": "string", "description": "CSS 选择器"},
            "expected": {
                "type": "boolean",
                "default": True,
                "description": "true=应勾选，false=应未勾选",
            },
        },
        ["selector"],
    ),
    _make_schema(
        "verify_select_value",
        "验证下拉框的选中值。",
        {
            "selector": {"type": "string", "description": "CSS 选择器"},
            "expected": {"type": "string", "description": "期望的选中值"},
        },
        ["selector", "expected"],
    ),
    _make_schema(
        "verify_element_text_equals",
        "验证元素文本是否精确等于期望值。",
        {
            "selector": {"type": "string", "description": "CSS 选择器"},
            "expected": {"type": "string", "description": "期望的精确文本"},
        },
        ["selector", "expected"],
    ),
    _make_schema(
        "verify_db_record_count",
        "验证数据库表中的记录数量。",
        {
            "table": {"type": "string", "description": "表名"},
            "where": {"type": "string", "description": "可选，WHERE 条件"},
            "expected_count": {"type": "integer", "description": "期望数量"},
            "operator": {
                "type": "string",
                "enum": ["eq", "gte", "lte", "gt", "lt"],
                "default": "eq",
            },
        },
        ["table", "expected_count"],
    ),
    _make_schema(
        "verify_db_record_not_exists",
        "验证数据库中不存在符合条件的记录。",
        {
            "table": {"type": "string", "description": "表名"},
            "where": {"type": "string", "description": "WHERE 条件"},
        },
        ["table", "where"],
    ),
    _make_schema(
        "verify_api_call_count",
        "验证 API 调用次数。",
        {
            "url_pattern": {"type": "string", "description": "API URL 匹配模式"},
            "expected_count": {"type": "integer", "description": "期望调用次数"},
            "operator": {
                "type": "string",
                "enum": ["eq", "gte", "lte", "gt", "lt"],
                "default": "eq",
            },
        },
        ["url_pattern", "expected_count"],
    ),
    _make_schema(
        "verify_request_body",
        "验证 API 请求体中的字段值。",
        {
            "url_pattern": {"type": "string", "description": "API URL 匹配模式"},
            "expected_field": {
                "type": "string",
                "description": "期望的字段名（支持点号路径如 data.name）",
            },
            "expected_value": {"type": "string", "description": "期望的字段值"},
        },
        ["url_pattern", "expected_field", "expected_value"],
    ),
    _make_schema(
        "verify_image_loaded",
        "验证图片是否成功加载（naturalWidth > 0）。",
        {
            "selector": {"type": "string", "description": "img 元素的 CSS 选择器"},
            "expected": {
                "type": "boolean",
                "default": True,
                "description": "true=应已加载，false=应未加载",
            },
        },
        ["selector"],
    ),
    # ★ W3 T04 (2026-08-05): 网络故障注入（仅 NETWORK_FAULT_ENABLED 且用例声明时生效）
    _make_schema(
        "network_fault",
        "注入网络故障（offline/abort/fulfill/delay），用于降级测试。仅 NETWORK_FAULT_ENABLED 时生效。",
        {
            "mode": {
                "type": "string",
                "enum": ["offline", "abort", "fulfill", "delay"],
                "description": "故障模式",
            },
            "url_pattern": {
                "type": "string",
                "description": "URL 匹配模式（abort/fulfill/delay 用）",
            },
            "status": {
                "type": "integer",
                "default": 500,
                "description": "fulfill 模式响应状态码",
            },
            "body": {
                "type": "string",
                "default": "{}",
                "description": "fulfill 模式响应体",
            },
            "delay_ms": {
                "type": "integer",
                "default": 5000,
                "description": "delay 模式延迟毫秒数",
            },
        },
        ["mode"],
    ),
    # ★ W3 T01 (2026-08-05): 身份切换动作（本轮骨架，W4 per-case 热切换实现）
    _make_schema(
        "identity_switch",
        "切换登录身份（身份池 key）。本轮骨架，W4 实现会话内热切换。",
        {"identity": {"type": "string", "description": "身份池 key（如 admin/guest）"}},
        ["identity"],
    ),
    # ★ B5 (2026-08-16): 外部依赖 mock / 桩（仅 SERVICE_MOCK_ENABLED 且用例声明时生效）
    _make_schema(
        "service_mock",
        "对外部依赖（第三方 API / 支付网关 / 邮件服务）打桩：按 URL 模式拦截出站请求，"
        "返回 mock 响应（静态 JSON 或模板生成）、或改写/延迟请求。"
        "注意：这是可控打桩，不是故障注入（故障注入用 network_fault）。"
        "仅 SERVICE_MOCK_ENABLED 时生效。",
        {
            "mode": {
                "type": "string",
                "enum": ["register", "clear", "stats"],
                "default": "register",
                "description": "register=注册规则 / clear=清除全部 / stats=读命中统计",
            },
            "rules": {
                "type": "array",
                "description": "mock 规则数组；单规则可将字段平铺在顶层",
            },
            "url_pattern": {
                "type": "string",
                "description": "URL 匹配（子串 / * 通配 / ^正则）",
            },
            "methods": {
                "type": "array",
                "items": {"type": "string"},
                "description": "受拦截的 HTTP 方法，空=不限",
            },
            "action": {
                "type": "string",
                "enum": ["fulfill", "rewrite", "passthrough"],
                "default": "fulfill",
                "description": "fulfill=返回 mock 响应 / rewrite=改写请求后继续 / passthrough=只计数放行",
            },
            "status": {
                "type": "integer",
                "default": 200,
                "description": "fulfill 的 HTTP 状态码",
            },
            "body": {
                "type": "string",
                "description": "fulfill 的静态响应体（JSON 文本或纯文本）",
            },
            "template": {
                "type": "string",
                "description": "fulfill 的规则生成模板（{{seq}}/{{uuid}}/{{req.query.K}} 等占位符）",
            },
            "delay_ms": {
                "type": "integer",
                "default": 0,
                "description": "响应前可控延迟（毫秒）",
            },
            "times": {
                "type": "integer",
                "default": 0,
                "description": "最大生效次数（0=不限）；用尽后放行真实请求",
            },
            "rewrite": {
                "type": "object",
                "description": "action=rewrite 的改写体 {url,method,headers,post_data}",
            },
        },
        [],
    ),
    # ★ W3 T04 (2026-08-05): 契约校验（api_contract check 的 tool_name）
    _make_schema(
        "verify_api_contract",
        "验证 API 响应契约：必填字段存在 + 类型 + 业务码（fail-closed）。",
        {
            "url_pattern": {"type": "string", "description": "API URL 匹配模式"},
            "required_fields": {
                "type": "array",
                "items": {"type": "string"},
                "description": "必填字段列表",
            },
            "types": {"type": "object", "description": "字段类型映射 {field: type}"},
            "status_ok": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "允许的业务码列表",
            },
            "code_field": {"type": "string", "description": "业务码字段名"},
        },
        ["url_pattern"],
    ),
    # ★ 根因修复(2026-09-21): API 方法族动作统一入口（api_get/post/put/delete/patch
    #   经 ACTION_TAXONOMY 解析为 tool_name=api_request；method 语义由指令名携带）
    _make_schema(
        "api_request",
        "主动发起真实 HTTP 请求（API 方法族动作统一入口）。"
        "url 支持绝对地址或相对路径（相对时拼接 target_url）；"
        "结果同步登记 API 追踪面，可接 verify_api_* 断言族复核。",
        {
            "method": {
                "type": "string",
                "enum": ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
                "default": "GET",
                "description": "HTTP 方法",
            },
            "url": {"type": "string", "description": "请求 URL（绝对或相对路径）"},
            "base_url": {
                "type": "string",
                "description": "可选根地址；缺省用 config.target_url",
            },
            "params": {"type": "object", "description": "URL 查询参数"},
            "headers": {"type": "object", "description": "请求头（含鉴权头）"},
            "json": {"type": "object", "description": "JSON 请求体（GET/HEAD 忽略）"},
            "data": {"type": "object", "description": "表单/原始请求体（GET/HEAD 忽略）"},
            "timeout_ms": {
                "type": "integer",
                "default": 15000,
                "description": "超时毫秒数；超时返回结构化 API_TIMEOUT 归因",
            },
            "expect_status": {
                "type": "integer",
                "description": "期望状态码（提供时 ok=状态码精确匹配；缺省 2xx=True）",
            },
        },
        ["url"],
    ),
]


# 工具名前缀到 action 类型的映射规则
_TOOL_PREFIX_MAP = {
    "browser_click": "click",
    "browser_double_click": "double_click",
    "browser_right_click": "right_click",
    "browser_fill": "fill",
    "browser_select": "select_option",
    "browser_select_option": "select_option",
    "browser_check": "check",
    "browser_uncheck": "uncheck",
    "browser_hover": "hover",
    "browser_scroll": "scroll",
    "browser_press_key": "press_key",
    "browser_wait": "wait",
    "browser_navigate": "navigate",
    "browser_take_screenshot": "screenshot",
    "browser_upload_file": "upload_file",
    "browser_drag": "drag",
    "browser_extract_data": "verify",
    "verify_text_in_page": "verify",
    "verify_element_exists": "verify",
    "verify_no_error_text": "verify",
    "api_verify": "api_verify",
    # ★ 2026-07-02 扩展：新增工具映射
    "browser_focus": "focus",
    "browser_blur": "blur",
    "browser_long_press": "long_press",
    "browser_switch_tab": "switch_tab",
    "browser_switch_to_iframe": "switch_to_iframe",
    "browser_switch_to_default": "switch_to_default",
    "browser_download_file": "download_file",
    "browser_drag_offset": "drag_offset",
    "browser_resize_window": "resize_window",
    "browser_swipe": "swipe",
    "browser_context_menu_click": "context_menu_click",
    "verify_style": "verify_style",
    "verify_position": "verify_position",
    "verify_toast": "verify_toast",
    "verify_refresh": "verify_refresh",
    "verify_response_time": "response_time_check",
    # ★ 2026-07-02 扩展第二批：新增工具映射
    "browser_type_text": "type_text",
    "browser_page_reload": "page_reload",
    "browser_go_back": "go_back",
    "browser_go_forward": "go_forward",
    "browser_clear": "clear",
    "browser_key_down": "key_down",
    "browser_key_up": "key_up",
    "browser_wheel": "wheel",
    "browser_close_tab": "close_tab",
    "browser_middle_click": "middle_click",
    "browser_multi_touch_pinch": "multi_touch_pinch",
    "browser_set_date": "set_date",
    "verify_element_count": "element_count_check",
    "verify_element_enabled": "element_enabled_check",
    "verify_element_checked": "element_checked_check",
    "verify_select_value": "select_value_check",
    "verify_element_text_equals": "element_text_equals_check",
    "verify_db_record_count": "db_record_count_check",
    "verify_db_record_not_exists": "db_record_not_exists_check",
    "verify_api_call_count": "api_call_count_check",
    "verify_request_body": "request_body_check",
    "verify_image_loaded": "image_loaded_check",
    # ★ 2026-07-06 扩展第三批：补齐 9 个对称缺失 + 高频断言
    "verify_url": "url_check",
    "verify_element_exists_check": "element_exists_check",
    "verify_element_visible": "element_visible_check",
    "verify_element_attribute": "element_attribute_check",
    "verify_response_body": "api_response_body_check",
    "verify_db_record_exists": "db_record_exists_check",
    "verify_db_field_value": "db_field_value_check",
    "verify_db_query": "db_query_check",
    "verify_no_console_error": "console_error_check",
    "verify_dialog": "dialog_check",
    # ★ 2026-07-07 P0/P1/P2 致命遗漏补齐 12 个断言映射
    "verify_loading_complete": "loading_complete_check",
    "verify_db_ui_consistency": "db_ui_consistency_check",
    "verify_text_regex": "text_regex_check",
    "verify_list_order": "list_order_check",
    "verify_download": "download_check",
    "verify_storage": "storage_check",
    "verify_network": "network_check",
    "verify_animation_complete": "animation_complete_check",
    "verify_pagination": "pagination_check",
    "verify_element_attribute_all": "element_attribute_all_check",
    "verify_time_range": "time_range_check",
    "verify_websocket": "websocket_check",
    # ★ 2026-07-07 P3 补齐 3 个高频遗漏
    "verify_api_header": "api_header_check",
    "verify_page_load_time": "page_load_time_check",
    "verify_text_contains": "text_contains_check",
    # ★ W3 T04 (2026-08-05): 网络故障注入 / 身份切换
    "network_fault": "network_fault",
    "identity_switch": "identity_switch",
    # ★ B5 (2026-08-16): 外部依赖 mock / 桩（可控打桩，非故障注入）
    "service_mock": "service_mock",
    "verify_api_contract": "api_contract_check",
}


# ★ 动作/断言体系归一化（2026-08-04）：权威分类常量（四端共用）。
#   定义集中在 browser_tools/_taxonomy.py，此处 re-export 保持既有模块入口兼容。
#   设计依据：docs/system_design.md Part A §2。
from src.pipeline.steps.step4.browser_tools._taxonomy import (  # noqa: E402
    ACTION_TAXONOMY,
    CHECK_TAXONOMY,
    resolve_check_type,
    is_workflow_action,
)
