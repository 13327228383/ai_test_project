# -*- coding: utf-8 -*-
"""
同义词库管理 API (Synonym Library Blueprint)
=============================================
提供同义词库的 CRUD / 批量导入。
MySQL ``synonym_library`` 表（schema_migration _m002_synonym_library）：
  id INT AUTO_INCREMENT PK
  core_word VARCHAR(128) NOT NULL UNIQUE
  synonym_group TEXT NOT NULL
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
"""

from flask import Blueprint, jsonify, request
import logging

_log = logging.getLogger(__name__)

from src.core.db_manager import get_connection, release_connection
from src.users.auth import require_auth

synonym_bp = Blueprint("synonym", __name__)


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════


def _row_to_dict(row) -> dict:
    """将 MySQL 行转换为字典，datetime 转字符串。"""
    return {
        "id": row["id"],
        "core_word": row["core_word"],
        "synonym_group": row["synonym_group"],
        "created_at": (
            row["created_at"].strftime("%Y-%m-%d %H:%M:%S")
            if row.get("created_at")
            else ""
        ),
    }


# ═══════════════════════════════════════════════════════════════
# 1. GET /api/synonyms — 列出所有同义词
# ═══════════════════════════════════════════════════════════════


@synonym_bp.route("/api/synonyms", methods=["GET"])
@require_auth
def api_list_synonyms():
    """
    列出所有同义词。
    可选查询参数:
      - q: 搜索 core_word（模糊匹配）
    返回: {"success": true, "synonyms": [...]}
    """
    conn = None
    try:
        q = (request.args.get("q") or "").strip()
        conn = get_connection()

        if q:
            rows = conn.execute(
                "SELECT id, core_word, synonym_group, created_at "
                "FROM synonym_library "
                "WHERE core_word LIKE %s "
                "ORDER BY id ASC",
                (f"%{q}%",),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, core_word, synonym_group, created_at "
                "FROM synonym_library "
                "ORDER BY id ASC"
            ).fetchall()

        synonyms = [_row_to_dict(r) for r in (rows or [])]
        return jsonify({"success": True, "synonyms": synonyms})

    except Exception as e:
        _log.error("[api_list_synonyms] 异常: %s", e, exc_info=True)
        return jsonify({"success": False, "error": f"服务器内部错误: {e}"}), 500
    finally:
        release_connection(conn)


# ═══════════════════════════════════════════════════════════════
# 2. POST /api/synonyms — 添加或更新同义词
# ═══════════════════════════════════════════════════════════════


@synonym_bp.route("/api/synonyms", methods=["POST"])
@require_auth
def api_upsert_synonym():
    """
    添加或更新同义词。
    Body: {"core_word": "创建", "synonym_group": "创建,新增,添加,建立"}
    使用 INSERT ... ON DUPLICATE KEY UPDATE。
    返回: {"success": true, "id": 1}
    """
    conn = None
    try:
        body = request.get_json(silent=True)
        if not body:
            return jsonify({"success": False, "error": "请求体为空"}), 400

        core_word = (body.get("core_word") or "").strip()
        synonym_group = (body.get("synonym_group") or "").strip()

        if not core_word:
            return jsonify({"success": False, "error": "core_word 不能为空"}), 400
        if len(core_word) > 128:
            return (
                jsonify({"success": False, "error": "core_word 长度不能超过 128 字符"}),
                400,
            )
        if not synonym_group:
            return jsonify({"success": False, "error": "synonym_group 不能为空"}), 400

        conn = get_connection()
        conn.execute(
            "INSERT INTO synonym_library (core_word, synonym_group) "
            "VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE synonym_group = VALUES(synonym_group)",
            (core_word, synonym_group),
        )
        conn.commit()

        # 获取 id（新增或已有记录）
        row = conn.execute(
            "SELECT id FROM synonym_library WHERE core_word = %s", (core_word,)
        ).fetchone()

        syn_id = row["id"] if row else 0
        _log.info(
            "[api_upsert_synonym] 同义词已保存: core_word=%s, id=%s", core_word, syn_id
        )
        return jsonify({"success": True, "id": syn_id})

    except Exception as e:
        _log.error("[api_upsert_synonym] 异常: %s", e, exc_info=True)
        return jsonify({"success": False, "error": f"服务器内部错误: {e}"}), 500
    finally:
        release_connection(conn)


