# -*- coding: utf-8 -*-

"""

AI Provider 配置 mixin



职责：

- ai_provider / ai_model / verification_model

- ai_api_key / ai_base_url / ai_timeout

- detect_provider 静态方法

- DeepSeek / Ollama 向后兼容属性

"""


import logging
from pathlib import Path

_log = logging.getLogger(__name__)


class _ConfigAiMixin:
    """AI Provider 配置。"""

    @property
    def ai_provider(self) -> str:

        from src.core.config_session import get_session_key, get_session_provider

        sk = get_session_key()

        if sk:

            return get_session_provider()

        raw = self._get("AI_PROVIDER", "auto")

        if raw != "auto":

            return raw

        model = self.ai_model

        return self.detect_provider(model)

    @property
    def ai_model(self) -> str:
        from src.core.config_session import get_current_user

        uid = get_current_user()
        # ★ CLI 模式：无用户登录时 fallback 到 admin 用户
        if not uid:
            uid = "admin"
        if uid:
            try:
                from src.users.manager import get_user_credential

                user_model = get_user_credential(uid, "API_MODEL", project="*")
                if user_model:
                    return user_model
            except Exception as _e:
                _log.debug("model + ai + ai_model: %s", _e, exc_info=True)
        return self._get("AI_MODEL") or self._get("DEEPSEEK_MODEL", "")

    @property
    def verification_model(self) -> str:
        from src.core.config_session import get_current_user

        uid = get_current_user()
        # ★ CLI 模式：无用户登录时 fallback 到 admin 用户
        if not uid:
            uid = "admin"
        if uid:
            try:
                from src.users.manager import get_user_credential

                user_model = get_user_credential(uid, "VERIFICATION_MODEL", project="*")
                if user_model:
                    return user_model
            except Exception as _e:
                _log.debug("model + verification_model + import: %s", _e, exc_info=True)
        return self._get("VERIFICATION_MODEL", self.ai_model)

    @property
    def ai_api_key(self) -> str:

        from src.core.config_session import (
            get_session_key,
            set_session_key,
            get_current_user,
        )

        sk = get_session_key()

        if sk and sk != "local":

            return sk

        uid = get_current_user()

        # ★ CLI 模式：无用户登录时 fallback 到 admin 用户
        if not uid:
            uid = "admin"

        if uid:

            try:

                from src.users.manager import get_user_credential, get_user_api_key

                persist_key = get_user_credential(uid, "API_KEY", project="*")

                if persist_key:

                    set_session_key(persist_key)

                    return get_session_key()

                provider = self.ai_provider

                key = get_user_api_key(uid, provider)

                if key:

                    set_session_key(key)

                    return get_session_key()

            except Exception as _e:

                _log.debug("import + session + session: %s", _e, exc_info=True)

        provider = self.ai_provider

        if provider == "nvidia":

            return self._get("NVIDIA_API_KEY", "")

        if provider == "anthropic":

            return self._get("ANTHROPIC_API_KEY", "") or self._get(
                "ANTHROPIC_AUTH_TOKEN", ""
            )

        if provider == "deepseek":

            return self._get("DEEPSEEK_API_KEY", "")

        if provider == "openai":

            return self._get("OPENAI_API_KEY", "")

        return ""

    @property
    def ai_base_url(self) -> str:

        from src.core.config_session import get_current_user

        provider = self.ai_provider

        uid = get_current_user()

        # ★ CLI 模式：无用户登录时 fallback 到 admin 用户
        if not uid:
            uid = "admin"

        if uid:

            try:

                from src.users.manager import get_user_credential

                user_base = get_user_credential(uid, "API_BASE_URL", project="*")

                if user_base:

                    return str(user_base).strip().strip("`").strip()

            except Exception as _e:

                _log.debug("ai + ai_base_url + import: %s", _e, exc_info=True)

        if provider == "openai":

            return self._get("OPENAI_BASE_URL", "https://api.openai.com")

        elif provider == "anthropic":

            return self._get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")

        elif provider == "nvidia":

            return (
                str(self._get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"))
                .strip()
                .strip("`")
                .strip()
            )

        else:

            return (
                str(self._get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
                .strip()
                .strip("`")
                .strip()
            )

    @property
    def ai_timeout(self) -> int:

        return self._get("DEEPSEEK_TIMEOUT", 60)

    @staticmethod
    def detect_provider(model_name: str) -> str:

        m = model_name.lower()

        if m.startswith("deepseek-ai/"):

            return "nvidia"

        if m.startswith("deepseek"):

            return "deepseek"

        if m.startswith(("gpt-", "o1-", "o3-", "o4-")):

            return "openai"

        if m.startswith("claude"):

            return "anthropic"

        if m.startswith("qwen"):

            return "openai"

        return "deepseek"

    @property
    def deepseek_base_url(self) -> str:

        return self._get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

    @property
    def deepseek_model(self) -> str:

        return self._get("DEEPSEEK_MODEL", self.ai_model)

    @property
    def ollama_api_base(self) -> str:

        return self._get("OLLAMA_API_BASE", "http://localhost:11434")

    @property
    def ollama_model(self) -> str:
        """Ollama 通用模型名 - 单一入口：user_credentials.OLLAMA_MODEL

        已废弃通用入口：请使用类型明确的 ollama_task_model / ollama_verify_model /

        ollama_embed_model / ollama_vision_model。无硬编码默认值。

        """

        from src.core.config_session import get_current_user

        uid = get_current_user()

        if uid:

            try:

                from src.users.manager import get_user_credential

                val = get_user_credential(uid, "OLLAMA_MODEL", project="*")

                if val:

                    return val

            except Exception as _e:

                _log.debug("ollama_model: %s", _e, exc_info=True)

        return self._get("OLLAMA_MODEL", "")

    @property
    def ollama_embed_model(self) -> str:
        """Ollama 嵌入模型 - 单一入口：user_credentials.EMBED_LOCAL_MODEL

        无硬编码默认值，未配置返回空字符串。

        """

        from src.core.config_session import get_current_user

        uid = get_current_user()

        if uid:

            try:

                from src.users.manager import get_user_credential

                val = get_user_credential(uid, "EMBED_LOCAL_MODEL", project="*")

                if val:

                    return val

            except Exception as _e:

                _log.debug("ollama_embed_model: %s", _e, exc_info=True)

        return self._get("EMBED_LOCAL_MODEL", "") or self._get("OLLAMA_EMBED_MODEL", "")

    @property
    def ollama_enabled(self) -> bool:

        return self._get("OLLAMA_ENABLED", True)

    @property
    def ollama_task_model(self) -> str:
        """Ollama 任务模型 - 单一入口：user_credentials.TASK_LOCAL_MODEL

        无硬编码默认值，未配置返回空字符串。

        """

        from src.core.config_session import get_current_user

        uid = get_current_user()

        if uid:

            try:

                from src.users.manager import get_user_credential

                val = get_user_credential(uid, "TASK_LOCAL_MODEL", project="*")

                if val:

                    return val

            except Exception as _e:

                _log.debug("ollama_task_model: %s", _e, exc_info=True)

        return self._get("TASK_LOCAL_MODEL", "") or self._get("OLLAMA_TASK_MODEL", "")

    @property
    def ollama_verify_model(self) -> str:
        """Ollama 校验模型 - 单一入口：user_credentials.VERIFY_LOCAL_MODEL

        无硬编码默认值，未配置返回空字符串。

        """

        from src.core.config_session import get_current_user

        uid = get_current_user()

        if uid:

            try:

                from src.users.manager import get_user_credential

                val = get_user_credential(uid, "VERIFY_LOCAL_MODEL", project="*")

                if val:

                    return val

            except Exception as _e:

                _log.debug("ollama_verify_model: %s", _e, exc_info=True)

        return self._get("VERIFY_LOCAL_MODEL", "") or self._get(
            "OLLAMA_VERIFY_MODEL", ""
        )

    @property
    def ollama_vision_model(self) -> str:
        """Ollama 视觉模型 - 单一入口：user_credentials.VISION_LOCAL_MODEL

        无硬编码默认值，未配置返回空字符串（不启用）。

        """

        from src.core.config_session import get_current_user

        uid = get_current_user()

        if uid:

            try:

                from src.users.manager import get_user_credential

                val = get_user_credential(uid, "VISION_LOCAL_MODEL", project="*")

                if val:

                    return val

            except Exception as _e:

                _log.debug("ollama_vision_model: %s", _e, exc_info=True)

        return self._get("VISION_LOCAL_MODEL", "") or self._get(
            "OLLAMA_VISION_MODEL", ""
        )

    # ★ 每个用途的 provider 选择 ("cloud" | "ollama" | "disk")
    # disk：磁盘 SentenceTransformer 模型（models/sentence-transformers/）
    # 优先读 user_credentials（用户UI配置），其次环境变量，最后默认值

    def _get_user_provider(self, cred_key: str, default: str = "cloud") -> str:
        """从 user_credentials 读取 provider 配置。"""

        try:

            from src.core.config_session import get_current_user

            uid = get_current_user()

            if uid:

                from src.users.manager import get_user_credential

                val = get_user_credential(uid, cred_key, project="*")

                if val and val in ("cloud", "ollama", "disk"):

                    return val

        except Exception as _e:

            _log.debug("_get_user_provider + import + session: %s", _e, exc_info=True)

        return self._get(cred_key, default)

    @property
    def task_provider(self) -> str:
        """任务模型的 provider：cloud 或 ollama，默认 ollama（本地优先）。

        ★ 2026-08-27：默认从 cloud 改为 ollama，与 config.yaml ai.default_provider
          一致（无云端 key 环境避免 401 泄漏；用户可在面板显式切回 cloud）。
        """

        return self._get_user_provider("TASK_PROVIDER", "ollama")

    @property
    def verify_provider(self) -> str:
        """校验模型的 provider：cloud 或 ollama，默认 ollama（本地优先）。"""

        return self._get_user_provider("VERIFY_PROVIDER", "ollama")

    @property
    def embed_provider(self) -> str:
        """嵌入模型的 provider：cloud 或 ollama，默认 ollama（本地免费）。"""

        return self._get_user_provider("EMBED_PROVIDER", "ollama")

    @property
    def vision_provider(self) -> str:
        """视觉模型的 provider：cloud 或 ollama，默认 cloud。"""

        return self._get_user_provider("VISION_PROVIDER", "cloud")

    @property
    def disk_embed_model(self) -> str:
        """磁盘嵌入模型 - 单一入口：user_credentials.DISK_EMBED_MODEL

        磁盘模型位于 models/sentence-transformers/，优先级高于 ollama。

        无硬编码默认值，未配置返回空字符串。

        """

        from src.core.config_session import get_current_user

        uid = get_current_user()

        if uid:

            try:

                from src.users.manager import get_user_credential

                val = get_user_credential(uid, "DISK_EMBED_MODEL", project="*")

                if val:

                    return val

            except Exception as _e:

                _log.debug("disk_embed_model: %s", _e, exc_info=True)

        return self._get("DISK_EMBED_MODEL", "")

    def list_disk_embed_models(self) -> list:
        """扫描 models/sentence-transformers/ 目录，返回可用的磁盘嵌入模型列表。

        返回：[{"name": "all-MiniLM-L6-v2", "path": "...", "size_mb": 123.4}, ...]

        """

        import os

        _cache_dir = str(
            Path(__file__).resolve().parent.parent.parent.parent
            / "models"
            / "sentence-transformers"
        )

        if not Path(_cache_dir).is_dir():
            return []

        models = []
        for entry in os.listdir(_cache_dir):
            if not entry.startswith("models--"):
                continue
            # models--sentence-transformers--all-MiniLM-L6-v2 → all-MiniLM-L6-v2
            parts = entry.split("--")
            if len(parts) < 3:
                continue
            model_name = "--".join(parts[2:])
            org = parts[1]  # sentence-transformers
            full_name = f"{org}/{model_name}"
            model_path = str(Path(_cache_dir) / entry)

            # 计算目录大小
            total_size = 0
            for dirpath, _, filenames in os.walk(model_path):
                for f in filenames:
                    try:
                        total_size += (Path(dirpath) / f).stat().st_size
                    except OSError as _e:
                        _log.debug("[ai_loader] 文件 stat 失败(§5): %s", _e)

            models.append(
                {
                    "name": model_name,
                    "full_name": full_name,
                    "path": model_path,
                    "size_mb": round(total_size / (1024 * 1024), 1),
                }
            )

        return models

    # ═══════════════════════════════════════════════════════════════

    # ★ 通用 Provider URL 集中访问 (config.yaml providers.* 节)

    # 取代散落在 model_registry.py / helpers.py / pricing_fetcher.py 的硬编码

    # ═══════════════════════════════════════════════════════════════

    def provider_base_url(self, provider: str) -> str:
        """统一获取各 Provider 的 base URL (openai/anthropic/nvidia/deepseek)。"""

        if not provider:

            return ""

        return self._get(f"PROVIDERS_{provider.upper()}_BASE_URL", "") or ""

    def provider_models_url(self, provider: str) -> str:
        """获取 Provider 模型列表 URL。"""

        if not provider:

            return ""

        return self._get(f"PROVIDERS_{provider.upper()}_MODELS_URL", "") or ""

    def deepseek_pricing_url(self) -> str:

        return self._get(
            "PROVIDERS_DEEPSEEK_PRICING_URL",
            "https://api-docs.deepseek.com/quick_start/pricing",
        )

    # ═══════════════════════════════════════════════════════════════

    # AI 重试 / 风险分析 / 健康检查默认值

    # ═══════════════════════════════════════════════════════════════

    def ai_cloud_retry_max(self) -> int:

        return self._get("AI_CLOUD_RETRY_MAX", 3) or 3

    def ai_cloud_retry_base_delay(self) -> float:

        return self._get("AI_CLOUD_RETRY_BASE_DELAY", 1.5) or 1.5

    def ai_cloud_retry_max_delay(self) -> float:

        return self._get("AI_CLOUD_RETRY_MAX_DELAY", 10.0) or 10.0

    def ai_local_check_cache_ttl(self) -> int:

        return self._get("AI_LOCAL_CHECK_CACHE_TTL", 5) or 5

    def ai_risk_analyzer_max_tokens(self) -> int:

        return self._get("AI_RISK_ANALYZER_MAX_TOKENS", 4096) or 4096

    def ai_risk_analyzer_minor_max_tokens(self) -> int:

        return self._get("AI_RISK_ANALYZER_MINOR_MAX_TOKENS", 2048) or 2048

    def ai_health_check_default_model(self) -> str:
        """健康检查默认模型 - 复用 ai_model（单一入口，无硬编码模型名）"""

        return self.ai_model or self._get("AI_HEALTH_CHECK_DEFAULT_MODEL", "")
