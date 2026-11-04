# -*- coding: utf-8 -*-
"""系统行为判据（唯一实现）：判断一段文本是否描述可执行/可测试的系统行为。

需求文档的 operation 不等于 UI 操作手册。同一条需求可以用三种方式陈述，
三者都构成可测试的行为：
  - 交互动词：用户对界面的动作（点击/输入/选择…，来自 operation_verbs.json 权威词典）
  - 系统行为谓词：系统对外可观察的反应（展示/返回/提示/置空/缓存/失效…）
  - 情态约束：系统必须保持的性质（必须/支持/不超过/唯一/非负…）
HTTP 接口清单（method + path）本身即接口契约，直接可测。

本模块是该判据的唯一定义处。提取期（declarative_line / numbered_list）与完整性
检查（_fd_completeness）只 import 使用，不各自维护词表；接口判据复用
step3._rule_extractor_keywords.is_api_operation（单一事实源）。
"""

import re
from functools import lru_cache

from src.pipeline.step3._rule_extractor_keywords import is_api_operation

# 原子行为的最小长度：低于「动作+对象」四字无法表达一个可测行为
MIN_ATOMIC_ACTION_LEN = 4

# 非 UI 动作的行为谓词（跨系统/行业通用）：系统「做/呈现/保持」什么
BEHAVIOR_VERBS = re.compile(
    r"展示|显示|呈现|列出|返回|提示|跳转|弹出|刷新|记录|发送|告警|通知|失效|同步|"
    r"置空|回填|排序|分页|校验|升级|保留|缓存|关联|拥有|具备|允许|拦截|触发"
)

# 通用情态/约束语汇：需求以「必须/需/支持/禁止/不超过…」陈述强制行为
MODAL_CONSTRAINT = re.compile(
    r"必须|须|应(?![用对答急届])|需(?![求要])|支持|不允许|不得|不可|不能|禁止|"
    r"仅|只能|至少|最多|至多|不超过|不少于|不大于|不小于|大于|小于|等于|"
    r"非负|正整数|唯一|默认|统一|自动|必填|选填|可选|不影响|不改变|"
    r"(?<![A-Za-z])(?:must|shall|should|requires?|required|supports?|cannot|"
    r"may not|at most|at least|not allowed|only|default|unique)(?![A-Za-z])",
    re.IGNORECASE,
)


@lru_cache(maxsize=1)
def interaction_verbs() -> re.Pattern:
    """交互动词正则（由 operation_verbs.json 权威词典构建）。

    词典加载器在 analyzers._fd_extraction（唯一加载处）中，此处显式调用保证
    在任意导入顺序下词典都已就绪。
    """
    from src.pipeline.steps.step2.analyzers._fd_extraction import (
        _OPERATION_VERBS_DICT,
        _load_operation_dictionary,
    )

    _load_operation_dictionary()
    return re.compile(
        "|".join(re.escape(w) for w in _OPERATION_VERBS_DICT), re.IGNORECASE
    )


def is_behavior_predicate(text: str) -> bool:
    """文本是否以系统行为谓词表达。

    此类谓词描述系统自身产生的结果（展示/返回/告警/失效/置空…），
    本身即可观测，不依赖「动作+对象」二元结构。
    """
    return bool(text and BEHAVIOR_VERBS.search(text))


def has_executable_predicate(text: str) -> bool:
    """文本是否陈述了可执行/可测试的系统行为。

    交互动词、系统行为谓词、情态约束、HTTP 接口四者任一命中即为可执行；
    纯定义、术语表、文档自述、版本记录没有这些信号，判为不可执行。
    """
    if not text or not text.strip():
        return False
    if is_api_operation(text):
        return True
    return bool(
        interaction_verbs().search(text)
        or BEHAVIOR_VERBS.search(text)
        or MODAL_CONSTRAINT.search(text)
    )
