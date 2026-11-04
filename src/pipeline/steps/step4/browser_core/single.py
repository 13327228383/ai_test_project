# -*- coding: utf-8 -*-
"""BrowserCore 单意图执行 mixin —— Facade。

职责：
- 组合 single_mixins/ 下的各 mixin，暴露统一的 _BrowserCoreSingleMixin

★ 架构定位（2026-06-18）★
========================================
本文件 169 行起的"AI 多轮循环"分支（else: ai_round 循环）是**兜底路径**，
仅当 step_action 不属于结构化白名单（click/fill/select_option/navigate/upload/
assert/verify）时触发。

它**不是**"发散测试"的执行路径。

发散测试（重放 / 安全 / 执行顺序异常等"对意图的反向全面覆盖"）由独立模块
src/pipeline/steps/step3_inverse/ 生成结构化反向意图，回到 step4 主流程的
_execute_structured_actions 路径执行——走 8 层 DOM 剪枝 + locator 精准查询
+ tool_calls 协议，不走本兜底路径。

因此本兜底循环按"先粗后细"策略优化：
- 仅取剪枝后前 50 个交互元素喂 AI（按元素截断防 JSON 切坏）
- AI 输出动作意图（target_hint），系统调 locator 精准查 → 不让 AI 在大 DOM 中"漫游"
"""

from .single_mixins._base import _BrowserCoreSingleBaseMixin
from .single_mixins._structured import _BrowserCoreSingleStructuredMixin
from .single_mixins._ai_loop import _BrowserCoreSingleAiLoopMixin
from .single_mixins._agent_checks import _BrowserCoreAgentChecksMixin


class _BrowserCoreSingleMixin(
    _BrowserCoreSingleBaseMixin,
    _BrowserCoreSingleStructuredMixin,
    _BrowserCoreSingleAiLoopMixin,
    _BrowserCoreAgentChecksMixin,
):
    """单意图执行能力 Facade。

    由四个职责单一的 mixin 组合而成：
    - _BrowserCoreSingleBaseMixin:     主框架（前置检查、插件、步骤循环、硬验证、结果汇总）
    - _BrowserCoreSingleStructuredMixin: 结构化动作/断言执行
    - _BrowserCoreSingleAiLoopMixin:   AI 多轮循环兜底路径
    - _BrowserCoreAgentChecksMixin:    Agent 探索后 checks 验证（§4.3 拆出，2026-08-06）
    """

    pass
