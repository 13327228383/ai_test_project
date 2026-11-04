# -*- coding: utf-8 -*-
"""
Prompt 模板管理 API（2026-07-14 重构：DB 唯一事实源）
=====================================================
- prompt_templates 表是唯一事实源，前端可编辑
- 种子模板（is_seed=1）不可删除，可"恢复默认"（从 prompt_seeds.py 取原值覆盖）
- project_prompt_overrides 已删除，不再支持项目级覆盖
- 同义词覆盖 CRUD 保留不变
"""
import json
from src.core.guarded import guard
import logging

from flask import Blueprint, jsonify, request

from src.core.db_manager import get_connection, release_connection
from src.models.permission import check_admin_or_permission
from src.core.prompt_seeds import PROMPT_SEEDS, get_seed

_log = logging.getLogger(__name__)
overrides_bp = Blueprint("overrides", __name__)


def _check(perm):
    """权限校验。失败返回 (False, (jsonify, code))，成功返回 (True, None)。"""
    ok, err = check_admin_or_permission(perm, project=_current_project())
    if not ok:
        code = 401 if err.startswith("请先登录") else 403
        return False, (jsonify({"ok": False, "error": err}), code)
    return True, None


def _current_project():
    """获取当前项目：session 持久化优先 → 全局 state → 请求参数兜底。"""
    from src.backend.state import _current_project as _state_proj

    try:
        from flask import session as _sess

        proj = _sess.get("project_name") or ""
    except Exception:
        proj = ""
    if not proj:
        proj = _state_proj() or ""
    if not proj:
        proj = (
            request.args.get("project")
            or (request.get_json(silent=True) or {}).get("project")
            or ""
        )
    return (proj or "").strip()


def _refresh_prompt_cache():
    with guard("_refresh_prompt_cache: from src.core.prompt_templates impor", log=_log, level=logging.WARNING):
        from src.core.prompt_templates import prompt_templates

        prompt_templates.reset_cache()


def _refresh_synonym_cache():
    with guard("_refresh_synonym_cache: from src.extensions.quality.requirem", log=_log, level=logging.WARNING):
        from src.extensions.quality.requirement_gate.constants import (
            _GateConstantsMixin,
        )

        _GateConstantsMixin.reload_synonyms_from_db()


# ═══════════════════════════════════════════════════════════════
# Prompt 模板 CRUD（DB 唯一事实源）
# ═══════════════════════════════════════════════════════════════


@overrides_bp.route("/api/admin/prompts", methods=["GET"])
def api_list_global_prompts():
    ok, err = _check("system.settings")
    if not ok:
        return err
    conn = None
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT name, template_text, category, description, is_seed, enabled, updated_at "
            "FROM prompt_templates ORDER BY is_seed DESC, name"
        ).fetchall()
        seed_names = {s["name"] for s in PROMPT_SEEDS}
        items = [
            {
                "name": r["name"],
                "template_text": r["template_text"] or "",
                "category": r.get("category") or "其他",
                "description": r.get("description") or "",
                "is_seed": int(r.get("is_seed") or 0),
                "has_seed_default": r["name"] in seed_names,
                "enabled": int(r.get("enabled") or 0),
                "updated_at": str(r["updated_at"]) if r.get("updated_at") else "",
            }
            for r in (rows or [])
        ]
        return jsonify({"ok": True, "items": items})
    except Exception as e:
        _log.error("[list_global_prompts] %s", e, exc_info=True)
        return jsonify({"ok": False, "error": f"服务器内部错误: {e}"}), 500
    finally:
        release_connection(conn)


