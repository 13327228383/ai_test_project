# -*- coding: utf-8 -*-
"""
权威动作/断言分类体系 (Authoritative Taxonomy)
==============================================
动作/断言体系归一化的唯一权威分类骨架（四端共用：Step3 生成侧 / DB 注册表 /
Step4 执行端 / 前端展示）。设计依据：docs/system_design.md Part A §2。

- ACTION_TAXONOMY：动作类（browser_*）权威枚举
  key = action_id（action_actions.action_id），value = {
      category_id, tool_name, applicable_tags, is_workflow
  }
  tool_name 为空且 is_workflow=True 表示「无单工具」的工作流动作（如 import_workflow）。
- CHECK_TAXONOMY：断言类（verify_*/check）权威枚举
  key = check 类型（Step3 生成侧），value = {
      tool_name, track, aliases, description
  }
  track ∈ {ui, api, db} 表示执行轨；未知 check 类型 → FAIL（fail-closed），禁止静默 PASS。

★ W3 T01 关键改动 [已复核-DeepSeek-V4-Flash 2026-08-05]（2026-08-05）：
  - ACTION_TAXONOMY 新增 network_fault（network 类）/ identity_switch（identity 类）
  - CHECK_TAXONOMY 新增 api_contract（api 轨契约校验）
  - 新增 W3_FEATURE_FLAGS 开关注册表（11 项全部默认 OFF = 零行为变更）
  四端契约已同步：_tool_definitions.ALL_TOOLS / _TOOL_PREFIX_MAP / defaults 种子。
"""

from typing import Any

