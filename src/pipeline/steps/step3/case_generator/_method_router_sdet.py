# -*- coding: utf-8 -*-
"""★ A2(2026-08-16): SDET 直觉方法论路由器（从 _test_method_rules.py 抽离，避免单文件超 800 行）。

将 undecided(FD) 的方法编码按「高级 SDET 直觉」升级为 EP/BV/DT 之一，
使 testing_methods 中的等价类划分/边界值分析/判定表真正落地到生成路径。

信号设计严格对应「什么场景高级 SDET 会用该方法」的语义，非为触发而触发：
  - DT 判定表：多条件组合 / 权限矩阵 / 角色×操作
  - BV 边界值：数值型字段含上下界/范围/阈值
  - EP 等价类：输入域有明确分区（枚举/校验）
  - FD 功能分解：以上均不命中的兜底主流程

复用 testing_methods.MethodRouter 做需求类型分类（「方法论路由器」真实落地），
再叠加语义信号做直觉加权，保证 EP/BV/DT 在语义正确的场景下各触发真实路径。
"""

from __future__ import annotations
from src.core.guarded import guard

import logging
from typing import Any

from ._test_method_rules import normalize_method_code

_log = logging.getLogger(__name__)

# SDET 直觉路由关键词
_DT_KEYWORDS: tuple[str, ...] = (
    "权限", "角色", "越权", "授权", "筛选", "过滤", "组合条件", "组合", "判定", "矩阵",
)
_BV_KEYWORDS: tuple[str, ...] = (
    "数量", "金额", "价格", "年龄", "长度", "次数", "分数", "页数", "占比",
    "上限", "下限", "边界", "最大", "最小", "范围", "阈值", "限额", "库存",
    "余额", "额度",
)
_EP_KEYWORDS: tuple[str, ...] = (
    "状态", "类型", "性别", "等级", "分类", "渠道", "来源", "方式", "规格",
    "校验", "格式", "必填", "合法", "非法", "等价", "分区", "输入", "填写",
    "录入", "提交",
)
# ★ 2026-09-20: Web 专属/安全方法论到达信号（语义严格对应该方法论适用场景，非为触发而触发）
_AU_KEYWORDS: tuple[str, ...] = ("越权", "未授权", "访问控制", "权限不足", "无权", "水平越权", "垂直越权", "idor")
_CS_KEYWORDS: tuple[str, ...] = ("csrf", "跨站请求", "防伪", "同源", "令牌")
_SX_KEYWORDS: tuple[str, ...] = ("xss", "跨站脚本", "注入", "payload", "<script>", "onerror", "javascript:")
_SI_KEYWORDS: tuple[str, ...] = ("sql注入", "sql", "union select", "or 1=1", "select * from")
_SS_KEYWORDS: tuple[str, ...] = ("登录", "登出", "注销", "会话", "超时", "token", "cookie", "过期")
_CT_KEYWORDS: tuple[str, ...] = ("兼容", "响应式", "分辨率", "浏览器", "多端", "移动端", "断点")
_PF_KEYWORDS: tuple[str, ...] = ("性能", "加载", "响应时间", "并发", "负载", "吞吐")
_ET_KEYWORDS: tuple[str, ...] = ("探索", "随机", "非常规", "异常场景", "fuzz", "模糊")


def route_method_by_intuition(
    test_method: Any,
    control_type: str = "",
    explore_task: dict | None = None,
    case_signal: str = "",
) -> str:
    """按高级 SDET 直觉将 undecided(FD) 的方法编码升级为 EP/BV/DT。

    仅当传入编码为 undecided（playwright / '' / FD）时生效；若已明确指定
    EP/BV/DT/... 等真实编码则原样返回（不覆盖显式决策，保证既有链路稳定）。

    ★ 根因修复(2026-09-21)：新增 `case_signal`（用例自身语义文本，如标题/类型）。
      此前只吃 explore_task 的 operation/module/反馈 + control_type，导致「用例类型
      已是 性能/兼容/安全-XSS… 但 explore_task 无对应关键词」的用例坍缩为 FD（方法论
      用了却没落码）。case_signal 并入信号文本，让安全(注入/XSS/SQL)/性能/兼容等
      用例按自身语义确定性落码；仅当直觉仍判 FD 才升级，显式与已判非 FD 的决策不变。

    Returns:
        str: 方法论编码（EP / BV / DT / FD / 其它已指定编码）。
    """
    _explicit = normalize_method_code(test_method)
    # 已明确指定非 FD 编码 → 保留显式决策
    if _explicit != "FD":
        return _explicit

    _et = explore_task if isinstance(explore_task, dict) else {}
    _op = str(_et.get("operation") or "").strip()
    _module = str(_et.get("module") or "").strip()
    _feedback = str(_et.get("expected_feedback") or "").strip()
    _req_text = " ".join([_op, _module, _feedback])
    # 字段名（枚举/数值信号）
    _fields = _et.get("_field_verbose_names") or []
    if isinstance(_fields, list):
        _field_text = " ".join(str(_f) for _f in _fields)
    else:
        _field_text = ""
    _all_text = " ".join([_req_text, _field_text, str(control_type or "")]).lower()
    # ★ case_signal（用例自身 semantic 标签：标题/类型）并入信号文本
    if case_signal:
        _all_text = " ".join([_all_text, str(case_signal).lower()])

    # 1) 用 MethodRouter 做需求类型分类（「方法论路由器」真实参与落地）
    _req_type = "crud"
    with guard("route_method_by_intuition: from src.testing_methods.method_rout", log=_log):
        from src.testing_methods.method_router import MethodRouter

        _route = MethodRouter().route(requirement_text=_req_text)
        _req_type = str(getattr(_route, "requirement_type", "crud") or "crud")

    # 2) 语义信号检测
    _combo = any(_kw in _all_text for _kw in _DT_KEYWORDS)
    _numeric = any(_kw in _all_text for _kw in _BV_KEYWORDS)
    _enum = any(_kw in _all_text for _kw in _EP_KEYWORDS)
    # ★ 2026-09-20: Web 专属/安全信号；2026-09-21 新增 SQL/XSS 注入信号
    _viol = any(_kw in _all_text for _kw in _AU_KEYWORDS)
    _csrf = any(_kw in _all_text for _kw in _CS_KEYWORDS)
    _sql = any(_kw in _all_text for _kw in _SI_KEYWORDS)
    _xss = any(_kw in _all_text for _kw in _SX_KEYWORDS)
    _sess = any(_kw in _all_text for _kw in _SS_KEYWORDS)
    _compat = any(_kw in _all_text for _kw in _CT_KEYWORDS)
    _perf = any(_kw in _all_text for _kw in _PF_KEYWORDS)
    _explore = any(_kw in _all_text for _kw in _ET_KEYWORDS)

    # 3) SDET 直觉选择（最具体者优先：越权 > SQL注入 > XSS > CSRF > 会话 > 兼容 > 性能 >
    #        组合 > 数值 > 分区 > 探索 > FD 兜底）
    if _viol:
        return "AU"
    if _sql:
        return "SI"
    if _xss:
        return "SX"
    if _csrf:
        return "CS"
    if _sess:
        return "SS"
    if _compat:
        return "CT"
    if _perf:
        return "PF"
    if _req_type == "permission" or _combo:
        return "DT"
    if _numeric:
        return "BV"
    if _req_type in ("form", "search") or _enum:
        return "EP"
    if _explore:
        return "ET"
    return "FD"
