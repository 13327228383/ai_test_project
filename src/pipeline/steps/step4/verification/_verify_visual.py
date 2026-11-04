# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""视觉断言（vision 截图驱动，高度解耦，单一职责）。

场景2：纯视觉检查（柱状图遮挡纵坐标、布局错位等 DOM 拿不到的缺陷）。
双路径：① 云端 AI（VisualVerifier，主路径）② 本地 VLM（VLMOracle，降级路径）。

★ P1-5: 接入本地 VLMOracle（Ollama + Qwen2.5-VL，零成本）。
  当 vision_model_enabled=False 或云端调用失败时 → 降级到本地 VLMOracle。

★ T04 (2026-08-11) 移除「假 PASS 兜底」 [已复核-DeepSeek-V3 2026-08-16]
================================================================
根因：本模块此前在**未做任何实际校验**的 5 个分支上一律返回 ``passed=True``
（无 expectation / 无 page / 截图失败 / VLM 无法判定 / 云端+VLM 双不可用）。
调用方只读 ``passed`` 时，会把「没验」当成「验过且通过」——即 fail-open 假 PASS，
与 §13 fail-closed 原则冲突，也与同仓 ``extensions/perf_baseline/gate.py``
既有的 ``NO_DATA``「绝不静默 PASS」约定不一致。

修复：引入显式三态 ``status`` 终态字段，与 perf_baseline gate 对齐：
  - ``PASS``    真实校验且通过     → passed=True
  - ``FAIL``    真实校验且不通过   → passed=False
  - ``NO_DATA`` **未做实际校验**   → passed=None, skipped=True, confidence=0.0

``passed=None`` 是刻意设计：任何仍在裸读 ``passed`` 的旧调用方会得到 falsy 值
而非 True，即「宁可不报通过」，符合 fail-closed；而适配层
``_db_ui._handle_visual_check`` 会按仓内既有约定（``skipped`` 轨不阻断 AND、
但被 ``_confidence._compute_confidence`` 排除出分母 → 置信度自然下降触发人工审核）
把 NO_DATA 翻译为「不阻断但不计分」，因此不会造成大面积误判 FAIL。

设计原则：
- vision 不可用（config.vision_model_enabled=False / provider 无 chat_vision / 调用失败）
  → 先尝试本地 VLM，再降级为 NO_DATA（不判 fail，也**不判 pass**）。
- 调用失败优雅降级，不抛异常影响主链路。
★ T04.1 LocalBackend 本地见图转换 [已复核-DeepSeek-V3 2026-08-16]：新增 src/oracle/vlm/ 包，
  降级链补齐为 云端 AI → VLMOracle(Ollama) → LocalBackend(本地 DOM+phash) → NO_DATA，
  使无视觉大模型环境下"视觉校验"仍能给出有限但诚实的结论。

★ 本轮增强（HY3，[未复核-需 GLM/DeepSeek 把关]）：
  云端 AI 视觉复核路径改用 ``src.ai.visual_verify.VisualVerifier``（既有类，未重写）。
  新增 fail-soft 门控：
    - 当 VISION_MODEL_ENABLED=true 且 VISION_MODEL / VISION_API_KEY / VISION_BASE_URL
      就绪 → 对截图执行视觉复核；
    - 任何异常 / 模型不可用 / 配置未就绪 → 记 warning 并 SKIP（NO_DATA），绝不让用例失败；
    - 未启用时保持当前 skipped 行为（零行为变化）。
  改动严格控制在 visual_review 子系统内（本文件），不触及核心 dispatch 与其他步骤。