# ----------------------------------------------------------------------------
# 动作类权威枚举（Part A §2.1）
# ----------------------------------------------------------------------------
ACTION_TAXONOMY: dict[str, dict[str, Any]] = {
    # ── mouse 鼠标操作 ──
    "click": {
        "category_id": "mouse",
        "tool_name": "browser_click",
        "applicable_tags": ["button", "a", "div", "li", "td", "tr", "span", "input"],
        "is_workflow": False,
    },
    "double_click": {
        "category_id": "mouse",
        "tool_name": "browser_double_click",
        "applicable_tags": ["div", "tr", "td", "span", "img"],
        "is_workflow": False,
    },
    "right_click": {
        "category_id": "mouse",
        "tool_name": "browser_right_click",
        "applicable_tags": ["div", "tr", "td", "span", "a", "button"],
        "is_workflow": False,
    },
    "hover": {
        "category_id": "mouse",
        "tool_name": "browser_hover",
        "applicable_tags": ["div", "span", "a", "li", "button", "tr", "td"],
        "is_workflow": False,
    },
    "drag": {
        "category_id": "mouse",
        "tool_name": "browser_drag",
        "applicable_tags": ["div", "img", "tr", "span"],
        "is_workflow": False,
    },
    "scroll": {
        "category_id": "mouse",
        "tool_name": "browser_scroll",
        "applicable_tags": [],
        "is_workflow": False,
    },
    "middle_click": {
        "category_id": "mouse",
        "tool_name": "browser_middle_click",
        "applicable_tags": ["a", "div", "span", "button"],
        "is_workflow": False,
    },
    "multi_touch_pinch": {
        "category_id": "mouse",
        "tool_name": "browser_multi_touch_pinch",
        "applicable_tags": ["div", "img", "span"],
        "is_workflow": False,
    },
    # ── form 表单输入 ──
    "fill": {
        "category_id": "form",
        "tool_name": "browser_fill",
        "applicable_tags": ["input", "textarea"],
        "is_workflow": False,
    },
    "select_option": {
        "category_id": "form",
        "tool_name": "browser_select",
        "applicable_tags": ["select"],
        "is_workflow": False,
    },
    "check": {
        "category_id": "form",
        "tool_name": "browser_check",
        "applicable_tags": ["input"],
        "is_workflow": False,
    },
    "uncheck": {
        "category_id": "form",
        "tool_name": "browser_uncheck",
        "applicable_tags": ["input"],
        "is_workflow": False,
    },
    "upload_file": {
        "category_id": "form",
        "tool_name": "browser_upload_file",
        "applicable_tags": ["input"],
        "is_workflow": False,
    },
    "set_date": {
        "category_id": "form",
        "tool_name": "browser_set_date",
        "applicable_tags": ["input"],
        "is_workflow": False,
    },
    # ── keyboard 键盘操作 ──
    "press_key": {
        "category_id": "keyboard",
        "tool_name": "browser_press_key",
        "applicable_tags": [],
        "is_workflow": False,
    },
    "type_text": {
        "category_id": "keyboard",
        "tool_name": "browser_type_text",
        "applicable_tags": ["input", "textarea"],
        "is_workflow": False,
    },
    "clear": {
        "category_id": "keyboard",
        "tool_name": "browser_clear",
        "applicable_tags": ["input", "textarea"],
        "is_workflow": False,
    },
    "key_down": {
        "category_id": "keyboard",
        "tool_name": "browser_key_down",
        "applicable_tags": [],
        "is_workflow": False,
    },
    "key_up": {
        "category_id": "keyboard",
        "tool_name": "browser_key_up",
        "applicable_tags": [],
        "is_workflow": False,
    },
    "wheel": {
        "category_id": "keyboard",
        "tool_name": "browser_wheel",
        "applicable_tags": ["div", "span", "input", "textarea"],
        "is_workflow": False,
    },
    # ── navigation 页面导航 ──
    "navigate": {
        "category_id": "navigation",
        "tool_name": "browser_navigate",
        "applicable_tags": [],
        "is_workflow": False,
    },
    "wait": {
        "category_id": "navigation",
        "tool_name": "browser_wait",
        "applicable_tags": [],
        "is_workflow": False,
    },
    "page_reload": {
        "category_id": "navigation",
        "tool_name": "browser_page_reload",
        "applicable_tags": [],
        "is_workflow": False,
    },
    "go_back": {
        "category_id": "navigation",
        "tool_name": "browser_go_back",
        "applicable_tags": [],
        "is_workflow": False,
    },
    "go_forward": {
        "category_id": "navigation",
        "tool_name": "browser_go_forward",
        "applicable_tags": [],
        "is_workflow": False,
    },
    "close_tab": {
        "category_id": "navigation",
        "tool_name": "browser_close_tab",
        "applicable_tags": [],
        "is_workflow": False,
    },
    # ── advanced 高级交互 ──
    "focus": {
        "category_id": "advanced",
        "tool_name": "browser_focus",
        "applicable_tags": ["input", "textarea", "select", "button", "a"],
        "is_workflow": False,
    },
    "blur": {
        "category_id": "advanced",
        "tool_name": "browser_blur",
        "applicable_tags": ["input", "textarea", "select", "button", "a"],
        "is_workflow": False,
    },
    "long_press": {
        "category_id": "advanced",
        "tool_name": "browser_long_press",
        "applicable_tags": ["div", "a", "button", "span", "img"],
        "is_workflow": False,
    },
    "switch_tab": {
        "category_id": "advanced",
        "tool_name": "browser_switch_tab",
        "applicable_tags": [],
        "is_workflow": False,
    },
    "switch_to_iframe": {
        "category_id": "advanced",
        "tool_name": "browser_switch_to_iframe",
        "applicable_tags": ["iframe"],
        "is_workflow": False,
    },
    "switch_to_default": {
        "category_id": "advanced",
        "tool_name": "browser_switch_to_default",
        "applicable_tags": [],
        "is_workflow": False,
    },
    "download_file": {
        "category_id": "advanced",
        "tool_name": "browser_download_file",
        "applicable_tags": ["a", "button"],
        "is_workflow": False,
    },
    "drag_offset": {
        "category_id": "advanced",
        "tool_name": "browser_drag_offset",
        "applicable_tags": ["div", "img", "span"],
        "is_workflow": False,
    },
    "resize_window": {
        "category_id": "advanced",
        "tool_name": "browser_resize_window",
        "applicable_tags": [],
        "is_workflow": False,
    },
    "swipe": {
        "category_id": "advanced",
        "tool_name": "browser_swipe",
        "applicable_tags": ["div", "span"],
        "is_workflow": False,
    },
    "context_menu_click": {
        "category_id": "advanced",
        "tool_name": "browser_context_menu_click",
        "applicable_tags": ["div", "tr", "td", "span", "a", "button"],
        "is_workflow": False,
    },
    # ── import_export 导入导出工作流 ──
    "import_workflow": {
        "category_id": "import_export",
        "tool_name": "",  # 无单工具：由 DataFactory 工作流处理
        "applicable_tags": [],
        "is_workflow": True,
    },
    # ── W3 T04 (2026-08-05): 网络故障注入动作 ──
    #   仅当 NETWORK_FAULT_ENABLED 且用例显式声明才生效（双确认，零行为变更）。
    #   ★ 增强（2026-08-12）：throttle / connection_reset / delay_distribution
    "network_fault": {
        "category_id": "network",
        "tool_name": "network_fault",
        "applicable_tags": [],
        "is_workflow": False,
        # ★ 参数 schema：描述 network_fault 动作支持的所有参数
        "params_schema": {
            "mode": {
                "type": "string",
                "required": True,
                "enum": [
                    "offline",
                    "abort",
                    "fulfill",
                    "delay",
                    "throttle",
                    "connection_reset",
                ],
                "description": "故障注入模式",
            },
            "url_pattern": {
                "type": "string",
                "required": False,
                "default": "**/*",
                "description": "URL glob 模式（offline/throttle 外的 mode 需要）",
            },
            "status": {
                "type": "integer",
                "required": False,
                "default": 500,
                "description": "fulfill 模式的 HTTP 状态码",
            },
            "body": {
                "type": "string",
                "required": False,
                "default": "{}",
                "description": "fulfill 模式的响应体",
            },
            "delay_ms": {
                "type": "integer",
                "required": False,
                "default": 5000,
                "description": "delay 模式的固定延迟（毫秒）",
            },
            "delay_distribution": {
                "type": "string",
                "required": False,
                "enum": ["uniform", "normal"],
                "description": "delay 模式的随机延迟分布类型",
            },
            "delay_min": {
                "type": "integer",
                "required": False,
                "default": 0,
                "description": "uniform 分布最小延迟（毫秒）",
            },
            "delay_max": {
                "type": "integer",
                "required": False,
                "default": 5000,
                "description": "uniform 分布最大延迟（毫秒）",
            },
            "delay_mean": {
                "type": "number",
                "required": False,
                "default": 3000,
                "description": "normal 分布均值（毫秒）",
            },
            "delay_stddev": {
                "type": "number",
                "required": False,
                "default": 0,
                "description": "normal 分布标准差（毫秒）",
            },
            "download_throughput": {
                "type": "integer",
                "required": False,
                "default": 50000,
                "description": "throttle 模式下载带宽（bytes/s，-1=无限制）",
            },
            "upload_throughput": {
                "type": "integer",
                "required": False,
                "default": 20000,
                "description": "throttle 模式上传带宽（bytes/s，-1=无限制）",
            },
            "latency": {
                "type": "integer",
                "required": False,
                "default": 100,
                "description": "throttle 模式额外延迟（毫秒）",
            },
        },
    },
    # ── W3 T01 (2026-08-05): 身份切换动作（本轮骨架，W4 per-case 热切换实现）──
    #   声明即幂等注册；执行端默认由 IDENTITY_POOL_ENABLED 门控，不启用时无行为。
    "identity_switch": {
        "category_id": "identity",
        "tool_name": "identity_switch",
        "applicable_tags": [],
        "is_workflow": False,
    },
    # ── 根因修复(2026-09-21): API 方法族动作（统一入口 api_request）──
    #   此前用例集/文档声明了 api_get/post/put/delete/patch，但 ACTION_TAXONOMY
    #   无对应条目 → 执行层 NO_HANDLER。统一解析为 tool_name=api_request，
    #   method 语义由指令名携带（驱动器/生成侧按族名填 method 入参）。
    "api_get": {
        "category_id": "api",
        "tool_name": "api_request",
        "applicable_tags": [],
        "is_workflow": False,
        "default_args": {"method": "GET"},
    },
    "api_post": {
        "category_id": "api",
        "tool_name": "api_request",
        "applicable_tags": [],
        "is_workflow": False,
        "default_args": {"method": "POST"},
    },
    "api_put": {
        "category_id": "api",
        "tool_name": "api_request",
        "applicable_tags": [],
        "is_workflow": False,
        "default_args": {"method": "PUT"},
    },
    "api_delete": {
        "category_id": "api",
        "tool_name": "api_request",
        "applicable_tags": [],
        "is_workflow": False,
        "default_args": {"method": "DELETE"},
    },
    "api_patch": {
        "category_id": "api",
        "tool_name": "api_request",
        "applicable_tags": [],
        "is_workflow": False,
        "default_args": {"method": "PATCH"},
    },
    # ── B5 (2026-08-16): 外部依赖 mock / 桩（可控打桩，非故障注入）──
    #   仅当 SERVICE_MOCK_ENABLED 且用例显式声明才生效（双确认，零行为变更）。
    "service_mock": {
        "category_id": "network",
        "tool_name": "service_mock",
        "applicable_tags": [],
        "is_workflow": False,
        "params_schema": {
            "mode": {
                "type": "string",
                "required": False,
                "default": "register",
                "enum": ["register", "clear", "stats"],
                "description": "register=注册规则 / clear=清除全部 / stats=读命中统计",
            },
            "rules": {
                "type": "array",
                "required": False,
                "description": "mock 规则数组；单规则可将字段平铺在顶层",
            },
            "url_pattern": {
                "type": "string",
                "required": False,
                "description": "URL 匹配（子串 / * 通配 / ^正则）",
            },
            "methods": {
                "type": "array",
                "required": False,
                "description": "受拦截的 HTTP 方法，空=不限",
            },
            "action": {
                "type": "string",
                "required": False,
                "default": "fulfill",
                "enum": ["fulfill", "rewrite", "passthrough"],
                "description": "fulfill=返回 mock 响应 / rewrite=改写请求后继续 / passthrough=只计数放行",
            },
            "status": {
                "type": "integer",
                "required": False,
                "default": 200,
                "description": "fulfill 的 HTTP 状态码",
            },
            "body": {
                "type": "string",
                "required": False,
                "description": "fulfill 的静态响应体（str 原样；dict/list → JSON）",
            },
            "template": {
                "type": "string",
                "required": False,
                "description": "fulfill 的规则生成模板（{{seq}}/{{uuid}}/{{req.query.K}} 等占位符）",
            },
            "headers": {
                "type": "object",
                "required": False,
                "description": "fulfill 追加的响应头",
            },
            "delay_ms": {
                "type": "integer",
                "required": False,
                "default": 0,
                "description": "响应前可控延迟（毫秒）",
            },
            "times": {
                "type": "integer",
                "required": False,
                "default": 0,
                "description": "最大生效次数（0=不限）；用尽后放行真实请求",
            },
            "rewrite": {
                "type": "object",
                "required": False,
                "description": "action=rewrite 的改写体 {url,method,headers,post_data}",
            },
            "seed": {
                "type": "integer",
                "required": False,
                "description": "模板随机占位符的随机种子（可复现）",
            },
        },
    },
}

