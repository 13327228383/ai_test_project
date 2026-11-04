# -*- coding: utf-8 -*-
"""
全流程暂停控制器 (PauseController)
===================================
★ A2 改造：由"进程级单例"改为"按 project 隔离的多实例字典"。
  原实现：单 threading.Event，项目 A 暂停会阻塞项目 B。
  新实现：dict[project] → PauseController，每个项目独立 Event。
  兼容策略：保留 pause/resume/is_paused/check_pause 等无 project 参数方法，
  自动从 config.project_name 获取当前项目（向后兼容旧调用方）。
暂停粒度为当前迭代单元（需求/测试用例/步骤）完成后挂起。
"""

import threading
from src.core.guarded import guard
import logging
from src.core.pipeline_config import pipeline_config

_log = logging.getLogger(__name__)


class PauseController:
    """单项目暂停控制器（持有独立 threading.Event）。

    ★ A2 改造：不再作为全局单例直接使用，而是由 PauseControllerRegistry
    按 project 维度管理多个实例。每个实例持有独立的 threading.Event，
    确保项目 A 的暂停/恢复不影响项目 B。
    """

    def __init__(self):
        self._event = threading.Event()

    def pause(self):
        """触发暂停。执行循环在下一个检查点停止。"""
        self._event.set()

    def resume(self):
        """解除暂停，执行循环继续。"""
        self._event.clear()

    @property
    def is_paused(self) -> bool:
        return self._event.is_set()

    def sleep_while_paused(
        self, interval: float = 0.5, max_pause_seconds: float = None
    ):
        """阻塞当前线程直到 resume，超时后自动恢复，防止永久挂起。"""
        import time as _time

        max_pause_seconds = max_pause_seconds or pipeline_config.pause_max_wait_seconds
        start = _time.time()
        while self._event.is_set():
            if _time.time() - start >= max_pause_seconds:
                _log.warning(
                    "流水线同步暂停超过 %.1fs，自动恢复，防止永久挂起",
                    max_pause_seconds,
                )
                self._event.clear()
                break
            self._event.wait(interval)

    def check_pause(self):
        """★ 修复断链：_base.py:129 调 pause_controller.check_pause() 但方法从未定义。
        语义=同步检查暂停状态（暂停则阻塞至恢复），等同 sleep_while_paused。
        Step4 步骤循环每个子步骤开头调用，作为暂停检查点。"""
        self.sleep_while_paused()

    async def async_sleep_while_paused(
        self, interval: float = 0.5, max_pause_seconds: float = None
    ):
        """异步版本 — 挂起当前协程直到 resume，不阻塞事件循环。
        max_pause_seconds: 最长暂停时间（默认30分钟），超时后自动恢复，防止浏览器孤儿进程。"""
        import asyncio
        import time as _time

        max_pause_seconds = max_pause_seconds or pipeline_config.pause_max_wait_seconds
        start = _time.time()
        while self._event.is_set():
            elapsed = _time.time() - start
            if elapsed > max_pause_seconds:
                _log.info(
                    "  [PAUSE] ⚠ 暂停超过 %d 分钟，自动恢复执行（防止浏览器进程泄漏）",
                    (max_pause_seconds // 60),
                )
                self._event.clear()
                break
            await asyncio.sleep(interval)


class PauseControllerRegistry:
    """★ A2 改造：按 project 隔离的 PauseController 注册表。

    - for_project(project): 返回指定项目的 PauseController（不存在则创建）。
    - 无 project 参数的兼容方法（pause/resume/is_paused/check_pause/
      sleep_while_paused/async_sleep_while_paused）：自动从 config.project_name
      获取当前项目，委托给对应的 PauseController 实例。
    - resume_all(): 恢复所有项目的暂停（用于全局停止/退出等场景）。
    """

    def __init__(self):
        self._controllers: dict[str, PauseController] = {}
        self._lock = threading.Lock()

    def for_project(self, project: str) -> PauseController:
        """获取指定项目的 PauseController（不存在则创建）。"""
        if not project:
            project = "_default_"
        with self._lock:
            if project not in self._controllers:
                self._controllers[project] = PauseController()
            return self._controllers[project]

    def remove_project(self, project: str):
        """移除指定项目的 PauseController（任务结束后清理）。"""
        with self._lock:
            self._controllers.pop(project, None)

    def resume_all(self):
        """恢复所有项目的暂停（用于全局停止/退出等场景）。"""
        with self._lock:
            for ctrl in self._controllers.values():
                with guard("resume_all: ctrl.resume()", log=_log):
                    ctrl.resume()

    def _current(self) -> PauseController:
        """获取当前项目的 PauseController（从 config.project_name 推断）。"""
        try:
            from src.core.config_loader import config

            project = getattr(config, "project_name", "") or ""
        except Exception:
            project = ""
        return self.for_project(project)

    # ── 兼容方法：委托给当前项目的 PauseController ──
    # 这些方法保持与旧单例完全一致的签名，旧调用方无需改动。

    def pause(self):
        self._current().pause()

    def resume(self):
        self._current().resume()

    @property
    def is_paused(self) -> bool:
        return self._current().is_paused

    def sleep_while_paused(
        self, interval: float = 0.5, max_pause_seconds: float = None
    ):
        self._current().sleep_while_paused(interval, max_pause_seconds)

    def check_pause(self):
        self._current().check_pause()

    async def async_sleep_while_paused(
        self, interval: float = 0.5, max_pause_seconds: float = None
    ):
        await self._current().async_sleep_while_paused(interval, max_pause_seconds)


# ★ A2 改造：pause_controller 现在是 PauseControllerRegistry 实例，
#   保持模块级单例入口不变，但内部按 project 隔离。
pause_controller = PauseControllerRegistry()
