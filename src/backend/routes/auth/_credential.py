# -*- coding: utf-8 -*-
"""
用户级凭证管理
"""

from flask import jsonify, request

from src.backend.routes.auth._core import auth_bp, _log
from src.backend.state import _current_project

# ★ 凭证白名单（模块级常量，便于测试验证）
_ALLOWED_CRED_KEYS = {
    "API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "CONFLUENCE_USERNAME",
    "CONFLUENCE_PAT",
    "CONFLUENCE_PASSWORD",
    "FIGMA_KEY",
    "FIGMA_USERNAME",
    "FIGMA_PASSWORD",
    "API_DOCS_USERNAME",
    "API_DOCS_PASSWORD",
    "DB_USER",
    "DB_PASSWORD",
    "TARGET_USERNAME",
    "TARGET_PASSWORD",
    # ★ 问题3：模型配置字段独立保存（provider radio onchange 自动保存）
    "API_BASE_URL",
    "API_MODEL",
    "VERIFICATION_MODEL",
    "VISION_MODEL",
    "EMBED_MODEL",
    # ★ Fix: 视觉模型独立 API key（异构 provider 场景）
    "VISION_API_KEY",
    "VISION_BASE_URL",
    "TASK_PROVIDER",
    "VERIFY_PROVIDER",
    "EMBED_PROVIDER",
    "VISION_PROVIDER",
    "TASK_LOCAL_MODEL",
    "VERIFY_LOCAL_MODEL",
    "EMBED_LOCAL_MODEL",
    "VISION_LOCAL_MODEL",
    # ★ P4：磁盘嵌入模型独立保存（disk 来源选项）
    "DISK_EMBED_MODEL",
    # ★ 云端模型列表数据库缓存（key_hash + 24h 过期，避免切换 provider 时重复调云端 API）
    "CLOUD_MODEL_LIST_CACHE",
}


@auth_bp.route("/api/user-credentials", methods=["GET"])
def api_get_credentials():
    """获取当前登录用户的所有凭证（按项目过滤）。project 参数可选。

    数据库层加密存储，API 直返明文值供配置面板展示。
    ★ 支持 keys 参数按需返回指定凭证，减少不必要的数据传输。
    ★ 返回时附带已缓存的模型列表（cloud_model_list），前端可直接填充 select。
    """
    from src.core.config_loader import get_current_user
    from src.users.manager import get_all_user_credentials, get_user_credentials_by_keys
    import json as _json

    uid = get_current_user()
    if not uid:
        return jsonify({"ok": False, "error": "未登录"}), 401
    project = request.args.get("project") or _current_project() or ""

    # ★ 按需加载：如果传了 keys 参数，只查询指定凭证
    keys_str = request.args.get("keys", "")
    if keys_str:
        keys = [x for k in keys_str.split(",") if (x := k.strip())]
        creds = get_user_credentials_by_keys(uid, keys, project=project)
    else:
        creds = get_all_user_credentials(uid, project=project)

    # ★ 解析缓存的模型列表，前端可直接填充 select
    model_list = None
    cache_str = (creds or {}).pop("CLOUD_MODEL_LIST_CACHE", "")
    if cache_str:
        try:
            model_list = _json.loads(cache_str)
        except Exception:
            model_list = None

    # ★ 修复：直接返回明文，前端配置面板需要看到完整值。
    #   数据库层（users/manager.py）依然加密存储，不影响安全性。
    result = {"credentials": creds if isinstance(creds, dict) else {}}
    if model_list:
        result["cloud_model_list"] = model_list
    return jsonify(result)


