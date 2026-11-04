# -*- coding: utf-8 -*-
"""Step4 检查执行 — 算法规则元信息清单（只读，供"算法规则清单"展示）。

设计：Step2/Step3 用"规则注册表 + meta()"暴露规则清单；Step4 的校验逻辑是
函数式(verify_*)与汇总层判定(_flag_*)，并非统一类注册表。为保持一致、诚实、
且不硬编码脱节，这里采用两层：
  1. 固定聚合层规则：可解释、有明确判定语义的命名规则（轨道推断/汇总层维度/
     置信度等），来源为代码中真实存在的 _flag_* / infer_required_tracks 等。
  2. 自动发现的 _verify_*.py 断言校验器：从 verification 包扫描 `_verify_*.py`，
     用其模块 docstring 作为描述，保证与源码同步、零手工维护。

返回结构与 Step2/Step3 的 meta() 对齐（rule_id/name/description/stage/priority/
version），前端 loadRuleList 兼容。纯函数、无副作用、可独立单测。
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 0. 规则 stage 架构（单一事实源）
#    aggregation = 汇总/聚合层判定规则（_flag_* / 裁决 / 置信度）
#    checker     = _verify_*.py 自动发现的断言校验器
#    plugin      = Step4 浏览器执行插件类规则（self_healing / visual_baseline /
#                  fuzzy_match），随 plugins 系统运行
#    测试与前端均以本常量校验，新增 stage 只改此处。
# ──────────────────────────────────────────────
_STAGE_TAXONOMY: tuple[str, ...] = ("aggregation", "checker", "plugin")

# ──────────────────────────────────────────────
# 1. 固定聚合层规则（来自代码中真实存在的判定函数）
# ──────────────────────────────────────────────
_AGGREGATION_RULES: list[dict[str, Any]] = [
    {
        "rule_id": "step4.track_inference",
        "name": "三轨推断与路由",
        "description": "按 verification_plan.required_tracks 声明优先，缺失时从各轨"
        "assertion 非空性兜底推断所需轨道(ui/api/db)；UI 恒在，完全无断言默认全三轨。"
        "实现: _tracks.infer_required_tracks / _verify_ui_objective 门禁。",
        "stage": "aggregation",
        "priority": 90,
        "version": "1.0",
    },
    {
        "rule_id": "step4.track_protocol",
        "name": "单轨校验器协议",
        "description": "统一 Ui/Api/Db 三轨校验器协议(TrackVerifier)，各轨以相同"
        "结构产出 status/passed/evidence/evidence_ref，构造期 fail-fast 校验"
        "非 UNKNOWN 轨必须带证据引用，杜绝『通过但无证据』。实现: _track_protocol / tracks。",
        "stage": "aggregation",
        "priority": 85,
        "version": "1.0",
    },
    {
        "rule_id": "step4.ui_objective_assert",
        "name": "UI 客观断言",
        "description": "通过 DOM 查询客观校验 ui_checks（存在性/文本/属性/计数/网络"
        "无异常/加载完成等），PASS/FAIL 均回填具体 evidence。实现: _db_ui._verify_ui_objective。",
        "stage": "aggregation",
        "priority": 80,
        "version": "1.0",
    },
    {
        "rule_id": "step4.consistency",
        "name": "跨轨一致性校验",
        "description": "仅当用例声明 consistency_check 时触发；前端/接口/DB 三值两两"
        "精度容差比对，不一致覆盖 passed=False；有效来源不足 2 → inconclusive 不误杀。"
        "实现: _consistency.verify_consistency_async。",
        "stage": "aggregation",
        "priority": 78,
        "version": "1.0",
    },
    {
        "rule_id": "step4.api_contract",
        "name": "接口契约检测",
        "description": "用例声明 api_contract 时对 API 轨观测到的端点响应做结构校验"
        "(必填字段存在/类型/嵌套/数组长度)，不符追加 [contract_violation]。"
        "fail-open：默认只标记，opt-in fail_on_violation=True 才硬失败。"
        "实现: _api_contract。",
        "stage": "aggregation",
        "priority": 72,
        "version": "1.0",
    },
    {
        "rule_id": "step4.idempotency",
        "name": "幂等一致性检测",
        "description": "声明 idempotency.expect_repeat_same 时，同一操作重复调用(≥2次)"
        "结果不一致(状态码/ok 语义/DB 状态) → 追加 [idempotency_violation]。"
        "fail-open：默认只标，opt-in fail_on_violation 才硬失败。实现: _op_quality。",
        "stage": "aggregation",
        "priority": 70,
        "version": "1.0",
    },
    {
        "rule_id": "step4.concurrency",
        "name": "并发竞态检测",
        "description": "声明 concurrency.expect_consistent 时，同一操作在并发窗口内"
        "(时间接近/重叠, ≥concurrency_hint 次)调用结果不一致 → 追加 [concurrency_race]。"
        "fail-open：默认只标，opt-in fail_on_race 才硬失败。实现: _op_quality。",
        "stage": "aggregation",
        "priority": 68,
        "version": "1.0",
    },
    {
        "rule_id": "step4.perf_slow",
        "name": "响应性能慢检测",
        "description": "声明 performance.max_ms 时，API 轨命中端点调用耗时超过阈值 → "
        "追加 [perf_slow](含实测 ms)。fail-open：默认只标，opt-in fail_on_slow 才硬失败。"
        "实现: _op_quality._flag_performance。",
        "stage": "aggregation",
        "priority": 66,
        "version": "1.0",
    },
    {
        "rule_id": "step4.network_anomaly",
        "name": "网络异常/弱网检测",
        "description": "声明 network(expect_status/max_ms) 时，命中端点状态码不在期望 / "
        "耗时超阈值 → 追加 [network_anomaly]/[network_slow]。"
        "fail-open：默认只标，opt-in fail_on_network 才硬失败。实现: _op_quality。",
        "stage": "aggregation",
        "priority": 64,
        "version": "1.0",
    },
    {
        "rule_id": "step4.mutation_scan",
        "name": "变异测试检测",
        "description": "声明 mutation_scan.keys 且驱动侧对同一 case 做 baseline 全关 + "
        "逐 key 激活双跑观测时，比对同一端点调用差异 → 有差异追加 [mutation_detected]。"
        "回答「缺陷激活后引擎是否感知」。fail-open：opt-in fail_on_mutation 才硬失败。"
        "实现: _op_quality。",
        "stage": "aggregation",
        "priority": 62,
        "version": "1.0",
    },
    {
        "rule_id": "step4.security_xss",
        "name": "XSS 反射检测",
        "description": "声明 xss.check_reflected 时，注入 payload 被响应体原样反射"
        "(未转义/未过滤) → 追加 [xss_reflected]。fail-open：opt-in fail_on_xss 才硬失败。"
        "实现: _security_full。",
        "stage": "aggregation",
        "priority": 60,
        "version": "1.0",
    },
    {
        "rule_id": "step4.security_leak",
        "name": "敏感数据泄露检测",
        "description": "声明 sensitive_leak 时，命中端点响应体/响应头出现声明的敏感模式"
        "(正则或字段名) → 追加 [sensitive_leak]。fail-open：opt-in fail_on_leak 才硬失败。"
        "实现: _security_full。",
        "stage": "aggregation",
        "priority": 58,
        "version": "1.0",
    },
    {
        "rule_id": "step4.security_authz",
        "name": "越权绕过检测",
        "description": "声明 security_check.authz_gated 且 API 轨观测到「未授权身份调用"
        "受保护端点却返回成功」→ 追加 [security_authz_bypass]。fail-open：默认只标，"
        "opt-in fail_on_bypass 才硬失败。实现: _flag_security_authz_bypass。",
        "stage": "aggregation",
        "priority": 56,
        "version": "1.0",
    },
    {
        "rule_id": "step4.config_hidden",
        "name": "配置隐藏疑似标注",
        "description": "UI 轨因 [render_blank] 失败 且 源轨(DB/API)确有数据时，额外标注"
        " [config_hidden_suspect]，区分「源有数据被配置压制」与「纯模板缺陷」。"
        "仅加标记不改 FAIL 语义。实现: _flag_config_hidden_suspect。",
        "stage": "aggregation",
        "priority": 54,
        "version": "1.0",
    },
    {
        "rule_id": "step4.a11y",
        "name": "页面可访问性(8 规则)",
        "description": "声明 a11y.enabled 或页面级默认开启时，对目标页面跑 8 条通用规则"
        "(img-alt/label 关联/button 文本/heading 跳级/a-href/aria-label/重复 id)，"
        "规则驱动无 AI 依赖。有违规默认只标 [a11y_violations:N]，opt-in fail_on_a11y "
        "才硬失败。实现: _a11y_gate + src/extensions/a11y。",
        "stage": "aggregation",
        "priority": 52,
        "version": "1.0",
    },
    {
        "rule_id": "step4.confidence",
        "name": "三轨置信度评分",
        "description": "按声明轨子集计算多因子数值置信度(level/recommendation/factors)，"
        "skipped 轨不计入分母。实现: _confidence._compute_confidence。",
        "stage": "aggregation",
        "priority": 50,
        "version": "1.0",
    },
    {
        "rule_id": "step4.dom_pruning",
        "name": "DOM 智能剪枝（8 层）",
        "description": "Step4 浏览器执行对目标页 DOM 做多层智能剪枝(Elements)："
        "过滤无交互/隐藏/非视口节点，W3C ARIA 交互角色 + 常见 UI 框架菜单兜底，"
        "剪枝后仅保留交互元素并注入 data-ai-id/bbox，紧凑向量喂 AI，控制 token 成本。"
        "实现: dom_extraction.get_structured_state / browser_core 8 层剪枝 + SmartLocator。",
        "stage": "aggregation",
        "priority": 46,
        "version": "1.0",
    },
    {
        "rule_id": "step4.url_match",
        "name": "URL 归一化匹配",
        "description": "对导航/重定向后的实际 URL 与期望 check URL 做归一化匹配(忽略"
        "通用 query 顺序/hash/尾斜杠差异)，判断导航是否到达目标页面。"
        "实现: _url_match._match_url_pattern。",
        "stage": "aggregation",
        "priority": 44,
        "version": "1.0",
    },
    {
        "rule_id": "step4.verdict_fusion",
        "name": "三轨裁决融合",
        "description": "汇总层把 ui/api/db 三轨子结果融合为单条用例最终裁决(passed/status/"
        "summary)，未声明轨按 UNKNOWN 跳过不计入 AND，声明轨缺证据 fail-fast 不误判。"
        "实现: verdict.decide_verdict。",
        "stage": "aggregation",
        "priority": 42,
        "version": "1.0",
    },
    {
        "rule_id": "step4.db_assert",
        "name": "DB 轨数据断言",
        "description": "对 DB 轨的 value/row-count/存在性等断言做真实查询校验，产出"
        "passed + evidence；与 UI/API 轨结果两两比对支撑跨轨一致性。"
        "实现: verification._verify_db_ext / db_verifier.verify_db。",
        "stage": "aggregation",
        "priority": 40,
        "version": "1.0",
    },
    {
        "rule_id": "step4.api_schema_diff",
        "name": "接口契约演进对比",
        "description": "对历史与当前接口 schema(YAPI/OpenAPI)做差异对比，识别响应结构"
        "增减/类型漂移等破坏性契约变更。实现: contract_builder.load_openapi_schemas / "
        "diff_contract_schemas。",
        "stage": "aggregation",
        "priority": 38,
        "version": "1.0",
    },
    {
        "rule_id": "step4.self_healing",
        "name": "定位器自愈",
        "description": "元素定位失败时记录错配证据，训练受保护定位器并用多候选策略自愈"
        "重定位，避免因主题/文案微动导致的假失败。实现: plugins.self_healing."
        "SelfHealingPlugin。",
        "stage": "plugin",
        "priority": 36,
        "version": "1.0",
    },
    {
        "rule_id": "step4.visual_baseline",
        "name": "视觉回归基线",
        "description": "对目标页截图建立视觉基线并在后续执行对比差异(人工接受/拒绝)，"
        "检测 UI 回归。实现: plugins.visual_baseline.VisualBaselinePlugin + "
        "record_visual_baseline。",
        "stage": "plugin",
        "priority": 34,
        "version": "1.0",
    },
    {
        "rule_id": "step4.fuzzy_match",
        "name": "模糊语义相似度匹配",
        "description": "对元素文本/意图做 token 化(Jaccard)与语义相似度匹配，支持跨命名"
        "差异的控件/菜单定位。实现: plugins.fuzzy_match_engine(similarity.jaccard / "
        "semantic_similarity + tokenizer)。",
        "stage": "plugin",
        "priority": 32,
        "version": "1.0",
    },
]


# ──────────────────────────────────────────────
# 2. _verify_*.py 断言校验器自动发现
# ──────────────────────────────────────────────
def _discover_verify_checkers() -> list[dict[str, Any]]:
    """扫描 verification 包内 `_verify_*.py` 模块，产出校验器元信息。

    - rule_id:   `step4.checker.<模块名去 _verify_/>`（如 _verify_style → style）
    - name:      模块 docstring 首行（无则用文件名）
    - description: 模块 docstring 全文（无则空）
    - stage:     "checker"
    - priority:  50（同一层，不参与优先级排序语义）
    """
    # 扫描 verification 包目录下的 _verify_*.py（__path__ 仅子包才有，用文件系统 glob 兜底）
    pkg_root = __package__ if __package__ else "src.pipeline.steps.step4.verification"
    pkg_dir = Path(__file__).resolve().parent
    found: list[dict[str, Any]] = []
    for fp in sorted(pkg_dir.glob("_verify_*.py")):
        modname = pkg_root + "." + fp.stem
        short = fp.stem
        rule_id = "step4.checker." + short[len("_verify_"):]
        try:
            mod = importlib.import_module(modname)
        except Exception as e:  # noqa: BLE001
            _log.warning("[step4._rule_meta] 跳过校验器 %s: %s", modname, e)
            continue
        doc = (mod.__doc__ or "").strip()
        first_line = doc.splitlines()[0].strip() if doc else short
        desc = doc or "Step4 断言校验器：%s（源文件无 docstring，描述待补充）" % short
        found.append(
            {
                "rule_id": rule_id,
                "name": first_line,
                "description": desc,
                "stage": "checker",
                "priority": 50,
                "version": "1.0",
            }
        )
    found.sort(key=lambda r: r["rule_id"])
    return found


def get_rule_meta_list() -> list[dict[str, Any]]:
    """获取 Step4 全部算法规则元信息（聚合层 + 自动发现校验器）。"""
    checkers = _discover_verify_checkers()
    return _AGGREGATION_RULES + checkers


__all__ = ["get_rule_meta_list"]