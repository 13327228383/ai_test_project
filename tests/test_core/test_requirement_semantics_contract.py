# -*- coding: utf-8 -*-
"""契约：需求语义在**上游一次性判定**并落结构化字段，下游只读不推（§6）。

覆盖两处历史缺陷：
  1. 访问限制语义（`access_control`）曾被多处从需求文本重推 ——
     权威实现现已归位 `src/core/requirement_semantics.py`，需求落库咽喉点
     `requirement_store.upsert_requirements` 一次性判定并随 `payload_json` 持久化；
     `step3/case_rules/base.py` 只保留同名别名，不得再定义词表。
  2. `step3/_identity_assign.pick_identity` 曾独立从文本重推同一语义 ——
     现优先读取 `access_control` 字段，字段缺失（历史需求）才回落同一权威函数。
"""

import pytest

from src.core.requirement_semantics import (
    is_access_restriction_req,
    requirement_semantics,
)

_ROLES = {"admin": {"password": "x"}, "viewer": {"password": "y"}}


def test_authority_has_single_definition():
    """生成端不得再持有第二份权限词表（词表只能存在于上游事实源）。"""
    src = open("src/pipeline/step3/case_rules/base.py", encoding="utf-8").read()
    assert "_ACCESS_CONTEXT_KEYWORDS" not in src, "权限语境词表在生成端重复定义"
    assert "_ACCESS_RESTRICTION_KEYWORDS" not in src, "限制/拒绝词表在生成端重复定义"


def test_repository_choke_point_persists_structured_field():
    """需求落库咽喉点必须产出 `access_control`（唯一产生点）。"""
    src = open(
        "src/pipeline/utils/requirement_store/crud_mixins/_write.py", encoding="utf-8"
    ).read()
    assert "requirement_semantics(req)" in src, (
        "upsert_requirements 未在上游判定并持久化 access_control，"
        "下游只能退回从文本重推（同一语义多处推导）"
    )


def test_precision_positive_creation_is_not_access_control():
    """正向创建需求不得被判为访问控制（宽召回词表误用会导致断言错配）。"""
    req = {
        "operation": "管理员在商品页新增商品",
        "description": "管理员可以新增商品",
        "expected_feedback": "商品创建成功并出现在列表",
    }
    assert requirement_semantics(req)["access_control"] is False


def test_precision_restriction_requirement_is_access_control():
    """访问限制需求必须被判为访问控制（低权被拒断言才可达）。"""
    req = {
        "operation": "查看者访问审计日志",
        "description": "低权角色访问受限功能",
        "expected_feedback": "系统拒绝访问并提示权限不足",
    }
    assert requirement_semantics(req)["access_control"] is True
    assert is_access_restriction_req("维护中的资产不允许删除") is False, (
        "状态型拒绝（无权限语境词）不是访问控制语义"
    )


def test_pick_identity_reads_structured_field_first():
    """结构化字段为 False 时不指派身份——即便文本本身命中限制词。"""
    from src.pipeline.step3._identity_assign import pick_identity

    text = "查看者无权新增商品，系统禁止访问"
    assert pick_identity(text, _ROLES, access_control=False) is None
    assert pick_identity(text, _ROLES, access_control=True) == "viewer"


def test_pick_identity_falls_back_to_authority_when_field_missing():
    """字段缺失（历史需求）时回落**同一权威函数**，不是第二套判据。"""
    from src.pipeline.step3._identity_assign import pick_identity

    assert pick_identity("查看者无权新增商品，系统禁止访问", _ROLES, None) == "viewer"
    assert pick_identity("维护中的资产不允许删除", _ROLES, None) is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
