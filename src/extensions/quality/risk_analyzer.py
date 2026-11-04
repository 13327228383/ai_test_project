# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Dict
from src.core.guarded import guard

"""
风险分析器 (RiskAnalyzer)
==========================
根据需求内容评估风险等级，决定使用哪个模型生成测试用例。

三级降级策略：
  1. k-NN 分类器：查历史标注数据做 embedding 相似度投票（主分类器，需 ≥20 条样本）
  2. 关键词匹配 + 4簇 embedding（冷启动兜底，原有逻辑）
  3. LLM 兜底：前两级不命中时一次性调用云端 AI 判定

风险等级：
  high   → 核心链路（支付/权限/认证/数据修改）→ deepseek-v4-pro + 深度推理
  medium → 普通 CRUD / 业务逻辑 → deepseek-v4-pro（标准）
  low    → 展示/查询/UI 文案 → deepseek-v4-flash（快速低成本）
"""

import json
import logging
import os
import time as _time_mod
from collections import Counter

from src.core.pipeline_config import pipeline_config

# ── 嵌入相似度阈值（从 PipelineConfig 读取，可通过环境变量覆盖）──
_EMBEDDING_SIMILARITY_THRESHOLD = pipeline_config.risk_embedding_similarity_threshold

_log = logging.getLogger("risk_analyzer")

# ── k-NN 参数 ──
_KNN_MIN_SAMPLES = 20  # 冷启动门槛：全库不足此数时跳过 k-NN
_KNN_TOP_K = 5  # 取 top-k 最近邻投票
_KNN_VOTE_THRESHOLD = 3  # top-k 中至少 N 个一致才采纳
_KNN_COSINE_THRESHOLD = 0.82  # cosine 相似度门槛
_KNN_MAX_SAMPLES_PER_LEVEL = 200  # 每个 risk_level 最多采样数（防大项目淹没）

# ── 高风险关键词（涉及核心业务安全/数据完整性）──
HIGH_RISK_KEYWORDS = [
    # 支付/金额
    "支付",
    "转账",
    "金额",
    "费用",
    "结算",
    "退款",
    "扣款",
    "充值",
    "提现",
    # 权限/认证
    "权限",
    "角色",
    "登录",
    "认证",
    "授权",
    "token",
    "session",
    "密码",
    "修改密码",
    "越权",
    "管理员",
    "普通用户",
    "访问控制",
    # 数据修改/删除
    "删除",
    "批量删除",
    "物理删除",
    "逻辑删除",
    "清空",
    # 审批/流程
    "审批",
    "驳回",
    "通过",
    "流程",
    "工单",
    # 安全
    "加密",
    "解密",
    "脱敏",
    "审计",
    "日志",
    # 外部对接
    "回调",
    "webhook",
    "接口对接",
    "同步",
]

# ── 低风险关键词（纯展示/查询）──
LOW_RISK_KEYWORDS = [
    "查看",
    "展示",
    "显示",
    "列表",
    "详情",
    "统计",
    "报表",
    "看板",
    "导出",
    "下载",
    "预览",
    "查询",
    "搜索",
    "文案",
    "提示",
    "说明",
    "帮助",
    "关于",
    "布局",
    "样式",
    "颜色",
    "字体",
    "间距",
    "图标",
]

# ── 高风险标识词 → 如果在需求中找到这些词且不在低风险场景中 → 升级为 high
DATA_MUTATION_KEYWORDS = [
    "新增",
    "创建",
    "添加",
    "编辑",
    "修改",
    "更新",
    "保存",
    "提交",
    "上传",
    "导入",
    "配置",
    "设置",
    "绑定",
    "分配",
]

SEMANTIC_HIGH_RISK_CLUSTERS = {
    # ★ 通用化: 移除金融/仓储行业术语，保留通用安全概念
    "核心数据资产": ["余额", "账户", "资金", "额度", "收款", "付款"],
    "身份权限": ["身份", "实名", "二次验证", "验证码", "登录态", "会话", "账号安全"],
    "敏感数据": ["手机号", "身份证", "银行卡", "隐私", "个人信息", "敏感信息"],
    "状态一致性": ["并发提交"],
}

