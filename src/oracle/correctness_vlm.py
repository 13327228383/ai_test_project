# -*- coding: utf-8 -*-
"""★ P1-5: VLM Oracle 视觉校验入口 — correctness 类型 oracle。

职责边界：
  - 实现 VLMOracle.check(layout_spec, screenshot, dom_summary)
  - 构造通用 prompt（不绑定业务术语），调用 vlm_adapter
  - 解析 VLM 响应 → {match, diff_regions, grounding, confidence}
  - 降级：Ollama 不可用时返回 confidence=0 的降级 evidence（不阻断流水线）

通用性（§12.4 通用性自检）：
  1. 跨系统：layout_spec 是通用 JSON 描述，适配 React/Vue/原生
  2. 跨行业：prompt 不含行业术语，只描述"期望 vs 实际"对比
  3. 跨页面：layout_spec.elements 可描述任意页面类型
  4. 跨 Tab：不依赖 Tab 结构，纯视觉对比
  5. 数据完整性：screenshot 缺失时降级为纯文本对比
  6. 根因溯源：解决"执行期缺少真值闭环"P1 痛点
  7. 覆盖性：所有调用方统一走 check() 入口
  8. 全局观：作为 Step4 oracle 增强层，不修改现有 check 管道
  9. 职责边界：仅做视觉校验，不做导航/决策/数据注入
"""
from __future__ import annotations
from src.core.guarded import guard

import json
import logging
from typing import Any, Optional

_log = logging.getLogger(__name__)


