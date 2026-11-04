# -*- coding: utf-8 -*-
"""切项目自动套用算法推荐值（用户偏好驱动的自动调参）。

设计要点
--------
- 本模块承载「切项目自动套推荐值」的核心逻辑，供两处复用：
  1) ``pipeline_config/_routes.py`` 的 ``POST /api/pipeline-config/apply-recommended``
     （手动「一键套用」按钮可选复用）；
  2) ``project_core/_project.py`` 的 ``POST /api/project``（切项目时静默套用）。
- 偏好存储复用 ``user_preferences`` 表（按需建表，与 ``pipeline_user_config`` 同模式），
  与 config.yaml 解耦——它是「用户行为偏好」，不是引擎配置项。
- 为避免与 ``_routes`` 形成顶层循环导入，``CONFIG_SCHEMA`` 在函数内延迟导入。
"""

import json
from src.core.guarded import guard
import logging

_log = logging.getLogger(__name__)

# 偏好键：切项目是否自动套用算法推荐值（默认关闭 = 零行为变更）
PREF_AUTO_APPLY_RECOMMENDED = "pipeline_auto_apply_recommended_on_switch"


def _conn():
    from src.core.db_manager import get_connection

    return get_connection()


def _release(conn):
    with guard("_release: conn.close()", log=_log):
        conn.close()
    with guard("_release: from src.core.db_manager import rele", log=_log):
        from src.core.db_manager import release_connection

        release_connection(conn)


def _ensure_user_preferences(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_preferences (
            user_id    VARCHAR(128) NOT NULL,
            pref_key   VARCHAR(128) NOT NULL,
            pref_value TEXT NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, pref_key)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )


_uc_source_ensured = False


def _ensure_user_config(conn):
    # 与 _routes._ensure_table 保持同构；自动套用依赖该表存在
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pipeline_user_config (
            id           BIGINT AUTO_INCREMENT PRIMARY KEY,
            user_id      VARCHAR(128) NOT NULL,
            config_key   VARCHAR(128) NOT NULL,
            config_value TEXT NOT NULL,
            source       VARCHAR(16) NOT NULL DEFAULT 'manual',
            updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_user_key (user_id, config_key)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    # 旧表可能缺 source 列（幂等：已存在则忽略 Duplicate column 错误）
    global _uc_source_ensured
    if _uc_source_ensured:
        return
    try:
        conn.execute(
            "ALTER TABLE pipeline_user_config "
            "ADD COLUMN source VARCHAR(16) NOT NULL DEFAULT 'manual'"
        )
        _uc_source_ensured = True
    except Exception:
        # 列已存在或其他错误：仅告警一次，不阻断后续
        _uc_source_ensured = True


# ─────────────────────────── 用户偏好读写 ───────────────────────────

def read_pref(user_id: str, key: str, default=None):
    """读取用户偏好；值以 JSON 解析（支持 bool/number/str）。"""
    conn = _conn()
    try:
        _ensure_user_preferences(conn)
        row = conn.execute(
            "SELECT pref_value FROM user_preferences WHERE user_id=%s AND pref_key=%s",
            (user_id, key),
        ).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["pref_value"])
        except (ValueError, TypeError):
            return row["pref_value"]
    except Exception as _e:
        _log.debug("[auto_apply] 读偏好失败: %s", _e)
        return default
    finally:
        _release(conn)


def write_pref(user_id: str, key: str, value) -> None:
    """写入用户偏好（UPSERT，值以 JSON 存储）。"""
    conn = _conn()
    try:
        _ensure_user_preferences(conn)
        conn.execute(
            "INSERT INTO user_preferences(user_id, pref_key, pref_value) VALUES(%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE pref_value=VALUES(pref_value)",
            (user_id, key, json.dumps(value)),
        )
        conn.commit()
    except Exception as _e:
        _log.error("[auto_apply] 写偏好失败: %s", _e)
        raise
    finally:
        _release(conn)