# ── 嵌入语义相似度阈值（冷启动兜底用，覆盖第25行config值以保持原有行为）──
_EMBEDDING_SIMILARITY_THRESHOLD = 0.5


# ======================================================================
# 第1级：k-NN 分类器
# ======================================================================

# ★ 性能修复(07-04): _load_knn_samples TTL 缓存
#   原实现每次调用都执行 3 轮 SQL JOIN，197 条需求 = 591 次相同查询
#   单次 Step3 运行中历史样本变化不影响分类结果（k-NN 基于统计）
_knn_samples_cache: list[dict] | None = None
_knn_samples_cache_ts: float = 0.0
_KNN_SAMPLES_CACHE_TTL = 60.0  # 60 秒 TTL


def _load_knn_samples() -> list[dict]:
    """从 case_meta 表加载历史标注样本（跨项目共享，带采样上限防护）。

    ★ 性能修复(07-04): 加 60s TTL 缓存，避免每次调用都查库（591 次 SQL → 1 次）

    返回: [{"text": "...", "risk_level": "high", "vector": [0.1, ...]}, ...]
    """
    global _knn_samples_cache, _knn_samples_cache_ts
    now = _time_mod.time()
    if (
        _knn_samples_cache is not None
        and (now - _knn_samples_cache_ts) < _KNN_SAMPLES_CACHE_TTL
    ):
        return _knn_samples_cache
    try:
        from src.core.db_manager import get_connection
    except Exception as _e:
        _log.debug("db_manager 导入失败: %s", _e, exc_info=True)
        return []

    samples = []
    c = None
    try:
        c = get_connection()
        # 每个 risk_level 最多取 _KNN_MAX_SAMPLES_PER_LEVEL 条
        for level in ("high", "medium", "low"):
            rows = c.execute(
                """
                SELECT tc.title, tc.req_id, cm.risk_level, cm.risk_embedding
                FROM case_meta cm
                JOIN test_cases tc
                  ON tc.case_id = cm.case_id AND tc.project = cm.project AND tc.version_id = cm.version_id
                WHERE cm.risk_embedding IS NOT NULL
                  AND cm.risk_level = %s
                ORDER BY cm.updated_at DESC
                LIMIT %s
                """,
                (level, _KNN_MAX_SAMPLES_PER_LEVEL),
            ).fetchall()
            for row in rows or []:
                emb_raw = row.get("risk_embedding")
                if not emb_raw:
                    continue
                try:
                    vec = json.loads(emb_raw) if isinstance(emb_raw, str) else emb_raw
                    if not isinstance(vec, list) or len(vec) < 128:
                        continue
                    text = str(row.get("title", "") or "")
                    if not text.strip():
                        text = str(row.get("req_id", "") or "")
                    if not text.strip():
                        continue
                    samples.append({"text": text, "risk_level": level, "vector": vec})
                except (json.JSONDecodeError, TypeError) as _e:
                    _log.debug("[risk_analyzer] kNN 样本 JSON 解析失败跳过(§5): %s", _e)
                    continue
    except Exception as e:
        _log.warning("[kNN] 加载样本失败: %s", str(e)[:120], exc_info=True)
        return []
    finally:
        if c:
            try:
                c.close()
            except Exception as _e:
                _log.debug("[risk_analyzer] c.close 失败: %s", _e, exc_info=True)
    _log.info("[kNN] 加载 %d 条历史样本", len(samples))
    # ★ 性能修复(07-04): 更新 TTL 缓存
    _knn_samples_cache = samples
    _knn_samples_cache_ts = _time_mod.time()
    return samples