class VLMOracle:
    """视觉大模型 Oracle — 通过 VLM 对比期望布局与实际截图，给出视觉真值。

    使用方式:
        oracle = VLMOracle()
        result = oracle.check(
            layout_spec={"elements": [{"type": "button", "text": "保存", "region": [0,0,100,40]}]},
            screenshot=screenshot_bytes_or_path,
            dom_summary="页面含 1 个保存按钮，位于右上角",
        )
        # result = {"match": True/False/None, "diff_regions": [...], "grounding": [...], "confidence": 0.0~1.0}
    """

    def __init__(self, provider: str = "ollama", model: Optional[str] = None):
        """初始化 VLM Oracle。

        参数:
            provider: VLM provider 名（默认 ollama，预留扩展）
            model:    模型名（None=用 provider 默认）
        """
        self.provider_name = provider
        self.model = model
        self._provider = None
        with guard("__init__: from src.oracle.vlm_adapter import g", log=_log, level=logging.WARNING):
            from src.oracle.vlm_adapter import get_provider

            self._provider = get_provider(provider)

    def is_available(self) -> bool:
        """检查 VLM provider 是否可用（调用前预检）。"""
        if not self._provider:
            return False
        try:
            check_fn = self._provider.get("check")
            return bool(check_fn and check_fn())
        except Exception as _ce:
            _log.debug(
                "[correctness_vlm] provider check失败(返回False): %s", str(_ce)[:60]
            )
            return False

    def check(
        self,
        layout_spec: Any,
        screenshot: Any = None,
        dom_summary: str = "",
    ) -> dict[str, Any]:
        """视觉校验入口 — 对比期望布局与实际截图。

        参数:
            layout_spec:  期望布局描述（JSON/dict/str），如:
                          {"elements": [{"type": "button", "text": "保存", "region": [x,y,w,h]}]}
            screenshot:   实际截图（bytes/path/base64），None=纯文本对比
            dom_summary:  DOM 文本摘要（辅助 VLM 理解页面结构）

        返回:
            {
                "match": bool|None,        # True=匹配, False=不匹配, None=无法判定(降级)
                "diff_regions": list,      # 差异区域 [{x,y,w,h,desc}]
                "grounding": list,         # 元素定位依据 [{element, found, region}]
                "confidence": float,       # 0.0~1.0（0=降级，无法判定）
                "reason": str,             # 结论原因/降级原因
                "raw_response": str,       # VLM 原始响应（调试用）
            }

        降级策略:
            - provider 不可用 → match=None, confidence=0, reason="ollama_unavailable"
            - screenshot 缺失 → 降级为纯文本对比（confidence 上限 0.5）
            - 响应解析失败 → match=None, confidence=0, reason="parse_failed"
        """
        # 降级路径1：provider 不可用
        if not self.is_available():
            return self._degrade("ollama_unavailable")

        # 降级路径2：screenshot 缺失（纯文本对比，置信度受限）
        text_only = screenshot is None
        if text_only:
            _log.info("[VLMOracle] screenshot 缺失，降级为纯文本对比")

        # 构造 prompt
        prompt = self._build_prompt(layout_spec, dom_summary, text_only)
        if not prompt:
            return self._degrade("prompt_build_failed")

        # 调用 VLM
        generate_fn = self._provider.get("generate") if self._provider else None
        if not generate_fn:
            return self._degrade("provider_no_generate")

        try:
            result = generate_fn(prompt, image=screenshot, model=self.model)
        except Exception as e:
            _log.warning("[VLMOracle] generate 调用异常: %s", str(e)[:100])
            return self._degrade("generate_exception")

        if not result or not result.get("text"):
            return self._degrade("generate_empty")

        # 解析响应
        raw_text = result.get("text", "")
        parsed = self._parse_response(raw_text)
        if parsed is None:
            return self._degrade("parse_failed", raw_response=raw_text)

        # 纯文本模式置信度受限
        if text_only:
            parsed["confidence"] = min(parsed.get("confidence", 0.0), 0.5)
            parsed["reason"] = f"text_only_mode: {parsed.get('reason', '')}"

        parsed["raw_response"] = raw_text
        return parsed

    # ═══════════════════════════════════════════════════════════════
    # 内部方法
    # ═══════════════════════════════════════════════════════════════

    def _build_prompt(
        self,
        layout_spec: Any,
        dom_summary: str,
        text_only: bool,
    ) -> str:
        """构造 VLM 提示词（通用模板，不绑定业务术语）。

        通用性：prompt 只描述"期望布局 vs 实际截图"对比任务，
               不含任何行业/系统/页面特定术语。
        """
        # 序列化 layout_spec
        if isinstance(layout_spec, (dict, list)):
            try:
                spec_text = json.dumps(layout_spec, ensure_ascii=False, indent=2)
            except Exception:
                spec_text = str(layout_spec)
        else:
            spec_text = str(layout_spec or "")

        if not spec_text.strip():
            _log.warning("[VLMOracle] layout_spec 为空")
            return ""

        mode_desc = (
            "纯文本对比模式（无截图）" if text_only else "视觉对比模式（含截图）"
        )

        # ★ 根因修复(2026-08-29)：区分两种 layout_spec 形态。
        #   视觉复核主路径(_verify_visual._try_vlm_fallback)传入的二是自由描述 expectation
        #   ({"expectation": ..., "check_type": "visual"})，本没有离散 elements。原 prompt 一律
        #   走"元素 grounding(每个期望元素是否找到)"框架，模型对无 elements 可 ground 的情况
        #   恒报 match=False/confidence=0 → 常规流水线每个 visual_check 误判 FAIL（回归）。
        #   故：含 "elements" 列表 → 元素 grounding 模式；否则 → 自由期望二元裁决模式。
        has_elements = (
            isinstance(layout_spec, dict) and isinstance(layout_spec.get("elements"), list)
        )

        if has_elements:
            prompt = self._prompt_element_mode(spec_text, dom_summary, mode_desc)
        else:
            prompt = self._prompt_expectation_mode(spec_text, dom_summary, mode_desc)

        return prompt

    def _prompt_expectation_mode(
        self, spec_text: str, dom_summary: str, mode_desc: str
    ) -> str:
        """自由期望二元裁决模式：判断截图是否符合一段自然语言期望。

        通用性：期望描述不含业务术语（如"柱状图柱子未遮挡纵坐标"），
        模型依据截图 + DOM 摘要二选一，无法确定时返回 match=null（fail-closed）。
        """
        return f"""你是一个视觉校验专家。请根据实际页面（{"DOM 摘要" if mode_desc.startswith("纯文本") else "见附图截图"}）
判断它是否符合"期望描述"，只输出严格 JSON。

【对比模式】{mode_desc}

【期望描述】
{spec_text}

【DOM 文本摘要】
{dom_summary or "(无)"}

【任务】严格二选一（无法确定时用 null），输出 JSON：
{{
  "match": true 或 false 或 null,
  "confidence": 0.0,
  "reason": "判定原因（通用描述，不含业务术语）"
}}

判定规则：
1. match=true  仅当截图清楚符合期望描述（对象/颜色/文字/布局均满足）；
2. match=false 仅当截图明确违背期望描述（对象缺失、颜色/文字不符、布局错位、明显偏差）；
3. match=null  无法确定时（证据不足以二选一）；
4. confidence  0.0~1.0 反映把握程度；
5. reason      结合截图与 DOM 摘要，简洁说明依据。

只输出 JSON，不要其他内容。"""

    def _prompt_element_mode(
        self, spec_text: str, dom_summary: str, mode_desc: str
    ) -> str:
        """离散元素 grounding 模式：逐个判断期望元素是否在实际页面找到。"""
        return f"""你是一个视觉校验专家。请对比"期望布局"与"实际页面"，判断视觉上是否匹配。

【对比模式】{mode_desc}

【期望布局描述】
{spec_text}

【DOM 文本摘要】
{dom_summary or "(无)"}

【任务】
请分析实际页面（{"根据 DOM 摘要" if mode_desc.startswith("纯文本") else "见附图截图"}）与期望布局的匹配程度，输出严格的 JSON 格式结果：

{{
  "match": true/false,
  "diff_regions": [
    {{"x": 0, "y": 0, "w": 100, "h": 50, "desc": "差异描述"}}
  ],
  "grounding": [
    {{"element": "期望元素标识", "found": true/false, "region": [x, y, w, h]}}
  ],
  "confidence": 0.0,
  "reason": "判定原因（通用描述，不含业务术语）"
}}

要求：
1. match: 布尔值，true=视觉匹配，false=有差异
2. diff_regions: 视觉差异区域列表（无差异时为空数组）
3. grounding: 每个期望元素是否在实际页面中找到（含定位）
4. confidence: 0.0~1.0，判定置信度
5. reason: 简洁原因（如"按钮缺失"、"布局错位"、"完全匹配"）

只输出 JSON，不要其他内容。"""

    def _parse_response(self, raw_text: str) -> Optional[dict[str, Any]]:
        """解析 VLM 响应为标准结构（容错：支持 ```json 包裹/纯 JSON/混合文本）。

        返回: {match, diff_regions, grounding, confidence, reason} 或 None（解析失败）
        """
        if not raw_text or not raw_text.strip():
            return None

        text = raw_text.strip()

        # 尝试1：直接 JSON 解析
        try:
            return self._normalize_result(json.loads(text))
        except (json.JSONDecodeError, ValueError) as _e:
            _log.debug("[correctness_vlm] 直接 JSON 解析失败: %s", _e, exc_info=True)

        # 尝试2：提取 ```json ... ``` 代码块
        if "```json" in text:
            try:
                start = text.index("```json") + 7
                end = text.index("```", start)
                return self._normalize_result(json.loads(text[start:end].strip()))
            except (ValueError, json.JSONDecodeError) as _e:
                _log.debug(
                    "[correctness_vlm] ```json``` 块解析失败: %s", _e, exc_info=True
                )

        # 尝试3：提取第一个 {...} 块
        try:
            start = text.index("{")
            end = text.rindex("}") + 1
            return self._normalize_result(json.loads(text[start:end]))
        except (ValueError, json.JSONDecodeError) as _e:
            _log.debug("[correctness_vlm] {…} 块解析失败: %s", _e, exc_info=True)

        _log.warning("[VLMOracle] 响应解析失败（非 JSON）: %s", text[:120])
        return None

    def _normalize_result(self, data: Any) -> Optional[dict[str, Any]]:
        """将 VLM 响应归一化为标准结构（容错字段缺失/类型错误）。"""
        if not isinstance(data, dict):
            return None

        # match: 严格布尔化（None 视为无法判定）
        match_val = data.get("match")
        if isinstance(match_val, str):
            match_val = match_val.lower().strip() in ("true", "yes", "1", "匹配")
        elif not isinstance(match_val, bool):
            match_val = None

        # diff_regions: 列表化
        diff = data.get("diff_regions", [])
        if not isinstance(diff, list):
            diff = []

        # grounding: 列表化
        grounding = data.get("grounding", [])
        if not isinstance(grounding, list):
            grounding = []

        # confidence: 浮点化 + 范围限制
        try:
            conf = float(data.get("confidence", 0.0))
            conf = max(0.0, min(1.0, conf))
        except (TypeError, ValueError):
            conf = 0.0

        return {
            "match": match_val,
            "diff_regions": diff,
            "grounding": grounding,
            "confidence": conf,
            "reason": str(data.get("reason", ""))[:512],
        }

    def _degrade(self, reason: str, raw_response: str = "") -> dict[str, Any]:
        """构造降级响应（不阻断流水线，调用方据 confidence=0 判定）。"""
        return {
            "match": None,
            "diff_regions": [],
            "grounding": [],
            "confidence": 0.0,
            "reason": f"degraded: {reason}",
            "raw_response": raw_response,
        }


# ═══════════════════════════════════════════════════════════════
# 便捷工厂函数（供 Step4 插件调用）
# ═══════════════════════════════════════════════════════════════

_default_oracle: Optional[VLMOracle] = None


def get_vlm_oracle() -> VLMOracle:
    """获取默认 VLMOracle 实例（单例，惰性初始化）。

    通用性：所有调用方统一入口，避免重复初始化。
    """
    global _default_oracle
    if _default_oracle is None:
        _default_oracle = VLMOracle()
    return _default_oracle
