# -*- coding: utf-8 -*-
from __future__ import annotations

"""规则提取 - 控件推断模块
====================================
★ 架构债清理(2026-07-25): 从 _rule_extractor.py 拆出（§4.3 单文件≤800行）。
★ P0-downgrade-removal(2026-07-29): 删除降级逻辑，降级=失败，不再生成 display 降级用例。

职责：
  1. _infer_control_type_from_operation: 根据 operation 推断控件类型（13种，不含display）
  2. _infer_page_from_api_path: 从 API 路径推断页面路径
通用性（§4.2）：nav_map 数据驱动 + 通用语义推断，无业务关键词硬编码。
"""

import logging

_log = logging.getLogger(__name__)


def _infer_control_type_from_operation(operation: str) -> str:
    """根据需求 operation 推断控件类型（通用语义，中英文双语支持）。

    ★ v3.0 (2026-07-10): 扩展为 14 种 control_type，激活全部 14 个 case_rules。
      原 v2.0 只产出 5 种（input/select/upload/display/button），导致 9 个规则死代码。
      新增英文同义词映射 + table/form/modal/date/checkbox/radio/switch/navigation 产出。
    """
    if not operation:
        return "button"
    op = operation.lower()

    # ★ v3.0: 按 specificity 从高到低匹配（特殊控件优先于通用 input/button）
    # 1. 日期/时间 → date
    if any(
        k in op
        for k in ["日期", "时间", "date", "time", "calendar", "日期选择", "时间选择"]
    ):
        return "date"
    # 2. 复选 → checkbox（比 select 更 specific）
    if any(k in op for k in ["复选", "勾选", "checkbox", "check box", "多选框"]):
        return "checkbox"
    # 3. 单选 → radio
    if any(k in op for k in ["单选", "radio", "单选框", "单选按钮"]):
        return "radio"
    # 4. 开关/启用/禁用/切换 → switch
    if any(
        k in op
        for k in [
            "开关",
            "启用",
            "禁用",
            "切换",
            "switch",
            "toggle",
            "开启",
            "关闭功能",
            "enable",
            "disable",
        ]
    ):
        return "switch"
    # 5. 导航/菜单/标签页/面包屑 → navigation
    if any(
        k in op
        for k in [
            "导航",
            "菜单",
            "标签页",
            "面包屑",
            "navigation",
            "menu",
            "tab",
            "breadcrumb",
            "侧边栏",
            "navbar",
        ]
    ):
        return "navigation"
    # 6. 弹窗/对话框 → modal
    if any(
        k in op
        for k in [
            "弹窗",
            "对话框",
            "确认框",
            "modal",
            "dialog",
            "popup",
            "提示框",
            "抽屉",
            "drawer",
        ]
    ):
        return "modal"
    # 7. 表格/列表/记录 → table
    if any(
        k in op
        for k in ["表格", "列表", "记录", "table", "list", "grid", "数据表", "明细"]
    ):
        return "table"
    # 8. 表单/多字段 → form
    if any(
        k in op
        for k in [
            "表单",
            "多字段",
            "form",
            "multi_field",
            "composite",
            "复合表单",
            "登记表",
        ]
    ):
        return "form"
    # 9. 上传/导入 → upload（需要 upload_file 动作）
    # ★ 通用修复(2026-07-20): 把"下载"/"导出"从 upload 分类中移除
    #   原因：下载/导出操作的本质是点击按钮触发文件下载，不需要 upload_file 动作
    #   错误分类会导致 fallback 用例生成 upload_file + click('导入')，与需求不匹配
    #   通用性：所有"下载XX模版"、"导出XX数据"操作都应归类为 button（点击类）
    #   覆盖性：影响 _infer_control_type_from_operation 的所有调用方（fallback_cases、rule_extractor 等）
    if any(k in op for k in ["上传", "导入", "upload", "import"]):
        return "upload"
    # 10. 选择/下拉/选中 → select（checkbox/radio 已被前面拦截）
    if any(
        k in op
        for k in ["选择", "下拉", "选中", "select", "choose", "dropdown", "pick"]
    ):
        return "select"
    # 11. 输入/填写/编辑/录入/修改 + create/edit → input
    if any(
        k in op
        for k in [
            "输入",
            "填写",
            "编辑",
            "录入",
            "修改",
            "新增",
            "添加",
            "创建",
            "input",
            "fill",
            "edit",
            "modify",
            "update",
            "create",
            "add",
            "enter",
        ]
    ):
        return "input"
    # 12. 查询/搜索/筛选/检索/查找 + search/filter → search（激活 search_rule）
    if any(
        k in op
        for k in [
            "查询",
            "搜索",
            "筛选",
            "检索",
            "查找",
            "search",
            "filter",
            "find",
            "query",
        ]
    ):
        return "search"
    # 13. ★ P0-downgrade-removal(2026-07-29): 删除 display 降级分支
    #    展示/显示/查看/加载/浏览 类操作不再降级为 display 用例，
    #    而是 fallback 到 button 类型，由 AgentExplorer 探索执行。
    #    降级 = 失败，引擎不应对用例做降级处理。
    # 14. 默认点击类操作（提交、确认、删除、新建等都是 button）
    return "button"


def _infer_page_from_api_path(api_path: str) -> str | None:
    """从 API 路径推断页面路径（通用，取前缀）。

    例如 /dataManagement/auxiliaryFormulation/getList → /dataManagement/auxiliaryFormulation
    例如 /api/user/login → /api/user
    例如 /productTask/{id} → /productTask（过滤占位符段）
    """
    if not api_path or not api_path.startswith("/"):
        return None
    parts = api_path.split("/")
    # /dataManagement/auxiliaryFormulation/getList → ["", "dataManagement", "auxiliaryFormulation", "getList"]
    # 过滤掉含 {占位符} 的路径段，取前两级非占位符段
    clean_parts = [
        p for p in parts[1:] if p and not (p.startswith("{") and p.endswith("}"))
    ]
    if len(clean_parts) >= 2:
        return "/" + "/".join(clean_parts[:2])
    # ★ Fix33 (2026-07-09): 单段 API 路径（如 /fmsGetFormulaById）是 API 端点，
    #   不是页面路由。返回 None 让调用方走其他路径（DOM page_path 或 AI fallback）。
    return None
