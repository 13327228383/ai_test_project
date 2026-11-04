# -*- coding: utf-8 -*-
"""
API Key 设置 / 清除 / Ollama 模型列表
"""

from flask import jsonify, request

from src.backend.routes.auth._core import (
    auth_bp,
    _log,
    _validate_local_ollama,
    _validate_key,
)


@auth_bp.route("/api/key", methods=["POST"])
def api_set_key():
    """设置 API Key（支持持久化保存到用户 DB）。自动识别提供商 + 验证有效性。
    ★ v2: 支持 per-task-type provider 选择（cloud/ollama）和本地模型配置。"""
    data = request.get_json(silent=True) or {}
    key = data.get("key", "").strip()
    local = data.get("local", False)
    persist = data.get("persist", True)  # ★ 默认持久化到用户 DB，跨 session 生效
    base_url = (
        data.get("base_url", "").strip().strip("`").strip()
    )  # ★ 自定义 API 端点（token 平台代理等）
    model = data.get("model", "").strip()  # ★ 云端任务模型
    verification_model = data.get("verification_model", "").strip()  # ★ 云端校验模型
    vision_model = data.get("vision_model", "").strip()  # ★ 云端视觉模型
    vision_api_key = data.get(
        "vision_api_key", ""
    ).strip()  # ★ 视觉模型独立 API key（异构 provider）
    vision_base_url = data.get("vision_base_url", "").strip()  # ★ 视觉模型独立 base_url
    embed_model = data.get("embed_model", "").strip()  # ★ 云端嵌入模型
    # ★ v2: per-task-type provider + 本地模型
    task_provider = data.get("task_provider", "").strip()  # "cloud" | "ollama"
    verify_provider = data.get("verify_provider", "").strip()
    embed_provider = data.get(
        "embed_provider", ""
    ).strip()  # "cloud" | "ollama" | "disk"
    vision_provider = data.get("vision_provider", "").strip()
    task_local_model = data.get("task_local_model", "").strip()
    verify_local_model = data.get("verify_local_model", "").strip()
    embed_local_model = data.get("embed_local_model", "").strip()
    vision_local_model = data.get("vision_local_model", "").strip()
    disk_embed_model = data.get("disk_embed_model", "").strip()  # ★ P4: 磁盘嵌入模型名
    from src.core.config_loader import (
        set_session_key,
        _detect_provider_from_key,
        get_current_user,
    )

    # ★ v2: local=true 向后兼容 → 全部设为 ollama
    # ★ P4: embed_provider 可能为 disk（磁盘模型），需保留用户选择
    if local:
        ok, err = _validate_local_ollama()
        if not ok:
            return jsonify({"ok": False, "error": err, "provider": "local"})
        # ★ 不再用 "local" 覆盖 api_key，改为设置每个用途的 provider
        uid = get_current_user()
        if uid and persist:
            from src.users.manager import set_user_credential

            set_user_credential(uid, "TASK_PROVIDER", "ollama", project="*")
            set_user_credential(uid, "VERIFY_PROVIDER", "ollama", project="*")
            # ★ P4: embed 保留用户选择（disk 或 ollama），不强制覆盖
            _embed_prov = (
                embed_provider
                if embed_provider in ("cloud", "ollama", "disk")
                else "ollama"
            )
            set_user_credential(uid, "EMBED_PROVIDER", _embed_prov, project="*")
            set_user_credential(uid, "VISION_PROVIDER", "ollama", project="*")
            # ★ P4: 保存磁盘嵌入模型名（embed_provider=disk 时）
            if disk_embed_model and _embed_prov == "disk":
                set_user_credential(
                    uid, "DISK_EMBED_MODEL", disk_embed_model, project="*"
                )
        # ★ 本地模式仍需 session key 标记（兼容旧 RouterModule 检查）
        set_session_key("local")
        return jsonify(
            {
                "ok": True,
                "provider": "local",
                "masked": "local",
                "persisted": bool(uid and persist),
            }
        )

    if not key:
        return jsonify({"ok": False, "error": "请输入 Key"})
    if "****" in key:
        return jsonify(
            {"ok": False, "error": "当前提交的是脱敏 Key，请粘贴完整原始 Key 后再保存"}
        )
    if len(key) < 10:
        return jsonify({"ok": False, "error": "Key 格式不正确"})
    if len(key) > 1024:
        return jsonify({"ok": False, "error": "Key 过长"})
    if len(base_url) > 512:
        return jsonify({"ok": False, "error": "Base URL 过长"})
    if len(model) > 128:
        return jsonify({"ok": False, "error": "任务模型名称过长"})
    if len(verification_model) > 128:
        return jsonify({"ok": False, "error": "校验模型名称过长"})
    if len(vision_model) > 128:
        return jsonify({"ok": False, "error": "视觉模型名称过长"})
    if len(embed_model) > 128:
        return jsonify({"ok": False, "error": "嵌入模型名称过长"})
    # 防御：Key 必须为纯 ASCII，否则 HTTP Header 编码会报 latin-1 错误
    if not key.isascii():
        return jsonify(
            {
                "ok": False,
                "error": "Key 包含非法字符（仅支持英文、数字和符号），请检查是否多复制了空格或 emoji",
            }
        )
    provider = _detect_provider_from_key(key)
    # ★ 如果用户提供了 base_url，使用它验证 Key；否则使用默认地址
    validation_url = base_url if base_url else ""
    # ★ P0-07: 跳过内网代理验证的白名单走 src.core.constants (中性化、可通过 env 扩展)
    try:
        from src.core.constants import is_ssrf_safe_host

        _skip_validation = validation_url and is_ssrf_safe_host(
            validation_url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
        )
    except Exception:
        _skip_validation = any(h in validation_url for h in ("localhost", "127.0.0.1"))
    if not _skip_validation:
        ok, err = _validate_key(key, provider, base_url=validation_url)
        if not ok:
            return jsonify({"ok": False, "error": err, "provider": provider})
    else:
        ok = True
    info = set_session_key(key)
    uid = get_current_user()
    if uid:
        from src.users.manager import log_action, set_user_credential

        log_action(
            uid,
            "set-key",
            scope_type="system",
            detail=f"provider={info['provider']} base={base_url or 'default'} model={model or 'default'}",
        )
        # ★ 持久化到用户 DB（跨 session 生效）
        if persist:
            set_user_credential(uid, "API_KEY", key, project="*")
            if base_url:
                set_user_credential(uid, "API_BASE_URL", base_url, project="*")
            if model:
                set_user_credential(uid, "API_MODEL", model, project="*")
            if verification_model:
                set_user_credential(
                    uid, "VERIFICATION_MODEL", verification_model, project="*"
                )
            if vision_model:
                set_user_credential(uid, "VISION_MODEL", vision_model, project="*")
            # ★ Fix: 视觉模型独立 API key 持久化
            if vision_api_key:
                set_user_credential(uid, "VISION_API_KEY", vision_api_key, project="*")
            if vision_base_url:
                set_user_credential(
                    uid, "VISION_BASE_URL", vision_base_url, project="*"
                )
            if embed_model:
                set_user_credential(uid, "EMBED_MODEL", embed_model, project="*")
            # ★ v2: per-task-type provider（embed 支持 disk）
            if task_provider in ("cloud", "ollama"):
                set_user_credential(uid, "TASK_PROVIDER", task_provider, project="*")
            if verify_provider in ("cloud", "ollama"):
                set_user_credential(
                    uid, "VERIFY_PROVIDER", verify_provider, project="*"
                )
            if embed_provider in ("cloud", "ollama", "disk"):
                set_user_credential(uid, "EMBED_PROVIDER", embed_provider, project="*")
            if vision_provider in ("cloud", "ollama"):
                set_user_credential(
                    uid, "VISION_PROVIDER", vision_provider, project="*"
                )
            # ★ v2: per-task-type local model
            if task_local_model:
                set_user_credential(
                    uid, "TASK_LOCAL_MODEL", task_local_model, project="*"
                )
            if verify_local_model:
                set_user_credential(
                    uid, "VERIFY_LOCAL_MODEL", verify_local_model, project="*"
                )
            if embed_local_model:
                set_user_credential(
                    uid, "EMBED_LOCAL_MODEL", embed_local_model, project="*"
                )
            if vision_local_model:
                set_user_credential(
                    uid, "VISION_LOCAL_MODEL", vision_local_model, project="*"
                )
            # ★ P4: 磁盘嵌入模型名（独立保存，embed_provider=disk 时使用）
            if disk_embed_model:
                set_user_credential(
                    uid, "DISK_EMBED_MODEL", disk_embed_model, project="*"
                )
    return jsonify(
        {
            "ok": True,
            "provider": info["provider"],
            "key": info["key"],
            "persisted": bool(uid and persist),
        }
    )


