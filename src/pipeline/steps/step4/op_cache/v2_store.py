# -*- coding: utf-8 -*-
"""
★ P1-8: op_cache_v2 读写存储 — 真实原子指令序列缓存。

三拍设计（SOTA）：
  1. record: Step4 执行成功后，将真实 action_sequence 写入 op_cache_v2
  2. replay: 二次执行同一意图时，查询命中 → 直接重放 action_sequence，不调 AI
  3. repair: 页面 fingerprint 变化时 → fallback 到 AI（缓存不命中或失效）

与旧 step4_op_cache 的区别：
  - 旧表：基于 goal_normalized + dom_fingerprint 的单条 AI 决策缓存
  - v2表：基于 intent_hash 的完整原子指令序列缓存（多步）

通用性（§12.4 通用性自检）：
  1. 跨系统：intent_hash 基于 case_id + actions 摘要，不绑定业务术语
  2. 跨行业：action_sequence 是通用 JSON 数组
  3. 跨页面：page_fingerprint 可为 URL 哈希或 DOM 指纹
  4. 降级：查询失败不阻断，返回 None 让调用方 fallback 到 AI
  5. 职责边界：仅做缓存读写，不做执行/决策
"""
from __future__ import annotations
from src.core.guarded import guard

import hashlib
import json
import logging
from typing import Any, Optional

_log = logging.getLogger(__name__)

_TABLE = "op_cache_v2"


# ═══════════════════════════════════════════════════════════════
# 意图哈希计算（纯函数，无副作用）
# ═══════════════════════════════════════════════════════════════


def compute_intent_hash(case_id: str, actions: list[dict[str, Any]]) -> str:
    """计算意图哈希（SHA-256，64 字符）。

    通用性：基于 case_id + actions 摘要（type + target_hint + value），
           不含业务术语，同一用例同一 actions 生成相同 hash。

    参数:
        case_id: 用例 ID
        actions: 动作列表 [{type, target_hint, value, ...}]

    返回: 64 字符 SHA-256 哈希
    """
    parts = [str(case_id or "").strip()]
    for a in actions or []:
        a_type = str(a.get("type") or a.get("action") or "").strip()
        target = str(a.get("target_hint") or a.get("selector") or "").strip()
        value = str(a.get("value") or "").strip()
        parts.append("%s|%s|%s" % (a_type, target, value))
    raw = "\n".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_page_fingerprint(
    page_url: str, compact_state: Optional[list[dict]] = None
) -> str:
    """计算页面指纹（MD5，16 字符）。

    通用性：优先用 DOM 结构指纹（复用 compute_semantic_fingerprint），
           无 DOM 时降级为 URL 哈希。

    参数:
        page_url: 页面 URL
        compact_state: DOM 紧凑状态列表（可选）

    返回: 16 字符指纹
    """
    if compact_state:
        with guard("compute_page_fingerprint: from src.pipeline.steps.step4.op_cac", log=_log):
            from src.pipeline.steps.step4.op_cache import compute_semantic_fingerprint

            return compute_semantic_fingerprint(compact_state)
    # 降级：URL 哈希
    return hashlib.md5(str(page_url or "").encode("utf-8")).hexdigest()[:16]


# ═══════════════════════════════════════════════════════════════
# 存储类
# ═══════════════════════════════════════════════════════════════