def _try_knn_classification(req_text: str) -> str | None:
    """k-NN 分类：embed 新需求 → 与历史样本余弦比较 → top-k 投票。

    返回: "high" / "medium" / "low"，或 None（样本不足/无信心时）
    """
    if not req_text or not str(req_text).strip():
        return None

    samples = _load_knn_samples()
    if len(samples) < _KNN_MIN_SAMPLES:
        _log.debug("[kNN] 样本不足 %d/%d，跳过 k-NN", len(samples), _KNN_MIN_SAMPLES)
        return None

    try:
        from src.extensions.semantic_index import embed, cosine
    except ImportError:
        _log.debug("[kNN] semantic_index 不可用，跳过")
        return None

    query_vec = embed(req_text)
    if not query_vec:
        return None

    # 计算余弦相似度并排序
    scored = []
    for s in samples:
        try:
            sim = cosine(query_vec, s["vector"])
            if sim >= _KNN_COSINE_THRESHOLD:
                scored.append((sim, s["risk_level"]))
        except Exception as _e:
            _log.debug("kNN 余弦相似度计算失败: %s", _e, exc_info=True)
            continue

    if len(scored) < _KNN_VOTE_THRESHOLD:
        _log.debug("[kNN] 达到门槛的样本不足 %d/%d", len(scored), _KNN_VOTE_THRESHOLD)
        return None

    # 取 top-k 投票
    scored.sort(key=lambda x: x[0], reverse=True)
    top_k = scored[:_KNN_TOP_K]
    votes = Counter()
    for _, level in top_k:
        votes[level] += 1

    winner = max(votes, key=votes.get)
    if votes[winner] >= _KNN_VOTE_THRESHOLD:
        _log.info("[kNN] 判定 %s (votes=%s, top_sim=%.4f)", winner, votes, top_k[0][0])
        return winner

    _log.debug("[kNN] 投票未达门槛 %s", votes)
    return None


# ======================================================================
# 第2级：关键词匹配 + 4簇 embedding（冷启动兜底，原有逻辑）
# ======================================================================


def _semantic_high_risk_hits(text: str) -> dict[str, list]:
    hits = {}
    for category, terms in SEMANTIC_HIGH_RISK_CLUSTERS.items():
        matched = [term for term in terms if term.lower() in text]
        if matched:
            hits[category] = matched
    return hits


def _embedding_semantic_risk(text: str) -> dict[str, float]:
    """
    使用语义向量相似度判断需求是否命中高风险语义簇。

    对每个 SEMANTIC_HIGH_RISK_CLUSTERS 中的类别，生成代表性文本
    （如 "金融资产风险: 余额 账户 资金 授信..."），
    与需求文本做语义相似度比较。相似度 > 0.5 视为命中。

    参数:
        text: 需求文本（module + description 拼接）

    返回:
        dict: {category: similarity_score}，仅包含命中的类别
              如果 semantic_index 不可用则返回空 dict
    """
    try:
        from src.extensions.semantic_index import similarity
    except ImportError:
        _log.debug("[EmbeddingRisk] semantic_index 不可用，跳过嵌入语义分析")
        return {}

    if not text or not str(text).strip():
        return {}

    import time as _et

    embedding_hits = {}
    for category, terms in SEMANTIC_HIGH_RISK_CLUSTERS.items():
        # 生成代表性文本：类别名 + 风险描述 + 关键词列表
        representative = f"{category}风险: {' '.join(terms)}"
        try:
            _et0 = _et.time()
            score = similarity(text, representative)
            _log.info(
                "[EmbeddingRisk] %s 相似度=%.4f (耗时%.2fs)",
                category,
                score,
                _et.time() - _et0,
            )
        except Exception:
            _log.debug(
                "[EmbeddingRisk] 相似度计算失败: category=%s", category, exc_info=True
            )
            continue

        if score > _EMBEDDING_SIMILARITY_THRESHOLD:
            embedding_hits[category] = round(score, 4)

    _log.info(
        "[EmbeddingRisk] 完成: %d 个命中 / %d 个类别",
        len(embedding_hits),
        len(SEMANTIC_HIGH_RISK_CLUSTERS),
    )
    return embedding_hits


