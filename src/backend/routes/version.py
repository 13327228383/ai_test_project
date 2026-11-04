# -*- coding: utf-8 -*-
"""
项目版本管理路由 Blueprint
===========================
版本 CRUD + 激活切换 + 版本需求快照查询。
"""

from flask import Blueprint, jsonify, request
from src.core.guarded import guard
import logging

_log = logging.getLogger(__name__)
from src.backend.state import _current_project
from src.users.manager import (
    get_project_versions,
    create_project_version,
    activate_project_version,
    update_project_version,
    archive_project_version,
    get_active_version,
    log_action,
)
from src.users.project_auth import (
    delete_project_version,
    purge_project_version,
    restore_project_version,
)
from src.models.permission import has_project_permission
from src.backend.pydantic_schemas import validate_body, VersionCreateSchema

version_bp = Blueprint("version", __name__)


def _get_project():
    """获取当前项目名，未选择时返回错误。"""
    proj = request.args.get("project") or _current_project()
    if not proj:
        return None, (jsonify({"ok": False, "error": "未选择项目"}), 400)
    return proj, None


# ── 版本列表 ──
@version_bp.route("/api/project/versions")
def api_list_versions():
    from src.core.config_loader import get_current_user

    proj, err = _get_project()
    if err:
        return err
    # ★ 修复: 添加鉴权，防止未认证信息泄露
    uid = get_current_user() or ""
    if not uid:
        return jsonify({"ok": False, "error": "请先登录"}), 401
    if not has_project_permission(uid, proj, "project.config.view"):
        return jsonify({"ok": False, "error": "无权查看版本列表"}), 403
    versions = get_project_versions(proj)
    active = get_active_version(proj)
    return jsonify(
        {
            "ok": True,
            "project": proj,
            "versions": versions,
            "active_id": active["id"] if active else None,
            "active_label": active["version_label"] if active else "",
        }
    )


# ── 活跃版本 ──
@version_bp.route("/api/project/versions/active")
def api_get_active_version():
    from src.core.config_loader import get_current_user

    proj, err = _get_project()
    if err:
        return err
    # ★ 修复: 添加鉴权
    uid = get_current_user() or ""
    if not uid:
        return jsonify({"ok": False, "error": "请先登录"}), 401
    if not has_project_permission(uid, proj, "project.config.view"):
        return jsonify({"ok": False, "error": "无权查看版本信息"}), 403
    active = get_active_version(proj)
    if not active:
        return jsonify({"ok": False, "error": "没有活跃版本"}), 404
    return jsonify(active)


# ── 创建版本 ──
@version_bp.route("/api/project/versions", methods=["POST"])
def api_create_version():
    from src.core.config_loader import get_current_user

    uid = get_current_user() or ""
    proj, err = _get_project()
    if err:
        return err
    if not uid:
        return jsonify({"ok": False, "error": "请先登录"}), 401
    # 权限：系统级 project.create 或 项目内 admin
    if not has_project_permission(uid, proj, "project.version.create"):
        return (
            jsonify({"ok": False, "error": "无权创建版本（需要项目管理员权限）"}),
            403,
        )

    data, schema_err = validate_body(VersionCreateSchema)
    if schema_err:
        return schema_err
    label = (data.get("version_name") or data.get("version_label") or "").strip()
    if not label:
        return jsonify({"ok": False, "error": "版本标签不能为空"}), 400
    desc = data.get("description", "") or ""
    # ★ 安全：新版本只能创建为 draft，要通过 activate 端点才能激活
    status = "draft"

    vid = create_project_version(proj, label, desc, uid, status)
    if vid is None:
        return jsonify({"ok": False, "error": "版本创建失败，可能已存在同名版本"}), 409

    log_action(
        uid,
        "version-create",
        scope_type="project",
        scope_id=proj,
        detail=f"version={label} id={vid}",
    )
    return jsonify({"ok": True, "version_id": vid, "version_label": label})