class OpCacheV2Store:
    """op_cache_v2 MySQL 持久化存储。

    使用方式:
        store = OpCacheV2Store()
        # record: 执行成功后写入
        store.set(project, version_id, intent_hash, page_fp, action_sequence)
        # replay: 二次执行时查询
        cached = store.get(project, version_id, intent_hash)
        if cached:
            store.increment_hit(project, version_id, intent_hash)
            # 直接重放 cached["action_sequence"]
    """

    def __init__(self):
        self._last_error: str = ""

    def _get_conn(self):
        """获取数据库连接。"""
        from src.models.base import get_conn

        return get_conn()

    def get(
        self, project: str, version_id: int, intent_hash: str
    ) -> Optional[dict[str, Any]]:
        """查询缓存（replay 第①步）。

        返回: {action_sequence, page_fingerprint, hit_count} 或 None（未命中/异常）
        降级：查询异常不阻断，返回 None 让调用方 fallback 到 AI。
        """
        if not project or not intent_hash:
            return None
        conn = None
        try:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT action_sequence, page_fingerprint, hit_count "
                "FROM %s WHERE project=%%s AND version_id=%%s AND intent_hash=%%s"
                % _TABLE,
                (project, int(version_id or 0), intent_hash),
            ).fetchone()
            if not row:
                self._last_error = ""
                return None
            # 解析 action_sequence JSON
            raw_seq = row.get("action_sequence")
            if isinstance(raw_seq, str):
                action_sequence = json.loads(raw_seq)
            elif isinstance(raw_seq, (list, dict)):
                action_sequence = raw_seq
            else:
                action_sequence = []
            self._last_error = ""
            return {
                "action_sequence": action_sequence,
                "page_fingerprint": str(row.get("page_fingerprint") or ""),
                "hit_count": int(row.get("hit_count") or 0),
            }
        except Exception as e:
            self._last_error = str(e)[:200]
            _log.warning(
                "[v2_store] get 失败 project=%s hash=%s: %s",
                project,
                intent_hash[:12],
                str(e)[:120],
            )
            return None
        finally:
            if conn:
                with guard("get: conn.close()", log=_log, level=logging.WARNING):
                    conn.close()

    def set(
        self,
        project: str,
        version_id: int,
        intent_hash: str,
        page_fingerprint: str,
        action_sequence: list[dict[str, Any]],
    ) -> bool:
        """写入缓存（record 第①拍）。

        幂等：使用 REPLACE INTO，同一 intent_hash 覆盖更新。
        通用性：action_sequence 序列化为 JSON 存储。

        返回: True=成功, False=失败
        """
        if not project or not intent_hash:
            self._last_error = "missing project or intent_hash"
            return False
        seq_json = json.dumps(action_sequence or [], ensure_ascii=False)
        conn = None
        try:
            conn = self._get_conn()
            conn.execute(
                "REPLACE INTO %s (project, version_id, intent_hash, "
                "page_fingerprint, action_sequence, hit_count, created_at) "
                "VALUES (%%s, %%s, %%s, %%s, %%s, 0, NOW())" % _TABLE,
                (
                    project,
                    int(version_id or 0),
                    intent_hash,
                    str(page_fingerprint or "")[:64],
                    seq_json,
                ),
            )
            conn.commit()
            self._last_error = ""
            _log.debug(
                "[v2_store] set 成功 project=%s hash=%s actions=%d",
                project,
                intent_hash[:12],
                len(action_sequence or []),
            )
            return True
        except Exception as e:
            self._last_error = str(e)[:200]
            _log.warning(
                "[v2_store] set 失败 project=%s hash=%s: %s",
                project,
                intent_hash[:12],
                str(e)[:120],
            )
            return False
        finally:
            if conn:
                with guard("set: conn.close()", log=_log, level=logging.WARNING):
                    conn.close()

    def increment_hit(self, project: str, version_id: int, intent_hash: str) -> bool:
        """命中计数 +1（replay 命中后调用）。

        返回: True=成功, False=失败
        """
        if not project or not intent_hash:
            return False
        conn = None
        try:
            conn = self._get_conn()
            conn.execute(
                "UPDATE %s SET hit_count = hit_count + 1, updated_at = NOW() "
                "WHERE project=%%s AND version_id=%%s AND intent_hash=%%s" % _TABLE,
                (project, int(version_id or 0), intent_hash),
            )
            conn.commit()
            self._last_error = ""
            return True
        except Exception as e:
            self._last_error = str(e)[:200]
            _log.warning(
                "[v2_store] increment_hit 失败 project=%s hash=%s: %s",
                project,
                intent_hash[:12],
                str(e)[:120],
            )
            return False
        finally:
            if conn:
                with guard("increment_hit: conn.close()", log=_log, level=logging.WARNING):
                    conn.close()

    def delete(self, project: str, version_id: int, intent_hash: str) -> bool:
        """删除缓存（repair 时清理失效缓存）。

        返回: True=成功, False=失败
        """
        if not project or not intent_hash:
            return False
        conn = None
        try:
            conn = self._get_conn()
            conn.execute(
                "DELETE FROM %s WHERE project=%%s AND version_id=%%s AND intent_hash=%%s"
                % _TABLE,
                (project, int(version_id or 0), intent_hash),
            )
            conn.commit()
            self._last_error = ""
            return True
        except Exception as e:
            self._last_error = str(e)[:200]
            _log.warning(
                "[v2_store] delete 失败 project=%s hash=%s: %s",
                project,
                intent_hash[:12],
                str(e)[:120],
            )
            return False
        finally:
            if conn:
                with guard("delete: conn.close()", log=_log, level=logging.WARNING):
                    conn.close()

    def get_stats(self, project: str, version_id: int) -> dict[str, Any]:
        """获取缓存统计（总条数、总命中数）。

        返回: {total, total_hits} 或空 dict（异常时）
        """
        if not project:
            return {}
        conn = None
        try:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT COUNT(*) AS total, COALESCE(SUM(hit_count), 0) AS total_hits "
                "FROM %s WHERE project=%%s AND version_id=%%s" % _TABLE,
                (project, int(version_id or 0)),
            ).fetchone()
            self._last_error = ""
            if row:
                return {
                    "total": int(row.get("total") or 0),
                    "total_hits": int(row.get("total_hits") or 0),
                }
            return {"total": 0, "total_hits": 0}
        except Exception as e:
            self._last_error = str(e)[:200]
            _log.warning(
                "[v2_store] get_stats 失败 project=%s: %s", project, str(e)[:120]
            )
            return {}
        finally:
            if conn:
                with guard("get_stats: conn.close()", log=_log, level=logging.WARNING):
                    conn.close()


# ═══════════════════════════════════════════════════════════════
# 单例（惰性初始化）
# ═══════════════════════════════════════════════════════════════

_default_store: Optional[OpCacheV2Store] = None


def get_v2_store() -> OpCacheV2Store:
    """获取默认 OpCacheV2Store 实例（单例，惰性初始化）。

    通用性：所有调用方统一入口，避免重复初始化。
    """
    global _default_store
    if _default_store is None:
        _default_store = OpCacheV2Store()
    return _default_store