"""

import asyncio
import logging
import os
from typing import Any, Optional

_log = logging.getLogger(__name__)

# ── 终态常量（与 src/extensions/perf_baseline/gate.py 的 NO_DATA 语义对齐）──
STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_NO_DATA = "NO_DATA"

# NO_DATA 的标准化原因码（供报告层聚合统计，不绑定业务术语）
REASON_NO_EXPECTATION = "NO_EXPECTATION"
REASON_NO_PAGE = "NO_PAGE"
REASON_SCREENSHOT_FAILED = "SCREENSHOT_FAILED"
REASON_VLM_UNDECIDED = "VLM_UNDECIDED"
REASON_NO_VISION_PROVIDER = "NO_VISION_PROVIDER"
REASON_LOCAL_UNDECIDED = "LOCAL_UNDECIDED"  # ★ T04.1: LocalBackend 无法判定
REASON_CLOUD_DISABLED = "CLOUD_VISION_DISABLED"  # ★ 本轮: 云端视觉复核门控未启用
REASON_CLOUD_NOT_READY = "CLOUD_VISION_NOT_READY"  # ★ 本轮: 视觉配置未就绪 → SKIP
REASON_CLOUD_UNAVAILABLE = "CLOUD_VISION_UNAVAILABLE"  # ★ 本轮: 模型不可用/异常 → SKIP


def _no_data(reason: str, evidence: str) -> dict[str, Any]:
    """构造 NO_DATA 终态（fail-closed 的统一出口，绝不静默 PASS）。

    Args:
        reason:   标准化原因码（REASON_* 常量之一）。
        evidence: 人类可读说明，进报告与日志。

    Returns:
        终态字典。``passed=None`` 表示「无结论」，不同于 True/False。
    """
    return {
        "passed": None,
        "status": STATUS_NO_DATA,
        "reason": reason,
        "evidence": evidence,
        "skipped": True,
        "confidence": 0.0,
    }


_MDLTIME_CRASH_MARKERS = (
    "encountered an error while running the model",
    "connection forcibly closed",
    "conn_reset",
    "refused",
)


def _local_vlm_crash_hint(exc) -> str:
    """识别本地 VLM(Ollama) 图片推理运行期崩溃，返回诊断提示。

    背景（2026-09-02 实证）：本地 ollama 的视觉模型（如 qwen2.5vl:3b）对**图片**推理
    会崩溃（HTTP 500，模型子进程连接被强制关闭），但**纯文本**正常。若不区分，引擎只
    会静默降级 NO_DATA，难以定位是"模型不可用"还是"模型图片推理崩溃"。此函数仅改进
    诊断，不改变判定语义（仍 fail-closed）。
    通用性：不绑定模型名，按崩溃信号（连接被重置/模型运行错误/拒连）识别。
    """
    _msg = str(exc)
    if any(_m in _msg.lower() for _m in _MDLTIME_CRASH_MARKERS):
        return "（本地视觉模型图片推理运行期异常：连接被重置/模型运行错误；纯文本通道正常）"
    return ""


def _verdict(passed: bool, evidence: str, confidence: float) -> dict[str, Any]:
    """构造真实校验终态（PASS/FAIL）。

    Args:
        passed:     是否通过。
        evidence:   证据文本。
        confidence: 判定置信度 0.0~1.0（来自模型真实输出，不伪造）。

    Returns:
        终态字典，``skipped=False`` 表示确实做了校验。
    """
    return {
        "passed": bool(passed),
        "status": STATUS_PASS if passed else STATUS_FAIL,
        "reason": "",
        "evidence": evidence,
        "skipped": False,
        "confidence": float(confidence),
    }


def _read_vision_gate() -> tuple[bool, Optional[str], Optional[str], Optional[str]]:
    """读取云端视觉复核 fail-soft 门控。

    优先级：环境变量 ``VISION_MODEL_ENABLED`` / ``VISION_MODEL`` / ``VISION_API_KEY``
    / ``VISION_BASE_URL`` 优先；未设置时回退到 ``config``（既有配置）。

    Returns:
        ``(enabled, model, api_key, base_url)``：
        - enabled: 是否启用云端视觉复核（显式 false / 缺省未启用 → False）；
        - model / api_key / base_url: 视觉模型配置，缺失为 None。
    """
    # 1) 启用门控：环境变量优先，否则回退 config.vision_model_enabled（注意其默认 True）
    env_enabled = os.environ.get("VISION_MODEL_ENABLED")
    if env_enabled is not None:
        enabled = env_enabled.strip().lower() in ("1", "true", "yes", "on")
    else:
        try:
            from src.core.config_loader import config

            enabled = bool(getattr(config, "vision_model_enabled", False))
        except Exception:
            enabled = False

    # 2) 配置读取：环境变量优先，否则回退 config
    try:
        from src.core.config_loader import config

        model = os.environ.get("VISION_MODEL") or getattr(config, "vision_model", "") or None
        api_key = os.environ.get("VISION_API_KEY") or getattr(
            config, "vision_api_key", ""
        ) or None
        base_url = os.environ.get("VISION_BASE_URL") or getattr(
            config, "vision_base_url", ""
        ) or None
    except Exception:
        model = os.environ.get("VISION_MODEL") or None
        api_key = os.environ.get("VISION_API_KEY") or None
        base_url = os.environ.get("VISION_BASE_URL") or None

    return enabled, (model or None), (api_key or None), (base_url or None)


async def _cloud_visual_review_via_verifier(
    screenshot_bytes: bytes,
    expectation: str,
) -> Optional[dict[str, Any]]:
    """★ 本轮增强: 复用 VisualVerifier 执行云端 AI 视觉复核（fail-soft 门控）。

    这是核心路径改动，由 HY3 实现，[未复核-需 GLM/DeepSeek 把关]。

    门控与降级：
    - 未启用（VISION_MODEL_ENABLED 非真）→ 返回 None，保持当前 skipped 行为（零变化）；
    - 配置未就绪（model/api_key/base_url 任一缺失）→ warning + 返回 None（SKIP）；
    - provider 不支持 chat_vision / 构造异常 / 调用失败 / success=False
      → warning + 返回 None（SKIP，绝不让用例失败）；
    - 调用成功 → 返回真实 _verdict（PASS/FAIL）。

    Args:
        screenshot_bytes: 页面 PNG 截图 bytes（纯内存，不落盘）。
        expectation:     自然语言视觉预期描述。

    Returns:
        - 真实校验终态（PASS/FAIL），或
        - ``None`` 表示本路径不适用/降级，调用方继续走 VLM 降级链。
    """
    # ── 门控读取（异常即视为未启用，安全降级）──
    try:
        enabled, model, api_key, base_url = _read_vision_gate()
    except Exception as _e:  # noqa: BLE001
        _log.debug("[VisualCheck] 读取视觉门控异常(降级 SKIP): %s", _e, exc_info=True)
        return None

    if not enabled:
        _log.debug("[VisualCheck] VISION_MODEL_ENABLED 未启用，跳过云端视觉复核")
        return None

    if not (model and api_key and base_url):
        _log.warning(
            "[VisualCheck] 云端视觉复核门控已启用，但 VISION_MODEL/VISION_API_KEY/"
            "VISION_BASE_URL 未就绪 → SKIP（不阻断用例）"
        )
        return None

    # ── 构造 provider + VisualVerifier（复用既有类，不重写）──
    try:
        from src.ai.ai_providers import get_provider
        from src.ai.visual_verify import VisualVerifier
        from src.core.config_loader import config

        provider_name = config.detect_provider(model)
        provider = get_provider(
            provider_name, api_key, base_url, model, timeout=30, max_retries=1
        )
    except Exception as _e:  # noqa: BLE001
        _log.warning(
            "[VisualCheck] 视觉 provider 构造失败(降级 SKIP): %s", str(_e)[:80]
        )
        return None

    if not hasattr(provider, "chat_vision"):
        _log.warning(
            "[VisualCheck] Provider(%s) 不支持视觉模型(chat_vision 缺失) → SKIP",
            provider_name,
        )
        return None

    # 回调统一用 no-op（视觉复核不依赖统计/通知；核心路径保持轻量）
    _noop = lambda *a, **k: None  # noqa: E731
    verifier = VisualVerifier(
        provider=provider,
        provider_name=provider_name,
        model=model,
        refresh_config_callback=_noop,
        notify_start_callback=_noop,
        notify_finish_callback=_noop,
        stats_record_callback=_noop,
    )

    # ── 调用（后台线程执行，避免阻塞事件循环）──
    try:
        result = await asyncio.to_thread(
            verifier.verify, screenshot_bytes, expectation, "三轨视觉复核"
        )
    except Exception as _e:  # noqa: BLE001
        _log.warning(
            "[VisualCheck] 视觉复核异常(降级 SKIP，不阻断用例): %s", str(_e)[:80]
        )
        return None

    if not result or not result.get("success"):
        _reason = (result or {}).get("reason", "no-result") if result else "no-result"
        _log.warning(
            "[VisualCheck] 视觉复核未成功(模型不可用/失败) → SKIP: %s",
            str(_reason)[:80],
        )
        return None

    passed = bool(result.get("passed", False))
    reason = result.get("reason", "") or ("符合" if passed else "不符合")
    # VisualVerifier 透传 confidence 时优先使用；否则按 1.0（已给出明确判定）
    try:
        _conf = float(result.get("confidence", 1.0) or 1.0)
    except (TypeError, ValueError):
        _conf = 1.0
    return _verdict(
        passed, "视觉断言[%s]: %s" % (expectation[:30], reason), _conf
    )


async def _try_vlm_fallback(
    screenshot_bytes: bytes,
    expectation: str,
    page: Any,
) -> Optional[dict[str, Any]]:
    """★ P1-5 + T04.1: 视觉降级链 —— 本地 VLMOracle → LocalBackend 本地见图转换。

    通用性：不绑定业务术语，layout_spec 从 expectation 文本构造。
    降级：Ollama 不可用/失败 → 尝试 LocalBackend（纯计算，零依赖）；
          LocalBackend 也无法判定 → NO_DATA（fail-closed，绝不伪造 PASS）。

    Returns:
        - 终态字典（PASS/FAIL/NO_DATA），或
        - ``None`` 表示整条降级链异常，调用方应继续降级。
    """
    # 预取 DOM 文本摘要（供 VLMOracle 与 LocalBackend 共用）
    dom_summary = ""
    if page is not None:
        with guard("_try_vlm_fallback: dom_summary = await page.evaluate( '", log=_log):
            dom_summary = await page.evaluate(
                "() => document.body ? document.body.innerText.substring(0, 2000) : ''"
            )

    # ── 路径 1: 本地 VLMOracle（Ollama）──
    _local_crash = ""
    try:
        from src.oracle.correctness_vlm import get_vlm_oracle

        oracle = get_vlm_oracle()
        if oracle.is_available():
            layout_spec = {
                "expectation": expectation,
                "check_type": "visual",
            }
            result = await asyncio.to_thread(
                oracle.check, layout_spec, screenshot_bytes, dom_summary
            )
            if result and isinstance(result, dict):
                try:
                    confidence = float(result.get("confidence", 0) or 0)
                except (TypeError, ValueError):
                    confidence = 0.0
                match = result.get("match")
                reason_text = str(result.get("reason", ""))

                # ★ T04: match 为 None = 模型未给出判定 → 一律 NO_DATA。
                if match is None:
                    _log.info(
                        "[VLM] 无法判定(confidence=%.2f)，记为 NO_DATA", confidence
                    )
                    return _no_data(
                        REASON_VLM_UNDECIDED,
                        "VLM 无法判定: %s (confidence=%.2f)%s"
                        % (reason_text[:60], confidence, _local_crash),
                    )

                return _verdict(
                    bool(match),
                    "VLM视觉断言[%s]: %s (confidence=%.2f)"
                    % (expectation[:30], reason_text[:80], confidence),
                    confidence,
                )
            _log.info("[VLM] VLMOracle 无有效结果，降级到 LocalBackend")
        else:
            _log.info("[VLM] Ollama 不可用，跳过 VLM 降级")
    except Exception as e:
        _log.warning("[VLM] VLMOracle 调用异常: %s", str(e)[:80])
        _local_crash = _local_vlm_crash_hint(e) or _local_crash

    # ── 路径 2: LocalBackend 本地「见图转换」（T04.1，纯计算零依赖）──
    try:
        from src.oracle.vlm import VisualStatus, registry
        from src.oracle.vlm.backends.local import ElementAsserter

        # ★ 差距3修复(2026-09-02)：补齐 expected_elements 上下文，使本地规则后端能对
        #   「期望元素存在/缺失」类视觉断言产出真实 PASS/FAIL，而非恒 NO_DATA。
        #   root：原 context 仅含 dom_summary，ElementAsserter 因无关键词恒落 NO_DATA，
        #   导致无 VLM 环境下视觉维度欠缺「诚实判定」。通用性：关键词来自 expectation
        #   的引号/书名号文本，不绑定任何业务术语；纯计算零外部依赖。
        _vlm_ctx = {
            "dom_summary": dom_summary,
            "expected_elements": ElementAsserter.extract_keywords(expectation),
        }

        verdict = await asyncio.to_thread(
            registry.verify_with_fallback,
            screenshot_bytes,
            expectation,
            _vlm_ctx,
        )
        reason = str(verdict.evidence.get("reason", ""))
        if verdict.status is VisualStatus.PASS:
            return _verdict(
                True,
                "本地视觉校验[%s]: %s (confidence=%.2f)"
                % (expectation[:30], reason[:80], verdict.confidence),
                verdict.confidence,
            )
        if verdict.status is VisualStatus.FAIL:
            return _verdict(
                False,
                "本地视觉校验[%s]: %s (confidence=%.2f)"
                % (expectation[:30], reason[:80], verdict.confidence),
                verdict.confidence,
            )
        # ★ T04.1: LocalBackend 判定 NO_DATA → 保持 NO_DATA（fail-closed，不伪造 PASS）
        return _no_data(
            REASON_LOCAL_UNDECIDED,
            "本地视觉校验无法判定: %s%s" % (reason[:120], _local_crash),
        )
    except Exception as e:
        _log.warning("[VLM] LocalBackend 调用异常: %s", str(e)[:80])
        return None


async def verify_visual_bytes(
    screenshot_bytes: bytes,
    expectation: str,
    page: Any = None,
) -> dict[str, Any]:
    """字节驱动的视觉校验**唯一降级链入口**。

    云端 VisualVerifier → 本地 VLMOracle → LocalBackend → NO_DATA。
    供「只有截图 bytes、无 page」的调用方（如实证横切门禁 visual_checker）复用，
    避免各调用方自行拼装其中一段链（曾各自实现导致实证门禁永不走云端）。

    参数：
        screenshot_bytes: 页面/控件 PNG 截图 bytes。
        expectation:      自然语言视觉预期。
        page:             可选；提供时用于取 DOM 摘要喂本地 VLM（视觉看图可不传）。

    返回：
        终态字典（PASS/FAIL/NO_DATA），与 ``verify_visual`` 同构。
    """
    result = await _cloud_visual_review_via_verifier(screenshot_bytes, expectation)
    if result is None:
        result = await _try_vlm_fallback(screenshot_bytes, expectation, page)
    if result is None:
        result = _no_data(
            REASON_NO_VISION_PROVIDER, "视觉模型不可用(云端+VLM)，未校验"
        )
    return result


async def _resolve_visual_target(page: Any, check: dict[str, Any]) -> str:
    """真值回填：返回页面上真实存在的目标元素选择器（稳定 CSS 路径），失败返回 ""。

    解析优先级（只返回「已验证可命中」的真值，杜绝臆造选择器静默回退）：
      1. 已提供选择器（selector/target_selector/css_selector/locator）且能命中 → 直接复用
         （LLM 命中即真值，控件级截图特写的主要入口）；
      2. 未提供 / 未命中 → 用期望文本关键词（expectation/description/assertion）在页面
         实时定位真实元素，并回填其真实 CSS 路径（tag#id 或 tag.cls1.cls2）——这是
         「真值回填」核心：选择器来自运行时实际 DOM，而非生成期 LLM 臆造；
      3. 全部失败 → 返回 ""，调用方回退整视口（fail-open，不产生虚假选择器）。

    通用性：跨系统/行业/页面，纯 DOM 与文本启发，无业务知识依赖。
    """
    cands = []
    for _k in ("selector", "target_selector", "css_selector", "locator"):
        _v = check.get(_k)
        if isinstance(_v, str) and _v.strip():
            cands.append(_v.strip())
    # 1) 已提供且能命中 → 视为真值直接采用
    for _sel in cands:
        try:
            _loc = page.locator(_sel).first
            if await _loc.count() > 0:
                return _sel
        except Exception:
            continue
    # 2) 期望文本关键词实时定位真实元素并回填真实 CSS 路径
    _text = " ".join(
        str(check.get(_k) or "") for _k in ("expectation", "description", "assertion")
    ).strip()
    if not _text:
        return ""
    import re as _re

    _keywords = (
        _re.findall(r"[\u4e00-\u9fa5]{2,8}", _text)[:5]
        + _re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,32}", _text)[:3]
    )
    _skip_tags = {"html", "body"}
    for _kw in _keywords:
        if not _kw:
            continue
        try:
            _loc = page.get_by_text(_kw, exact=False).first
            if await _loc.count() == 0:
                continue
            _path = await _loc.evaluate(
                "(_el) => { "
                "  if (!_el) return ''; "
                "  const t = _el.tagName.toLowerCase(); "
                "  if (t === 'html' || t === 'body') return ''; "
                "  if (_el.id) return t + '#' + _el.id; "
                "  const cls = Array.from(_el.classList||[]).filter((c)=>!c.startsWith('ant-')).slice(0,3); "
                "  return t + (cls.length ? '.' + cls.join('.') : ''); "
                "}"
            )
            if _path and _path not in _skip_tags:
                _log.info(
                    "[VisualCheck] 真值回填选择器: 期望文本关键词 '%s' → %s",
                    _kw[:20],
                    _path,
                )
                return _path
        except Exception:
            continue
    return ""


async def verify_visual(page, check: dict[str, Any]) -> dict[str, Any]:
    """视觉断言：截图 → 云端AI(VisualVerifier) → VLM降级 → 判断是否符合 expectation。

    Args:
        page:  Playwright page 对象，可为 None。
        check: ``{"type": "visual_check", "expectation": "柱状图柱子未遮挡纵坐标"}``

    Returns:
        ``{"passed": bool|None, "status": "PASS"|"FAIL"|"NO_DATA", "reason": str,
           "evidence": str, "skipped": bool, "confidence": float}``

        ★ 未做实际校验时 ``status="NO_DATA"`` 且 ``passed=None``（不再假 PASS）。
    """
    # expectation 优先；AI 若把视觉描述放 assertion（违反 prompt 但可能发生）→ 兜底，避免 FN 漏报视觉缺陷
    expectation = (check.get("expectation") or check.get("assertion") or "").strip()
    if not expectation:
        return _no_data(REASON_NO_EXPECTATION, "visual_check 无 expectation，未校验")

    if page is None:
        return _no_data(REASON_NO_PAGE, "无 page 对象，视觉断言未校验")

    # ★ 控件级截图优化（2026-09-01）+ 真值回填（2026-09-01）：
    #   先按「已提供选择器是否命中」解析目标；未提供或未命中 → 用期望文本关键词实时
    #   定位真实元素并回填其真实 CSS 路径（真值回填，杜绝生成期 AI 臆造选择器全落地失败）；
    #   全部失败才回退整视口（fail-open，不产生虚假选择器）。
    target_selector = (
        await _resolve_visual_target(page, check)
        if page is not None
        else ""
    )
    # 截图（纯内存，不落盘）— 提前到配置读取前，共用于云端AI和VLM两条路径
    try:
        if target_selector:
            loc = page.locator(target_selector).first
            if await loc.count() > 0:
                screenshot_bytes = await loc.screenshot()
                _log.info("[VisualCheck] 已截目标控件特写: %s", target_selector[:60])
            else:
                _log.warning("[VisualCheck] 目标选择器 '%s' 未匹配元素，回退整视口截图",
                             target_selector[:60])
                screenshot_bytes = await page.screenshot(full_page=False)
        else:
            screenshot_bytes = await page.screenshot(full_page=False)
    except Exception as e:
        _log.warning("[VisualCheck] 截图失败: %s", str(e)[:80])
        return _no_data(
            REASON_SCREENSHOT_FAILED, "截图失败(%s)，视觉断言未校验" % str(e)[:60]
        )

    # ── 统一降级链（云端 VisualVerifier → 本地 VLM → LocalBackend → NO_DATA）──
    final_result = await verify_visual_bytes(screenshot_bytes, expectation, page)

    # ★ P1-5 持久化(2026-07-27): VLM 视觉校验结果写入 visual_review 表
    #   根因: visual_review 表零 INSERT → VLM 复核结果丢失 → Step5 无视觉维度数据
    #   通用性: 不绑定业务术语，vlm_diff 通用 JSON
    #   降级: 持久化失败不阻断主流程（warning 记录）
    #   职责边界: _verify_visual 负责触发持久化，store 负责写入逻辑
    #   ★ T04: 仅持久化真实校验结果（NO_DATA 不写库，避免污染视觉维度统计）；
    #          confidence 改用模型真实置信度，不再用 passed 反推 1.0/0.0（伪造数据）。
    if final_result.get("status") != STATUS_NO_DATA:
        with guard("verify_visual: from src.backend.state import _curre", log=_log, level=logging.WARNING):
            from src.backend.state import _current_project
            from src.core.versioning import current_version_id
            from src.oracle.store import write_visual_review

            _vr_project = _current_project() or ""
            _vr_vid = int(current_version_id(_vr_project) or 0)
            _vr_diff = {
                "match": final_result.get("passed", False),
                "evidence": final_result.get("evidence", ""),
                "expectation": expectation,
                "confidence": final_result.get("confidence", 0.0),
                "status": final_result.get("status", ""),
            }
            write_visual_review(
                project=_vr_project,
                version_id=_vr_vid,
                execution_id=0,  # 执行上下文不可用，标 0 表示独立复核
                screenshot_path="",  # 截图不落盘
                vlm_diff=_vr_diff,
                baseline_id=None,
            )

    return final_result