# ── 激活版本 ──
@version_bp.route("/api/project/versions/<int:version_id>/activate", methods=["POST"])
def api_activate_version(version_id):
    from src.core.config_loader import get_current_user

    uid = get_current_user() or ""
    proj, err = _get_project()
    if err:
        return err
    if not uid:
        return jsonify({"ok": False, "error": "请先登录"}), 401
    if not has_project_permission(uid, proj, "project.version.edit"):
        return (
            jsonify({"ok": False, "error": "无权切换版本（需要项目管理员权限）"}),
            403,
        )

    ok = activate_project_version(proj, version_id)
    if not ok:
        return jsonify({"ok": False, "error": "激活失败，版本不存在"}), 404

    log_action(
        uid,
        "version-activate",
        scope_type="project",
        scope_id=proj,
        detail=f"version_id={version_id}",
    )
    # ★ 根因修复：激活后必须把新 active 版本同步到线程本地 + Flask session。
    #   _current_version() 存于 thread-local，若只更新 DB 状态不写请求级上下文，
    #   RAG 持久化/_blue_red/scheduler 等消费方会继续走空值 fallback 或旧版本残留。
    with guard("api_activate_version: _active = get_active_version(proj)", log=_log, level=logging.WARNING):
        _active = get_active_version(proj)
        _label = (
            str(_active.get("version_label") or "") if _active else ""
        )
        if _label:
            from src.backend.state import _set_current_version

            _set_current_version(_label)
    # 重载配置以更新活跃版本
    with guard("api_activate_version: from src.core.config_loader import c", log=_log, level=logging.WARNING):
        from src.core.config_loader import config as cfg

        cfg.reload(proj)
    return jsonify({"ok": True})


# ── 编辑版本 ──
@version_bp.route("/api/project/versions/<int:version_id>", methods=["PUT"])
def api_update_version(version_id):
    from src.core.config_loader import get_current_user

    uid = get_current_user() or ""
    proj, err = _get_project()
    if err:
        return err
    if not uid:
        return jsonify({"ok": False, "error": "请先登录"}), 401
    if not has_project_permission(uid, proj, "project.version.edit"):
        return (
            jsonify({"ok": False, "error": "无权编辑版本（需要项目管理员权限）"}),
            403,
        )

    raw_data = request.get_json(silent=True) or {}
    data, schema_err = validate_body(VersionCreateSchema)
    if schema_err:
        return schema_err
    fields = {}
    activated = False
    archived = False
    label = (data.get("version_name") or data.get("version_label") or "").strip()
    if label:
        fields["version_label"] = label
    if data.get("description") is not None and data.get("description", "").strip():
        fields["description"] = data["description"].strip()
    status = raw_data.get("status", "").strip()
    # ★ 修复: 状态变更必须走状态机函数，禁止直接写入 status 字段
    if status == "active":
        ok_act = activate_project_version(proj, version_id)
        if not ok_act:
            return jsonify({"ok": False, "error": "激活失败，版本可能不存在"}), 404
        activated = True
    elif status == "archived":
        ok_arch = archive_project_version(version_id, project=proj)
        if not ok_arch:
            return jsonify({"ok": False, "error": "归档失败，版本不存在或状态不允许"}), 404
        archived = True
    elif status == "draft":
        # draft 状态只能从 deleted 恢复，走 restore 函数
        ok_rest = restore_project_version(proj, version_id)
        if not ok_rest:
            return jsonify({"ok": False, "error": "恢复失败，版本不存在或状态不允许"}), 404

    # 只更新非 status 字段（label, description）
    ok = True
    if fields:
        ok = update_project_version(version_id, project=proj, **fields)

    if not fields and not activated and not archived and status != "draft":
        return jsonify({"ok": False, "error": "无有效字段"}), 400

    if not ok:
        # ★ 修复: 字段更新失败不影响已完成的状态变更
        _log.warning("[version/update] 状态变更已成功但字段更新失败: id=%s", version_id)

    log_action(
        uid,
        "version-edit",
        scope_type="project",
        scope_id=proj,
        detail=f"version_id={version_id} fields={list(fields.keys())}",
    )
    return jsonify({"ok": True})


# ── 归档版本 ──
@version_bp.route("/api/project/versions/<int:version_id>/archive", methods=["POST"])
def api_archive_version(version_id):
    from src.core.config_loader import get_current_user

    uid = get_current_user() or ""
    proj, err = _get_project()
    if err:
        return err
    if not uid:
        return jsonify({"ok": False, "error": "请先登录"}), 401
    if not has_project_permission(uid, proj, "project.version.archive"):
        return (
            jsonify({"ok": False, "error": "无权归档版本（需要项目管理员权限）"}),
            403,
        )

    ok = archive_project_version(version_id, project=proj)
    if not ok:
        return jsonify({"ok": False, "error": "归档失败，版本不存在"}), 404

    log_action(
        uid,
        "version-archive",
        scope_type="project",
        scope_id=proj,
        detail=f"version_id={version_id}",
    )
    return jsonify({"ok": True})