def _keyword_based_risk(req: dict) -> tuple[str, Dict]:
    """关键词匹配 + 4簇 embedding（冷启动兜底，原有逻辑完整保留）。"""
    module = (req.get("module", "") or "").lower()
    desc = (req.get("description", "") or "").lower()
    req_id = (req.get("req_id", "") or "").lower()
    combined = f"{module} {desc} {req_id}"

    # ── 评分 ──
    high_score = 0
    low_score = 0

    for kw in HIGH_RISK_KEYWORDS:
        if kw in combined:
            high_score += 1

    for kw in LOW_RISK_KEYWORDS:
        if kw in combined:
            low_score += 1

    # 数据变更词
    has_mutation = any(kw in combined for kw in DATA_MUTATION_KEYWORDS)

    # 关键词级语义风险命中
    semantic_hits = _semantic_high_risk_hits(combined)

    # 嵌入向量级语义风险命中（补充层）
    # ★ 性能修复(07-04): 统一 strip 归一化，与 assess_requirement_risk L437 保持一致
    #   避免 cache key 碎片化导致重复 cloud embed miss（每条多花 2-3s）
    embedding_text = f"{module} {desc}".strip()
    embedding_hits = _embedding_semantic_risk(embedding_text)
    embedding_score = len(embedding_hits)

    # 合并关键词与嵌入的命中（取并集，任一命中即算命中）
    merged_semantic_categories = set(semantic_hits.keys()) | set(embedding_hits.keys())
    merged_semantic_score = len(merged_semantic_categories)

    # ── 判等（max_tokens 由 token_estimator 动态计算，这里不设上限）──
    # ★ 零硬编码: 模型从 config 读取，风险等级只影响 temperature
    from src.core.config_loader import config as _risk_cfg

    _gen_model = _risk_cfg.ai_model
    _verify_model = _risk_cfg.verification_model
    if (
        high_score >= 2
        or merged_semantic_score >= 1
        or (high_score >= 1 and has_mutation)
    ):
        risk = "high"
        params = {
            "model": _gen_model,
            "temperature": 0.2,
            "label": "旗舰(高风险深度推理)",
            "risk_reasons": {
                "keyword_high_score": high_score,
                "semantic_hits": semantic_hits,
                "semantic_embedding_hits": embedding_hits,
                "semantic_embedding_score": embedding_score,
                "has_mutation": has_mutation,
                "source": "keyword",
            },
        }
    elif high_score >= 1 or has_mutation:
        risk = "medium"
        params = {
            "model": _gen_model,
            "temperature": 0.3,
            "label": "旗舰(标准)",
            "risk_reasons": {
                "keyword_high_score": high_score,
                "semantic_hits": semantic_hits,
                "semantic_embedding_hits": embedding_hits,
                "semantic_embedding_score": embedding_score,
                "has_mutation": has_mutation,
                "source": "keyword",
            },
        }
    elif low_score >= 3 and high_score == 0 and not has_mutation:
        risk = "low"
        params = {
            "model": _verify_model,
            "temperature": 0.3,
            "label": "校验(快速低成本)",
            "risk_reasons": {
                "keyword_high_score": high_score,
                "semantic_hits": semantic_hits,
                "semantic_embedding_hits": embedding_hits,
                "semantic_embedding_score": embedding_score,
                "has_mutation": has_mutation,
                "source": "keyword",
            },
        }
    else:
        # 默认中等
        risk = "medium"
        params = {
            "model": _gen_model,
            "temperature": 0.3,
            "label": "Pro(默认)",
            "risk_reasons": {
                "keyword_high_score": high_score,
                "semantic_hits": semantic_hits,
                "semantic_embedding_hits": embedding_hits,
                "semantic_embedding_score": embedding_score,
                "has_mutation": has_mutation,
                "source": "keyword",
            },
        }

    return risk, params


# ======================================================================
# 第3级：LLM 兜底
# ======================================================================

_LLM_RISK_PROMPT = """请判断以下需求的风险等级，只回复 high、medium、low 中的一个词。

判定标准：
- high: 涉及支付/金额/权限/认证/删除/审批/安全/外部对接等核心链路
- medium: 涉及新增/编辑/修改/更新等数据变更操作
- low: 纯展示/查询/导出/UI文案等无数据变更

需求内容: %s

风险等级:"""


