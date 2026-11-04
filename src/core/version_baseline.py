# -*- coding: utf-8 -*-
"""版本基线复制。

V2 不应从零开始，也不能直接把 V1 执行状态当作 V2 已执行。
本模块只复制 requirements 版本快照。
测试用例不做物理复制，避免十几个版本后产生十几份重复测试用例；测试用例读取层通过版本继承关系虚拟展开。
不复制 test_cases / case_requirement_mapping / case_meta / executions / coverage_results / coverage_summaries。
"""

import logging

_log = logging.getLogger(__name__)


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except Exception as e:
        _log.warning("配置值转换异常: %s", e, exc_info=True)
        return default


def clone_version_baseline(
    project: str, from_version_id: int, to_version_id: int
) -> dict[str, int]:
    """复制历史版本基线到目标版本。

    复制规则：
    - requirements：复制为 V2 需求，change_type=unchanged，记录 origin_req_id/origin_version_id
    - test_cases：不复制，读取时通过 requirements.origin_* 虚拟继承
    - case_requirement_mapping：不复制，覆盖统计通过虚拟继承计算
    - case_meta：不复制，历史版本测试用例按原版本读取其元数据
    - executions：不复制，V2 执行状态必须重新产生
    """
    project = str(project or "").strip()
    from_version_id = _as_int(from_version_id)
    to_version_id = _as_int(to_version_id)
    if not project or from_version_id <= 0 or to_version_id <= 0:
        return {
            "requirements": 0,
            "test_cases": 0,
            "case_requirement_mapping": 0,
            "case_meta": 0,
        }
    if from_version_id == to_version_id:
        return {
            "requirements": 0,
            "test_cases": 0,
            "case_requirement_mapping": 0,
            "case_meta": 0,
        }

    from src.core.db_manager import get_connection

    c = get_connection()
    try:
        counts = {
            "requirements": 0,
            "test_cases": 0,
            "case_requirement_mapping": 0,
            "case_meta": 0,
        }

        counts["requirements"] = c.execute(
            """
            INSERT IGNORE INTO requirements(
                req_id, project, version_id, stable_req_key, source_anchor,
                content_hash, semantic_hash, change_type, origin_req_id, origin_version_id,
                module, description, is_generated, dependency_req_id, review_status,
                payload_json, created_at, updated_at
            )
            SELECT
                req_id, project, %s,
                stable_req_key, source_anchor, content_hash, semantic_hash,
                'unchanged', req_id, version_id,
                module, description, is_generated, dependency_req_id, review_status,
                payload_json, NOW(), NOW()
            FROM requirements
            WHERE project=%s AND version_id=%s
            """,
            (to_version_id, project, from_version_id),
        ).rowcount

        # 测试用例实体采用 copy-on-read，不在版本切换/Step2 中物理复制。

        c.commit()
        _log.info(
            "[VersionBaseline] 已复制基线 project=%s from=%s to=%s counts=%s",
            project,
            from_version_id,
            to_version_id,
            counts,
        )
        return counts
    except Exception:
        try:
            c.rollback()
        except Exception as _e:
            _log.debug("commit: %s", _e, exc_info=True)
        raise
    finally:
        try:
            c.close()
        except Exception as _e:
            _log.debug("ai + close(): %s", _e, exc_info=True)
