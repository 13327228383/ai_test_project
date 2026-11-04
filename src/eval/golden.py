# -*- coding: utf-8 -*-
"""★ P2-12 GoldenSetStore — 自评估黄金集存储（CRUD）。

定位：
  golden_set 表的读写层。存储引擎自评估的「期望结论」——给定一个场景，
  期望的 verdict（PASS/FAIL）和 confidence。SelfEvalRunner 用此对比引擎
  实际产出的 verdict，计算 accuracy/false_pass_rate/false_fail_rate。

职责边界（§12.4）：
  - 仅做 CRUD，不做评估（评估由 runner.py 负责）
  - 不依赖业务术语，scenario 是通用场景描述
  - 降级：写入失败不阻断，返回 0/False

通用性（§12.4）：
  1. 跨系统：scenario 是通用文本，不绑定业务
  2. 跨行业：任何测试场景都适用
  3. 降级：异常返回空结果，不阻断
  4. 职责边界：仅 CRUD，评估由 runner.py 负责
"""
from __future__ import annotations
from src.core.guarded import guard

import logging
from typing import Any, Optional

_log = logging.getLogger(__name__)

_TABLE = "golden_set"


class GoldenSetStore:
    """golden_set 表的 CRUD 操作层。

    使用方式:
        store = GoldenSetStore()
        # 添加场景
        sid = store.add_scenario("project", "新建配方-正常流程", "PASS", 0.95)
        # 查询场景
        scenarios = store.get_scenarios("project")
        # 删除场景
        store.delete_scenario("project", sid)
    """

    def __init__(self):
        self._last_error: str = ""

    def _get_conn(self):
        """获取数据库连接。"""
        from src.models.base import get_conn

        return get_conn()

    # ═══════════════════════════════════════════════════════════════
    # Create
    # ═══════════════════════════════════════════════════════════════

    def add_scenario(
        self,
        project: str,
        scenario: str,
        expected_verdict: str,
        expected_confidence: float = 0.0,
        notes: str = "",
        case_id: str = "",
    ) -> int:
        """添加一个黄金集场景。

        参数:
            project: 项目名
            scenario: 场景描述（如"新建配方-正常流程"）
            expected_verdict: 期望结论（PASS/FAIL）
            expected_confidence: 期望置信度（0~1）
            notes: 备注
            case_id: 关联用例ID（★P2-12接入: 空=按scenario匹配，非空=按case_id匹配）

        返回: 新增记录 ID（0=失败）
        """
        if not project or not scenario or not expected_verdict:
            self._last_error = "missing project/scenario/expected_verdict"
            return 0
        conn = None
        try:
            conn = self._get_conn()
            cursor = conn.execute(
                "INSERT INTO %s (project, case_id, scenario, expected_verdict, "
                "expected_confidence, notes, created_at) "
                "VALUES (%%s, %%s, %%s, %%s, %%s, %%s, NOW())" % _TABLE,
                (
                    project,
                    case_id[:128],
                    scenario[:512],
                    expected_verdict.upper(),
                    float(expected_confidence),
                    notes or None,
                ),
            )
            conn.commit()
            self._last_error = ""
            return int(cursor.lastrowid or 0)
        except Exception as e:
            self._last_error = str(e)[:200]
            _log.warning("[GoldenSetStore] add_scenario 失败: %s", str(e)[:120])
            return 0
        finally:
            if conn:
                with guard("add_scenario: conn.close()", log=_log):
                    conn.close()

    # ═══════════════════════════════════════════════════════════════
    # Read
    # ═══════════════════════════════════════════════════════════════

    def get_scenarios(self, project: str) -> list[dict[str, Any]]:
        """查询项目的所有黄金集场景。

        返回: [{id, project, case_id, scenario, expected_verdict, expected_confidence, notes, created_at}, ...]
        """
        if not project:
            return []
        conn = None
        try:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT id, project, case_id, scenario, expected_verdict, expected_confidence, notes, created_at "
                "FROM %s WHERE project=%%s ORDER BY created_at ASC" % _TABLE,
                (project,),
            ).fetchall()
            self._last_error = ""
            result = []
            for r in rows:
                result.append(
                    {
                        "id": int(r.get("id") or 0),
                        "project": r.get("project") or "",
                        "case_id": r.get("case_id") or "",
                        "scenario": r.get("scenario") or "",
                        "expected_verdict": (r.get("expected_verdict") or "").upper(),
                        "expected_confidence": float(r.get("expected_confidence") or 0),
                        "notes": r.get("notes") or "",
                        "created_at": str(r.get("created_at") or ""),
                    }
                )
            return result
        except Exception as e:
            self._last_error = str(e)[:200]
            _log.warning("[GoldenSetStore] get_scenarios 失败: %s", str(e)[:120])
            return []
        finally:
            if conn:
                with guard("get_scenarios: conn.close()", log=_log):
                    conn.close()

    def get_scenario_by_id(self, scenario_id: int) -> Optional[dict[str, Any]]:
        """按 ID 查询单个场景。"""
        if not scenario_id:
            return None
        conn = None
        try:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT id, project, case_id, scenario, expected_verdict, expected_confidence, notes, created_at "
                "FROM %s WHERE id=%%s" % _TABLE,
                (int(scenario_id),),
            ).fetchone()
            self._last_error = ""
            if not row:
                return None
            return {
                "id": int(row.get("id") or 0),
                "project": row.get("project") or "",
                "case_id": row.get("case_id") or "",
                "scenario": row.get("scenario") or "",
                "expected_verdict": (row.get("expected_verdict") or "").upper(),
                "expected_confidence": float(row.get("expected_confidence") or 0),
                "notes": row.get("notes") or "",
                "created_at": str(row.get("created_at") or ""),
            }
        except Exception as e:
            self._last_error = str(e)[:200]
            _log.warning("[GoldenSetStore] get_scenario_by_id 失败: %s", str(e)[:120])
            return None
        finally:
            if conn:
                with guard("get_scenario_by_id: conn.close()", log=_log):
                    conn.close()

    def has_case(self, project: str, case_id: str) -> bool:
        """★ P2-12: 检查指定 case_id 是否已存在 golden_set 记录（去重用）。

        通用性：按 (project, case_id) 查询，跨系统/行业/页面通用。
        降级：查询失败返回 False（允许重复注入，安全侧）。
        """
        if not project or not case_id:
            return False
        conn = None
        try:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM %s WHERE project=%%s AND case_id=%%s"
                % _TABLE,
                (project, case_id),
            ).fetchone()
            self._last_error = ""
            return bool(row and int(row.get("cnt") or 0) > 0)
        except Exception as e:
            self._last_error = str(e)[:200]
            _log.warning("[GoldenSetStore] has_case 失败: %s", str(e)[:120])
            return False
        finally:
            if conn:
                with guard("has_case: conn.close()", log=_log):
                    conn.close()

    # ═══════════════════════════════════════════════════════════════
    # Delete
    # ═══════════════════════════════════════════════════════════════

    def delete_scenario(self, project: str, scenario_id: int) -> bool:
        """删除单个场景。

        返回: True=删除成功, False=失败
        """
        if not project or not scenario_id:
            return False
        conn = None
        try:
            conn = self._get_conn()
            conn.execute(
                "DELETE FROM %s WHERE id=%%s AND project=%%s" % _TABLE,
                (int(scenario_id), project),
            )
            conn.commit()
            self._last_error = ""
            return True
        except Exception as e:
            self._last_error = str(e)[:200]
            _log.warning("[GoldenSetStore] delete_scenario 失败: %s", str(e)[:120])
            with guard("delete_scenario: if conn: conn.rollback()", log=_log):
                if conn:
                    conn.rollback()
            return False
        finally:
            if conn:
                with guard("delete_scenario: conn.close()", log=_log):
                    conn.close()

    def delete_all(self, project: str) -> int:
        """删除项目的所有场景。

        返回: 删除的行数
        """
        if not project:
            return 0
        conn = None
        try:
            conn = self._get_conn()
            cursor = conn.execute(
                "DELETE FROM %s WHERE project=%%s" % _TABLE,
                (project,),
            )
            conn.commit()
            self._last_error = ""
            return int(cursor.rowcount or 0)
        except Exception as e:
            self._last_error = str(e)[:200]
            _log.warning("[GoldenSetStore] delete_all 失败: %s", str(e)[:120])
            with guard("delete_all: if conn: conn.rollback()", log=_log):
                if conn:
                    conn.rollback()
            return 0
        finally:
            if conn:
                with guard("delete_all: conn.close()", log=_log):
                    conn.close()

    # ═══════════════════════════════════════════════════════════════
    # Stats
    # ═══════════════════════════════════════════════════════════════

    def count_scenarios(self, project: str) -> int:
        """统计场景数量。"""
        if not project:
            return 0
        conn = None
        try:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM %s WHERE project=%%s" % _TABLE,
                (project,),
            ).fetchone()
            self._last_error = ""
            return int(row.get("cnt") or 0) if row else 0
        except Exception as e:
            self._last_error = str(e)[:200]
            _log.warning("[GoldenSetStore] count_scenarios 失败: %s", str(e)[:120])
            return 0
        finally:
            if conn:
                with guard("count_scenarios: conn.close()", log=_log):
                    conn.close()


# ═══════════════════════════════════════════════════════════════
# 单例（惰性初始化）
# ═══════════════════════════════════════════════════════════════

_default_store: Optional[GoldenSetStore] = None


def get_golden_store() -> GoldenSetStore:
    """获取默认 GoldenSetStore 实例（单例，惰性初始化）。"""
    global _default_store
    if _default_store is None:
        _default_store = GoldenSetStore()
    return _default_store
