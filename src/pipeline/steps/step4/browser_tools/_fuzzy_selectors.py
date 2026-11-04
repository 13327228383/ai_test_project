# -*- coding: utf-8 -*-
"""模糊选择器常量模块 — 从 locator.py 拆分（§4.3 单文件≤800行）。

职责：定义 click/input 两种 kind 的模糊匹配 CSS 选择器模板。
被 _basic_locator.py / _fallback_locator.py 依赖。
"""
import logging

_log = logging.getLogger(__name__)


# ── 通用模糊定位兜底：先精确，再核心关键词子串匹配 ──
#   供 browser_tools 与结构化执行器共享，避免各处重复实现。
#   ★ v6 通用修复(2026-07-20): 扩展 click 模糊匹配模板，覆盖 W3C ARIA 交互角色 +
#     主流 UI 框架菜单项选择器。原模板只有 button/a/role=button，导致菜单项/Tab/树节点
#     无法被模糊定位，是 Step4 执行"click(sidebar菜单)"失败的根因之一。
#   ★ v7 通用修复(2026-07-22): 新增对话框底部按钮选择器，覆盖 Modal/Dialog/Footer 按钮。
#     根因: Ant Design Modal footer 的"确定"按钮用 <button class="ant-btn ant-btn-primary">
#     <span>确 定</span></button>，button:has-text("确定") 应匹配但因 span 内空格
#     (Ant Design Button 图标间距)和多个按钮共存导致定位歧义。
#     通用修复: 优先用对话框 footer 容器限定范围，再匹配按钮文本。
#     通用性: 适用于 Ant Design/ElementUI/Naive UI/Bootstrap Modal 的确定/取消/保存按钮
_CLICK_FUZZY_SELECTORS = (
    # ★ v7: 对话框底部按钮优先（确定/取消/保存/提交在 footer 中最常见）
    #   Ant Design Modal/Drawer footer
    '.ant-modal-footer button:has-text("{kw}")',
    '.ant-modal-confirm-btns button:has-text("{kw}")',
    '.ant-drawer-footer button:has-text("{kw}")',
    #   ElementUI Dialog/Drawer footer
    '.el-dialog__footer button:has-text("{kw}")',
    '.el-drawer__footer button:has-text("{kw}")',
    #   Naive UI Modal/Drawer footer
    '.n-modal__action button:has-text("{kw}")',
    '.n-drawer__action button:has-text("{kw}")',
    #   Bootstrap Modal footer
    '.modal-footer button:has-text("{kw}")',
    #   通用 [role=dialog] 内的 button
    '[role="dialog"] button:has-text("{kw}")',
    '[role="alertdialog"] button:has-text("{kw}")',
    # 原生 HTML 标签
    'button:has-text("{kw}")',
    'a:has-text("{kw}")',
    'input[type="submit"][value*="{kw}"]',
    # ★ v6: W3C ARIA 交互角色
    '[role="button"]:has-text("{kw}")',
    '[role="menuitem"]:has-text("{kw}")',
    '[role="menuitemcheckbox"]:has-text("{kw}")',
    '[role="menuitemradio"]:has-text("{kw}")',
    '[role="tab"]:has-text("{kw}")',
    '[role="treeitem"]:has-text("{kw}")',
    '[role="link"]:has-text("{kw}")',
    '[role="option"]:has-text("{kw}")',
    '[role="checkbox"]:has-text("{kw}")',
    '[role="radio"]:has-text("{kw}")',
    '[role="switch"]:has-text("{kw}")',
    # ★ v6: Ant Design 菜单项（兜底，即使无 role 也能识别）
    '.ant-menu-item:has-text("{kw}")',
    '.ant-menu-submenu-title:has-text("{kw}")',
    '.ant-pro-menu-item:has-text("{kw}")',
    # ★ v6: ElementUI 菜单项
    '.el-menu-item:has-text("{kw}")',
    '.el-submenu__title:has-text("{kw}")',
    # ★ v6: Naive UI 菜单项
    '.n-menu-item:has-text("{kw}")',
    '.n-submenu-title:has-text("{kw}")',
)
_INPUT_FUZZY_SELECTORS = (
    '[placeholder*="{kw}"]',
    'input[aria-label*="{kw}"]',
    'input[name*="{kw}"]',
    'textarea[placeholder*="{kw}"]',
    # ★ 隐式 label 关联（HTML 规范：<label>名称<input></label>，控件是 label 的**后代**）
    #   原模板只有相邻(+)/兄弟(~)组合子，对包裹式 label 恒 0 命中 → fill 定位失败。
    'label:has-text("{kw}") input',
    'label:has-text("{kw}") select',
    'label:has-text("{kw}") textarea',
    'label:has-text("{kw}") + input',
    'label:has-text("{kw}") ~ input',
    # ★ v6: Ant Design 输入框（class 兜底）
    '.ant-input[placeholder*="{kw}"]',
    '.ant-input-affix-wrapper input[placeholder*="{kw}"]',
    # ★ v6: ElementUI 输入框
    '.el-input__inner[placeholder*="{kw}"]',
    '.el-textarea__inner[placeholder*="{kw}"]',
)