@overrides_bp.route("/api/admin/prompts", methods=["POST"])
def api_save_global_prompt():
    ok, err = _check("system.settings")
    if not ok:
        return err
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    template_text = data.get("template_text") or ""
    category = (data.get("category") or "自定义").strip()
    description = (data.get("description") or "").strip()
    enabled = 1 if data.get("enabled", True) else 0
    if not name or len(name) > 128:
        return jsonify({"ok": False, "error": "模板名称无效（1-128 字符）"}), 400
    conn = None
    try:
        conn = get_connection()
        # 检查是否已存在（保留 is_seed 标识，不允许通过保存降级种子模板）
        existing = conn.execute(
            "SELECT is_seed FROM prompt_templates WHERE name=%s", (name,)
        ).fetchone()
        is_seed = int(existing.get("is_seed") or 0) if existing else 0
        conn.execute(
            "INSERT INTO prompt_templates(name, template_text, category, description, is_seed, enabled) "
            "VALUES(%s,%s,%s,%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE template_text=VALUES(template_text), "
            "category=VALUES(category), description=VALUES(description), "
            "is_seed=0, enabled=VALUES(enabled)",
            (name, template_text, category, description, is_seed, enabled),
        )
        conn.commit()
        _refresh_prompt_cache()
        _log.info("[admin] prompt 已保存: name=%s", name)
        return jsonify({"ok": True})
    except Exception as e:
        _log.error("[save_global_prompt] %s", e, exc_info=True)
        return jsonify({"ok": False, "error": f"服务器内部错误: {e}"}), 500
    finally:
        release_connection(conn)


@overrides_bp.route("/api/admin/prompts/<name>", methods=["DELETE"])
def api_delete_global_prompt(name):
    ok, err = _check("system.settings")
    if not ok:
        return err
    conn = None
    try:
        conn = get_connection()
        # 种子模板不可删除
        existing = conn.execute(
            "SELECT is_seed FROM prompt_templates WHERE name=%s", (name,)
        ).fetchone()
        if existing and int(existing.get("is_seed") or 0) == 1:
            return (
                jsonify(
                    {"ok": False, "error": "种子模板不可删除，可使用「恢复默认」重置"}
                ),
                400,
            )
        conn.execute("DELETE FROM prompt_templates WHERE name=%s", (name,))
        conn.commit()
        _refresh_prompt_cache()
        _log.info("[admin] prompt 已删除: name=%s", name)
        return jsonify({"ok": True})
    except Exception as e:
        _log.error("[delete_global_prompt] %s", e, exc_info=True)
        return jsonify({"ok": False, "error": f"服务器内部错误: {e}"}), 500
    finally:
        release_connection(conn)


@overrides_bp.route("/api/admin/prompts/<name>/restore", methods=["POST"])
def api_restore_default_prompt(name):
    """恢复种子模板为默认值（从 prompt_seeds.py 取原值覆盖 DB 当前值）。"""
    ok, err = _check("system.settings")
    if not ok:
        return err
    from src.core.prompt_seeds import get_seed, get_seed_text

    seed = get_seed(name)
    if not seed:
        return (
            jsonify({"ok": False, "error": f"模板 {name} 不是种子模板，无法恢复默认"}),
            400,
        )
    conn = None
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO prompt_templates(name, template_text, category, description, is_seed, enabled) "
            "VALUES(%s,%s,%s,%s,1,1) "
            "ON DUPLICATE KEY UPDATE template_text=VALUES(template_text), "
            "category=VALUES(category), description=VALUES(description), is_seed=1, enabled=1",
            (
                name,
                seed["template_text"],
                seed.get("category", "其他"),
                seed.get("description", ""),
            ),
        )
        conn.commit()
        _refresh_prompt_cache()
        _log.info("[admin] prompt 已恢复默认: name=%s", name)
        return jsonify({"ok": True})
    except Exception as e:
        _log.error("[restore_default_prompt] %s", e, exc_info=True)
        return jsonify({"ok": False, "error": f"服务器内部错误: {e}"}), 500
    finally:
        release_connection(conn)


@overrides_bp.route("/api/admin/prompts/re-seed", methods=["POST"])
def api_reseed_prompts():
    """重新初始化缺失的种子模板（仅插入缺失的种子，不覆盖已有内容）。"""
    ok, err = _check("system.settings")
    if not ok:
        return err
    conn = None
    try:
        conn = get_connection()
        existing = conn.execute("SELECT name FROM prompt_templates").fetchall()
        existing_names = {r["name"] for r in (existing or [])}
        count = 0
        for seed in PROMPT_SEEDS:
            if seed["name"] not in existing_names:
                conn.execute(
                    "INSERT INTO prompt_templates(name, template_text, category, description, is_seed, enabled) "
                    "VALUES(%s,%s,%s,%s,1,1)",
                    (
                        seed["name"],
                        seed["template_text"],
                        seed.get("category", "其他"),
                        seed.get("description", ""),
                    ),
                )
                count += 1
        conn.commit()
        _refresh_prompt_cache()
        _log.info("[admin] 种子模板重新初始化: 恢复 %d 条", count)
        return jsonify({"ok": True, "count": count})
    except Exception as e:
        _log.error("[reseed_prompts] %s", e, exc_info=True)
        return jsonify({"ok": False, "error": f"服务器内部错误: {e}"}), 500
    finally:
        release_connection(conn)


