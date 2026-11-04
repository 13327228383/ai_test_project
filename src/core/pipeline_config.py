# -*- coding: utf-8 -*-
"""流水线集中配置入口。

读取优先级：用户 DB 配置 > 环境变量 > 默认值
"""

from __future__ import annotations
from src.core.guarded import guard

import os
import time as _time
import logging
import threading as _threading
from dataclasses import dataclass
from pathlib import Path as _Path
from typing import Any

from src.core import pipeline_config_selectors as _pcds

_log = logging.getLogger(__name__)

# ── ★ 模块加载时自动注入 .env 到 os.environ（零第三方依赖）──
#    根因: pipeline_config 的默认值在类定义时求值，必须在任何 _as_* 调用前加载 .env
#    优先级: 已存在的环境变量优先（不会被 .env 覆盖）
_ENV_PATH = _Path(__file__).resolve().parents[2] / ".env"
if _ENV_PATH.is_file():
    _injected = 0
    for _raw in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        _line = _raw.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _key, _, _value = _line.partition("=")
        _key = _key.strip()
        _value = _value.strip().strip('"').strip("'")
        if _key and _key not in os.environ:
            os.environ[_key] = _value
            _injected += 1
    if _injected:
        _log.info("[pipeline_config] 从 .env 注入了 %d 个环境变量", _injected)

# ── 用户级配置缓存（TTL 5s，PUT/DELETE API 更新 DB 后自动失效）──
_user_config_cache: dict = {}
_user_config_uid: str = ""  # 缓存对应的用户
_user_config_ts: float = 0.0  # 缓存时间戳
_USER_CONFIG_CACHE_TTL: float = 5.0  # 秒
_table_ensured = False  # 模块级标志：表已确认存在后跳过重复 DDL
_user_config_lock = _threading.Lock()  # ★ 防并发竞态


