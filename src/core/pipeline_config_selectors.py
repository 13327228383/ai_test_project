# -*- coding: utf-8 -*-
"""流水线 DOM 选择器集中默认值。

这些默认值被 src/core/pipeline_config.py 引用，作为 dom_* 配置键的缺省值。
项目可通过 DB/环境变量/YAML 覆盖对应配置键，无需修改本文件。
"""
from __future__ import annotations

import json

# 顶部导航栏选择器（完整默认含 Ant Design / ElementUI 等框架类）
DOM_TOP_NAV_SELECTORS = (
    "header nav a[href], "
    '[role="banner"] nav a[href], '
    ".top-nav a[href], .header-menu a[href], .main-nav a[href], "
    '[role="navigation"] > ul > li > a[href], '
    "nav > ul > li, .navbar > ul > li, "
    ".ant-menu-horizontal > li, "
    ".ant-menu-horizontal > .ant-menu-item, "
    "section.menu .ant-menu-horizontal > li, "
    "nav.ant-menu-horizontal > a[href], "
    "nav.el-menu--horizontal > a[href]"
)

# 菜单链接选择器
DOM_MENU_SELECTORS = (
    '[role="navigation"] a[href], nav a[href], '
    ".sidebar a[href], .menu a[href], .nav a[href], .main-menu a[href], "
    ".ant-menu a[href], .el-menu a[href]"
)

# 按钮式顶部导航选择器
DOM_TOP_NAV_BUTTON_SELECTORS = (
    "header nav button, .top-nav button, .header-menu button, "
    ".ant-menu-horizontal > li, "
    ".ant-menu-horizontal > .ant-menu-item, "
    "section.menu .ant-menu-horizontal > li, "
    "nav.ant-menu-horizontal > li"
)

# 登录成功后导航元素检测选择器
DOM_LOGIN_SUCCESS_NAV_SELECTORS = (
    '[role="navigation"], nav, [role="banner"], '
    '[class*="avatar"], .sidebar, '
    ".ant-layout-sider, .el-menu"
)

# Tab 选项卡选择器
DOM_TAB_SELECTORS = (
    '[role="tab"]:not(.ant-menu *), '
    ".ant-tabs-tab:not(.ant-menu *), "
    ".ant-tabs-tab-btn:not(.ant-menu *), "
    "label:not([for]):not(.ant-menu label):not(.ant-layout-sider label), "
    ".ant-radio-button-wrapper:not(.ant-menu *), "
    ".ant-radio-wrapper:not(.ant-menu *)"
)

# 内容区 Radio 导航选择器
DOM_RADIO_SELECTORS = (
    ".ant-radio-button-wrapper:not(.ant-menu *), " ".ant-radio-wrapper:not(.ant-menu *)"
)

# Radio 输入候选选择器（用于收集 menuKey/tab 候选值）
DOM_RADIO_INPUT_SELECTORS = (
    '.ant-radio-button-wrapper input[type="radio"], '
    '.ant-radio-wrapper input[type="radio"]'
)

# data-menu-key 选择器
DOM_DATA_MENU_KEY_SELECTOR = "[data-menu-key]"

# SPA 菜单选择器
DOM_SPA_MENU_SELECTORS = (
    ".ant-menu-item[data-menu-id], "
    ".ant-menu-submenu[data-submenu-id], "
    ".ant-menu-submenu-title, "
    ".ant-menu-inline .ant-menu-item, "
    ".ant-menu-inline .ant-menu-submenu-title, "
    ".ant-menu-horizontal .ant-menu-item, "
    ".ant-menu-horizontal .ant-menu-submenu-title"
)

# SPA 菜单备选选择器
DOM_SPA_MENU_FALLBACK_SELECTORS = (
    ".ant-menu-overflow-item, " "[data-menu-id], [data-submenu-id]"
)

# SPA 侧边栏菜单树 JS 选择器（JSON 对象）
DOM_SPA_SIDEBAR_TREE_SELECTORS = json.dumps(
    {
        "menu_item": ".ant-menu-item",
        "submenu": ".ant-menu-submenu",
        "submenu_title": ".ant-menu-submenu-title",
        "submenu_sub": ".ant-menu-sub",
        "menu": ".ant-menu",
        "menu_ul": "ul.ant-menu",
    }
)

# SPA 子菜单标题选择器
DOM_SPA_SUBMENU_TITLE_SELECTOR = ".ant-menu-submenu-title"

# SPA 叶子菜单选择器
DOM_SPA_LEAF_MENU_SELECTORS = ".ant-menu-item, .ant-menu-submenu-open .ant-menu-item"

# 功能清单选择器映射（JSON 对象）
DOM_FEATURE_SELECTORS = json.dumps(
    {
        "button": 'button, [role="button"], [class*="btn"], [class*="button"]',
        "input": 'input, textarea, select, [role="textbox"], [role="combobox"]',
        "link": 'a[href]:not([href^="#"]):not([href^="javascript:"])',
        "table": "table",
        "chart": 'canvas, svg[class*="chart"], [class*="echarts"], [class*="chart"], [class*="Chart"]',
        "dropdown": '.ant-select, .el-select, [role="listbox"], .ant-select-selection, .el-select__inner',
        "tab": '[role="tab"], .ant-tabs, .el-tabs, .ant-tabs-tab, .el-tabs__item',
        "modal": '.modal, [role="dialog"], .ant-modal, .el-dialog, .ant-modal-content, .el-dialog__body',
        "upload": 'input[type="file"], [class*="upload"], .ant-upload, .el-upload',
        "drag": '[draggable="true"], [class*="drag"], .ant-dragger, .el-dragger',
        "pagination": '.pagination, .ant-pagination, .el-pagination, [role="navigation"][class*="pagin"]',
        "search": '.search, [class*="search"], .ant-input-search, .el-input__search, [role="search"]',
        "switch": '[role="switch"], .ant-switch, .el-switch, button[aria-pressed]',
        "tree": '.tree, [role="tree"], .ant-tree, .el-tree',
        "calendar": '.calendar, .ant-calendar, .el-calendar, .ant-picker, .el-date-editor, [role="grid"][class*="calendar"]',
        "slider": '.slider, .ant-slider, .el-slider, [role="slider"], input[type="range"]',
        "rich_text": '.ql-editor, .tox-tinymce, .w-e-text, [contenteditable="true"], [class*="rich-text"], [class*="RichText"]',
        "cascader": '.ant-cascader, .el-cascader, [class*="cascader"]',
        "steps": '.ant-steps, .el-steps, [class*="steps"], [role="list"][class*="step"]',
        "timeline": '.ant-timeline, .el-timeline, [class*="timeline"]',
    }
)