# ═══════════════════════════════════════════════════════════════
# 同义词覆盖（synonym_group 存 JSON list，与 constants.py 读取侧 json.loads 对齐）
# ═══════════════════════════════════════════════════════════════


@overrides_bp.route("/api/project-overrides/synonyms", methods=["GET"])
def api_list_synonym_overrides():
    ok, err = _check("project.config.view")
    if not ok:
        return err
    proj = _current_project()
    if not proj:
        return jsonify({"ok": False, "error": "未选择项目"}), 400
    conn = None
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT core_word, synonym_group, updated_at "
            "FROM project_synonym_overrides WHERE project=%s ORDER BY core_word",
            (proj,),
        ).fetchall()
        items = []
        for r in rows or []:
            sg = r["synonym_group"]
            try:
                parsed = json.loads(sg) if isinstance(sg, str) else sg
            except Exception:
                parsed = []
            items.append(
                {
                    "core_word": r["core_word"],
                    "synonyms": parsed if isinstance(parsed, list) else [],
                    "updated_at": str(r["updated_at"]) if r.get("updated_at") else "",
                }
            )
        return jsonify({"ok": True, "items": items})
    except Exception as e:
        _log.error("[list_synonym_overrides] %s", e, exc_info=True)
        return jsonify({"ok": False, "error": f"服务器内部错误: {e}"}), 500
    finally:
        release_connection(conn)


@overrides_bp.route("/api/project-overrides/synonyms", methods=["POST"])
def api_save_synonym_override():
    ok, err = _check("project.config.edit")
    if not ok:
        return err
    proj = _current_project()
    if not proj:
        return jsonify({"ok": False, "error": "未选择项目"}), 400
    data = request.get_json(silent=True) or {}
    core_word = (data.get("core_word") or "").strip()
    synonyms = data.get("synonyms") or []
    if not core_word or len(core_word) > 128:
        return jsonify({"ok": False, "error": "core_word 无效（1-128 字符）"}), 400
    if not isinstance(synonyms, list):
        return jsonify({"ok": False, "error": "synonyms 必须是数组"}), 400
    seen, syn_list = set(), []
    for s in synonyms:
        s = str(s).strip()
        if s and s not in seen:
            seen.add(s)
            syn_list.append(s)
    group_json = json.dumps(syn_list, ensure_ascii=False)
    conn = None
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO project_synonym_overrides(project, core_word, synonym_group) "
            "VALUES(%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE synonym_group=VALUES(synonym_group)",
            (proj, core_word, group_json),
        )
        conn.commit()
        _refresh_synonym_cache()
        _log.info(
            "[overrides] synonym 覆盖已保存: project=%s core_word=%s", proj, core_word
        )
        return jsonify({"ok": True})
    except Exception as e:
        _log.error("[save_synonym_override] %s", e, exc_info=True)
        return jsonify({"ok": False, "error": f"服务器内部错误: {e}"}), 500
    finally:
        release_connection(conn)


@overrides_bp.route("/api/project-overrides/synonyms/<core_word>", methods=["DELETE"])
def api_delete_synonym_override(core_word):
    ok, err = _check("project.config.edit")
    if not ok:
        return err
    proj = _current_project()
    if not proj:
        return jsonify({"ok": False, "error": "未选择项目"}), 400
    conn = None
    try:
        conn = get_connection()
        conn.execute(
            "DELETE FROM project_synonym_overrides WHERE project=%s AND core_word=%s",
            (proj, core_word),
        )
        conn.commit()
        _refresh_synonym_cache()
        _log.info(
            "[overrides] synonym 覆盖已删除: project=%s core_word=%s", proj, core_word
        )
        return jsonify({"ok": True})
    except Exception as e:
        _log.error("[delete_synonym_override] %s", e, exc_info=True)
        return jsonify({"ok": False, "error": f"服务器内部错误: {e}"}), 500
    finally:
        release_connection(conn)