@auth_bp.route("/api/user-credentials", methods=["POST"])
def api_save_credentials():
    """保存当前登录用户的凭证（合并模式，只更新传入的字段）。按项目隔离。"""
    try:
        from src.core.config_loader import get_current_user

        uid = get_current_user()
        if not uid:
            return jsonify({"ok": False, "error": "未登录"}), 401
        data = request.get_json(silent=True) or {}
        fields = data.get("fields", {})
        if not fields:
            return jsonify({"ok": False, "error": "没有字段需要保存"}), 400
        # ★ 安全修复：凭证 key 名白名单校验（引用模块级常量 _ALLOWED_CRED_KEYS）
        filtered = {k: v for k, v in fields.items() if k in _ALLOWED_CRED_KEYS}
        if len(filtered) < len(fields):
            rejected = set(fields.keys()) - _ALLOWED_CRED_KEYS
            _log.info("[SECURITY] 凭证保存：拒绝未授权的 key %s", rejected)
        if not filtered:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "无有效凭证字段（仅支持白名单字段",
                        "rejected": list(set(fields.keys()) - _ALLOWED_CRED_KEYS),
                    }
                ),
                400,
            )
        project = data.get("project") or _current_project() or ""

        # ★ 权限校验：项目级凭证（project != '*'）需要 project.config.edit 权限
        #   用户级凭证（project='*'，如 API_KEY/API_MODEL）不校验项目权限
        if project and project != "*":
            from src.models.permission import check_admin_or_permission

            ok, err = check_admin_or_permission("project.config.edit", project)
            if not ok:
                return jsonify({"ok": False, "error": err}), 403

        # ★ 修复：敏感字段的空值保护（前端明文直传，对比数据库值）
        #   - 空字符串：用户没输入新密码，跳过
        #   - 值与数据库存值相同：跳过（无需重复加密写入）
        SENSITIVE_KEYS = {
            "API_KEY",
            "DEEPSEEK_API_KEY",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "CONFLUENCE_PAT",
            "CONFLUENCE_PASSWORD",
            "FIGMA_PASSWORD",
            "FIGMA_KEY",
            "API_DOCS_PASSWORD",
            "DB_PASSWORD",
            "TARGET_PASSWORD",
        }
        from src.users.manager import save_user_credentials_batch

        result = save_user_credentials_batch(
            uid, filtered, project=project, sensitive_keys=SENSITIVE_KEYS
        )
        saved = result["saved"]
        skipped_sensitive = result["skipped_sensitive"]

        # ★ 跨项目共享：同步写入 project='*' 全局兜底，切换项目后自动回填
        #    读端已支持 project IN (当前项目, '*') 的 fallback（credentials.py L145）
        #    项目级凭证优先于全局，用户修改后自动覆盖全局
        global_saved = []
        if project and project != "*" and saved:
            try:
                gr = save_user_credentials_batch(
                    uid, filtered, project="*", sensitive_keys=SENSITIVE_KEYS
                )
                global_saved = gr.get("saved", [])
            except Exception as _ge:
                _log.warning("[api_save_credentials] 全局同步失败: %s", _ge)

        if not saved and not skipped_sensitive:
            return (
                jsonify({"ok": False, "error": "凭证保存全部失败，请检查数据库连接"}),
                500,
            )
        return jsonify(
            {
                "ok": True,
                "updated": saved,
                "project": project,
                "synced_global": global_saved,
                "skipped": [
                    k for k in filtered if k not in saved and k not in skipped_sensitive
                ]
                + skipped_sensitive,
                "skipped_sensitive": skipped_sensitive,
            }
        )
    except Exception as e:
        _log.error("[api_save_credentials] 未处理异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "服务器异常: " + str(e)[:200]}), 500


@auth_bp.route("/api/user-credentials", methods=["DELETE"])
def api_delete_credential():
    """删除当前登录用户的某个凭证（按项目隔离）。"""
    from src.core.config_loader import get_current_user
    from src.users.manager import delete_user_credential

    uid = get_current_user()
    if not uid:
        return jsonify({"ok": False, "error": "未登录"}), 401
    data = request.get_json(silent=True) or {}
    key = data.get("key", "")
    if not key:
        return jsonify({"ok": False, "error": "缺少 key"}), 400
    project = data.get("project") or _current_project() or ""
    delete_user_credential(uid, key, project=project)
    return jsonify({"ok": True})


@auth_bp.route("/api/user-credentials/copy", methods=["POST"])
def api_copy_credentials():
    """从源项目复制当前用户的全部凭据到目标项目（仅填充空字段，不覆盖已有值）。"""
    from src.core.config_loader import get_current_user
    from src.users.credentials import copy_user_credentials

    uid = get_current_user()
    if not uid:
        return jsonify({"ok": False, "error": "未登录"}), 401
    data = request.get_json(silent=True) or {}
    source = (data.get("source_project") or "").strip()
    target = (data.get("target_project") or _current_project() or "").strip()
    if not source or not target:
        return (
            jsonify(
                {"ok": False, "error": "source_project 和 target_project 不能为空"}
            ),
            400,
        )
    if source == target:
        return jsonify({"ok": False, "error": "源项目和目标项目不能相同"}), 400
    result = copy_user_credentials(uid, source, target)
    return jsonify(result)


@auth_bp.route("/api/user-credential-projects")
def api_credential_projects():
    """返回当前用户已保存过凭据的项目列表（用于"复制凭据"下拉框，只显示自己的）。"""
    from src.core.config_loader import get_current_user

    uid = get_current_user()
    if not uid:
        return jsonify({"ok": False, "error": "未登录"}), 401
    current = _current_project() or ""
    try:
        from src.users.manager import _conn

        c = _conn()
        rows = c.execute(
            "SELECT DISTINCT project FROM user_credentials_v2 WHERE user_id=%s AND value != '' AND project NOT IN (%s, '*') ORDER BY project",
            (uid, current),
        ).fetchall()
        c.close()
        projects = [r["project"] for r in rows if r["project"]]
        return jsonify({"ok": True, "projects": projects})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@auth_bp.route("/api/can-access")
def api_can_access():
    """检查当前用户是否可访问指定项目。"""
    from src.backend.helpers import _user_can_access_project
    from src.core.config_loader import get_current_user

    uid = get_current_user()
    proj = request.args.get("project", "")
    return jsonify({"ok": uid and proj and _user_can_access_project(uid, proj)})
