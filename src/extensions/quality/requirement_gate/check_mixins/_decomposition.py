# -*- coding: utf-8 -*-
"""
门禁1：需求拆解后门禁 Mixin
包含：check_decomposition

★ v2.1 重写: 从"关键词子串匹配"改为"source_quote 全句核对 + _verify_status 判定"
  - 旧逻辑: 用 title+desc 抽关键词做子串匹配，"用户/系统/权限"等通用词轻松通过
  - 新逻辑: 直接消费 source_verify 的 _verify_status 字段，做阻断率阈值判定

行业对标: Citation Quality Verification — 验证 AI 输出的引用是否真实存在于源文档。
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)

from ..constants import _GateConstantsMixin, GateResult


class _GateDecompositionMixin(_GateConstantsMixin):
    """需求拆解门禁 Mixin —— 校验拆解功能点是否有需求依据。"""

    # ══════════════════════════════════════════════════════════
    # 门禁1：需求拆解后门禁
    # ══════════════════════════════════════════════════════════

    def check_decomposition(self, decomposed_items: list[dict[str, Any]]) -> GateResult:
        """
        校验需求拆解结果是否在需求文档中有依据。

        v2.1 新逻辑:
          1. 消费 source_verify 阶段的 _verify_status 字段（三阶段校验结果）
          2. _verify_status == "blocked" 或 "no_quote" → blocker
          3. _verify_status == "passed"/"passed_corrected" → pass
          4. 无 _verify_status 字段 → 降级为旧关键词匹配（兼容未走 source_verify 的场景）
          5. 阻断率 > 阈值 → passed=False

        ★ 修复(07-05): 5B NLI 从未实现，删除 passed_nli 死分支；
           修复命名bug：5a 实际产出 "passed"（非 "passed_substring"）

        Args:
            decomposed_items: 拆解出的功能点列表
                [{"req_id": "REQ-001", "module": "...", "description": "...",
                  "source_quote": "...", "_verify_status": "...", "_verify_reason": "..."}, ...]

        Returns:
            GateResult: 阻断项 = source_quote 核对未通过的需求
        """
        result = GateResult()

        if not self.requirement_text:
            result.passed = False
            result.blockers.append(
                {
                    "item_id": "SYSTEM",
                    "reason": "【阻断】合并需求文档为空，无法校验拆解结果。请先执行Step1加载需求。",
                    "type": "baseline_missing",
                }
            )
            result.summary = "基准缺失，无法校验"
            return result

        if not decomposed_items:
            result.summary = "无拆解结果需校验"
            return result

        blocker_count = 0
        warn_count = 0
        pass_count = 0
        total = len(decomposed_items)

        for item in decomposed_items:
            if not isinstance(item, dict):
                continue

            req_id = item.get("req_id", item.get("id", "?"))
            title = (
                item.get("title", item.get("module", ""))
                + " "
                + item.get("description", "")[:30]
            )
            verify_status = item.get("_verify_status", "")
            verify_reason = item.get("_verify_reason", "")

            # ── 新逻辑：消费 source_verify 的三阶段校验结果 ──
            if verify_status:
                if verify_status in ("blocked", "no_quote"):
                    # source_quote 核对未通过 → blocker
                    result.passed = False
                    result.blockers.append(
                        {
                            "item_id": req_id,
                            "req_id": req_id,
                            "reason": (
                                "【阻断】需求 '%s' 的 source_quote 原文核对未通过。%s"
                                "该需求可能是AI幻觉生成，请人工确认。"
                                % (
                                    title.strip()[:50],
                                    verify_reason + "。" if verify_reason else "",
                                )
                            ),
                            "type": "hallucination",
                            "verify_status": verify_status,
                        }
                    )
                    blocker_count += 1
                elif verify_status in ("passed", "passed_corrected", "partial_match"):
                    pass_count += 1
                else:
                    # 未知状态 → 警告
                    result.warnings.append(
                        {
                            "item_id": req_id,
                            "req_id": req_id,
                            "reason": "【警告】需求 '%s' 的核对状态未知: %s"
                            % (title.strip()[:50], verify_status),
                            "type": "unknown_status",
                        }
                    )
                    warn_count += 1
            else:
                # ── 降级：无 _verify_status 字段，走旧关键词匹配 ──
                keywords = self._extract_keywords(title)
                match_score = self._check_in_requirement(keywords)

                if match_score < self._DECOMPOSITION_BLOCK_THRESHOLD:
                    result.passed = False
                    result.blockers.append(
                        {
                            "item_id": req_id,
                            "req_id": req_id,
                            "reason": (
                                "【阻断】需求 '%s' 在合并需求文档中找不到依据（关键词匹配 %.0f%%）。"
                                "该需求可能是AI幻觉生成。"
                                % (title.strip()[:50], match_score)
                            ),
                            "type": "hallucination",
                            "match_score": match_score,
                        }
                    )
                    blocker_count += 1
                elif match_score < self._DECOMPOSITION_WARN_THRESHOLD:
                    result.warnings.append(
                        {
                            "item_id": req_id,
                            "req_id": req_id,
                            "reason": "【警告】需求 '%s' 与需求文档匹配度较低(%.0f%%)。"
                            % (title.strip()[:50], match_score),
                            "type": "low_match",
                            "match_score": match_score,
                        }
                    )
                    warn_count += 1
                else:
                    pass_count += 1

        # ── 阻断率阈值判定 ──
        blocker_rate = blocker_count / total if total > 0 else 0.0

        if blocker_rate > self._DECOMPOSITION_BLOCK_THRESHOLD:
            # 阻断率超过阈值 → 硬阻断
            result.passed = False
            result.summary = (
                "拆解校验阻断: %d/%d 个需求 source_quote 核对未通过（阻断率 %.0f%% > 阈值 %.0f%%），"
                "%d 个警告"
                % (
                    blocker_count,
                    total,
                    blocker_rate * 100,
                    self._DECOMPOSITION_BLOCK_THRESHOLD * 100,
                    warn_count,
                )
            )
        elif blocker_rate > 0:
            # 有 blocker 但未达阈值 → 保留 passed=False（有 blocker 就不通过），
            # 由 pre_step_gate 做最终阈值决策（是否阻断）
            result.passed = False
            result.summary = (
                "拆解校验有阻断项: %d/%d 个需求核对未通过（阻断率 %.0f%% <= 阈值 %.0f%%），"
                "%d 个警告，%d 个通过。最终阻断决策由 pre_step_gate 阈值判定。"
                % (
                    blocker_count,
                    total,
                    blocker_rate * 100,
                    self._DECOMPOSITION_BLOCK_THRESHOLD * 100,
                    warn_count,
                    pass_count,
                )
            )
        elif warn_count > 0:
            result.summary = "拆解校验通过: %d 个需求, %d 个警告, %d 个通过" % (
                total,
                warn_count,
                pass_count,
            )
        else:
            result.summary = "拆解校验通过: %d 个需求均有依据" % total

        _log.info(
            "[门禁] %s (阻断 %d, 警告 %d, 通过 %d, 阻断率 %.0f%%)",
            result.summary,
            blocker_count,
            warn_count,
            pass_count,
            blocker_rate * 100,
        )

        return result
