# -*- coding: utf-8 -*-
"""需求语义的**上游判定**（唯一实现，下游只读不推）。

本模块只放"需求文本 → 结构化布尔/枚举"的判定，供需求落库咽喉点
（``requirement_store.upsert_requirements``）一次性计算并随 ``payload_json``
持久化；Step3 生成端与身份指派侧一律**读取**该字段，不得再自行从文本重推。

当前承载的语义：
  - ``access_control``：需求是否表达「访问限制」（低权/未认证执行受限操作应被系统拒绝）。
    判据 = 权限语境词 **且** 限制/拒绝词共现（精确判定）。

设计边界（勿混用）：
  - 宽召回词表（``_is_security_behavior``）用于"场景标注/探索计划"，**不可用于断言分派**
    ——"管理员可以在商品页新增商品"这类正向创建需求会被误判为访问控制。
  - 本模块的判定是**需求级**；用例级"是否要求被拒绝"由用例自身断言决定
    （``step3/_identity_assign.case_expects_denial``），两者不同层。
"""

from __future__ import annotations

#: 权限语境词（单独出现不足以定性，必须与限制词共现）
_ACCESS_CONTEXT_KEYWORDS = (
    "权限", "角色", "授权", "鉴权", "访问", "登录", "越权", "未认证", "匿名",
    "管理员", "查看者", "用户组", "岗位", "admin", "viewer", "role",
    "permission", "access", "privilege", "authoriz", "authenticat",
)

#: 限制/拒绝词（单独出现同样不足以定性："不允许为空"是字段校验）
_ACCESS_RESTRICTION_KEYWORDS = (
    "不允许", "禁止", "不得", "无权", "没有权限", "无权限", "权限不足",
    "仅具有", "仅拥有", "仅有", "只能", "仅能", "只可", "拒绝访问",
    "禁止访问", "访问被拒", "访问被拒绝", "未授权", "未登录", "强制登录",
    "重定向", "跳转到登录", "403", "401",
    "not allowed", "forbidden", "unauthorized", "access denied",
    "permission denied", "denied", "restricted",
)


def is_access_restriction_req(*texts: str) -> bool:
    """需求是否表达**访问限制语义**（低权/未认证执行受限操作应被系统拒绝）。

    判定 = 权限语境词 **且** 限制/拒绝词共现。
    """
    _t = " ".join(t for t in texts if t).lower()
    if not any(_kw in _t for _kw in _ACCESS_CONTEXT_KEYWORDS):
        return False
    return any(_kw in _t for _kw in _ACCESS_RESTRICTION_KEYWORDS)


def requirement_semantics(req: dict) -> dict:
    """按需求三元组计算结构化语义字段（落库前一次性判定）。

    返回可直接合并进需求 dict 的 ``{"access_control": bool}``。
    """
    if not isinstance(req, dict):
        return {"access_control": False}
    return {
        "access_control": is_access_restriction_req(
            str(req.get("operation") or ""),
            str(req.get("description") or ""),
            str(req.get("expected_feedback") or ""),
        )
    }


__all__ = [
    "is_access_restriction_req",
    "requirement_semantics",
]
