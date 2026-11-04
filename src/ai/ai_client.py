# -*- coding: utf-8 -*-
"""
AI 核心客户端 (AiBrain)
========================
本模块提供 AI API 调用的底层封装，包括：
- 通用 API 调用（按任务类型路由）
- JSON 响应解析
- Token 计费统计
- 观察者回调（供 GUI 注册）

设计原则：
- 不包含任何业务 Prompt（需求拆解/测试用例生成等其他模块负责）
- 所有方法返回统一的 {"success": bool, "data": ...} 字典

实现：facade + mixins 模式，将原 ~350 行类拆分为多个职责单一的 mixin。
"""

from src.ai.ai_client_mixins._base import _BaseMixin
from src.ai.ai_client_mixins._business import _BusinessMixin
from src.ai.ai_client_mixins._parse import _ParseMixin
from src.ai.ai_client_mixins._stats import _StatsMixin


class AiBrain(_BaseMixin, _BusinessMixin, _ParseMixin, _StatsMixin):
    """
    DeepSeek AI 调用封装。

    使用方式：
        from src.ai.ai_client import ai_brain
        result = ai_brain._call_api(system_prompt="...", user_message="...")
    """

    pass


# =============================================================================
# 模块级单例实例
# =============================================================================
ai_brain = AiBrain()

# ★ 不自动挂载：避免 case_generator → ai_client → case_generator 循环导入
# 调用方首次访问 ai_brain.model_test_cases 时会先调用 _lazy_mount()