# ═══════════════════════════════════════════════════════════════
# 3. DELETE /api/synonyms/<int:synonym_id> — 删除同义词
# ═══════════════════════════════════════════════════════════════


@synonym_bp.route("/api/synonyms/<int:synonym_id>", methods=["DELETE"])
@require_auth
def api_delete_synonym(synonym_id):
    """
    根据 id 删除同义词。
    返回: {"success": true}
    """
    conn = None
    try:
        conn = get_connection()

        # 先检查是否存在
        row = conn.execute(
            "SELECT id FROM synonym_library WHERE id = %s", (synonym_id,)
        ).fetchone()

        if not row:
            return (
                jsonify({"success": False, "error": f"未找到同义词: id={synonym_id}"}),
                404,
            )

        conn.execute("DELETE FROM synonym_library WHERE id = %s", (synonym_id,))
        conn.commit()

        _log.info("[api_delete_synonym] 同义词已删除: id=%s", synonym_id)
        return jsonify({"success": True})

    except Exception as e:
        _log.error("[api_delete_synonym] 异常: %s", e, exc_info=True)
        return jsonify({"success": False, "error": f"服务器内部错误: {e}"}), 500
    finally:
        release_connection(conn)


# ═══════════════════════════════════════════════════════════════
# 4. POST /api/synonyms/batch — 批量导入同义词
# ═══════════════════════════════════════════════════════════════


@synonym_bp.route("/api/synonyms/batch", methods=["POST"])
@require_auth
def api_batch_import_synonyms():
    """
    批量导入同义词。
    Body: {"synonyms": [{"core_word": "...", "synonym_group": "..."}, ...]}
    每条使用 INSERT ... ON DUPLICATE KEY UPDATE。
    返回: {"success": true, "imported": 3, "ids": [1, 2, 3]}
    """
    conn = None
    try:
        body = request.get_json(silent=True)
        if not body:
            return jsonify({"success": False, "error": "请求体为空"}), 400

        items = body.get("synonyms")
        if not isinstance(items, list) or len(items) == 0:
            return jsonify({"success": False, "error": "synonyms 必须是非空数组"}), 400

        conn = get_connection()
        imported_ids = []
        errors = []

        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append({"index": idx, "error": "每项必须是 JSON 对象"})
                continue

            core_word = (item.get("core_word") or "").strip()
            synonym_group = (item.get("synonym_group") or "").strip()

            if not core_word:
                errors.append({"index": idx, "error": "core_word 不能为空"})
                continue
            if len(core_word) > 128:
                errors.append(
                    {"index": idx, "error": "core_word 长度不能超过 128 字符"}
                )
                continue
            if not synonym_group:
                errors.append({"index": idx, "error": "synonym_group 不能为空"})
                continue

            try:
                conn.execute(
                    "INSERT INTO synonym_library (core_word, synonym_group) "
                    "VALUES (%s, %s) "
                    "ON DUPLICATE KEY UPDATE synonym_group = VALUES(synonym_group)",
                    (core_word, synonym_group),
                )

                row = conn.execute(
                    "SELECT id FROM synonym_library WHERE core_word = %s", (core_word,)
                ).fetchone()

                if row:
                    imported_ids.append(row["id"])
            except Exception as inner_e:
                errors.append(
                    {"index": idx, "core_word": core_word, "error": str(inner_e)}
                )

        conn.commit()

        _log.info(
            "[api_batch_import_synonyms] 批量导入完成: 成功=%d, 失败=%d",
            len(imported_ids),
            len(errors),
        )

        return jsonify(
            {
                "success": True,
                "imported": len(imported_ids),
                "ids": imported_ids,
                "errors": errors if errors else None,
            }
        )

    except Exception as e:
        _log.error("[api_batch_import_synonyms] 异常: %s", e, exc_info=True)
        return jsonify({"success": False, "error": f"服务器内部错误: {e}"}), 500
    finally:
        release_connection(conn)