def _llm_fallback_risk(req: dict) -> tuple[str, Dict]:
    """LLM 兜底：前两级都不命中时，一次性调用云端 AI 判定风险等级。"""
    module = (req.get("module", "") or "").strip()
    desc = (req.get("description", "") or "").strip()
    req_text = f"模块: {module}\n描述: {desc}" if module or desc else str(req)

    risk = "medium"  # 默认值
    with guard("_llm_fallback_risk: from src.ai.router_module import rou", log=_log, level=logging.WARNING):
        from src.ai.router_module import router

        result = router.route(
            prompt=_LLM_RISK_PROMPT % req_text[:500],
            task_type="requirement_analysis",
            temperature=0.0,
            max_tokens=32,
        )
        if result.get("success"):
            answer = str(result.get("data", "") or "").strip().lower()
            if "high" in answer:
                risk = "high"
            elif "low" in answer:
                risk = "low"
            else:
                risk = "medium"
            _log.info("[LLM兜底] 判定 %s (answer=%s)", risk, answer[:20])
        else:
            _log.warning(
                "[LLM兜底] AI调用失败: %s，使用默认 medium",
                str(result.get("error", ""))[:80],
            )

    from src.core.config_loader import config as _risk_cfg

    _gen_model = _risk_cfg.ai_model
    _verify_model = _risk_cfg.verification_model
    if risk == "high":
        params = {
            "model": _gen_model,
            "temperature": 0.2,
            "label": "旗舰(LLM兜底)",
            "risk_reasons": {"source": "llm_fallback"},
        }
    elif risk == "low":
        params = {
            "model": _verify_model,
            "temperature": 0.3,
            "label": "校验(LLM兜底)",
            "risk_reasons": {"source": "llm_fallback"},
        }
    else:
        params = {
            "model": _gen_model,
            "temperature": 0.3,
            "label": "Pro(LLM兜底)",
            "risk_reasons": {"source": "llm_fallback"},
        }
    return risk, params


# ======================================================================
# 主入口：三级降级
# ======================================================================


def assess_requirement_risk(req: dict) -> tuple[str, Dict]:
    """
    评估需求风险等级并返回推荐的模型参数。

    三级降级策略：
      1. k-NN 分类器（历史样本 ≥ 20 条时生效）
      2. 关键词匹配 + 4簇 embedding（冷启动兜底）
      3. LLM 兜底（前两级不命中时）

    参数:
        req: 需求字典 {"req_id": "REQ-001", "module": "...", "description": "..."}

    返回:
        (risk_level, model_params)
        risk_level: "high" | "medium" | "low"
        model_params: {"model": "...", "temperature": ..., "max_tokens": ..., "label": "..."}
    """
    module = (req.get("module", "") or "").strip()
    desc = (req.get("description", "") or "").strip()
    req_text = f"{module} {desc}".strip() or str(req)

    # ── 第1级：k-NN ──
    knn_result = _try_knn_classification(req_text)
    if knn_result is not None:
        from src.core.config_loader import config as _risk_cfg

        _gen_model = _risk_cfg.ai_model
        _verify_model = _risk_cfg.verification_model
        if knn_result == "high":
            return knn_result, {
                "model": _gen_model,
                "temperature": 0.2,
                "label": "旗舰(kNN)",
                "risk_reasons": {"source": "knn"},
            }
        elif knn_result == "low":
            return knn_result, {
                "model": _verify_model,
                "temperature": 0.3,
                "label": "校验(kNN)",
                "risk_reasons": {"source": "knn"},
            }
        else:
            return knn_result, {
                "model": _gen_model,
                "temperature": 0.3,
                "label": "Pro(kNN)",
                "risk_reasons": {"source": "knn"},
            }

    # ── 第2级：关键词匹配 ──
    risk, params = _keyword_based_risk(req)

    # ── 第3级：LLM 兜底 ──
    # 关键词匹配命中明确的高/低风险时不调用 LLM
    # 仅在默认 medium（即关键词未明确命中）时才走 LLM 兜底
    # ★ 性能修复(07-04): 默认关闭 LLM 兜底。
    #   原实现对几乎所有 medium 需求都触发 LLM 调用（5-10s/条），
    #   197 条需求多花 15-30 分钟，但 LLM 判定 value 有限（仅细分 high/low）。
    #   k-NN 冷启动时 medium 默认值合理，k-NN 预热后走 k-NN 无 AI 调用。
    #   需要时通过环境变量 RISK_LLM_FALLBACK_ENABLED=True 重新开启。
    if (
        risk == "medium"
        and params.get("risk_reasons", {}).get("keyword_high_score", 0) == 0
    ):
        if os.environ.get("RISK_LLM_FALLBACK_ENABLED", "").lower() in (
            "1",
            "true",
            "yes",
        ):
            llm_risk, llm_params = _llm_fallback_risk(req)
            return llm_risk, llm_params
        # 默认关闭，直接返回 medium（性能优先）

    return risk, params