def _read_user_config(key: str) -> Any:
    """延迟加载当前用户的流水线配置覆盖。返回 None 表示无覆盖。
    缓存 TTL 5s，保证 PUT/DELETE 后用户下次读取自动刷新。
    """
    global _user_config_cache, _user_config_uid, _user_config_ts
    try:
        from src.core.config_loader import get_current_user

        uid = get_current_user()
    except Exception as _e:
        _log.debug("[pipeline_config] get_current_user 获取失败: %s", _e)
        with _user_config_lock:
            return _user_config_cache.get(key) if _user_config_uid else None

    if not uid:
        with _user_config_lock:
            return _user_config_cache.get(key) if _user_config_uid else None

    # 用户变了/首次加载/缓存过期 → 重新查 DB
    need_reload = False
    with _user_config_lock:
        if (
            _user_config_uid != uid
            or (_time.time() - _user_config_ts) > _USER_CONFIG_CACHE_TTL
        ):
            need_reload = True

    if need_reload:
        with guard("_read_user_config: from src.core.db_manager import get_", log=_log):
            from src.core.db_manager import get_connection

            conn = None
            try:
                conn = get_connection()
                global _table_ensured
                if not _table_ensured:
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS pipeline_user_config (
                            id BIGINT AUTO_INCREMENT PRIMARY KEY,
                            user_id VARCHAR(128) NOT NULL,
                            config_key VARCHAR(128) NOT NULL,
                            config_value TEXT NOT NULL,
                            source VARCHAR(16) NOT NULL DEFAULT 'manual',
                            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                            UNIQUE KEY uk_user_key (user_id, config_key)
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                        """
                    )
                    _table_ensured = True
                rows = conn.execute(
                    "SELECT config_key, config_value FROM pipeline_user_config WHERE user_id=%s",
                    (uid,),
                ).fetchall()
                new_cache = {r["config_key"]: r["config_value"] for r in rows}
                with _user_config_lock:
                    _user_config_cache = new_cache
                    _user_config_uid = uid
                    _user_config_ts = _time.time()
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception as _pass_e:
                        _log.warning("非关键异常: %s", _pass_e, exc_info=True)

    # ★ 大小写归一化解锁：CONFIG_SCHEMA 用小写键（如 rag_rrf_k），引擎读大写键
    #   （如 RAG_RRF_K）。若不加此兜底，流水线配置里 RAG 项的修改会因键失配而
    #   落库但不生效（双源假象）。按 精确 → 大写 → 小写 逐级匹配，覆盖全部写法。
    k = key
    with _user_config_lock:
        if k in _user_config_cache:
            return _user_config_cache[k]
        up = k.upper()
        if up in _user_config_cache:
            return _user_config_cache[up]
        low = k.lower()
        if low in _user_config_cache:
            return _user_config_cache[low]
    return None


def invalidate_user_config_cache(uid: str = None) -> None:
    """主动失效用户配置缓存（PUT/DELETE API 写入 DB 后调用）。
    传 uid 时仅失效指定用户；不传时全局失效。
    """
    global _user_config_cache, _user_config_uid, _user_config_ts
    with _user_config_lock:
        if uid and _user_config_uid == uid:
            _user_config_cache = {}
            _user_config_uid = ""
            _user_config_ts = 0.0
        elif not uid:
            _user_config_cache = {}
            _user_config_uid = ""
            _user_config_ts = 0.0


def _coerce(val: Any, default: Any) -> Any:
    """将 DB/环境变量的字符串值转换为 default 的类型。
    关键：bool('False') == True，必须特殊处理。
    """
    if val is None:
        return default
    if isinstance(default, bool):
        if isinstance(val, bool):
            return val
        return str(val).strip().lower() in ("1", "true", "yes", "y", "on")
    if isinstance(default, int) and not isinstance(val, int):
        try:
            return int(val)
        except (ValueError, TypeError):
            return default
    if isinstance(default, float) and not isinstance(val, float):
        try:
            v = float(val)
            return default if (v != v) else v  # NaN != NaN，防前端清空数字框
        except (ValueError, TypeError):
            return default
    return val


def _read_system_config(key: str) -> Any:
    """读取系统级配置（system_config 表）。
    由前端「系统设置」页面维护，作为项目级配置的系统级默认值。
    """
    with guard("_read_system_config: from src.core.db_manager import get_", log=_log):
        from src.core.db_manager import get_connection, release_connection

        conn = None
        try:
            conn = get_connection()
            row = conn.execute(
                "SELECT `value` FROM system_config WHERE `key`=%s LIMIT 1", (key,)
            ).fetchone()
            if row and row.get("value") is not None:
                return row["value"]
        finally:
            if conn:
                release_connection(conn)
    return None


def _read_config_value(key: str, default: Any) -> Any:
    # 1. 用户级 DB 配置（最高优先级）
    user_val = _read_user_config(key)
    if user_val is not None:
        return _coerce(user_val, default)
    # 2. 环境变量（大小写不敏感匹配：先精确匹配，再试 UPPER_CASE）
    #    通用性修复：配置键命名不统一（部分小写/部分大写），导致 Linux（区分大小写）下
    #    用户按惯例设 UPPER_CASE 环境变量时读不到。同时支持两种写法保证可用性。
    env_val = os.environ.get(key)
    if env_val is None:
        env_val = os.environ.get(key.upper())
    if env_val is None and key != key.lower():
        env_val = os.environ.get(key.lower())
    if env_val is not None:
        return _coerce(env_val, default)
    # 3. config_loader 项目级配置（★ 大小写归一化：configloader 平铺键多为 UPPER 写法，
    #    而引擎侧部分调用方用小写键读（如 ai_local_timeout vs AI_LOCAL_TIMEOUT）。
    #    若只精确匹配，这类配置项的覆盖会落库却不生效；与 _read_user_config 同款兜底，
    #    按 精确 → 大写 → 小写 逐级尝试。）
    with guard("_read_config_value: from src.core.config_loader import c", log=_log):
        from src.core.config_loader import config

        if hasattr(config, "_get"):
            for cand in (key, key.upper(), key.lower()):
                val = config._get(cand, None)
                if val is not None:
                    return _coerce(val, default)
    # 4. 系统级 DB 配置（system_config 表，最低优先级）
    sys_val = _read_system_config(key)
    if sys_val is not None:
        return _coerce(sys_val, default)
    return default


# 模块内便捷别名（get_browser_parallel_recommendation 等内部函数使用）
_cfg = _read_config_value


def _as_int(
    key: str, default: int, min_value: int = None, max_value: int = None
) -> int:
    try:
        value = int(_read_config_value(key, default))
    except Exception as _e:
        _log.debug(
            "[pipeline_config] _as_int(%s) 解析失败，使用默认值 %s: %s",
            key,
            default,
            _e,
        )
        value = default
    if min_value is not None:
        value = max(value, min_value)
    if max_value is not None:
        value = min(value, max_value)
    return value


def _as_float(
    key: str, default: float, min_value: float = None, max_value: float = None
) -> float:
    try:
        value = float(_read_config_value(key, default))
    except Exception as _e:
        _log.debug(
            "[pipeline_config] _as_float(%s) 解析失败，使用默认值 %s: %s",
            key,
            default,
            _e,
        )
        value = default
    if min_value is not None:
        value = max(value, min_value)
    if max_value is not None:
        value = min(value, max_value)
    return value


def _as_bool(key: str, default: bool) -> bool:
    val = _read_config_value(key, default)
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("1", "true", "yes", "y", "on")


def _as_str(key: str, default: str) -> str:
    """★ 字符串配置读取（环境变量优先，其次 YAML，最后默认值）。"""
    val = _read_config_value(key, default)
    if val is None:
        return default
    return str(val).strip()


# ★ 架构债清理(2026-07-24): 自动调参逻辑拆分到 _auto_tune.py（§4.3 单文件≤800行）
from src.core._auto_tune import (  # noqa: E402, F401 — re-export 保持外部 import 兼容
    _detect_model_window,
    _detect_signals,
    detect_project_signals,
    _bound,
    auto_tune_step3,
    auto_tune_step2,
)


@dataclass
class PipelineConfig:
    keyword_min_match_ratio: int = _as_int(
        "PIPELINE_KEYWORD_MIN_MATCH_RATIO", 2, min_value=1
    )
    keyword_single_fallback: int = _as_int(
        "PIPELINE_KEYWORD_SINGLE_FALLBACK", 1, min_value=1
    )
    figma_context_max_chars: int = _as_int(
        "PIPELINE_FIGMA_CONTEXT_MAX_CHARS", 3000, min_value=500
    )
    quick_mode_sample_size: int = _as_int("quick_mode_sample_size", 3, min_value=1)
    default_step4_timeout_ms: int = _as_int(
        "default_step4_timeout_ms", 30000, min_value=1000
    )
    browser_max_ai_rounds: int = _as_int(
        "browser_max_ai_rounds", 8, min_value=1, max_value=30
    )
    # ★ v3.1 (2026-07-11): AI loop 连续无变化检测阈值，避免 AI 在相同无效动作上空转
    ai_loop_max_no_change_rounds: int = _as_int(
        "ai_loop_max_no_change_rounds", 3, min_value=2, max_value=5
    )
    # ★ Fix G(2026-09-27): AI loop 总墙钟预算（秒）—— 单条用例 AI 兜底最多消耗的时间。
    #   根因: 实测单条 TC-REQ-002-UX02 耗时 6.4min（占全轮 69%），AI 在无进展时仍跑满
    #   8 轮。此预算与 step4_case_timeout_sec 互补：后者是硬超时（asyncio.wait_for 杀整用例，
    #   丢证据），前者是软熔断（提前收口，保留已获 DOM/证据，不触发 harness 硬杀）。
    #   取值依据: 实测约 48s/轮 → 180s ≈ 4 轮，即 **8 轮上限的一半**，既砍掉长尾
    #   （384s→180s）又保留一半合法推进机会（正确性优先于速度，见 AGENTS.md §10.1）。
    step4_ai_loop_budget_sec: int = _as_int(
        "step4_ai_loop_budget_sec", 180, min_value=15, max_value=600
    )
    # ★ Fix G(2026-09-27): 连续无进展早停阈值（轮）—— 页面指纹（URL+可见文本）连续 N 轮
    #   完全不变即终止 AI loop（与 ai_loop_max_no_change_rounds 的动作签名判据互补，
    #   覆盖"动作不同但页面毫无变化"的空转）。
    ai_loop_max_no_progress_rounds: int = _as_int(
        "ai_loop_max_no_progress_rounds", 3, min_value=2, max_value=6
    )
    browser_max_dom_elements: int = _as_int(
        "BROWSER_MAX_DOM_ELEMENTS", 120, min_value=20, max_value=1000
    )
    # ★ v3.0 (2026-07-10): 默认并行度从 1 提升到 2
    #   推荐值：4 并发（8GB+内存）/ 2 并发（4GB内存）/ 1 并发（调试模式）
    #   注意：§10.1 红线"失败即停"强制 max_parallel=1，本配置仅用于推荐展示
    browser_max_parallel: int = _as_int(
        "browser_max_parallel", 2, min_value=1, max_value=8
    )
    # ★ v3.0 (2026-07-10): 单用例总超时（秒），防 AI 循环卡死（最坏8轮×3-8秒=5-10分钟）
    step4_case_timeout_sec: int = _as_int(
        "step4_case_timeout_sec", 300, min_value=30, max_value=600
    )
    # ★ v3.0.1 (2026-07-10): AI loop 无DOM动作致命bug检测阈值（秒），超时无成功DOM操作则中断
    step4_idle_timeout_sec: int = _as_int(
        "step4_idle_timeout_sec", 180, min_value=5, max_value=300
    )
    # ★ v3.0 (2026-07-10): 上传测试文件路径（规则生成用例使用，可配置）
    test_upload_valid_file: str = _cfg(
        "test_upload_valid_file", "test_files/sample.txt"
    )
    test_upload_invalid_file: str = _cfg(
        "test_upload_invalid_file", "test_files/invalid.exe"
    )
    browser_parallel_auto_limit: bool = _as_bool("BROWSER_PARALLEL_AUTO_LIMIT", True)
    browser_parallel_reserved_mb: int = _as_int(
        "BROWSER_PARALLEL_RESERVED_MB", 2048, min_value=512, max_value=32768
    )
    browser_parallel_instance_mb: int = _as_int(
        "BROWSER_PARALLEL_INSTANCE_MB", 800, min_value=256, max_value=4096
    )
    data_factory_download_timeout_ms: int = _as_int(
        "DATA_FACTORY_DOWNLOAD_TIMEOUT_MS", 30000, min_value=1000
    )
    data_factory_page_text_max_chars: int = _as_int(
        "DATA_FACTORY_PAGE_TEXT_MAX_CHARS", 3000, min_value=500
    )
    data_factory_ai_max_tokens: int = _as_int(
        "DATA_FACTORY_AI_MAX_TOKENS", 512, min_value=128, max_value=4096
    )
    rag_max_load_chunks: int = _as_int("RAG_MAX_LOAD_CHUNKS", 5000, min_value=100)
    rag_max_top_k: int = _as_int("RAG_MAX_TOP_K", 50, min_value=1, max_value=200)
    # ★ v4: RAG 语义模式 prompt 预算（字符数），控制 retrieve_and_resolve 输出长度
    rag_prompt_budget: int = _as_int(
        "RAG_PROMPT_BUDGET", 4000, min_value=500, max_value=20000
    )
    pause_max_wait_seconds: int = _as_int(
        "PIPELINE_PAUSE_MAX_WAIT_SECONDS", 1800, min_value=1
    )
    ai_local_timeout: float = _as_float("ai_local_timeout", 60.0, min_value=1.0)
    yapi_timeout: int = _as_int("YAPI_TIMEOUT_SEC", 30, min_value=5, max_value=120)

    # ── ★ KEEP-08: 置信度阈值与权重（可通过环境变量覆盖）──
    confidence_low_threshold: float = _as_float(
        "confidence_low_threshold", 0.50, min_value=0.0, max_value=1.0
    )
    confidence_medium_threshold: float = _as_float(
        "confidence_medium_threshold", 0.70, min_value=0.0, max_value=1.0
    )
    confidence_high_threshold: float = _as_float(
        "confidence_high_threshold", 0.90, min_value=0.0, max_value=1.0
    )
    confidence_weight_assertion: float = _as_float(
        "CONFIDENCE_WEIGHT_ASSERTION", 0.30, min_value=0.0, max_value=1.0
    )
    confidence_weight_structured: float = _as_float(
        "CONFIDENCE_WEIGHT_STRUCTURED", 0.20, min_value=0.0, max_value=1.0
    )
    confidence_weight_hard_verify: float = _as_float(
        "CONFIDENCE_WEIGHT_HARD_VERIFY", 0.18, min_value=0.0, max_value=1.0
    )
    confidence_weight_first_try: float = _as_float(
        "CONFIDENCE_WEIGHT_FIRST_TRY", 0.15, min_value=0.0, max_value=1.0
    )
    confidence_weight_semantic: float = _as_float(
        "CONFIDENCE_WEIGHT_SEMANTIC", 0.12, min_value=0.0, max_value=1.0
    )
    confidence_weight_cache: float = _as_float(
        "CONFIDENCE_WEIGHT_CACHE", 0.05, min_value=0.0, max_value=1.0
    )

    # ── ★ KEEP-14: baseline_health 阈值可配置 ──
    baseline_health_degraded_threshold: float = _as_float(
        "BASELINE_HEALTH_DEGRADED_THRESHOLD", 0.6, min_value=0.0, max_value=1.0
    )

    # ── ★ P2-02: AI 默认参数（集中管理，减少硬编码）──
    # ★ 默认温度 0.0：保证需求拆解/用例生成的输出稳定性（同输入→同输出）。
    # 需要创意性的场景（如脑图、对话）可在调用点显式传更高温度。
    ai_default_temperature: float = _as_float(
        "AI_DEFAULT_TEMPERATURE", 0.0, min_value=0.0, max_value=2.0
    )
    ai_default_max_tokens: int = _as_int(
        "AI_DEFAULT_MAX_TOKENS", 8192, min_value=256, max_value=65536
    )
    # ★ 确定性采样种子：相同 seed + 相同参数 → 相同输出（DeepSeek/SiliconFlow 均支持）
    # 设为 None 时不传 seed（保持随机性）；设为固定整数（如 42）时强制确定性输出。
    # 主要用于 requirement_analysis / feature_driven_extract 等需要稳定性的场景。
    ai_stability_seed: int | None = 42

    # ── ★ F1 全局修复 [未复核-需 GLM/DeepSeek 把关]（2026-08-29）：失败分类 AI 增补开关 ──
    #   True: 规则判定后调用 enrich_root_cause_ai，details 增补 ai_* 键（fail-open，不影响判定）
    #   False: 直接跳过 AI 增补（一键回退，不删代码）
    failure_ai_enrich_enabled: bool = _as_bool("FAILURE_AI_ENRICH_ENABLED", True)

    # ── ★ F3 全局修复 [未复核-需 GLM/DeepSeek 把关]（2026-08-29）：replay 成功后断言跳过开关 ──
    #   True: op_cache_v2 replay 命中时，"成功后断言"标记 NOT_APPLICABLE/SKIP（REPLAY_NO_ACTION）
    #   False: 不跳过任何断言（回到旧行为；注意原后处理补丁已删除，应急需连同恢复补丁）
    replay_assertion_skip_enabled: bool = _as_bool("REPLAY_ASSERTION_SKIP_ENABLED", True)

    # ★ 多轮共识提取：消除 AI 单次提取的完整性波动
    #   rounds: 提取轮数（默认 3，≥2 启用共识模式）
    #   min_consensus: 最少共识轮数（默认 2，即多数投票）
    #   设为 None 或 rounds=1 时回退到单轮提取
    ai_consensus_rounds: int | None = 3
    ai_consensus_min: int | None = 2

    # ★ 主题一致性门禁：把不被允许的需求（语义偏离整体主题）标 out_of_scope
    #   阈值 ∈ (0,1]，越低越宽松；设为 0 或 None 时关闭门禁
    #   打分 = 每条需求与文档提炼"主题种子"的 embedding 最大余弦相似度
    ai_theme_gate_threshold: float = _as_float(
        "AI_THEME_GATE_THRESHOLD", 0.0, min_value=0.0, max_value=1.0
    )

    # ★ Step3 方法论高危标签处置策略（T2 硬校验的落地开关）：
    #   tag   = 软打标（默认/当前行为）：仅打 method_valid=0 + 记日志，仍写入并执行
    #   block = 硬门禁：高危违法标签的用例不写入可执行集（入库即剔除 + 质量摘要标注）
    #   说明：T2 校验器早已检出"白名单外 scenario_type/test_method"，但调用方只记日志；
    #         此开关把 blocked 信号真正落为门禁，避免非法方法论用例流入 Step4 执行。
    step3_method_block: str = _as_str("STEP3_METHOD_BLOCK", "tag")

    # ── ★ 交叉验证双模型温度（通过温度差制造输出多样性） ──
    cross_validation_model_a_temperature: float = _as_float(
        "CROSS_VALIDATION_MODEL_A_TEMPERATURE", 0.0, min_value=0.0, max_value=2.0
    )
    cross_validation_model_b_temperature: float = _as_float(
        "CROSS_VALIDATION_MODEL_B_TEMPERATURE", 0.1, min_value=0.0, max_value=2.0
    )

    # ── ★ Figma 失败节点批量重试批次大小 ──
    figma_batch_size: int = _as_int("figma_batch_size", 20, min_value=1, max_value=100)

    # ── ★ P2-02: 需求分析阈值 ──
    requirement_gate_decomposition_threshold: float = _as_float(
        "REQ_GATE_DECOMPOSITION_THRESHOLD", 0.3, min_value=0.0, max_value=1.0
    )
    requirement_gate_intent_threshold: float = _as_float(
        "REQ_GATE_INTENT_THRESHOLD", 0.3, min_value=0.0, max_value=1.0
    )
    requirement_gate_intent_semantic_threshold: float = _as_float(
        "REQ_GATE_INTENT_SEMANTIC_THRESHOLD", 0.6, min_value=0.0, max_value=1.0
    )
    requirement_gate_decomposition_warn_threshold: float = _as_float(
        "REQ_GATE_DECOMPOSITION_WARN_THRESHOLD", 0.6, min_value=0.0, max_value=1.0
    )
    requirement_gate_step_block_threshold: float = _as_float(
        "REQ_GATE_STEP_BLOCK_THRESHOLD", 0.2, min_value=0.0, max_value=1.0
    )
    requirement_gate_step_semantic_threshold: float = _as_float(
        "REQ_GATE_STEP_SEMANTIC_THRESHOLD", 0.55, min_value=0.0, max_value=1.0
    )
    requirement_gate_top_score_threshold: float = _as_float(
        "REQ_GATE_TOP_SCORE_THRESHOLD", 0.55, min_value=0.0, max_value=1.0
    )

    # ── ★ case_validator 白名单配置覆盖（DB action_library_store 之外的额外白名单） ──
    # 注：实际白名单 = _DEFAULT_ACTION_TYPES（硬编码基线）+ 此配置 + DB action_library_store
    case_valid_action_types: str = _as_str("CASE_VALID_ACTION_TYPES", "")

    # ── ★ P2-02: RAG 检索阈值 ──
    rag_vector_search_threshold: float = _as_float(
        "RAG_VECTOR_SEARCH_THRESHOLD", 0.3, min_value=0.0, max_value=1.0
    )
    rag_semantic_dedup_threshold: float = _as_float(
        "RAG_SEMANTIC_DEDUP_THRESHOLD", 0.85, min_value=0.0, max_value=1.0
    )
    # ── ★ B2: RAG query 改写 / rerank（默认关闭，向后兼容） ──
    rag_enable_query_rewrite: bool = _as_bool("RAG_ENABLE_QUERY_REWRITE", False)
    rag_enable_rerank: bool = _as_bool("RAG_ENABLE_RERANK", False)
    rag_query_rewrite_timeout: float = float(
        _as_int("RAG_QUERY_REWRITE_TIMEOUT_SEC", 5, min_value=1, max_value=30)
    )

    # ── ★ L7 Cross-Encoder 重打分阈值（分数低于此值的 chunk 被过滤） ──
    rag_l7_cross_encoder_threshold: float = _as_float(
        "RAG_L7_CROSS_ENCODER_THRESHOLD", 0.3, min_value=0.0, max_value=1.0
    )

    # ── ★ B4: 视觉对比 SSIM 可选支持（默认关闭，向后兼容） ──
    visual_use_ssim: bool = _as_bool("VISUAL_USE_SSIM", False)

    # ── ★ P2-02: 风险分析 ──
    risk_embedding_similarity_threshold: float = _as_float(
        "RISK_EMBEDDING_SIMILARITY_THRESHOLD", 0.5, min_value=0.0, max_value=1.0
    )

    # ── ★ P2-02: 需求拆解/Coverage ──
    dedup_similarity_threshold: float = _as_float(
        "DEDUP_SIMILARITY_THRESHOLD", 0.85, min_value=0.0, max_value=1.0
    )
    # ★ S3-014: 交叉验证触发阈值 (high=仅高风险, medium=中高风险, low=全部)
    cross_validation_risk_threshold: str = (
        os.environ.get("CROSS_VALIDATION_RISK_THRESHOLD", "high") or "high"
    )
    # ★ v3.1: 置信度拦截后自动重建（默认关闭，开启后低置信度用例自动触发 AI 重新建模+去重）
    confidence_auto_rebuild: bool = _as_bool("CONFIDENCE_AUTO_REBUILD", False)
    confidence_auto_rebuild_max: int = _as_int(
        "CONFIDENCE_AUTO_REBUILD_MAX", 5, min_value=1, max_value=50
    )
    step2_changed_page_threshold: float = _as_float(
        "STEP2_CHANGED_PAGE_THRESHOLD", 0.5, min_value=0.0, max_value=1.0
    )

    # ── ★ v6: Step2 AI 调用参数 ──
    step2_discover_max_tokens: int = _as_int(
        "STEP2_DISCOVER_MAX_TOKENS", 2048, min_value=256, max_value=16384
    )
    step2_discover_raw_text_limit: int = _as_int(
        "STEP2_DISCOVER_RAW_TEXT_LIMIT", 10000, min_value=1000
    )

    # ── ★ Step2 智能分片配置 ──
    step2_shard_max_chars: int = _as_int(
        "STEP2_SHARD_MAX_CHARS", 15000, min_value=3000, max_value=50000
    )
    step2_shard_overlap_chars: int = _as_int(
        "STEP2_SHARD_OVERLAP_CHARS", 200, min_value=50, max_value=2000
    )
    step2_shard_max_count: int = _as_int(
        "STEP2_SHARD_MAX_COUNT", 8, min_value=2, max_value=20
    )
    step2_shard_model_safety_factor: float = _as_float(
        "STEP2_SHARD_MODEL_SAFETY_FACTOR", 0.6, min_value=0.3, max_value=0.9
    )
    step2_large_doc_threshold: int = _as_int(
        "STEP2_LARGE_DOC_THRESHOLD", 30000, min_value=5000, max_value=100000
    )
    step2_supplementary_min_table_hits: int = _as_int(
        "STEP2_SUPPLEMENTARY_MIN_TABLE_HITS", 2, min_value=1, max_value=10
    )
    step2_supplementary_figma_overlap: float = _as_float(
        "STEP2_SUPPLEMENTARY_FIGMA_OVERLAP", 0.3, min_value=0.0, max_value=1.0
    )

    # ── ★ Step2 行为阈值（消除硬编码）──
    step2_merge_short_module_chars: int = _as_int(
        "STEP2_MERGE_SHORT_MODULE_CHARS", 200, min_value=50, max_value=2000
    )
    step2_structural_max_modules: int = _as_int(
        "STEP2_STRUCTURAL_MAX_MODULES", 15, min_value=2, max_value=50
    )
    step2_dedup_jaccard_threshold: float = _as_float(
        "STEP2_DEDUP_JACCARD_THRESHOLD", 0.6, min_value=0.3, max_value=0.95
    )
    step2_ui_reduce_chars: int = _as_int(
        "STEP2_UI_REDUCE_CHARS", 500, min_value=100, max_value=3000
    )
    step2_ui_full_max_chars: int = _as_int(
        "STEP2_UI_FULL_MAX_CHARS", 3000, min_value=500, max_value=8000
    )
    step2_ui_shard_max_chars: int = _as_int(
        "STEP2_UI_SHARD_MAX_CHARS", 2000, min_value=200, max_value=5000
    )
    step2_knowledge_max_chars: int = _as_int(
        "STEP2_KNOWLEDGE_MAX_CHARS", 3000, min_value=500, max_value=10000
    )
    step2_quick_mode_max_chars: int = _as_int(
        "STEP2_QUICK_MODE_MAX_CHARS", 3000, min_value=500, max_value=10000
    )
    step2_per_module_timeout_seconds: int = _as_int(
        "STEP2_PER_MODULE_TIMEOUT_SECONDS", 300, min_value=30, max_value=1800
    )
    step2_max_modules: int = _as_int("STEP2_MAX_MODULES", 20, min_value=2, max_value=50)

    # ── ★ Step3 用例建模配置（支持算法自调优推荐值）──
    step3_parallel_workers: int = _as_int(
        "step3_parallel_workers", 10, min_value=1, max_value=20
    )
    step3_rag_top_k: int = _as_int("step3_rag_top_k", 5, min_value=0, max_value=20)
    step3_rag_retrieve_types: str = "figma_nodes,figma_components,similar_requirements"
    step3_ctx_ui: int = _as_int("step3_ctx_ui", 2200, min_value=200, max_value=8000)
    step3_ctx_trace: int = _as_int(
        "step3_ctx_trace", 1400, min_value=200, max_value=6000
    )
    step3_ctx_schema: int = _as_int(
        "step3_ctx_schema", 1400, min_value=200, max_value=6000
    )
    step3_ctx_data_dict: int = _as_int(
        "step3_ctx_data_dict", 2200, min_value=200, max_value=6000
    )
    step3_ctx_project: int = _as_int(
        "step3_ctx_project", 1000, min_value=100, max_value=4000
    )
    step3_ctx_rules: int = _as_int(
        "step3_ctx_rules", 900, min_value=100, max_value=3000
    )
    step3_ctx_few_shot: int = _as_int(
        "step3_ctx_few_shot", 900, min_value=100, max_value=3000
    )
    step3_ctx_feedback: int = _as_int(
        "step3_ctx_feedback", 700, min_value=100, max_value=2000
    )

    # ── ★ v9 精简: Step4 红队默认为关 ──
    step4_red_team_enabled: bool = _as_bool("step4_red_team_enabled", False)

    # ── Step4 自我修复（重建 HARNESS_ERROR 失败用例）默认关闭 ──
    #   根因：该闭环把失败原因注入 prompt 后**重新生成并替换**用例（_remodel_intent），
    #   属"对失败用例打补丁"——用例被改写后失败消失，反而掩盖引擎缺陷。
    #   需显式置 true 才启用。
    step4_self_repair_enabled: bool = _as_bool("step4_self_repair_enabled", False)

    # ── ★ v9 精简: Step4 风险分级采样 ──
    # ★ 根因修复(2026-09-24): 采样策略对称化 —— P0~P4 每档独立「纳入 + 抽样率」，
    #   默认全量纳入（全量覆盖优先），杜绝 P3/P4 被静默丢弃（实测 58% 用例消失）。
    step4_include_p0: bool = _as_bool("step4_include_p0", True)
    step4_p0_sample_rate: float = _as_float(
        "step4_p0_sample_rate", 1.0, min_value=0.0, max_value=1.0
    )
    step4_include_p1: bool = _as_bool("step4_include_p1", True)
    step4_p1_sample_rate: float = _as_float(
        "step4_p1_sample_rate", 1.0, min_value=0.0, max_value=1.0
    )
    step4_include_p2: bool = _as_bool("step4_include_p2", True)
    step4_p2_sample_rate: float = _as_float(
        "step4_p2_sample_rate", 1.0, min_value=0.0, max_value=1.0
    )
    step4_include_p3: bool = _as_bool("step4_include_p3", True)
    step4_p3_sample_rate: float = _as_float(
        "step4_p3_sample_rate", 1.0, min_value=0.0, max_value=1.0
    )
    step4_include_p4: bool = _as_bool("step4_include_p4", True)
    step4_p4_sample_rate: float = _as_float(
        "step4_p4_sample_rate", 1.0, min_value=0.0, max_value=1.0
    )
    # ★ 通用性修复(2026-08-09, 用户问题 #6): 失败即停默认改为 False
    #   根因: 默认 True 导致遇到首个失败用例整个管线停止，丢失后续用例的执行结果。
    #   软件测试工程理论要求：测试执行应收集所有失败信息，而非"第一个失败即停止"。
    #   修复: 默认 False（继续执行全部用例），用户可通过前端 UI 或环境变量
    #   step4_stop_on_failure=True 切换回交互模式。
    #   通用性: 跨系统/行业/场景 — 所有自动执行场景都应默认收集全部结果。
    step4_stop_on_failure: bool = _as_bool("step4_stop_on_failure", False)

    # ── 运行期 AI 闸门（架构约束：AI 前移到生成侧，运行期默认零 AI）──
    #   判据唯一实现在 src/ai/runtime_ai_gate.py；router.route 与 _call_api 只读。
    step4_runtime_ai: bool = _as_bool("step4_runtime_ai", False)

    # ── ★ v6: Step5/RAG 蓝红开关 ──
    step5_blue_red_enabled: bool = _as_bool("STEP5_BLUE_RED_ENABLED", True)
    rag_blue_red_enabled: bool = _as_bool("RAG_BLUE_RED_ENABLED", True)

    # ── ★ v7: 证据引擎 (Evidence Engine) ──
    evidence_engine_enabled: bool = _as_bool("EVIDENCE_ENGINE_ENABLED", True)
    evidence_bayesian_prior_weight: float = _as_float(
        "EVIDENCE_BAYESIAN_PRIOR_WEIGHT", 10.0, min_value=1.0, max_value=100.0
    )
    evidence_min_calibration_samples: int = _as_int(
        "EVIDENCE_MIN_CALIBRATION_SAMPLES", 30, min_value=5, max_value=1000
    )

    # ★ P1: 模糊匹配/语义去重阈值
    fuzzy_match_default_threshold: float = _as_float(
        "FUZZY_MATCH_THRESHOLD", 0.55, min_value=0.0, max_value=1.0
    )

    # ★ P1: 证据引擎置信度档位
    evidence_high_confidence: float = _as_float(
        "EVIDENCE_HIGH_CONFIDENCE", 0.85, min_value=0.0, max_value=1.0
    )
    evidence_medium_confidence: float = _as_float(
        "EVIDENCE_MEDIUM_CONFIDENCE", 0.70, min_value=0.0, max_value=1.0
    )

    # ★ P1: 证据引擎因子权重（与 evidence/confidence.py _DEFAULT_WEIGHTS 对齐）
    evidence_weight_evidence: float = _as_float(
        "EVIDENCE_WEIGHT_EVIDENCE", 0.40, min_value=0.0, max_value=1.0
    )
    evidence_weight_calibration: float = _as_float(
        "EVIDENCE_WEIGHT_CALIBRATION", 0.35, min_value=0.0, max_value=1.0
    )
    evidence_weight_extraction: float = _as_float(
        "EVIDENCE_WEIGHT_EXTRACTION", 0.15, min_value=0.0, max_value=1.0
    )
    evidence_weight_schema: float = _as_float(
        "EVIDENCE_WEIGHT_SCHEMA", 0.10, min_value=0.0, max_value=1.0
    )

    # ★ P1: 三轨置信度评分档位（与 verification/_confidence.py 对齐）
    verification_confidence_all_pass: float = _as_float(
        "VERIFICATION_CONFIDENCE_ALL_PASS", 0.95, min_value=0.0, max_value=1.0
    )
    verification_confidence_has_pass: float = _as_float(
        "VERIFICATION_CONFIDENCE_HAS_PASS", 0.65, min_value=0.0, max_value=1.0
    )
    verification_confidence_has_fail: float = _as_float(
        "VERIFICATION_CONFIDENCE_HAS_FAIL", 0.35, min_value=0.0, max_value=1.0
    )
    verification_confidence_all_fail: float = _as_float(
        "VERIFICATION_CONFIDENCE_ALL_FAIL", 0.10, min_value=0.0, max_value=1.0
    )

    # ★ P1: 红队/对抗快速通道阈值
    adversarial_fast_reliability: float = _as_float(
        "ADVERSARIAL_FAST_RELIABILITY", 0.85, min_value=0.0, max_value=1.0
    )

    # ★ P1: 追溯缓存命中阈值
    traceability_cache_hit_threshold: float = _as_float(
        "TRACEABILITY_CACHE_HIT_THRESHOLD", 0.9, min_value=0.0, max_value=1.0
    )

    # ★ P1: AI 重试参数(已在 ai_cloud_retry_* 中,这里增加本地检查TTL)
    ai_local_check_cache_ttl: int = _as_int(
        "AI_LOCAL_CHECK_CACHE_TTL", 5, min_value=1, max_value=60
    )

    # ★ P1: AI 云端调用重试参数（与 config_loader/ai.py 对齐）
    ai_cloud_retry_max: int = _as_int(
        "AI_CLOUD_RETRY_MAX", 3, min_value=1, max_value=10
    )
    ai_cloud_retry_base_delay: float = _as_float(
        "AI_CLOUD_RETRY_BASE_DELAY", 1.5, min_value=0.1, max_value=30.0
    )
    ai_cloud_retry_max_delay: float = _as_float(
        "AI_CLOUD_RETRY_MAX_DELAY", 10.0, min_value=1.0, max_value=60.0
    )

    # ★ P1: 风险分析 max_tokens
    risk_analyzer_max_tokens: int = _as_int(
        "RISK_ANALYZER_MAX_TOKENS", 4096, min_value=512, max_value=32768
    )
    risk_analyzer_minor_max_tokens: int = _as_int(
        "RISK_ANALYZER_MINOR_MAX_TOKENS", 2048, min_value=256, max_value=16384
    )

    # ★ P1: op_cache 容量
    op_cache_max_capacity: int = _as_int(
        "OP_CACHE_MAX_CAPACITY", 5000, min_value=100, max_value=100000
    )

    # ★ P1: RAG 引擎参数（消除 engine_core.py 硬编码）
    rag_embed_dim: int = _as_int("RAG_EMBED_DIM", 128, min_value=64, max_value=1024)
    rag_bm25_k1: float = _as_float("RAG_BM25_K1", 1.5, min_value=0.1, max_value=5.0)
    rag_bm25_b: float = _as_float("RAG_BM25_B", 0.75, min_value=0.1, max_value=1.0)
    rag_context_snippet_len: int = _as_int(
        "RAG_CONTEXT_SNIPPET_LEN", 200, min_value=50, max_value=1000
    )
    rag_similar_req_snippet_len: int = _as_int(
        "RAG_SIMILAR_REQ_SNIPPET_LEN", 150, min_value=50, max_value=1000
    )
    rag_similar_req_top_n: int = _as_int(
        "RAG_SIMILAR_REQ_TOP_N", 3, min_value=1, max_value=20
    )
    rag_embed_text_max_len: int = _as_int(
        "RAG_EMBED_TEXT_MAX_LEN", 2000, min_value=500, max_value=8000
    )
    rag_embed_chunk_preview: int = _as_int(
        "RAG_EMBED_CHUNK_PREVIEW", 500, min_value=100, max_value=2000
    )
    rag_embed_chunk_max_len: int = _as_int(
        "RAG_EMBED_CHUNK_MAX_LEN", 1000, min_value=200, max_value=4000
    )
    rag_embed_log_interval: int = _as_int(
        "RAG_EMBED_LOG_INTERVAL", 50, min_value=10, max_value=500
    )
    rag_keyword_max_count: int = _as_int(
        "RAG_KEYWORD_MAX_COUNT", 50, min_value=10, max_value=200
    )
    rag_upload_segment_max_len: int = _as_int(
        "RAG_UPLOAD_SEGMENT_MAX_LEN", 500, min_value=100, max_value=2000
    )
    rag_api_meta_truncate: int = _as_int(
        "RAG_API_META_TRUNCATE", 300, min_value=50, max_value=1000
    )
    rag_db_col_limit: int = _as_int("RAG_DB_COL_LIMIT", 15, min_value=5, max_value=100)
    rag_db_col_format_limit: int = _as_int(
        "RAG_DB_COL_FORMAT_LIMIT", 10, min_value=5, max_value=50
    )
    rag_page_element_limit: int = _as_int(
        "RAG_PAGE_ELEMENT_LIMIT", 5, min_value=1, max_value=50
    )

    # ★ P1: 性能评分阈值 (scorecard)
    scorecard_performance_fast_threshold: int = _as_int(
        "SCORECARD_PERFORMANCE_FAST_THRESHOLD", 5000, min_value=100, max_value=60000
    )
    scorecard_performance_slow_threshold: int = _as_int(
        "SCORECARD_PERFORMANCE_SLOW_THRESHOLD", 30000, min_value=1000, max_value=120000
    )

    # ── ★ 07-07: 组合断言增强开关 ──
    #   方案 A（场景规则注入）：始终启用，成本极低（~300字）
    #   方案 B（Few-Shot 示例注入）：可开关，影响 token 成本和 AI 效率（~2300字）
    #   方案 C（后处理算法追加）：始终启用，纯算法无 AI 调用
    combo_few_shot_enabled: bool = _as_bool("COMBO_FEW_SHOT_ENABLED", False)
    #   方案 C 后处理：单条用例最多追加的断言数（避免无限追加）
    combo_assertion_max_append: int = _as_int(
        "COMBO_ASSERTION_MAX_APPEND", 3, min_value=1, max_value=10
    )
    #   方案 C 后处理：追加断言时是否要求 DB/API 轨道（关闭则只追加 UI 断言，速度快）
    combo_assertion_require_multi_track: bool = _as_bool(
        "COMBO_ASSERTION_REQUIRE_MULTI_TRACK", True
    )

    # ── ★ P0-2废弃(2026-07-29): DOM爬取配置已移除，AgentExplorer 实时探索替代 ──

    # ── ★ DOM 选择器集中配置（消除 navigator/extractor/spa_menu 硬编码框架类）──
    # 完整默认值定义在 src/core/pipeline_config_selectors.py；项目可通过 DB/环境变量/YAML 覆盖
    dom_top_nav_selectors: str = _as_str(
        "dom_top_nav_selectors", _pcds.DOM_TOP_NAV_SELECTORS
    )
    dom_menu_selectors: str = _as_str("dom_menu_selectors", _pcds.DOM_MENU_SELECTORS)
    dom_top_nav_button_selectors: str = _as_str(
        "dom_top_nav_button_selectors", _pcds.DOM_TOP_NAV_BUTTON_SELECTORS
    )
    dom_login_success_nav_selectors: str = _as_str(
        "dom_login_success_nav_selectors", _pcds.DOM_LOGIN_SUCCESS_NAV_SELECTORS
    )
    dom_tab_selectors: str = _as_str("dom_tab_selectors", _pcds.DOM_TAB_SELECTORS)
    dom_radio_selectors: str = _as_str("dom_radio_selectors", _pcds.DOM_RADIO_SELECTORS)
    dom_radio_input_selectors: str = _as_str(
        "dom_radio_input_selectors", _pcds.DOM_RADIO_INPUT_SELECTORS
    )
    dom_data_menu_key_selector: str = _as_str(
        "dom_data_menu_key_selector", _pcds.DOM_DATA_MENU_KEY_SELECTOR
    )
    dom_spa_menu_selectors: str = _as_str(
        "dom_spa_menu_selectors", _pcds.DOM_SPA_MENU_SELECTORS
    )
    dom_spa_menu_fallback_selectors: str = _as_str(
        "dom_spa_menu_fallback_selectors", _pcds.DOM_SPA_MENU_FALLBACK_SELECTORS
    )
    dom_spa_sidebar_tree_selectors: str = _as_str(
        "dom_spa_sidebar_tree_selectors", _pcds.DOM_SPA_SIDEBAR_TREE_SELECTORS
    )
    dom_spa_submenu_title_selector: str = _as_str(
        "dom_spa_submenu_title_selector", _pcds.DOM_SPA_SUBMENU_TITLE_SELECTOR
    )
    dom_spa_leaf_menu_selectors: str = _as_str(
        "dom_spa_leaf_menu_selectors", _pcds.DOM_SPA_LEAF_MENU_SELECTORS
    )
    dom_feature_selectors: str = _as_str(
        "dom_feature_selectors", _pcds.DOM_FEATURE_SELECTORS
    )


pipeline_config = PipelineConfig()
pipeline_config._read_config_value = (
    _read_config_value  # ★ 兼容：允许实例访问模块级函数
)


# ── 浏览器并行推荐：进程枚举较慢（Windows 上 ~1s），结果 30s 缓存 ──
import threading as _bp_threading
import time as _bp_time

_BP_CACHE_TTL = 30  # 秒
_bp_cache: dict = {"ts": 0.0, "key": None, "result": None}
_bp_cache_lock = _bp_threading.Lock()


def get_browser_parallel_recommendation(configured_max: int = None) -> dict:
    configured_max = int(configured_max or int(_cfg("browser_max_parallel", 2)) or 2)
    configured_max = max(1, min(8, configured_max))
    # ★ 30s 缓存（按 configured_max 维度区分，避免不同入参互相覆盖）
    now = _bp_time.time()
    with _bp_cache_lock:
        if (
            _bp_cache["result"] is not None
            and _bp_cache["key"] == configured_max
            and now - _bp_cache["ts"] < _BP_CACHE_TTL
        ):
            return dict(_bp_cache["result"])  # 返回副本防止外部 mutate

    reserved_mb = int(_cfg("browser_parallel_reserved_mb", 2048))
    default_instance_mb = int(_cfg("browser_parallel_instance_mb", 800))

    info = {
        "configured_max": configured_max,
        "recommended": 1,
        "effective": configured_max,
        "auto_limit": bool(_cfg("browser_parallel_auto_limit", True)),
        "available_mb": None,
        "browser_avg_mb": default_instance_mb,
        "reserved_mb": reserved_mb,
        "reason": "未检测到系统内存，使用保守默认值",
    }
    try:
        import psutil

        mem = psutil.virtual_memory()
        available_mb = int(mem.available / 1024 / 1024)
        browser_names = (
            "chrome",
            "chromium",
            "msedge",
            "firefox",
            "webkit",
            "playwright",
        )
        browser_mem = []
        for proc in psutil.process_iter(["name", "memory_info"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if any(n in name for n in browser_names):
                    rss = proc.info["memory_info"].rss
                    if rss > 0:
                        browser_mem.append(int(rss / 1024 / 1024))
            except Exception as _e:
                _log.debug("浏览器进程内存采集跳过: %s", _e, exc_info=True)
                continue
        detected_avg_mb = (
            int(sum(browser_mem) / len(browser_mem))
            if browser_mem
            else default_instance_mb
        )
        browser_avg_mb = max(default_instance_mb, min(4096, detected_avg_mb))
        usable_mb = max(0, available_mb - reserved_mb)
        recommended = max(
            1, min(8, usable_mb // browser_avg_mb if browser_avg_mb else 1)
        )
        effective = (
            min(configured_max, recommended)
            if _cfg("browser_parallel_auto_limit", True)
            else configured_max
        )
        info.update(
            {
                "recommended": int(recommended),
                "effective": int(max(1, effective)),
                "available_mb": available_mb,
                "browser_avg_mb": browser_avg_mb,
                "reason": (
                    "基于可用内存 %dMB、预留 %dMB、浏览器平均占用 %dMB 估算"
                    % (available_mb, reserved_mb, browser_avg_mb)
                ),
            }
        )
    except Exception as e:
        info["effective"] = (
            min(configured_max, 1)
            if _cfg("browser_parallel_auto_limit", True)
            else configured_max
        )
        info["reason"] = "内存检测失败，使用保守默认值: %s" % str(e)[:120]

    # ★ 写入缓存
    with _bp_cache_lock:
        _bp_cache["ts"] = now
        _bp_cache["key"] = configured_max
        _bp_cache["result"] = dict(info)
    return info
