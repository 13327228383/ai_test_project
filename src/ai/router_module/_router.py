# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
路由模块 (RouterModule)
======================
按任务类型路由 AI 调用到用户指定的 provider 和模型。

核心设计：
1. 每类任务独立配置 provider 和模型，互不兜底
2. 用户指定什么就用什么，成功就成功，失败就失败
3. 任务类型感知：task / verify / embed / vision 四类独立

【2026-06-18 重构】原 469 行单文件按职责拆分：
- task_types.py     — TaskType 枚举 + _MODEL_PRICING + _TASK_MODEL_MAP 常量
- local_ollama.py   — call_local_model + validate_json_output 工具
- _router.py        — RouterModule 主类 + router 单例
- __init__.py       — facade re-export
"""

import time
import logging
import threading
from typing import Any


try:
    from src.core.config_loader import config as _project_config

    _HAS_PROJECT_CONFIG = True
except ImportError:
    _HAS_PROJECT_CONFIG = False

_log = logging.getLogger("router_module")
_log.setLevel(logging.INFO)


# ── 从拆分子模块 import ──
from .task_types import TaskType, _MODEL_PRICING
from .local_ollama import call_local_model, validate_json_output


def _get_cloud_retry_params() -> tuple:
    try:
        from src.core.pipeline_config import pipeline_config

        return (
            int(pipeline_config.ai_cloud_retry_max),
            float(pipeline_config.ai_cloud_retry_base_delay),
            float(pipeline_config.ai_cloud_retry_max_delay),
        )
    except Exception as e:
        _log.warning("[Router] 读取云端重试参数失败，使用默认值: %s", str(e)[:120])
        return (3, 1.5, 10.0)


_CLOUD_RETRY_MAX, _CLOUD_RETRY_BASE_DELAY, _CLOUD_RETRY_MAX_DELAY = (
    _get_cloud_retry_params()
)


def _resolve_local_timeout(local_timeout: float | None) -> float:
    """本地模型超时 SSOT：调用方未指定时回落配置 ai_local_timeout。"""
    if local_timeout is not None:
        return float(local_timeout)
    try:
        from src.core.pipeline_config import _read_config_value as _cfg

        return float(_cfg("ai_local_timeout", 60.0))
    except Exception:
        return 60.0


def _resolve_cloud_timeout(cloud_timeout: float | None) -> float:
    """云端超时 SSOT：调用方未指定时回落 config.ai_timeout（Provider 初始化用同一口径）。"""
    if cloud_timeout is not None:
        return float(cloud_timeout)
    try:
        from src.core.config_loader import config as _cfg

        return float(getattr(_cfg, "ai_timeout", 180) or 180)
    except Exception:
        return 180.0


def _route_timeout_budget(kwargs: dict[str, Any]) -> float:
    """一次 route() 的最长可能耗时上界（秒）—— 本地分支与云端分支取大者。

    why: 仅用于给看门狗的「在飞豁免」设硬上限；精确值由心跳保证，不需要算准重试退避。
    """
    return max(
        _resolve_local_timeout(kwargs.get("local_timeout")),
        _resolve_cloud_timeout(kwargs.get("cloud_timeout")),
    )


class RouterModule:
    """AI 调用路由器：按任务类型路由到用户指定的 provider 和模型。"""

    DEEPSEEK_API_BASE = "https://api.deepseek.com"

    def __init__(self):
        if _HAS_PROJECT_CONFIG:
            self._provider_name = getattr(_project_config, "ai_provider", "deepseek")
            self._api_key = getattr(_project_config, "ai_api_key", "")
            self._cloud_base = self._read_cloud_base()
            self._default_cloud_model = getattr(
                _project_config, "ai_model", _project_config.deepseek_model
            )
            from src.core.constants import OLLAMA_DEFAULT_API_BASE

            self._ollama_api_base = str(
                getattr(_project_config, "ollama_api_base", OLLAMA_DEFAULT_API_BASE)
            )
            self._ollama_model = str(getattr(_project_config, "ollama_model", ""))
            self._ollama_enabled = bool(
                getattr(_project_config, "ollama_enabled", True)
            )
        else:
            self._provider_name = "deepseek"
            self._api_key = ""
            self._cloud_base = self.DEEPSEEK_API_BASE
            self._default_cloud_model = getattr(_project_config, "ai_model", "")
            from src.core.constants import OLLAMA_DEFAULT_API_BASE

            try:
                from src.core.config_loader import config as _cfg

                self._ollama_api_base = str(
                    _cfg.ollama_api_base or OLLAMA_DEFAULT_API_BASE
                )
                self._ollama_model = str(_cfg.ollama_model or "")
                self._ollama_enabled = bool(_cfg.ollama_enabled)
            except Exception as e:
                _log.warning("Ollama配置读取异常: %s", e, exc_info=True)
                self._ollama_api_base = OLLAMA_DEFAULT_API_BASE
                self._ollama_model = ""
                self._ollama_enabled = True
        self._build_provider()

    def _read_cloud_base(self) -> str:
        raw = (
            getattr(_project_config, "ai_base_url", None)
            if _HAS_PROJECT_CONFIG
            else None
        )
        if not raw:
            return self.DEEPSEEK_API_BASE
        raw = str(raw).strip().strip("`").strip()
        raw = raw.replace("deepsees", "deepseek").replace("deepseel", "deepseek")
        if not (raw.startswith("https://") or raw.startswith("http://")):
            raw = "https://api.deepseek.com"
        return raw.rstrip("/")

    def _build_provider(self):
        try:
            from src.ai.ai_providers import get_provider as _get_provider

            # ★ 根因修复：此前不传 timeout，云端调用恒定落 factory 默认 120s，
            #   config.ai_timeout(.env DEEPSEEK_TIMEOUT) 在云端路径被完全忽略，
            #   Step2 大 prompt 冷启动首调必超时。现显式透传 config.ai_timeout。
            _cloud_timeout = _resolve_cloud_timeout(None)
            self._cloud_provider = _get_provider(
                name=self._provider_name,
                api_key=self._api_key,
                base_url=self._cloud_base,
                model=self._default_cloud_model,
                timeout=_cloud_timeout,
                max_retries=0,  # ★ 重试由 router 层统一控制，provider 只负责单次调用
            )
        except Exception as e:
            _log.warning("云端provider初始化异常: %s", e, exc_info=True)
            self._cloud_provider = None
        self._local_enabled_cache: bool | None = None
        self._local_last_check: float = 0.0
        self._local_checked: bool = False
        self._stats = {
            "total_calls": 0,
            "local_success": 0,
            "local_failures": 0,
            "cloud_success": 0,
            "cloud_failures": 0,
            "total_cloud_cost_usd": 0.0,
        }
        self._stats_lock = threading.Lock()

    def _get_model_name(self) -> str:
        return self._default_cloud_model or "unknown"

    @staticmethod
    def _get_provider_for_task(task_type: str) -> str:
        try:
            from src.core.config_loader import config as _cfg
        except Exception as e:
            _log.warning("配置加载异常: %s", e, exc_info=True)
            return "cloud"
        # ★ 系统级兜底：ai.default_provider（config.yaml）。项目级 task_provider/
        #   verify_provider/embed_provider 未配置（如 project_configs 加载失败）时
        #   回落系统默认，避免静默使用云端（云端 key 无效时会泄漏 401）。
        _default = str(getattr(_cfg, "ai_default_provider", "cloud") or "cloud")
        if task_type in ("verification", "verify"):
            return str(getattr(_cfg, "verify_provider", "") or _default)
        if task_type == "embedding":
            return str(getattr(_cfg, "embed_provider", "") or "ollama")
        return str(getattr(_cfg, "task_provider", "") or _default)

    @staticmethod
    def _get_local_model_for_task(task_type: str) -> str:
        try:
            from src.core.config_loader import config as _cfg
        except Exception as e:
            _log.warning("本地模型名获取异常: %s", e, exc_info=True)
            return ""
        # ★ 系统级兜底：ollama.model（config.yaml），未配项目级 *_LOCAL_MODEL 时使用
        _base = str(getattr(_cfg, "ollama_model", "") or "")
        if task_type in ("verification", "verify"):
            return str(getattr(_cfg, "ollama_verify_model", "") or "") or _base
        if task_type == "embedding":
            return str(getattr(_cfg, "ollama_embed_model", "") or "") or _base
        return str(getattr(_cfg, "ollama_task_model", "") or "") or _base

    @property
    def _local_enabled(self) -> bool:
        if self._local_checked:
            return bool(self._local_enabled_cache)
        self._local_enabled_cache = self._detect_local()
        self._local_checked = True
        self._local_last_check = time.time()
        if not self._local_enabled_cache:
            _log.info(
                "本地模型不可用，本会话全部走云端（如需使用请启动 Ollama 后重启服务）"
            )
        return self._local_enabled_cache

    def local_enabled(self) -> bool:
        """★ 2026-09-23 新增公开只读访问器：当前是否路由到本地（单实例）模型。

        供调用方做**自适应并发**（本地单实例模型不支持真并发，扇出过大只会排队雪崩，
        实测单次调用耗时随并发递增 166s→520s）。云端路由不适用此约束。
        """
        try:
            return bool(self._local_enabled)
        except Exception:
            return False

    @property
    def api_key(self) -> str:
        return self._api_key

    @api_key.setter
    def api_key(self, value: str):
        self._api_key = value

    def get_stats(self) -> dict:
        with self._stats_lock:
            return dict(self._stats)

    def _detect_local(self) -> bool:
        if not self._ollama_enabled:
            return False
        with guard("_detect_local: import requests as _r", log=_log):
            import requests as _r

            base = self._ollama_api_base.rstrip("/")
            resp = _r.get(f"{base}/api/ps", timeout=1)
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                if models:
                    gpu_models = [m for m in models if m.get("size_vram", 0) > 0]
                    if gpu_models:
                        _log.info(
                            "检测到 GPU 加速 (loaded=%s, 使用=%s)",
                            gpu_models[0].get("name", "?"),
                            self._ollama_model,
                        )
                    else:
                        _log.info(
                            "检测到本地模型 (CPU, loaded=%s, 使用=%s)",
                            models[0].get("name", "?"),
                            self._ollama_model,
                        )
                    return True
            resp2 = _r.get(f"{base}/api/tags", timeout=1)
            if resp2.status_code == 200:
                tags = resp2.json().get("models", [])
                if tags:
                    _log.info("检测到已安装本地模型 (%d 个)", len(tags))
                    return True
        return False

    def _call_cloud_model(
        self,
        prompt: str,
        system_prompt: str = "",
        model: str = None,
        temperature: float = 0.3,
        max_tokens: int = 8192,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        response_format: dict[str, Any] | None = None,
        seed: int | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if self._cloud_provider is None:
            return {
                "success": False,
                "error": "云端 Provider 未初始化",
                "error_type": "no_provider",
                "source": self._provider_name,
                "_elapsed": 0,
            }
        selected_model = model or self._default_cloud_model
        if self._provider_name == "nvidia" and "/" not in selected_model:
            selected_model = f"deepseek-ai/{selected_model}"
        result = self._cloud_provider.chat(
            system_prompt=system_prompt,
            user_message=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            model=selected_model,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            seed=seed,
            timeout=timeout,
        )
        return result

    def embed(self, texts: list[str], model: str | None = None) -> dict[str, Any]:
        """文本嵌入向量化（云端 embedding API）。

        默认使用 BAAI/bge-large-zh-v1.5（SiliconFlow，1024 维，中文优化）。
        单次最多 32 条文本，自动分批。

        ★ 根因修复(2026-08-27)：embed_provider 默认 ollama（config.yaml），但本方法
          硬编码走云端 → 在无有效云端 key 的环境泄漏 401 且拖慢流水线。现按 provider
          判定：非 cloud（本地模式）且未配置本地嵌入模型时，直接降级返回失败，
          由调用方回退关键词匹配（Jaccard 等），不再触碰云端。
        """
        if self._get_provider_for_task("embedding") != "cloud":
            return {
                "success": False,
                "error": "embed_provider 非 cloud（本地模式），跳过云端嵌入，由调用方降级关键词匹配",
                "data": [],
            }
        if self._cloud_provider is None:
            return {"success": False, "error": "云端 Provider 未初始化", "data": []}
        if not texts:
            return {"success": True, "data": [], "usage": {"prompt_tokens": 0, "total_tokens": 0}}

        # 分批：单次最多 32 条（SiliconFlow 限制 32）
        batch_size = 32
        all_embeddings = []
        total_usage = {"prompt_tokens": 0, "total_tokens": 0}
        effective_model = model or "BAAI/bge-large-zh-v1.5"

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            result = self._cloud_provider.embed(batch, model=effective_model)
            if not result.get("success"):
                return result
            all_embeddings.extend(result.get("data", []))
            usage = result.get("usage", {})
            total_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
            total_usage["total_tokens"] += usage.get("total_tokens", 0)

        return {
            "success": True,
            "data": all_embeddings,
            "usage": total_usage,
            "model": effective_model,
            "source": self._provider_name,
        }

    def select_cloud_model(
        self, task_type: str = "general", prefer_cheap: bool = True
    ) -> str:
        try:
            from src.core.config_loader import config as _cfg

            if task_type == "embedding":
                m = str(getattr(_cfg, "embed_model", "") or "")
                if m:
                    return m
            elif task_type in ("verification", "verify"):
                m = str(getattr(_cfg, "verification_model", "") or "")
                if m:
                    return m
            elif task_type == "vision":
                m = str(getattr(_cfg, "vision_model", "") or "")
                if m:
                    return m
        except Exception as _e:
            _log.debug("[router] 读取 vision_model 配置失败: %s", _e, exc_info=True)
        return self._default_cloud_model

    def _route_impl(
        self,
        prompt: str,
        system_prompt: str = "",
        task_type: str = "general",
        expect_json: bool = False,
        prefer_cheap: bool = True,
        local_timeout: float | None = None,
        cloud_model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 8192,
        force_cloud: bool = False,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        response_format: dict[str, Any] | None = None,
        seed: int | None = None,
        cloud_timeout: float | None = None,
        cloud_retry_max: int | None = None,
    ) -> dict[str, Any]:
        if _HAS_PROJECT_CONFIG:
            new_provider = getattr(_project_config, "ai_provider", "deepseek")
            new_key = getattr(_project_config, "ai_api_key", "")
            new_base = self._read_cloud_base()
            new_model = getattr(
                _project_config,
                "ai_model",
                getattr(_project_config, "deepseek_model", self._default_cloud_model),
            )
            if (
                new_key != self._api_key
                or new_base != self._cloud_base
                or new_provider != self._provider_name
                or new_model != self._default_cloud_model
            ):
                self._provider_name = new_provider
                self._api_key = new_key
                self._cloud_base = new_base
                self._default_cloud_model = new_model
                self._build_provider()

        t_start = time.time()
        route_path = []
        local_timeout = _resolve_local_timeout(local_timeout)
        _log.info("=" * 50)
        _log.info(
            "路由: task=%s expect_json=%s force_cloud=%s base=%s",
            task_type,
            expect_json,
            force_cloud,
            self._cloud_base,
        )

        provider = self._get_provider_for_task(task_type)
        use_local = not force_cloud and provider == "ollama"

        if use_local:
            local_model_name = self._get_local_model_for_task(task_type)
            if not local_model_name:
                _log.error(
                    "✗ 本地模型未配置：task=%s provider=ollama 但 *_LOCAL_MODEL 为空，请在面板配置",
                    task_type,
                )
                return {
                    "success": False,
                    "error": "本地模型未配置：provider=ollama 但未设置 *_LOCAL_MODEL，请在面板配置",
                    "error_type": "config_missing",
                    "source": "local",
                    "_elapsed": time.time() - t_start,
                    "_route_path": route_path,
                }
            _log.info("  本地模型: %s (task=%s)", local_model_name, task_type)
            local_result = call_local_model(
                prompt=prompt,
                system_prompt=system_prompt,
                model=local_model_name,
                api_base=self._ollama_api_base,
                timeout=local_timeout,
                temperature=temperature,
                max_tokens=max_tokens,
                seed=seed,  # ★ F2 全局修复 [未复核-需 GLM/DeepSeek 把关]: 透传 seed（本地 Ollama 路径）
            )
            route_path.append("local")
            with self._stats_lock:
                self._stats["total_calls"] += 1
            if local_result["success"]:
                is_valid, json_err = validate_json_output(
                    local_result["data"], expect_json
                )
                if is_valid:
                    with self._stats_lock:
                        self._stats["local_success"] += 1
                    _log.info(
                        "✓✓ 路由成功: 本地 model=%s (%.1fs)",
                        local_model_name,
                        time.time() - t_start,
                    )
                    return {
                        "success": True,
                        "data": local_result["data"],
                        "model": local_result.get("model", local_model_name),
                        "source": "local",
                        "usage": local_result.get("usage", {}),
                        "_elapsed": local_result.get("_elapsed", 0),
                        "_cost": 0.0,
                        "_route_path": route_path,
                    }
                else:
                    _log.warning("■ 本地 JSON 校验失败: %s", json_err)
                    with self._stats_lock:
                        self._stats["local_failures"] += 1
                    return {
                        "success": False,
                        "error": "本地模型 JSON 校验失败: %s" % json_err,
                        "error_type": "json_invalid",
                        "source": "local",
                        "_elapsed": time.time() - t_start,
                        "_route_path": route_path,
                    }
            else:
                _log.warning(
                    "■ 本地失败 (error_type=%s): %s",
                    local_result.get("error_type"),
                    local_result.get("error"),
                )
                with self._stats_lock:
                    self._stats["local_failures"] += 1
                return {
                    "success": False,
                    "error": "本地模型调用失败: %s" % local_result.get("error", "?"),
                    "error_type": local_result.get("error_type", "local_failed"),
                    "source": "local",
                    "_elapsed": time.time() - t_start,
                    "_route_path": route_path,
                }

        selected_model = cloud_model or self.select_cloud_model(task_type, prefer_cheap)
        if self._provider_name == "nvidia":
            max_tokens = min(max_tokens, 16384)
        if not self._api_key or self._api_key == "local":
            return {
                "success": False,
                "error": "云端模型未配置 API Key（%s）" % self._provider_name,
                "error_type": "no_api_key",
                "_route_path": route_path,
                "_elapsed": time.time() - t_start,
                "_cost": 0.0,
            }

        _log.info(
            "  云端模型: %s (task=%s, provider=%s)",
            selected_model,
            task_type,
            self._provider_name,
        )
        cloud_result = self._call_cloud_model(
            prompt=prompt,
            system_prompt=system_prompt,
            model=selected_model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice=tool_choice,
            seed=seed,
            timeout=cloud_timeout,
        )
        route_path.append(self._provider_name)

        retry_count = 0
        # ★ feature_driven_extract / requirement_analysis / cross_requirement_intent 不重试：
        #   Step2 规则提取已兜底（22+ 条三元组），AI 失败不阻断主流程
        #   cross_requirement_intent 是关联意图增强，失败不影响主流程用例
        # ★ case_modeling 也不重试（v3.0.1 2026-07-10）：
        #   3 次重试 × 75s + 延迟 ≈ 307s 超 5 分钟，违反单次 AI 调用 ≤3 分钟约束
        _NO_RETRY_TASKS = (
            "requirement_analysis",
            "feature_driven_extract",
            "cross_requirement_intent",
            "case_modeling",
        )
        _retry_max = 0 if task_type in _NO_RETRY_TASKS else _CLOUD_RETRY_MAX
        if cloud_retry_max is not None:
            _retry_max = max(0, int(cloud_retry_max))
        while not cloud_result["success"] and retry_count < _retry_max:
            retry_count += 1
            error_msg = str(cloud_result.get("error", ""))[:80]
            non_retryable = any(
                kw in error_msg.lower()
                for kw in ("401", "unauthorized", "400", "invalid")
            )
            if non_retryable:
                _log.debug("[Router] 云端错误不可重试 (%s)，直接返回", error_msg)
                break
            delay = min(_CLOUD_RETRY_BASE_DELAY**retry_count, _CLOUD_RETRY_MAX_DELAY)
            _log.info(
                "[Router] 云端失败 R%d/%d (%s)，%.1fs 后重试...",
                retry_count,
                _retry_max,
                error_msg,
                delay,
            )
            time.sleep(delay)
            cloud_result = self._call_cloud_model(
                prompt=prompt,
                system_prompt=system_prompt,
                model=selected_model,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                tool_choice=tool_choice,
                response_format=response_format,
                seed=seed,
                timeout=cloud_timeout,
            )
            route_path.append("%s-R%d" % (self._provider_name, retry_count))

        if cloud_result["success"]:
            with self._stats_lock:
                self._stats["total_calls"] += 1
                self._stats["cloud_success"] += 1
                self._stats["total_cloud_cost_usd"] += cloud_result.get("_cost", 0)
            _log.info(
                "✓✓ 路由成功: 云端 model=%s cost≈$%.6f",
                selected_model,
                cloud_result.get("_cost", 0),
            )
            return {
                "success": True,
                "data": cloud_result["data"],
                "model": selected_model,
                "source": cloud_result.get("source", self._provider_name),
                "usage": cloud_result.get("usage", {}),
                "_elapsed": cloud_result.get("_elapsed", 0),
                "_cost": cloud_result.get("_cost", 0.0),
                "_route_path": route_path,
            }
        else:
            with self._stats_lock:
                self._stats["total_calls"] += 1
                self._stats["cloud_failures"] += 1
            _log.error("✗✗ 路由失败: 云端失败（重试%d次）", retry_count)
            return {
                "success": False,
                "error": "云端模型调用失败（重试%d次）: %s"
                % (retry_count, cloud_result.get("error", "?")),
                "error_type": "cloud_failed",
                "_route_path": route_path,
                "_elapsed": time.time() - t_start,
                "_cost": 0.0,
            }

    def route(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """所有走 Router 的 AI 调用的唯一收口点。

        why: 执行槽看门狗以「无新日志/执行记录」判停滞，而等模型返回期间两者都不增长，
             正常的慢会被误判为挂死并被强拆执行槽 → 后续步骤与仍在运行的步骤并发。
             这里登记在飞状态，让看门狗把「等模型」判为进展。
             （不走 Router 的直连 Provider 分支由 _call_api 侧同样登记，见 ai_inflight 模块说明）
        """
        from src.ai.ai_inflight import ai_request_inflight
        from src.ai.runtime_ai_gate import runtime_ai_denied

        if runtime_ai_denied():
            # 运行期禁用：由调用方既有降级路径接管（本地语义/DOM 判定），
            # 返回 success=False 而非伪造结论 —— evidence 里会如实写明原因。
            return {
                "success": False,
                "data": None,
                "error": "运行期 AI 已禁用(step4_runtime_ai=False)",
                "error_type": "runtime_ai_disabled",
                "_route_path": "runtime_ai_disabled",
                "_elapsed": 0.0,
                "_cost": 0.0,
            }

        with ai_request_inflight(_route_timeout_budget(kwargs)):
            return self._route_impl(*args, **kwargs)


router = RouterModule()
