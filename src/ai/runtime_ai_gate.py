"""运行期 AI 闸门的唯一判据。

why: 架构要求「AI 能力前移到生成侧、运行期零 AI」—— 生成侧（Step2/3）允许用 AI
理解需求与生成结构化断言；执行侧（Step4 运行期）只做确定性判定，不再临场问模型。
此前运行期实测 0.70 次 AI/例（单次 5~30s），既是最大耗时项也违反该约束。

本模块是「是否允许 AI」的唯一实现；`router_module.route` 与直连 `_call_api`
两处收口点都只读它，不各写一份。

为什么用全局计数而非 ContextVar：Step4 会在**新线程**里 `asyncio.run`，
ContextVar 不跨线程传播；全局计数 + 锁既线程安全又支持重入。
"""

from __future__ import annotations

import threading

# Step4 运行期深度：>0 表示当前处于运行期（生成侧不受影响）。
_runtime_depth = 0
_runtime_lock = threading.Lock()


def enter_step4_runtime() -> None:
    global _runtime_depth
    with _runtime_lock:
        _runtime_depth += 1


def exit_step4_runtime() -> None:
    global _runtime_depth
    with _runtime_lock:
        _runtime_depth = max(0, _runtime_depth - 1)


def in_step4_runtime() -> bool:
    with _runtime_lock:
        return _runtime_depth > 0


def runtime_ai_enabled() -> bool:
    """运行期是否允许 AI（配置；默认关闭）。"""
    from src.core.pipeline_config import pipeline_config

    return bool(getattr(pipeline_config, "step4_runtime_ai", False))


def runtime_ai_denied() -> bool:
    """是否拦截本次 AI 调用：Step4 运行期 且 配置关闭运行期 AI。"""
    return in_step4_runtime() and not runtime_ai_enabled()
