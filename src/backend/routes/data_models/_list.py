# -*- coding: utf-8 -*-
"""列表路由：/api/ai-defaults, /api/task-models, /api/verification-models, /api/vision-models

[B3.3 收敛] ``_check_login_and_project()`` 原在本文件内联定义，现已下线，
统一改从唯一真源 ``src.backend.routes._data_helpers`` 导入。
实测本副本与真源均使用 ``state._current_project``，**逻辑逐字等价**，零行为变更。

[已复核-DeepSeek-V3 2026-08-16]
"""

import logging
from src.core.guarded import guard
from flask import jsonify, request

from src.backend.routes.data_models import data_models_bp

_log = logging.getLogger(__name__)


def _register_list_routes():
    from src.backend.helpers import _build_vision_model_entry

    # ★ [B3.3 收敛] 登录+项目权限校验统一走唯一真源 _data_helpers。
    #   原本此处有一份内联同名副本，与真源**逐行等价**（同样取 state._current_project），
    #   故本次去重**不产生任何行为变化**，仅消除三副本各自漂移的风险。
    from src.backend.routes._data_helpers import _check_login_and_project

    def _enrich_with_pricing(model_id: str, selected: bool) -> dict:
        """补充模型定价字段（input/output 美元/百万 token）。
        定价来自 pricing_fetcher（DeepSeek 官网抓取 24h 缓存 + 回退硬编码）。
        ★ 修复断链：前端 updateTaskModelInfo/updateVerifyModelInfo 依赖 input/output 字段，
          之前路由未返回导致 undefined.toFixed() 报错被静默吞掉。"""
        try:
            from src.ai.pricing_fetcher import pricing_fetcher

            price = pricing_fetcher.get_price(model_id)
            return {
                "name": model_id,
                "selected": selected,
                "input": price.get("input", 0),
                "output": price.get("output", 0),
            }
        except Exception:
            _log.warning("获取模型定价失败", exc_info=True)
            return {"name": model_id, "selected": selected, "input": 0, "output": 0}

    @data_models_bp.route("/api/ai-defaults")
    def api_ai_defaults():
        """返回当前项目的 AI 配置默认值（Base URL + Model），供前端面板展示。
        ★ 在 _PUBLIC_PATHS 白名单中，会绕过 app.before_request 的 session→thread-local
          同步；这里必须手动从 Flask session 恢复 uid，否则 config.ai_base_url 等
          property 读不到用户级 API_BASE_URL/API_MODEL，会 fallback 到 DeepSeek 官方域名。
        """
        try:
            from flask import session
            from src.core.config_loader import (
                config,
                get_current_user,
                set_current_user,
            )

            uid = session.get("user_id") or get_current_user()
            if uid:
                set_current_user(uid)
            return jsonify(
                {
                    "ok": True,
                    "base_url": config.ai_base_url,
                    "model": config.ai_model,
                    "provider": config.ai_provider,
                    "verification_model": config.verification_model,
                    "vision_model": config.vision_model,
                    "embed_model": config.embed_model,
                    "embed_provider": config.embed_provider,
                }
            )
        except Exception as e:
            _log.error("[api_ai_defaults] 异常: %s", e, exc_info=True)
            return jsonify({"ok": False, "error": "服务器内部错误"}), 500

    @data_models_bp.route("/api/task-models")
    def api_task_models():
        try:
            ok, err = _check_login_and_project()
            if not ok:
                return jsonify({"ok": False, "error": err}), (
                    401 if err.startswith("请先登录") else 403
                )
            from src.core.config_loader import (
                config,
                get_session_provider,
                is_vision_model,
                is_embedding_model,
            )
            from src.ai.model_registry import fetch_provider_models

            current = config.ai_model
            provider = get_session_provider()

            # ★ 无缓存：每次实时拉取
            models_data = []
            key = config.ai_api_key
            if key:
                models_data = fetch_provider_models(
                    provider, key, base_url=config.ai_base_url
                )

            # 筛选任务模型（非视觉、非嵌入）
            models = []
            seen = set()
            for m in models_data:
                model_id = m.get("id", "")
                if not model_id or model_id in seen:
                    continue
                if is_vision_model(model_id):
                    continue  # 排除视觉模型
                if is_embedding_model(model_id):
                    continue  # 排除嵌入模型
                seen.add(model_id)
                models.append(_enrich_with_pricing(model_id, model_id == current))

            return jsonify(
                {"ok": True, "models": models, "current": current, "provider": provider}
            )
        except Exception as e:
            _log.error("[api_task_models] 异常: %s", e, exc_info=True)
            return jsonify({"ok": False, "error": "服务器内部错误"}), 500

    @data_models_bp.route("/api/verification-models")
    def api_verification_models():
        """返回可用的校验模型列表（同任务模型池，但推荐 Flash）。"""
        try:
            ok, err = _check_login_and_project()
            if not ok:
                return jsonify({"ok": False, "error": err}), (
                    401 if err.startswith("请先登录") else 403
                )
            from src.core.config_loader import (
                config,
                get_session_provider,
                is_vision_model,
                is_embedding_model,
            )
            from src.ai.model_registry import fetch_provider_models

            current = config.verification_model
            provider = get_session_provider()

            # ★ 无缓存：每次实时拉取
            models_data = []
            key = config.ai_api_key
            if key:
                models_data = fetch_provider_models(
                    provider, key, base_url=config.ai_base_url
                )

            models = []
            seen = set()
            for m in models_data:
                model_id = m.get("id", "")
                if not model_id or model_id in seen:
                    continue
                if is_vision_model(model_id):
                    continue
                if is_embedding_model(model_id):
                    continue  # 排除嵌入模型
                seen.add(model_id)
                models.append(_enrich_with_pricing(model_id, model_id == current))

            return jsonify(
                {"ok": True, "models": models, "current": current, "provider": provider}
            )
        except Exception as e:
            _log.error("[api_verification_models] 异常: %s", e, exc_info=True)
            return jsonify({"ok": False, "error": "服务器内部错误"}), 500

    @data_models_bp.route("/api/vision-models")
    def api_vision_models():
        try:
            ok, err = _check_login_and_project()
            if not ok:
                return jsonify({"ok": False, "error": err}), (
                    401 if err.startswith("请先登录") else 403
                )
            from src.core.config_loader import (
                config,
                get_session_provider,
                is_vision_model,
            )
            from src.ai.model_registry import fetch_provider_models

            current = config.vision_model
            enabled = config.vision_model_enabled
            provider = get_session_provider()

            # ★ 无缓存：每次实时拉取
            models_data = []
            key = config.ai_api_key
            if key:
                models_data = fetch_provider_models(
                    provider, key, base_url=config.ai_base_url
                )

            seen = set()
            models = []
            for m in models_data:
                model_id = m.get("id", "")
                if not model_id or model_id in seen:
                    continue
                if not is_vision_model(model_id):
                    continue
                seen.add(model_id)
                # ★ 修复断链：调用 _build_vision_model_entry 补充 input_price/output_price/est_per_click
                #   之前该函数定义了却从未被调用，导致前端视觉面板恒显 $0.000
                try:
                    from src.ai.pricing_fetcher import pricing_fetcher

                    price = pricing_fetcher.get_price(model_id)
                    models.append(
                        _build_vision_model_entry(
                            model_id, price, selected=(model_id == current)
                        )
                    )
                except Exception:
                    models.append(
                        {
                            "name": model_id,
                            "selected": model_id == current,
                            "input_price": 0,
                            "output_price": 0,
                            "est_per_click": 0,
                        }
                    )
            return jsonify(
                {
                    "ok": True,
                    "models": models,
                    "current": current,
                    "enabled": enabled,
                    "provider": provider,
                }
            )
        except Exception as e:
            _log.error("[api_vision_models] 异常: %s", e, exc_info=True)
            return jsonify({"ok": False, "error": "服务器内部错误"}), 500

    @data_models_bp.route("/api/preview-models", methods=["POST"])
    def api_preview_models():
        """用 Key + Base URL 拉取模型列表。
        ★ 数据库缓存层：key+url 不变且 24h 内 → 直接返回数据库列表，不调云端 API。
        前端在用户输入 Key/URL 后调用，失焦时触发。"""
        try:
            data = request.get_json(silent=True) or {}
            key = (data.get("key") or "").strip()
            base_url = (data.get("base_url") or "").strip().strip("`").strip()
            if not key or len(key) < 10:
                return jsonify({"ok": False, "error": "请先输入有效的 API Key"}), 400
            if len(base_url) > 512:
                return jsonify({"ok": False, "error": "Base URL 过长"}), 400
            if not key.isascii():
                return jsonify({"ok": False, "error": "Key 包含非法字符"}), 400

            from src.core.config_loader import (
                _detect_provider_from_key,
                is_vision_model,
                is_embedding_model,
                get_current_user,
            )
            from src.ai.model_registry import fetch_provider_models
            import hashlib, json as _json, time

            provider = _detect_provider_from_key(key)

            # ★ 数据库缓存：key+url 不变且 24h 内直接返回
            key_hash = hashlib.sha256(
                (key + "|" + base_url).encode("utf-8")
            ).hexdigest()[:16]
            uid = get_current_user()

            if uid:
                with guard("api_preview_models: from src.users.manager import get_us", log=_log, level=logging.WARNING):
                    from src.users.manager import get_user_credentials_by_keys

                    cached = get_user_credentials_by_keys(
                        uid, ["CLOUD_MODEL_LIST_CACHE"], project="*"
                    )
                    cache_str = (cached or {}).get("CLOUD_MODEL_LIST_CACHE", "")
                    if cache_str:
                        cache = _json.loads(cache_str)
                        if cache.get("key_hash") == key_hash:
                            fetched_at = cache.get("fetched_at", 0)
                            if (
                                isinstance(fetched_at, (int, float))
                                and (time.time() - fetched_at) < 86400
                            ):
                                return jsonify(
                                    {
                                        "ok": True,
                                        "provider": cache.get("provider", provider),
                                        "task_models": cache.get("task_models", []),
                                        "verification_models": cache.get(
                                            "verification_models", []
                                        ),
                                        "vision_models": cache.get("vision_models", []),
                                        "embed_models": cache.get("embed_models", []),
                                        "cached": True,
                                    }
                                )

            # 拉取云端模型列表
            models_data = fetch_provider_models(provider, key, base_url=base_url)
            if not models_data:
                return (
                    jsonify(
                        {
                            "ok": False,
                            "error": f"无法从 {provider} 拉取模型列表，请检查 Key 和 Base URL 是否正确",
                        }
                    ),
                    502,
                )

            task_models = []
            verify_models = []
            vision_models = []
            embed_models = []
            seen_task = set()
            seen_verify = set()
            seen_vision = set()
            seen_embed = set()
            for m in models_data:
                model_id = m.get("id", "")
                if not model_id:
                    continue
                if is_embedding_model(model_id):
                    if model_id not in seen_embed:
                        seen_embed.add(model_id)
                        embed_models.append({"name": model_id, "selected": False})
                elif is_vision_model(model_id):
                    if model_id not in seen_vision:
                        seen_vision.add(model_id)
                        vision_models.append({"name": model_id, "selected": False})
                else:
                    if model_id not in seen_task:
                        seen_task.add(model_id)
                        entry = _enrich_with_pricing(model_id, False)
                        task_models.append(entry)
                    if model_id not in seen_verify:
                        seen_verify.add(model_id)
                        entry_v = _enrich_with_pricing(model_id, False)
                        verify_models.append(entry_v)

            # ★ 写入数据库缓存
            if uid:
                with guard("api_preview_models: from src.users.manager import save_u", log=_log, level=logging.WARNING):
                    from src.users.manager import save_user_credentials_batch

                    cache_data = _json.dumps(
                        {
                            "key_hash": key_hash,
                            "fetched_at": time.time(),
                            "provider": provider,
                            "task_models": task_models,
                            "verification_models": verify_models,
                            "vision_models": vision_models,
                            "embed_models": embed_models,
                        },
                        ensure_ascii=False,
                    )
                    save_user_credentials_batch(
                        uid, {"CLOUD_MODEL_LIST_CACHE": cache_data}, project="*"
                    )

            return jsonify(
                {
                    "ok": True,
                    "provider": provider,
                    "task_models": task_models,
                    "verification_models": verify_models,
                    "vision_models": vision_models,
                    "embed_models": embed_models,
                }
            )
        except Exception as e:
            _log.error("[api_preview_models] 异常: %s", e, exc_info=True)
            return jsonify({"ok": False, "error": "服务器内部错误"}), 500

    @data_models_bp.route("/api/models/validate", methods=["POST"])
    def api_validate_model():
        """★ P1: 模型 hi 测试 — 在选择/保存模型前验证其有效性。
        接收 {model, provider, key, base_url?} → 发 "say hi" → 返回 {ok, response, error, elapsed}
        """
        try:
            data = request.get_json(silent=True) or {}
            model = (data.get("model") or "").strip()
            key = (data.get("key") or "").strip()
            base_url = (data.get("base_url") or "").strip().strip("`").strip()
            provider = (data.get("provider") or "").strip()

            if not model:
                return jsonify({"ok": False, "error": "缺少 model 参数"}), 400
            if not key or len(key) < 10:
                return jsonify({"ok": False, "error": "请先输入有效的 API Key"}), 400
            if not key.isascii():
                return jsonify({"ok": False, "error": "Key 包含非法字符"}), 400

            if not provider:
                from src.core.config_loader import _detect_provider_from_key

                provider = _detect_provider_from_key(key)

            if not base_url:
                # 优先从 config_loader 读取 provider base_url
                try:
                    from src.core.config_loader import config as _cfg

                    base_url = _cfg.provider_base_url(provider) or ""
                except Exception:
                    _log.warning("读取provider base_url配置失败", exc_info=True)
                    base_url = ""
                if not base_url:
                    if provider == "deepseek":
                        base_url = "https://api.deepseek.com"
                    elif provider == "openai":
                        base_url = "https://api.openai.com"
                    elif provider == "anthropic":
                        base_url = "https://api.anthropic.com"
                    else:
                        base_url = "https://api.deepseek.com"

            # 发 hi 测试（超时 15s，指数退避 3 次）
            import time, requests

            t0 = time.time()
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            }
            # 标准化 URL
            api_url = base_url.rstrip("/")
            if not api_url.endswith("/v1"):
                api_url += "/v1"
            api_url += "/chat/completions"

            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "say hi in one word"}],
                "max_tokens": 20,
                "temperature": 0,
            }

            last_error = None
            for attempt in range(3):
                try:
                    resp = requests.post(
                        api_url, headers=headers, json=payload, timeout=15
                    )
                    elapsed = time.time() - t0
                    if resp.status_code == 200:
                        body = resp.json()
                        msg = body.get("choices", [{}])[0].get("message", {})
                        content = msg.get("content", "") or msg.get(
                            "reasoning_content", ""
                        )
                        return jsonify(
                            {
                                "ok": True,
                                "model": model,
                                "response": (
                                    content.strip()[:100] if content else "(empty)"
                                ),
                                "elapsed": round(elapsed, 2),
                            }
                        )
                    last_error = f"HTTP {resp.status_code}: {(resp.text or '')[:150]}"
                    # 500/503/429 → 重试
                    if resp.status_code < 500 and resp.status_code != 429:
                        break
                except Exception as e:
                    last_error = str(e)[:200]
                if attempt < 2:
                    time.sleep(1.5 ** (attempt + 1))

            elapsed = time.time() - t0
            return (
                jsonify(
                    {
                        "ok": False,
                        "model": model,
                        "error": last_error or "未知错误",
                        "elapsed": round(elapsed, 2),
                    }
                ),
                502,
            )

        except Exception as e:
            _log.error("[api_validate_model] 异常: %s", e, exc_info=True)
            return jsonify({"ok": False, "error": "服务器内部错误"}), 500