# ----------------------------------------------------------------------------
# 断言类权威枚举（Part A §2.2）— 11 种核心 check + 扩展 check
# ----------------------------------------------------------------------------
CHECK_TAXONOMY: dict[str, dict[str, Any]] = {
    # ── UI 轨核心 11 种 ──
    "element_exists": {
        "tool_name": "verify_element_exists_check",
        "track": "ui",
        "aliases": ["element_exists_check"],
        "description": "元素存在断言",
    },
    "text_contains": {
        "tool_name": "verify_text_contains",
        "track": "ui",
        "aliases": ["text_contains_check"],
        "description": "元素文本包含断言",
    },
    "url_matches": {
        "tool_name": "verify_url",
        "track": "ui",
        "aliases": ["url_check"],
        "description": "URL 匹配断言（url_check 归一入口）",
    },
    "no_error": {
        "tool_name": "verify_no_error_text",
        "track": "ui",
        "aliases": [],
        "description": "页面无错误提示断言",
    },
    "api_status": {
        "tool_name": "api_verify",
        "track": "api",
        "aliases": [],
        "description": "API 状态码断言",
    },
    "style": {
        "tool_name": "verify_style",
        "track": "ui",
        "aliases": [
            "color_check",
            "font_check",
            "visibility_check",
            "css_class_check",
            "attribute_check",
            "zindex_check",
        ],
        "description": "元素样式断言（颜色/字体/可见性/CSS类/属性/z-index）",
    },
    "toast": {
        "tool_name": "verify_toast",
        "track": "ui",
        "aliases": [
            "toast_appear_check",
            "toast_disappear_check",
            "loading_state_check",
        ],
        "description": "Toast/Loading 状态断言",
    },
    "refresh": {
        "tool_name": "verify_refresh",
        "track": "ui",
        "aliases": ["page_refresh_check"],
        "description": "页面刷新断言",
    },
    "position": {
        "tool_name": "verify_position",
        "track": "ui",
        "aliases": ["position_check", "exact_size_check"],
        "description": "元素位置/尺寸断言",
    },
    "element_count": {
        "tool_name": "verify_element_count",
        "track": "ui",
        "aliases": ["element_count_check"],
        "description": "元素数量断言",
    },
    "download": {
        "tool_name": "verify_download",
        "track": "ui",
        "aliases": ["download_check"],
        "description": "文件下载结果断言",
    },
    # ── UI 轨扩展 check ──
    "text_not_exists": {
        "tool_name": "verify_text_in_page",
        "track": "ui",
        "aliases": ["text_not_exists_check"],
        "description": "文本不存在断言（语义反转）",
    },
    "element_not_exists": {
        # ★ 补齐(2026-09-21): 原 tool_name 为空 → 该断言指令无法下发（注册缺口）。
        #   实现见 _single_exec_verify._verify_element_not_exists（存在/不存在能力对称）。
        "tool_name": "verify_element_not_exists",
        "track": "ui",
        "aliases": ["element_not_exists_check"],
        "description": "元素不存在断言",
    },
    "element_enabled": {
        "tool_name": "verify_element_enabled",
        "track": "ui",
        "aliases": ["element_enabled_check"],
        "description": "元素启用状态断言",
    },
    "element_checked": {
        "tool_name": "verify_element_checked",
        "track": "ui",
        "aliases": ["element_checked_check"],
        "description": "元素勾选状态断言",
    },
    "select_value": {
        "tool_name": "verify_select_value",
        "track": "ui",
        "aliases": ["select_value_check"],
        "description": "下拉框选中值断言",
    },
    "text_equals": {
        "tool_name": "verify_element_text_equals",
        "track": "ui",
        "aliases": ["element_text_equals_check"],
        "description": "元素文本精确相等断言",
    },
    "image_loaded": {
        "tool_name": "verify_image_loaded",
        "track": "ui",
        "aliases": ["image_loaded_check"],
        "description": "图片加载断言",
    },
    "loading_complete": {
        "tool_name": "verify_loading_complete",
        "track": "ui",
        "aliases": ["loading_complete_check"],
        "description": "异步加载完成断言",
    },
    "list_order": {
        "tool_name": "verify_list_order",
        "track": "ui",
        "aliases": ["list_order_check"],
        "description": "列表排序断言",
    },
    "element_visible": {
        "tool_name": "verify_element_visible",
        "track": "ui",
        "aliases": ["element_visible_check"],
        "description": "元素可见断言",
    },
    "element_attribute": {
        "tool_name": "verify_element_attribute",
        "track": "ui",
        "aliases": ["element_attribute_check"],
        "description": "元素属性断言",
    },
    "visual_check": {
        "tool_name": "verify_visual",
        "track": "ui",
        "aliases": [],
        "description": "视觉截图断言",
    },
    "geometry_check": {
        "tool_name": "verify_geometry",
        "track": "ui",
        "aliases": [],
        "description": "几何断言（no_overlap/in_viewport/not_occluded）",
    },
    "console_error": {
        "tool_name": "verify_no_console_error",
        "track": "ui",
        "aliases": ["console_error_check"],
        "description": "Console 错误断言",
    },
    "dialog": {
        "tool_name": "verify_dialog",
        "track": "ui",
        "aliases": ["dialog_check"],
        "description": "原生弹窗断言",
    },
    "text_regex": {
        "tool_name": "verify_text_regex",
        "track": "ui",
        "aliases": ["text_regex_check"],
        "description": "文本正则断言",
    },
    "storage": {
        "tool_name": "verify_storage",
        "track": "ui",
        "aliases": ["storage_check"],
        "description": "存储断言（cookie/localStorage/sessionStorage）",
    },
    "network": {
        "tool_name": "verify_network",
        "track": "ui",
        "aliases": ["network_check"],
        "description": "网络请求拦截断言",
    },
    "animation_complete": {
        "tool_name": "verify_animation_complete",
        "track": "ui",
        "aliases": ["animation_complete_check"],
        "description": "元素动画完成断言",
    },
    "pagination": {
        "tool_name": "verify_pagination",
        "track": "ui",
        "aliases": ["pagination_check"],
        "description": "分页数据正确性断言",
    },
    "element_attribute_all": {
        "tool_name": "verify_element_attribute_all",
        "track": "ui",
        "aliases": ["element_attribute_all_check"],
        "description": "多元素属性一致性断言",
    },
    "time_range": {
        "tool_name": "verify_time_range",
        "track": "ui",
        "aliases": ["time_range_check"],
        "description": "时间范围断言",
    },
    "websocket": {
        "tool_name": "verify_websocket",
        "track": "ui",
        "aliases": ["websocket_check"],
        "description": "WebSocket 消息断言",
    },
    "page_load_time": {
        "tool_name": "verify_page_load_time",
        "track": "ui",
        # ★ 2026-09-23 根治（性能族 ADI 归零/轨道错分）：
        #   生成端发射 performance_lcp_check / performance_fcp_check（PF 用例），
        #   本体语义即"页面加载性能断言"，但未登记为 page_load_time 族别名 →
        #   resolve_check_type 归一失败 → ADI 记 unresolved、轨道统计漏计 ui，
        #   性能族在 46 族盘点中恒为 "未点亮"。
        #   通用性：所有 *性能指标* 断言统一归 page_load_time 族（ui 轨）。
        "aliases": [
            "page_load_time_check",
            "performance_lcp",
            "performance_lcp_check",
            "performance_fcp",
            "performance_fcp_check",
        ],
        "description": "页面加载性能断言（含 LCP/FCP 指标阈值）",
    },
    "response_time": {
        "tool_name": "verify_response_time",
        "track": "api",
        "aliases": ["response_time_check"],
        "description": "API 响应时间断言",
    },
    # ── API 轨扩展 check ──
    "api_response_body": {
        "tool_name": "verify_response_body",
        "track": "api",
        "aliases": ["api_response_body_check"],
        "description": "API 响应体断言",
    },
    "api_call_count": {
        "tool_name": "verify_api_call_count",
        "track": "api",
        "aliases": ["api_call_count_check"],
        "description": "API 调用次数断言",
    },
    "api_header": {
        "tool_name": "verify_api_header",
        "track": "api",
        "aliases": ["api_header_check", "response_header_check"],
        "description": "API 响应头断言",
    },
    "request_body": {
        "tool_name": "verify_request_body",
        "track": "api",
        "aliases": ["request_body_check"],
        "description": "API 请求体断言",
    },
    # ── W3 T04 (2026-08-05): 契约校验族（api 轨）──
    #   从 YApi 响应 schema 生成断言，校验必填字段存在 + 类型 + 业务码（fail-closed）。
    "api_contract": {
        "tool_name": "verify_api_contract",
        "track": "api",
        "aliases": ["api_contract_check"],
        "description": "API 响应契约断言（必填字段/类型/业务码）",
    },
    # ── DB 轨 check ──
    "db_record_count": {
        "tool_name": "verify_db_record_count",
        "track": "db",
        "aliases": ["db_record_count_check"],
        "description": "DB 记录数断言",
    },
    "db_record_not_exists": {
        "tool_name": "verify_db_record_not_exists",
        "track": "db",
        "aliases": ["db_record_not_exists_check"],
        "description": "DB 记录不存在断言",
    },
    "db_record_exists": {
        "tool_name": "verify_db_record_exists",
        "track": "db",
        "aliases": ["db_record_exists_check"],
        "description": "DB 记录存在断言",
    },
    "db_field_value": {
        "tool_name": "verify_db_field_value",
        "track": "db",
        "aliases": ["db_field_value_check"],
        "description": "DB 字段值断言",
    },
    "db_query": {
        "tool_name": "verify_db_query",
        "track": "db",
        "aliases": ["db_query_check"],
        "description": "DB 自定义查询断言",
    },
    "db_ui_consistency": {
        "tool_name": "verify_db_ui_consistency",
        "track": "db",
        "aliases": ["db_ui_consistency_check"],
        "description": "DB 与 UI 一致性断言",
    },
    # ── ★ FIX4/AJ (2026-09-25): 访问控制行为断言（ui 轨）──
    #   访问控制需求（"低权角色不允许新增/编辑/删除，返回 403"）断言的是**行为**，
    #   不是页面文案。登记为 ui 轨权威族，使 ADI/轨道统计能归位该断言，
    #   避免未登记导致 unresolved 与轨道漏计（同 performance_lcp 的历史根因）。
    "access_control": {
        "tool_name": "verify_access_control",
        "track": "ui",
        "aliases": ["access_control_check"],
        "description": "访问控制行为断言（低权/未认证访问被拒绝：403/无权限/登录墙）",
    },
    # ── ★ F5 根因修复（2026-09-27）：补齐「执行端已注册、词表未登记」的 11 族 ──
    #   根因（三词表漂移，ponytail 五层 why）：
    #     L1 现象: ADI 日志刷屏 "[ADI] 检查族 'xxx' 未在 ADI_SCORE_TABLE 中 → 默认按 0 分
    #        处理（fail-closed）"，这批族（viewport_rendering_check / field_constraint_check /
    #        form_autocomplete_check / semantic_check / list_row_count_check /
    #        db_table_accessed_check 等）的**真实深度断言被静默按 0 分**计入 unresolved。
    #     L2 直接: Step3 生成端发射的 check 类型（由 case_rules / _fallback_cases /
    #        _intent_checks 发射）落在执行端注册表（UiTrackVerifier/ApiTrackVerifier/
    #        DbTrackVerifier → registered_check_types()）内、handler 齐备且实测可执行，
    #        但**未登记进 CHECK_TAXONOMY** → resolve_check_type 归一失败。
    #     L3 为何静默: ADI 只认 CHECK_TAXONOMY（自述为"断言类权威枚举"），它不读执行注册表，
    #        因此三方（生成端 / 执行端 / ADI 词表）各自一份词表，漂移必然复发。
    #     L4 影响: ①ADI 深度分布低估（真实值级断言记 0 → 引擎能力被误判为弱）；
    #        ②unresolved_top 被已实现的能力污染，诊断信号失真；
    #        ③ADI 门禁 / adi_signal 深断言信号（deep_assertion_fail_ratio）在开关打开时
    #        直接影响「假 PASS 拦截 / 回归判定」，等于拿错词表做判据。
    #     L5 根治: 把执行注册表里全部已注册类型**登记为权威族**，并加
    #        tests/test_oracle/test_taxonomy_drift.py 常驻漂移门禁（执行注册表 ⊆ CHECK_TAXONOMY
    #        ⊆ ADI_SCORE_TABLE），任一方新增类型而未同步即红。
    #   通用性: 跨系统/行业/页面 —— 纯词表登记，零业务硬编码。
    #   tool_name 留空：真实实现走 Step4 的 _UI_CHECK_DISPATCH / 各 TrackVerifier 处理器
    #     （按裸类型名分派），引擎侧 capability 投影（_single.py）遇空 tool_name 即跳过，
    #     维持零行为变更，不虚构 verify_* 实现名。
    "click_target_size": {
        "tool_name": "",
        "track": "ui",
        "aliases": ["click_target_size_check"],
        "description": "可点击目标尺寸断言（WCAG 2.5.8，易用性）",
    },
    "touch_target_mobile": {
        "tool_name": "",
        "track": "ui",
        "aliases": ["touch_target_mobile_check"],
        "description": "移动端触控目标尺寸断言（易用性）",
    },
    "viewport_overflow": {
        "tool_name": "",
        "track": "ui",
        "aliases": ["viewport_overflow_check"],
        "description": "视口水平溢出断言（无横向滚动条/响应式不破版）",
    },
    "viewport_rendering": {
        "tool_name": "",
        "track": "ui",
        "aliases": ["viewport_rendering_check"],
        "description": "多视口渲染断言（元素无重叠/不破版，兼容性）",
    },
    "form_autocomplete": {
        "tool_name": "",
        "track": "ui",
        "aliases": ["form_autocomplete_check"],
        "description": "表单输入目的自动填充断言（WCAG 1.3.5，易用性）",
    },
    "form_label_association": {
        "tool_name": "",
        "track": "ui",
        "aliases": ["form_label_association_check"],
        "description": "表单标签与控件关联断言（可访问性）",
    },
    "field_error_visible": {
        "tool_name": "",
        "track": "ui",
        "aliases": ["field_error_visible_check"],
        "description": "字段级错误提示可见断言（负向用例专用，属性/文本级）",
    },
    "field_constraint": {
        "tool_name": "",
        "track": "ui",
        "aliases": ["field_constraint_check"],
        "description": "字段约束属性断言（读控件 DOM 属性核对需求声明的约束）",
    },
    "list_row_count": {
        "tool_name": "",
        "track": "ui",
        "aliases": ["list_row_count_check"],
        "description": "列表精确行数计数断言（值级）",
    },
    "semantic": {
        "tool_name": "",
        "track": "ui",
        "aliases": ["semantic_check", "semantic"],
        "description": "AI 语义文本判定断言（机械文本优先、语义兜底）",
    },
    "db_table_accessed": {
        "tool_name": "",
        "track": "db",
        "aliases": ["db_table_accessed_check"],
        "description": "DB 表访问存在性探针断言（db 轨）",
    },
}


