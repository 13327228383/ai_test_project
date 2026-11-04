# -*- coding: utf-8 -*-
"""本地 Ollama 调用 + JSON 校验工具 — 从 router_module.py 拆出，零业务逻辑变更。"""
# ★ 根因修复(2026-08-31)：注解 `seed: int | None` 为 PEP 604 (Py3.10+)，本工程运行于
#   Python 3.9。缺此 future import 时该模块导入即抛 TypeError，导致 router 无法加载，
#   AI 任务被降级到云端路径。全仓其它使用 `X | None` 的模块均带此 import，此处补齐以
#   保持 3.9 兼容（复原本地 Ollama 路径可用性）。
from __future__ import annotations

import json
import time
import logging
from typing import Any

import requests


_log = logging.getLogger("router_module")


# ★ 兜底常量：仅 URL 兜底，模型名必须由调用方显式传入
# （router._get_local_model_for_task 已做未配置阻断，此处不再兜底默认模型名）
from src.core.constants import OLLAMA_DEFAULT_API_BASE


def call_local_model(
    prompt,
    system_prompt="",
    model="",
    api_base=OLLAMA_DEFAULT_API_BASE,
    timeout=15.0,
    temperature=0.3,
    max_tokens=4096,
    seed: int | None = None,
) -> dict[str, Any]:
    if not model:
        return {
            "success": False,
            "error": "本地模型名为空：调用方未传入 model 参数",
            "error_type": "config_missing",
            "source": "local",
            "_elapsed": 0.0,
        }
    url = "%s/api/generate" % api_base.rstrip("/")
    t0 = time.time()
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }
    # ★ F2 全局修复 [未复核-需 GLM/DeepSeek 把关]（2026-08-29）：
    #   本地 Ollama 路径 seed 透传 —— 此前 router.route 的 seed 参数在此被静默丢弃，
    #   导致即使调用方传 seed=42，本地 qwen2.5:7b 输出仍不确定（Ollama API 支持
    #   options.seed）。仅显式传 seed 时写入，保持默认 seed=None 调用方随机性不变。
    if seed is not None:
        payload["options"]["seed"] = int(seed)
    if system_prompt:
        payload["system"] = system_prompt
    _log.info("→ 本地: POST %s (model=%s, timeout=%.1fs)", url, model, timeout)
    try:
        resp = requests.post(
            url,
            json=payload,
            timeout=timeout,
            headers={"Content-Type": "application/json"},
        )
        elapsed = time.time() - t0
        if resp.status_code != 200:
            _log.error(
                "✗ 本地 HTTP %d (%.1fs): %s", resp.status_code, elapsed, resp.text[:200]
            )
            return {
                "success": False,
                "error": "Ollama HTTP %d: %s" % (resp.status_code, resp.text[:200]),
                "error_type": "http_error",
                "source": "local",
                "_elapsed": elapsed,
            }
        data = resp.json()
        usage = {
            "prompt_tokens": data.get("prompt_eval_count", 0),
            "completion_tokens": data.get("eval_count", 0),
        }
        _log.info(
            "✓ 本地成功 (%.1fs) %d chars tokens=[in:%d out:%d]",
            elapsed,
            len(data.get("response", "")),
            usage["prompt_tokens"],
            usage["completion_tokens"],
        )
        return {
            "success": True,
            "data": data.get("response", ""),
            "model": model,
            "source": "local",
            "usage": usage,
            "_elapsed": elapsed,
        }
    except requests.exceptions.Timeout:
        return {
            "success": False,
            "error": "本地 Ollama 超时（%.1fs）" % (time.time() - t0),
            "error_type": "timeout",
            "source": "local",
            "_elapsed": time.time() - t0,
        }
    except requests.exceptions.ConnectionError as e:
        return {
            "success": False,
            "error": "本地 Ollama 连接失败: %s" % e,
            "error_type": "connection_error",
            "source": "local",
            "_elapsed": time.time() - t0,
        }
    except Exception as e:
        return {
            "success": False,
            "error": "本地异常: %s" % e,
            "error_type": "unknown_error",
            "source": "local",
            "_elapsed": time.time() - t0,
        }


def validate_json_output(raw_text: str, expect_json: bool) -> tuple[bool, str]:
    if not expect_json:
        return True, ""
    # ★ 收敛到唯一真源 parse_json_response（markdown 围栏剥离 + 尾随文本 + 截断补全，
    #   能力是原先严格 json.loads 的严格超集）。原实现会在路由层丢弃"多需求长输出被
    #   截断缺 `]` 或带尾随文本"的合法结果，导致 AI 已生成的需求（如字段校验/错误码/
    #   健康契约）在进入下游解析前丢失，随后回退进劣质规则条。
    from src.ai.json_parser import parse_json_response
    result = parse_json_response(raw_text)
    if result.get("success"):
        return True, ""
    return False, "JSON 校验失败，前 200 字: %s" % (str(raw_text)[:200])
