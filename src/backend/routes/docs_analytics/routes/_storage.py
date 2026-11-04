# -*- coding: utf-8 -*-
"""
routes/_storage.py — 存储用量统计路由
======================================
- api_docs_storage_stats — 存储用量统计 API

★ 全 MySQL：统计 source_documents / execution_screenshots / visual_baselines /
  run_logs / requirements / test_cases 各表行数与字节数，无磁盘扫描。
"""

import logging
from flask import jsonify

from src.backend.state import _current_project
from src.models.permission import check_admin_or_permission

_log = logging.getLogger(__name__)

# (展示目录名, 表名, 大小列 SQL, 行数列 SQL, 隔离字段)
# 隔离字段: 'version_id' / 'version_label' / 'project'（仅 project）
# 说明：source_documents 用 version_label；visual_baselines 纯 COUNT 且无 (project,version_id) 索引，按 project。
# ★ 性能修复（2026-07-17）：run_logs.message 是 MEDIUMTEXT，visual_baselines.image_data 是 MEDIUMBLOB，
#   SUM(LENGTH()) 会强制 MySQL 读取每行完整数据来计算长度，随数据量线性恶化导致页面卡顿。
#   改为 "0"（不计算字节数），仅保留 COUNT(*) 行数统计。存储大小对日志/基线无关键意义。
# ★ 性能修复（2026-08-30）：execution_screenshots / run_logs 已有 version_id + (project,version_id) 索引，
#   此前按 project 隔离是「版本盲读」——会扫描该项目全部历史版本的截图/日志并累加，随版本累积越扫越多，
#   且与文件树（按激活版本）口径不一致。改为按激活版本过滤，锁存至当前版本、减少扫描。
_STORAGE_BUCKETS = [
    ("原始数据", "source_documents", "SUM(char_count)", "COUNT(*)", "version_label"),
    ("测试结果-报告", "generated_reports", "SUM(char_count)", "COUNT(*)", "version_id"),
    (
        "过程产物-需求",
        "requirements",
        "SUM(CHAR_LENGTH(description))",
        "COUNT(*)",
        "version_id",
    ),
    (
        "过程产物-意图",
        "test_cases",
        "SUM(CHAR_LENGTH(title)+CHAR_LENGTH(expected_outcome))",
        "COUNT(*)",
        "version_id",
    ),
    ("测试结果-截图", "execution_screenshots", "SUM(file_size)", "COUNT(*)", "version_id"),
    ("测试结果-日志", "run_logs", "0", "COUNT(*)", "version_id"),
    ("视觉基线", "visual_baselines", "0", "COUNT(*)", "project"),
]


def register(bp):
    """注册存储用量统计路由到 Blueprint。"""

    @bp.route("/api/docs/storage-stats")
    def api_docs_storage_stats():
        """获取项目存储用量统计（MySQL 各表行数+字节数）。"""
        ok, err = check_admin_or_permission("docs.view", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        if not _current_project():
            return jsonify({"ok": False, "error": "未选择项目"}), 400
        proj = _current_project()
        dirs_stats = {}
        total_size = 0
        total_files = 0

        from src.core.db_manager import get_connection, release_connection
        from src.core.versioning import current_version_id, current_version_label

        conn = None
        try:
            conn = get_connection()
            vid = current_version_id(proj)
            vlabel = current_version_label(proj)
            for label, table, size_sql, count_sql, isolate_by in _STORAGE_BUCKETS:
                try:
                    if isolate_by == "version_label":
                        where_sql = "WHERE project=%s AND version_label=%s"
                        params = (proj, vlabel)
                    elif isolate_by == "version_id":
                        where_sql = "WHERE project=%s AND version_id=%s"
                        params = (proj, vid)
                    else:
                        where_sql = "WHERE project=%s"
                        params = (proj,)
                    row = conn.execute(
                        "SELECT COALESCE(%s,0) AS sz, COALESCE(%s,0) AS cnt FROM %s %s"
                        % (size_sql, count_sql, table, where_sql),
                        params,
                    ).fetchone()
                except Exception as e:
                    _log.debug("[docs/storage-stats] 表 %s 统计失败: %s", table, e)
                    dirs_stats[label] = {"size": 0, "files": 0, "exists": False}
                    continue
                sz = int(row["sz"]) if row and row.get("sz") else 0
                cnt = int(row["cnt"]) if row and row.get("cnt") else 0
                total_size += sz
                total_files += cnt
                dirs_stats[label] = {
                    "size": sz,
                    "size_mb": round(sz / 1048576, 2),
                    "files": cnt,
                    "exists": cnt > 0,
                }

            # 版本统计（只查数量，不查完整记录）
            # ★ source_documents 用 version_label 隔离，不是 version_id
            version_count = 0
            try:
                row = conn.execute(
                    "SELECT COUNT(DISTINCT version_label) AS cnt FROM source_documents WHERE project=%s",
                    (proj,),
                ).fetchone()
                if row and row.get("cnt"):
                    version_count = int(row["cnt"])
            except Exception as e:
                _log.warning("[docs/storage-stats] 版本统计失败: %s", e)

        except Exception as e:
            _log.warning("[docs/storage-stats] 统计失败: %s", e)
        finally:
            release_connection(conn)

        return jsonify(
            {
                "project": proj,
                "total_size_mb": round(total_size / 1048576, 2),
                "total_files": total_files,
                "version_count": version_count,
                "directories": dirs_stats,
            }
        )