def resolve_check_type(check_type: str) -> str:
    """将任意 check 类型解析为 CHECK_TAXONOMY 权威 key。

    规则：
    1. 直接命中 CHECK_TAXONOMY → 返回该 key。
    2. 遍历 CHECK_TAXONOMY，若 check_type 在 aliases 中 → 返回权威 key。
    3. 去除 ``_check`` 后缀后再次匹配（兼容 Step3 生成的 *_check 类型）。
    4. 未命中 → 返回原值（上层据此 fail-closed）。
    """
    if not check_type:
        return ""
    if check_type in CHECK_TAXONOMY:
        return check_type
    for key, info in CHECK_TAXONOMY.items():
        if check_type in info.get("aliases", []):
            return key
    if check_type.endswith("_check"):
        stripped = check_type[: -len("_check")]
        if stripped in CHECK_TAXONOMY:
            return stripped
    return check_type


def is_workflow_action(action_id: str) -> bool:
    """判断 action_id 是否为工作流动作（无单工具）。"""
    entry = ACTION_TAXONOMY.get(action_id)
    return bool(entry and entry.get("is_workflow"))


# ----------------------------------------------------------------------------
# W3 特性开关注册表（T01，2026-08-05）
# ----------------------------------------------------------------------------
# 全部沿用 _read_config_value(key, default) 读取，**默认 OFF = 零行为变更**。
# 执行与否由各消费模块自行 _cfg 判断；此处仅为「权威默认值 + 单测对齐」清单。
W3_FEATURE_FLAGS: dict[str, object] = {
    # L0-1 归因报告区块
    "ATTRIBUTION_ENABLED": False,
    # 通过率分母剔除 BLOCKED/pre_gate_blocked（开启后口径变化，默认保留原口径）
    "ATTRIBUTION_STRICT_TPR": False,
    # L0-2 身份池总开关
    "IDENTITY_POOL_ENABLED": False,
    # 身份池 JSON 在 user_credentials_v2 中的 key（Fernet 加密）
    "IDENTITY_POOL_KEY": "IDENTITY_POOL",
    # run 级默认身份 key（空=沿用 TARGET_USERNAME 单身份，零行为变更）
    "IDENTITY_DEFAULT": "",
    # L0-4 契约断言生成/执行开关
    "CONTRACT_ENABLED": False,
    # 契约来源（yapi / openapi；2026-08-12 增强：openapi 支持 OpenAPI 3.0 / Swagger 2.0）
    "CONTRACT_SCHEMA_SOURCE": "yapi",
    # OpenAPI spec 文件路径或 URL（CONTRACT_SCHEMA_SOURCE=openapi 时生效）
    "CONTRACT_OPENAPI_SPEC": "",
    # L0-5 网络故障注入执行端总闸（用例声明 + 此开关双确认才生效）
    "NETWORK_FAULT_ENABLED": False,
    # L0-6 幂等比对报告区块开关（纯读）
    "IDEMPOTENCY_ENABLED": False,
    # L0-7 性能采集落库 + 基线报告开关
    "PERF_BASELINE_ENABLED": False,
    # 基线对比窗口（对比前 N 个 run）
    "PERF_BASELINE_WINDOW": 1,
}


__all__ = [
    "ACTION_TAXONOMY",
    "CHECK_TAXONOMY",
    "W3_FEATURE_FLAGS",
    "resolve_check_type",
    "is_workflow_action",
]
