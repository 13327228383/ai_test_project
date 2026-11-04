# -*- coding: utf-8 -*-
from __future__ import annotations

"""靶子实证验证包（empirical）。

导出 constants 中的公开符号，便于上层（T02/T03/T04）直接
``from src.pipeline.steps.step4.empirical import NO_ASSERTION_DOWNGRADE`` 使用。
"""

from src.pipeline.steps.step4.empirical.constants import (
    NO_ASSERTION_DOWNGRADE,
    API_MODE,
    BLOCKED_BY_API_PARAM,
    BLOCKED_BY_CLIENT_VALIDATION,
)
from src.pipeline.steps.step4.empirical.sut_harness import SutHarness
from src.pipeline.steps.step4.empirical.deep_assertion_runner import (
    DeepAssertionRunner,
)
from src.pipeline.steps.step4.empirical.target_empirical import (
    TargetEmpiricalVerifier,
)
from src.pipeline.steps.step4.empirical.plan import (
    VerificationPlan,
    execute,
    from_upload_parsed,
    from_verification_plan,
)
from src.pipeline.steps.step4.empirical.target_cfgs import (
    TARGET_CFGS,
    get_target_cfg,
    TARGET_CFG_SCHEMA,
)

__all__ = [
    "NO_ASSERTION_DOWNGRADE",
    "API_MODE",
    "BLOCKED_BY_API_PARAM",
    "BLOCKED_BY_CLIENT_VALIDATION",
    "SutHarness",
    "DeepAssertionRunner",
    "TargetEmpiricalVerifier",
    "VerificationPlan",
    "execute",
    "from_upload_parsed",
    "from_verification_plan",
    "TARGET_CFGS",
    "get_target_cfg",
    "TARGET_CFG_SCHEMA",
]