def is_auto_apply_enabled(user_id: str) -> bool:
    """开关状态：默认 True（用户介入最少 —— 切项目即静默套用算法推荐值）。

    ★ 2026-08-26 产品决策（用户原话「用户介入越少越好」）：默认开启自动套用，
      新用户首次切项目即获得按项目上下文计算的智能默认值，零配置；老用户已手动
      精调的键由 apply_recommended_to_user(force=False) 保留（source='manual' 不覆盖）。
      偏好开关仍保留在 UI（pipeline-config.html 的 chkAutoApply），想完全手动的用户可关闭。
    """
    return bool(read_pref(user_id, PREF_AUTO_APPLY_RECOMMENDED, True))


# ─────────────────────────── 推荐值计算 ───────────────────────────

def _compute_recommended() -> dict:
    """基于当前项目上下文计算 Step2 + Step3 算法推荐值（零用户输入）。

    改用 detect_project_signals 做「按项目」检测，使推荐值真正随项目差异
    （需求数 / 文档数 / trace/figma 可用性）变化，而非所有项目一套值。
    """
    from src.core.pipeline_config import (
        auto_tune_step2,
        auto_tune_step3,
        detect_project_signals,
    )
    from src.core.config_loader import config as _cfg

    proj = getattr(_cfg, "project_name", "") or ""
    sig = detect_project_signals(proj)
    model_window = sig["model_window"]

    step2 = auto_tune_step2(
        doc_total_chars=sig["doc_total_chars"],
        model_window=model_window,
        has_figma=sig["has_figma"],
        has_db=sig["has_db"] or sig["has_trace"],
        doc_has_headings=False,
        num_reqs=sig["num_reqs"],
        num_docs=sig["num_docs"],
        has_trace=sig["has_trace"],
    )
    step3 = auto_tune_step3(
        model_window=model_window,
        num_reqs=sig["num_reqs"] or 1,
        has_rag=sig["has_rag"],
        has_trace=sig["has_trace"],
        has_db_schema=sig["has_db"],
    )
    return {"step2": step2, "step3": step3}


def _valid_config_keys() -> set:
    """复用 pipeline_config 白名单，避免任意键注入。"""
    from src.backend.routes.pipeline_config._routes import CONFIG_SCHEMA

    keys = set()
    for group in CONFIG_SCHEMA.values():
        keys.update(group.get("items", {}).keys())
    return keys


def apply_recommended_to_user(user_id: str, force: bool = False) -> int:
    """计算推荐值并写入 pipeline_user_config（仅白名单内键），返回套用项数。

    force=False（切项目静默套用）：
      仅覆盖「自动套用(auto)」或「尚未配置」的键；保留用户手动配置(manual)。
    force=True（用户显式「一键套用」按钮）：
      覆盖全部推荐键，并标记为 manual（视为用户采纳，后续切换不再被自动覆盖）。
    """
    rec = _compute_recommended()
    flat = {}
    for group in rec.values():
        for k, v in group.items():
            if k == "_meta":
                continue
            flat[k] = v

    valid = _valid_config_keys()
    conn = _conn()
    applied = 0
    new_src = "manual" if force else "auto"
    try:
        _ensure_user_config(conn)
        # 读取已有键及其来源（区分 manual / auto），用于 B：保留手动配置
        existing_rows = conn.execute(
            "SELECT config_key, source FROM pipeline_user_config WHERE user_id=%s",
            (user_id,),
        ).fetchall()
        existing = {r["config_key"]: (r["source"] or "manual") for r in existing_rows}

        for k, v in flat.items():
            if k not in valid:
                continue
            if v is None:
                continue
            # B：非强制模式下跳过用户手动配置，保留其精调结果
            if (not force) and existing.get(k) == "manual":
                continue
            conn.execute(
                "INSERT INTO pipeline_user_config(user_id, config_key, config_value, source) "
                "VALUES(%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE config_value=VALUES(config_value), source=VALUES(source)",
                (user_id, k, str(v), new_src),
            )
            applied += 1
        conn.commit()
    except Exception as _e:
        _log.error("[auto_apply] 套用推荐值失败: %s", _e)
        raise
    finally:
        _release(conn)

    with guard("apply_recommended_to_user: from src.core.pipeline_config import", log=_log):
        from src.core.pipeline_config import invalidate_user_config_cache

        invalidate_user_config_cache(user_id)
    return applied