# ★ D-2-1: 软删除版本
@version_bp.route("/api/project/versions/<int:version_id>/delete", methods=["POST"])
def api_delete_version(version_id):
    from src.core.config_loader import get_current_user

    uid = get_current_user() or ""
    proj, err = _get_project()
    if err:
        return err
    if not uid:
        return jsonify({"ok": False, "error": "请先登录"}), 401
    if not has_project_permission(uid, proj, "project.version.delete"):
        return (
            jsonify({"ok": False, "error": "无权删除版本（需要项目管理员权限）"}),
            403,
        )

    ok = delete_project_version(proj, version_id)
    if not ok:
        return jsonify({"ok": False, "error": "删除失败，版本不存在或为活跃版本"}), 400

    log_action(
        uid,
        "version-delete",
        scope_type="project",
        scope_id=proj,
        detail=f"version_id={version_id}",
    )
    return jsonify({"ok": True})


# ★ D-2-2: 恢复软删除版本
@version_bp.route("/api/project/versions/<int:version_id>/restore", methods=["POST"])
def api_restore_version(version_id):
    from src.core.config_loader import get_current_user

    uid = get_current_user() or ""
    proj, err = _get_project()
    if err:
        return err
    if not uid:
        return jsonify({"ok": False, "error": "请先登录"}), 401
    # ★ 修复: restore 与 delete 使用同一权限粒度
    if not has_project_permission(uid, proj, "project.version.delete"):
        return (
            jsonify({"ok": False, "error": "无权恢复版本（需要版本删除权限）"}),
            403,
        )

    ok = restore_project_version(proj, version_id)
    if not ok:
        return (
            jsonify({"ok": False, "error": "恢复失败，版本不存在或非已删除状态"}),
            400,
        )

    log_action(
        uid,
        "version-restore",
        scope_type="project",
        scope_id=proj,
        detail=f"version_id={version_id}",
    )
    return jsonify({"ok": True})


# ★ v6.0(2026-08-12) 新增: 物理删除版本（永久删除已软删除的版本）
@version_bp.route("/api/project/versions/<int:version_id>/purge", methods=["DELETE"])
def api_purge_version(version_id):
    from src.core.config_loader import get_current_user

    uid = get_current_user() or ""
    proj, err = _get_project()
    if err:
        return err
    if not uid:
        return jsonify({"ok": False, "error": "请先登录"}), 401
    if not has_project_permission(uid, proj, "project.version.delete"):
        return (
            jsonify({"ok": False, "error": "无权物理删除版本（需要版本删除权限）"}),
            403,
        )

    ok = purge_project_version(proj, version_id)
    if not ok:
        return (
            jsonify({"ok": False, "error": "物理删除失败，版本不存在或非已删除状态"}),
            400,
        )

    log_action(
        uid,
        "version-purge",
        scope_type="project",
        scope_id=proj,
        detail=f"version_id={version_id}",
    )
    return jsonify({"ok": True})


