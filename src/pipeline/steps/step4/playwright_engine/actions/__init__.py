# -*- coding: utf-8 -*-
"""元素动作执行模块（包入口）：点击、填充、悬停、选择、拖拽、上传等。

【2026-10-27 死代码治理】
click.py / fill.py / type_text.py 及其余零引用动作方法已删除（证据见 AGENTS.md §4.11 F）：
引擎动作执行唯一路径是 action_library_store + browser_tools；本包只保留实际被引用的方法
（interactions._locate_and_act、motion.scroll_page、drag_offset_element 等）。

  interactions.py — _locate_and_act（其它原子动作共享的定位+执行助手）
  motion.py       — scroll_page（_single_exec_browser 直接调用）

【2026-07-02 扩展】新增 19 个子 mixin（覆盖全场景原子操作）：
  focus.py        — focus_element + blur_element
  long_press.py   — long_press_element
  tab_switch.py   — switch_tab_element
  iframe_switch.py — switch_to_iframe + switch_to_default
  file_download.py — download_file_element
  drag_offset.py  — drag_offset_element
  window_resize.py — resize_window_element
  swipe.py        — swipe_element
  context_menu.py — context_menu_click_element
  page_reload.py  — page_reload_element（主动刷新）
  go_back.py      — go_back_element + go_forward_element（浏览器导航）
  clear.py        — clear_element（清空输入框）
  key_press.py    — key_down_element + key_up_element（裸按键，组合键）
  wheel.py        — wheel_element（原生滚轮事件）
  close_tab.py    — close_tab_element（关闭标签页）
  middle_click.py — middle_click_element（中键点击）
  multi_touch.py  — multi_touch_pinch_element（多点触控缩放）
  set_date.py     — set_date_element（日期选择器）

ActionsMixin 多继承拼合，外部接口 100% 兼容。
"""

from .interactions import InteractionsMixin
from .motion import MotionMixin
from .focus import FocusMixin
from .long_press import LongPressMixin
from .tab_switch import TabSwitchMixin
from .iframe_switch import IframeSwitchMixin
from .file_download import FileDownloadMixin
from .drag_offset import DragOffsetMixin
from .window_resize import WindowResizeMixin
from .swipe import SwipeMixin
from .context_menu import ContextMenuMixin
from .page_reload import PageReloadMixin
from .go_back import GoBackMixin
from .clear import ClearMixin
from .key_press import KeyPressMixin
from .wheel import WheelMixin
from .close_tab import CloseTabMixin
from .middle_click import MiddleClickMixin
from .multi_touch import MultiTouchMixin
from .set_date import SetDateMixin


class ActionsMixin(
    InteractionsMixin,
    MotionMixin,
    FocusMixin,
    LongPressMixin,
    TabSwitchMixin,
    IframeSwitchMixin,
    FileDownloadMixin,
    DragOffsetMixin,
    WindowResizeMixin,
    SwipeMixin,
    ContextMenuMixin,
    PageReloadMixin,
    GoBackMixin,
    ClearMixin,
    KeyPressMixin,
    WheelMixin,
    CloseTabMixin,
    MiddleClickMixin,
    MultiTouchMixin,
    SetDateMixin,
):
    """通过 AI ID 定位元素并执行各类动作（23 个子 mixin 组合）。"""

    _page = None
    _timeout_ms: int = 30000