@auth_bp.route("/api/key", methods=["DELETE"])
def api_clear_key():
    from src.core.config_loader import clear_session_key

    clear_session_key()
    return jsonify({"ok": True})


# ═══════════════════════════════════════════════════════════════
# Ollama 模型列表
# ═══════════════════════════════════════════════════════════════

_OLLAMA_MODEL_CATEGORIES = {
    "embed": ("embed", "bge-", "mxbai-", "nomic-"),
    "vision": ("vl", "llava", "bakllava", "vision"),
    "chat": (),  # 兜底
}


def _categorize_ollama_model(name: str) -> str:
    """根据模型名称自动分类。"""
    lower = name.lower()
    if any(kw in lower for kw in _OLLAMA_MODEL_CATEGORIES["embed"]):
        return "embed"
    if any(kw in lower for kw in _OLLAMA_MODEL_CATEGORIES["vision"]):
        return "vision"
    return "chat"


# ★ Ollama 模型列表 TTL 缓存：/api/tags 本地响应约 2s，模型列表低频变化，
#   每次请求同步拉取会让配置页每次打开都卡顿。缓存 30s，首冷后续命中。
_ollama_tags_cache = {"ts": 0.0, "models": None, "error": None}
_OLLAMA_TAGS_CACHE_TTL = 30.0


