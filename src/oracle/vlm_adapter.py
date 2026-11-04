# -*- coding: utf-8 -*-
"""★ P1-5: VLM Provider 适配层 — 封装 Ollama 本地视觉大模型调用。

职责边界：
  - 仅负责与 VLM Provider（默认 Ollama）通信，不做业务校验逻辑
  - 提供 availability 检查 + generate 调用 + 超时/降级处理
  - 预留 provider 扩展点（通过 _PROVIDERS 注册表）

通用性：
  - 不绑定特定模型名（model 参数可配置，默认 qwen2.5-vl）
  - 不绑定业务术语，prompt 由调用方构造
  - Ollama 不可用时返回 None（降级信号），不抛异常阻断流水线

降级策略（§13.4 Step4 evidence 提取）：
  - Ollama 进程未启动 → check_ollama_available()=False → generate()=None
  - 调用超时 → generate()=None + warning 日志
  - 响应解析失败 → generate()=None + warning 日志
  调用方收到 None 后应走降级路径（返回 confidence=0 的 evidence）
"""
from __future__ import annotations
from src.core.guarded import guard

import base64
import json
import logging
import os
from typing import Any, Optional

_log = logging.getLogger(__name__)

# ── 默认配置（可通过 config.yaml 覆盖）──
_DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
_DEFAULT_VLM_MODEL = "qwen2.5vl:3b"  # ★ 匹配实际已安装的模型名（Ollama 官方 tag）
_DEFAULT_TIMEOUT = 120  # 秒，VLM 推理较慢（3b 模型首次推理需 30-90s）

#: VLM 视觉编码器安全输入上限（像素）。超出会让 Ollama 视觉推理 500（内部 OOM/编码失败），
#: 喂图前统一缩放到最长边 ≤ 此值。qwen2.5vl 在 CPU+GPU 混合下 768 实测 59s 稳定；
#: 1024 会因冷加载+更大图叠加触发 120s 默认超时，故取 768 平衡稳定性与清晰度。
_MAX_IMAGE_EDGE = 768


def _get_config(key: str, default: str = "") -> str:
    """从 config_loader 读取配置（失败返回默认值，不阻断）。"""
    try:
        from src.core.config_loader import config

        # 优先读 oracle.* 配置段
        val = getattr(config, "oracle", None)
        if val is not None:
            # config.oracle 是 namespace，属性访问
            return str(getattr(val, key, default) or default)
        return default
    except Exception:
        return default


def get_ollama_url() -> str:
    """获取 Ollama 服务地址。"""
    return _get_config("ollama_url", _DEFAULT_OLLAMA_URL)


def get_vlm_model() -> str:
    """获取 VLM 模型名。"""
    return _get_config("vlm_model", _DEFAULT_VLM_MODEL)


def get_vlm_timeout() -> int:
    """获取 VLM 调用超时秒数。"""
    try:
        return int(_get_config("vlm_timeout", str(_DEFAULT_TIMEOUT)))
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT


def check_ollama_available() -> bool:
    """检查 Ollama 服务是否可用（轻量探测 /api/tags）。

    通用性：不依赖具体模型是否已拉取，只检查服务进程是否启动。
    返回: True=可用, False=不可用（调用方应降级）
    """
    try:
        import urllib.request
        import urllib.error

        url = get_ollama_url().rstrip("/") + "/api/tags"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                # 进一步检查是否有可用模型
                data = json.loads(resp.read().decode("utf-8"))
                models = data.get("models", []) or []
                if models:
                    return True
                _log.warning("[vlm_adapter] Ollama 可用但无已拉取模型")
                return False
            return False
    except urllib.error.URLError as e:
        _log.info("[vlm_adapter] Ollama 不可达（未启动?）: %s", str(e)[:80])
        return False
    except Exception as e:
        _log.warning("[vlm_adapter] Ollama 探测异常: %s", str(e)[:80])
        return False


def _prepare_image_bytes(raw: bytes) -> Optional[bytes]:
    """图像预处理：超大分辨率/调色板(P)等模式会让 VLM 视觉推理 500。

    统一转 RGB + 等比缩放到最长边 ≤ _MAX_IMAGE_EDGE，作为 base64 前的格式适配。
    PIL 不可用或处理失败时回退原始字节（不阻断，交由 provider 自行判断）。
    """
    if not raw:
        return None
    try:
        from PIL import Image
        import io as _io

        im = Image.open(_io.BytesIO(raw))
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        w, h = im.size
        longest = max(w, h)
        if longest > _MAX_IMAGE_EDGE:
            ratio = _MAX_IMAGE_EDGE / float(longest)
            im = im.resize((max(1, int(w * ratio)), max(1, int(h * ratio))), Image.LANCZOS)
        buf = _io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as _e:
        _log.debug("[vlm_adapter] 图像预处理失败，回退原始字节: %s", str(_e)[:80])
        return raw