# ★ v6.0(2026-08-12) 新增: 版本对比（比较两个版本的需求/用例差异）
@version_bp.route("/api/project/versions/compare")
def api_compare_versions():
    from src.core.config_loader import get_current_user

    uid = get_current_user() or ""
    proj, err = _get_project()
    if err:
        return err
    if not uid:
        return jsonify({"ok": False, "error": "请先登录"}), 401
    if not has_project_permission(uid, proj, "project.config.view"):
        return jsonify({"ok": False, "error": "无权查看版本对比"}), 403

    v1_id = request.args.get("v1", type=int)
    v2_id = request.args.get("v2", type=int)
    if not v1_id or not v2_id:
        return jsonify({"ok": False, "error": "需要 v1 和 v2 参数"}), 400

    from src.users._user_db_core import _conn

    conn = None
    try:
        conn = _conn()
        # 获取两个版本的信息
        versions = {}
        for vid in (v1_id, v2_id):
            row = conn.execute(
                "SELECT id, version_label, description, status, created_at, activated_at "
                "FROM project_versions WHERE id=%s AND project=%s",
                (vid, proj),
            ).fetchone()
            if not row:
                return jsonify({"ok": False, "error": f"版本 {vid} 不存在"}), 404
            versions[vid] = dict(row)

        # 对比需求
        # （requirements 表无 title/priority/status 字段；其有效描述维度为
        #   module=功能模块、description=需求描述、change_type=变更类型、review_status=审核状态）
        v1_reqs = conn.execute(
            "SELECT req_id, module AS title, description, change_type, review_status "
            "FROM requirements WHERE version_id=%s",
            (v1_id,),
        ).fetchall()
        v2_reqs = conn.execute(
            "SELECT req_id, module AS title, description, change_type, review_status "
            "FROM requirements WHERE version_id=%s",
            (v2_id,),
        ).fetchall()

        v1_set = {r["req_id"]: dict(r) for r in v1_reqs} if v1_reqs else {}
        v2_set = {r["req_id"]: dict(r) for r in v2_reqs} if v2_reqs else {}

        req_added = {k: v2_set[k] for k in v2_set if k not in v1_set}
        req_removed = {k: v1_set[k] for k in v1_set if k not in v2_set}
        req_common = {k: v2_set[k] for k in v2_set if k in v1_set}
        req_changed = {}
        for k in req_common:
            ra, rb = v1_set[k], v2_set[k]
            if ra.get("description") != rb.get("description") or ra.get("title") != rb.get("title") or ra.get("change_type") != rb.get("change_type"):
                req_changed[k] = {"v1": ra, "v2": rb}

        # 对比用例（test_cases 表无 priority 字段，风险等级用 risk_level）
        v1_cases = conn.execute(
            "SELECT case_id, title, risk_level, case_type FROM test_cases WHERE version_id=%s",
            (v1_id,),
        ).fetchall()
        v2_cases = conn.execute(
            "SELECT case_id, title, risk_level, case_type FROM test_cases WHERE version_id=%s",
            (v2_id,),
        ).fetchall()

        v1_cset = {c["case_id"]: dict(c) for c in v1_cases} if v1_cases else {}
        v2_cset = {c["case_id"]: dict(c) for c in v2_cases} if v2_cases else {}

        case_added = {k: v2_cset[k] for k in v2_cset if k not in v1_cset}
        case_removed = {k: v1_cset[k] for k in v1_cset if k not in v2_cset}
        case_common = {k: v2_cset[k] for k in v2_cset if k in v1_cset}

        return jsonify({
            "ok": True,
            "v1": versions[v1_id],
            "v2": versions[v2_id],
            "requirements": {
                "v1_count": len(v1_set),
                "v2_count": len(v2_set),
                "added": list(req_added.values()),
                "removed": list(req_removed.values()),
                "changed": list(req_changed.values()),
                "unchanged": len(req_common) - len(req_changed),
            },
            "test_cases": {
                "v1_count": len(v1_cset),
                "v2_count": len(v2_cset),
                "added": list(case_added.values()),
                "removed": list(case_removed.values()),
                "unchanged": len(case_common),
            },
        })
    except Exception as e:
        _log.error("[version/compare] 异常: %s", e)
        return jsonify({"ok": False, "error": f"对比失败: {str(e)[:100]}"}), 500
    finally:
        if conn:
            with guard("api_compare_versions: conn.close()", log=_log):
                conn.close()


# ── 版本需求快照查询 ──
@version_bp.route("/api/project/versions/<int:version_id>/requirements")
def api_version_requirements(version_id):
    from src.core.config_loader import get_current_user

    uid = get_current_user() or ""
    proj, err = _get_project()
    if err:
        return err
    if not uid:
        return jsonify({"ok": False, "error": "请先登录"}), 401
    # ★ 补权限校验：原接口无任何鉴权，仅靠 project 过滤兜底；显式校验 project.config.view
    if not has_project_permission(uid, proj, "project.config.view"):
        return jsonify({"ok": False, "error": "无权查看版本需求"}), 403
    from src.core.db_manager import get_connection

    c = get_connection()
    try:
        rows = c.execute(
            """
            SELECT req_id, module, description, dependency_req_id, review_status,
                   is_generated, stable_req_key, source_anchor, content_hash,
                   semantic_hash, change_type, origin_req_id, origin_version_id
            FROM requirements
            WHERE project=%s AND version_id=%s
            ORDER BY req_id
            """,
            (proj, version_id),
        ).fetchall()
        reqs = [dict(r) for r in rows or []]
    finally:
        try:
            c.close()
        except Exception as _e:
            _log.debug("fetchall + close(): %s", _e, exc_info=True)
    return jsonify({"project": proj, "version_id": version_id, "requirements": reqs})