def _get_ollama_models_cached(base: str):
    """拉取 Ollama /api/tags，带 TTL 缓存，返回 (models, error)。"""
    import time as _time

    now = _time.time()
    cache = _ollama_tags_cache
    if cache["models"] is not None and (now - cache["ts"]) < _OLLAMA_TAGS_CACHE_TTL:
        return cache["models"], cache["error"]

    models: list = []
    error = None
    try:
        import requests as _r

        resp = _r.get(f"{base}/api/tags", timeout=3)
        if resp.status_code == 200:
            tags = resp.json().get("models", [])
            for m in tags:
                name = m.get("name", "")
                size_bytes = m.get("size", 0)
                models.append(
                    {
                        "name": name,
                        "size_mb": (
                            round(size_bytes / (1024 * 1024), 1) if size_bytes else 0
                        ),
                        "category": _categorize_ollama_model(name),
                    }
                )
        else:
            error = f"Ollama 返回 {resp.status_code}"
    except Exception as e:
        error = f"Ollama 不可用: {str(e)[:80]}"

    cache["models"] = models
    cache["error"] = error
    cache["ts"] = now
    return models, error


@auth_bp.route("/api/ollama-models")
def api_ollama_models():
    """获取 Ollama 已安装模型列表 + 当前 per-task-type 配置。"""
    from src.core.config_loader import config as _cfg

    base = str(getattr(_cfg, "ollama_api_base", "http://localhost:11434")).rstrip("/")
    models, error = _get_ollama_models_cached(base)

    # 当前 per-task-type 配置
    def _read_local_model(cfg_attr, cred_key):
        """优先读 user_credentials 中的本地模型名，其次读 config env。"""
        try:
            from src.core.config_session import get_current_user

            uid = get_current_user()
            if uid:
                from src.users.manager import get_user_credential

                m = get_user_credential(uid, cred_key, project="*")
                if m:
                    return m
        except Exception as _e:
            _log.debug("read + model + _read_local_model: %s", _e, exc_info=True)
        return str(getattr(_cfg, cfg_attr, ""))

    current = {
        "task": {
            "provider": str(getattr(_cfg, "task_provider", "cloud")),
            "model": str(getattr(_cfg, "ai_model", "")),
        },
        "verify": {
            "provider": str(getattr(_cfg, "verify_provider", "cloud")),
            "model": str(getattr(_cfg, "verification_model", "")),
        },
        "embed": {
            "provider": str(getattr(_cfg, "embed_provider", "ollama")),
            "model": str(getattr(_cfg, "ollama_embed_model", "")),
            # ★ P4: 磁盘嵌入模型（embed_provider=disk 时使用）
            "disk_model": str(getattr(_cfg, "disk_embed_model", "") or ""),
        },
        "vision": {
            "provider": str(getattr(_cfg, "vision_provider", "cloud")),
            "model": str(getattr(_cfg, "vision_model", "")),
        },
    }
    # ★ 本地模型名覆盖
    for key, attr, cred_key in [
        ("task", "ollama_task_model", "TASK_LOCAL_MODEL"),
        ("verify", "ollama_verify_model", "VERIFY_LOCAL_MODEL"),
        ("embed", "ollama_embed_model", "EMBED_LOCAL_MODEL"),
        ("vision", "ollama_vision_model", "VISION_LOCAL_MODEL"),
    ]:
        local = _read_local_model(attr, cred_key)
        if local:
            current[key]["local_model"] = local

    return jsonify(
        {
            "ok": True,
            "models": models,
            "current": current,
            "error": error,
        }
    )


@auth_bp.route("/api/disk-embed-models")
def api_disk_embed_models():
    """获取磁盘嵌入模型列表 + 当前配置。

    扫描 models/sentence-transformers/ 目录，返回可用的 SentenceTransformer 模型。
    """
    from src.core.config_loader import config as _cfg

    try:
        models = _cfg.list_disk_embed_models()
    except Exception as e:
        models = []
        _log.warning("磁盘嵌入模型扫描失败: %s", str(e)[:80])

    current_model = str(getattr(_cfg, "disk_embed_model", "") or "")
    current_provider = str(getattr(_cfg, "embed_provider", "ollama"))

    return jsonify(
        {
            "ok": True,
            "models": models,
            "current": {
                "model": current_model,
                "provider": current_provider,
            },
        }
    )