def _encode_image(image: Any) -> Optional[str]:
    """将图像编码为 base64 字符串（含分辨率归一化预处理）。

    支持输入:
      - bytes: 原始图像字节
      - str(os path): 读取文件后编码
      - str(base64): 已编码的 base64 字符串（自动检测，跳过预处理）
    返回: base64 字符串 或 None（失败）
    """
    if image is None:
        return None
    # bytes → 预处理后编码
    if isinstance(image, (bytes, bytearray)):
        raw = _prepare_image_bytes(bytes(image)) or bytes(image)
        return base64.b64encode(raw).decode("ascii")
    # str 输入
    if isinstance(image, str):
        # 已是 base64（粗略检测：无路径分隔符且长度>100且非文件路径）→ 无法预处理，直接返回
        if (
            len(image) > 100
            and not os.path.exists(image)
            and not image.startswith("/")
            and "\\" not in image
            and not image.startswith("data:")
        ):
            return image
        # 文件路径 → 读取 + 预处理 + 编码
        if os.path.exists(image):
            try:
                with open(image, "rb") as f:
                    raw = f.read()
                raw = _prepare_image_bytes(raw) or raw
                return base64.b64encode(raw).decode("ascii")
            except Exception as e:
                _log.warning(
                    "[vlm_adapter] 读取图像文件失败 %s: %s", image, str(e)[:80]
                )
                return None
        # data: URI
        if image.startswith("data:image"):
            # 去掉 data:image/png;base64, 前缀
            try:
                return image.split(",", 1)[1]
            except (IndexError, ValueError) as _e:
                _log.debug("[vlm_adapter] data URI 解析失败(§5): %s", _e)
                return None
    _log.warning("[vlm_adapter] 不支持的图像输入类型: %s", type(image).__name__)
    return None


def generate(
    prompt: str,
    image: Any = None,
    model: Optional[str] = None,
    timeout: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    """调用 VLM 生成结果（Ollama /api/chat 接口）。

    参数:
        prompt:  文本提示词（描述期望 + 询问是否匹配）
        image:   图像输入（bytes/path/base64），None=纯文本调用
        model:   模型名（None=用默认 qwen2.5-vl）
        timeout: 超时秒数（None=用默认 60s）

    返回:
        {"text": str, "eval_count": int, "done": bool} 或 None（失败/降级）

    通用性：纯通信层，不解析 prompt 语义，不构造业务结论。
    降级：Ollama 不可用/超时/响应异常 → 返回 None + warning 日志
    """
    if not prompt:
        _log.warning("[vlm_adapter] prompt 为空，跳过 VLM 调用")
        return None

    url = get_ollama_url().rstrip("/") + "/api/chat"
    use_model = model or get_vlm_model()
    use_timeout = timeout or get_vlm_timeout()

    # 构造 Ollama chat 请求体
    payload: dict[str, Any] = {
        "model": use_model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0.1},  # 低温度保证稳定性
    }

    # 附加图像（如果提供）
    if image is not None:
        img_b64 = _encode_image(image)
        if img_b64:
            payload["messages"][0]["images"] = [img_b64]
        else:
            _log.warning("[vlm_adapter] 图像编码失败，降级为纯文本调用")

    try:
        import urllib.request
        import urllib.error

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=use_timeout) as resp:
            if resp.status != 200:
                _log.warning("[vlm_adapter] Ollama 返回 %d", resp.status)
                return None
            data = json.loads(resp.read().decode("utf-8"))
            # Ollama chat 响应: {message: {content, role}, done, eval_count, ...}
            text = ""
            if isinstance(data.get("message"), dict):
                text = data["message"].get("content", "") or ""
            return {
                "text": text,
                "eval_count": data.get("eval_count", 0),
                "done": bool(data.get("done", False)),
            }
    except urllib.error.URLError as e:
        _log.warning("[vlm_adapter] Ollama 调用失败（服务未启动?）: %s", str(e)[:80])
        return None
    except TimeoutError:
        _log.warning("[vlm_adapter] Ollama 调用超时 (%ds)", use_timeout)
        return None
    except Exception as e:
        _log.warning("[vlm_adapter] Ollama 调用异常: %s", str(e)[:100])
        return None


# ═══════════════════════════════════════════════════════════════
# Provider 注册表（预留扩展点，当前仅 Ollama）
# ═══════════════════════════════════════════════════════════════
_PROVIDERS: dict[str, dict[str, Any]] = {
    "ollama": {
        "check": check_ollama_available,
        "generate": generate,
    },
    # 预留：未来可注册 "openai", "azure", "local_vllm" 等
}


def get_provider(name: str = "ollama") -> Optional[dict[str, Any]]:
    """获取指定 provider 的方法集合（预留扩展，当前仅 ollama）。"""
    return _PROVIDERS.get(name)


def list_available_providers() -> list[str]:
    """列出当前可用的 provider（探测后返回）。"""
    available = []
    for name, methods in _PROVIDERS.items():
        with guard("list_available_providers: if methods['check'](): available.app", log=_log):
            if methods["check"]():
                available.append(name)
    return available
