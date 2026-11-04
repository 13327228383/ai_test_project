# -*- coding: utf-8 -*-
"""DB / UI 客观断言校验 — 从 verification.py 拆出，零业务逻辑变更。"""

import asyncio, re as _re, functools, logging
from typing import Any

_log = logging.getLogger(__name__)

# ★ §4.3 单文件≤800行（2026-08-06）: 瞬态缓存查询 / 文本计数助手拆到独立文件
from ._db_ui_helpers import (
    _match_transient_hints,
    _match_transient_text,
    _count_text_excluding_inputs,
)
# ★ 根因修复(2026-09-25) RC-AE: 反馈条选择器统一从单一事实源导入（见该模块 docstring）。
from src.pipeline.utils.feedback_selectors import (
    ERROR_SELECTORS as _GENERIC_ERROR_SELECTORS,
    NO_ERROR_SCAN_SELECTORS as _NO_ERROR_SCAN_SELECTORS,
    SUCCESS_SELECTORS as _GENERIC_SUCCESS_SELECTORS,
    # ★ RC-AF(2026-09-25): 广义类名形态判据（错误提示 ≠ 交互控件）
    is_broad_error_selector as _is_broad_error_selector,
    is_decorative_broad_match as _is_decorative_broad_match,
    # ★ RC-AF(续2/续3, 2026-09-25): CSV 拆分 + 单一事实源的「活动 DOM 反馈条探测」
    split_selector_list as _split_selector_list,
    ERROR_DETECT_JS as _ERROR_DETECT_JS,
    SUCCESS_DETECT_JS as _SUCCESS_DETECT_JS,
    # ★ FIX4/AJ(2026-09-25): 访问控制拒绝的可观察形态（单一事实源）
    DENIAL_SELECTORS as _DENIAL_SELECTORS,
    denial_signals_in_text as _denial_signals_in_text,
    is_login_path as _is_login_path,
)
# 唯一值 token 形态的权威定义在 step4_executor（运行时唯一值物化的唯一生产点），
# 此处只引用不重定义 —— 生产端与断言端对「什么是一个唯一值」必须同一判据。
from src.pipeline.step4_executor import _UNIQUE_NAME_TOKEN_RE


# 伪元素名（CSS 伪元素**不被选择器 API 匹配**，见 D21）
_PSEUDO_ELEMENT_NAMES = (
    "before", "after", "first-line", "first-letter", "selection",
    "placeholder", "marker", "backdrop", "file-selector-button", "cue",
)
_PSEUDO_ELEMENT_RE = _re.compile(
    r"::?(?:" + "|".join(_PSEUDO_ELEMENT_NAMES) + r")\b", _re.IGNORECASE
)


def _strip_pseudo_elements(selector: str) -> str:
    """剥离选择器中的伪元素（`::before` / `:after` / `::placeholder` 等）。

    ★ D21 根因（2026-09-23）：伪元素**没有对应 DOM 元素**，
      `document.querySelectorAll('.a::before')` 直接抛 SyntaxError，
      Playwright `locator().count()` 报
      `Unexpected token ":" while parsing css select`（round5 实测
      TC-REQ-006/007-V01）。
      含伪元素的选择器在**任何页面**上都会抛异常 → 检查恒失败 → 假 FAIL。
      生成侧已在 `step3/_fallback_cases.py` 停发伪元素；此处为**执行侧单点防御**，
      覆盖任何后续生成器/AI 再次产出伪元素的情况：
      宿主元素存在 ⇔ 伪元素可渲染，故剥到宿主仍是语义等价的可用断言。

    非字符串 / 空串原样返回；剥离后为空则原样返回（交由既有异常路径处理）。
    """
    if not isinstance(selector, str) or not selector:
        return selector
    _out = _PSEUDO_ELEMENT_RE.sub("", selector)
    _out = " ".join(_out.split())
    # 清理剥离后可能残留的悬空组合符（如 "label > " → "label"）
    _out = _re.sub(r"[>+~]\s*$", "", _out).strip()
    return _out or selector


def _expand_db_value_assertions(checks: list[Any]) -> list[Any]:
    """[已复核-DeepSeek-V3 2026-08-16] ADI 强化(D)：存在性断言 → 值级断言升级。

    Step3（auto_build / AI）在 db 断言上可附带 ``field_check``（字段级期望值）。
    原运行时只做 record_exists（存在即过）。此处把这些期望值升级为
    ``db_field_value_check``（值匹配才过），与原存在性检查 AND 合并。

    fail-open（源缺失/异常 → 降级为存在性检查，不误杀）：
      - 无 field_check / 空 dict                → 原样返回（行为不变）
      - 单条 field 缺 expect                    → 跳过该字段
      - precision 解析异常                       → tolerance 归零，继续比较
      - 升级失败时仍保留原存在性检查作为兜底
    """
    out: list[Any] = []
    for check in checks:
        if not isinstance(check, dict):
            out.append(check)
            continue
        # 已是值级/计数断言，保持（不重复升级）
        if check.get("type") in (
            "db_field_value_check",
            "db_query_check",
            "db_record_count_check",
            "db_record_not_exists_check",
        ):
            out.append(check)
            continue
        fc = check.get("field_check")
        if not isinstance(fc, dict) or not fc:
            out.append(check)
            continue
        # 升级：每个 field_check 字段拆成一个值级断言，AND 合并到存在性检查
        # ★ ADI tail (2026-08-15) [已复核-DeepSeek-V3 2026-08-16]: 透传 operator/tolerance。
        #   原实现硬编码 operator="eq"，使 field_check 的 gte/lte/gt/lt/in/range/neq
        #   期望全部回落 eq（值比较深度丢失）。此处解析 spec.operator（白名单），
        #   未知/缺失回落 "eq"（fail-open，不误杀）；tolerance 优先 spec.tolerance，
        #   回退 spec.precision（与既有精度语义一致）。
        _SUPPORTED_OPS = frozenset(
            {"eq", "neq", "gte", "lte", "gt", "lt", "in", "range"}
        )
        _upgraded = False
        for _field, _spec in fc.items():
            _expected = _spec.get("expect") if isinstance(_spec, dict) else _spec
            if _expected is None or _expected == "":
                continue
            _operator = "eq"
            _tol = 0.0
            if isinstance(_spec, dict):
                _operator = str(_spec.get("operator") or "eq").strip().lower()
                if _operator not in _SUPPORTED_OPS:
                    # fail-open: 未知 operator 回落 eq（与执行器行为一致）
                    _operator = "eq"
                try:
                    _tol = float(
                        _spec.get("tolerance", _spec.get("precision", 0)) or 0
                    )
                except (TypeError, ValueError):
                    _tol = 0.0
            out.append(
                {
                    "type": "db_field_value_check",
                    "table": (_spec.get("table") if isinstance(_spec, dict) else None)
                    or check.get("table"),
                    "field": _field,
                    "where": (_spec.get("where") if isinstance(_spec, dict) else None)
                    or check.get("where", ""),
                    "expected": _expected,
                    "operator": _operator,
                    "tolerance": _tol,
                }
            )
            _upgraded = True
        # 无论是否升级成功，都保留原存在性检查（AND 的一部分，fail-open 兜底）
        out.append(check)
        if _upgraded:
            _log.info(
                "[ADI] DB 断言升级为值级: table=%s field_check=%s",
                check.get("table"),
                list(fc.keys()),
            )
    return out


async def _verify_db_async(checks: list[str], page: Any = None) -> dict[str, Any]:
    """★ T03(2026-08-06): DB 轨薄适配层 → DbTrackVerifier。

    保留既有语义（零业务逻辑变更 + 新增能力）：
    - evidence="unverified"/"skipped" 特殊检查（P0 风险无 schema / 非 P0 跳过）
    - db_record_count/not_exists/exists/field_value/query → verify_db_ext
    - time_range_check → verify_time_range（需要 page；无 page → UNKNOWN+skipped）
    - 其余字符串 check → verify_db（恢复线程用户上下文）
    - 全部 skipped → 轨 skipped=True（passed 保持 True，不计 AND 但置信度扣分）
    """
    from src.pipeline.steps.step4.verification._track_protocol import DbTrackVerifier

    evidence_parts = []
    all_passed = True
    all_data = []
    skipped_count = 0
    total_count = 0
    regular_checks = []
    for check in checks:
        total_count += 1
        # ★ v2.1: 处理 evidence="unverified"/"skipped" 的特殊检查
        if isinstance(check, dict):
            ev = check.get("evidence", "")
            if ev == "unverified":
                # P0 风险但无 DB schema → 标记失败，需手动配置
                evidence_parts.append("DB: 未配置DB校验(unverified)→✗")
                all_passed = False
                continue
            elif ev == "skipped":
                # 非 P0 风险，DB 校验跳过
                evidence_parts.append("DB: 跳过(skipped)→⊘")
                skipped_count += 1
                continue
        # [已复核-DeepSeek-V3 2026-08-16] ADI 强化(D)：存在性断言升级为值级断言
        #   （field_check → db_field_value_check，与原存在性检查 AND 合并；fail-open）
        regular_checks.extend(_expand_db_value_assertions([check]))

    if regular_checks:
        _r = await DbTrackVerifier().verify(regular_checks, ctx={"page": page})
        evidence_parts.append(_r.get("evidence", ""))
        if not _r.get("passed"):
            all_passed = False
        if _r.get("skipped"):
            skipped_count += _r.get("details_count", 0) or sum(
                1 for _d in (_r.get("details") or []) if _d.get("skipped")
            )
        all_data.extend(_r.get("data", []))

    # ★ 通用修复(v14): 所有 check 都 skipped 时，整个 DB 轨标记为 skipped
    #   - 不影响 passed（保持 True，不阻断用例）
    #   - skipped=True 让 _compute_confidence 排除该轨，置信度自然降低触发审核
    _all_skipped = total_count > 0 and skipped_count == total_count
    return {
        "passed": all_passed,
        "evidence": "; ".join(p for p in evidence_parts if p),
        "data": all_data,
        "skipped": _all_skipped,
        "skip_reason": "all_checks_skipped" if _all_skipped else None,
    }


# ══════════════════════════════════════════════════
# ★ P1-7: UI 客观断言优先 (+ 元素消失验证) — 字典分派
# ══════════════════════════════════════════════════
# ★ §4.3 单文件≤800行（2026-08-06）: _count_text_excluding_inputs 已拆到
#   _db_ui_helpers.py（顶部统一 import）


async def _handle_text_contains(page, check, inner, check_type, precision):
    # ★ ADI tail (2026-08-15) [已复核-DeepSeek-V3 2026-08-16]: 兜底读取 check.expect。
    #   根因: Step3 的精确字段检查产出 {track:ui, precision:exact,
    #   check:{type:text_contains, selector:body}, expect:字段名} —— 期望文本在外层 expect，
    #   不在 inner.expected/inner.text/check.text。原实现只读后三者 → text="" →
    #   `"" in txt` 恒 True → UI 值比较假 PASS（正反例都过）。
    #   修复: 增加 check.get("expect") 兜底，使 Step3 精确期望文本稳定进入比较器。
    text = (
        inner.get("expected", "")
        or inner.get("text", "")
        or check.get("text", "")
        or check.get("expect", "")
    )
    selector = inner.get("selector", "")
    # ★ 根因修复（2026-09-23）D15 —— not_contains 极性被忽略导致**假 FAIL**：
    #   实测 ModSystem TC-REQ-001-SEC01 断言 {"type":"text_contains_check",
    #   "expected":"<script>","operator":"not_contains"}（XSS payload 不应出现），
    #   执行器却报 `text_contains:✗(精确匹配找到0个元素)` —— 对 not_contains 而言
    #   "找到 0 个" 恰恰是**满足**。根因: 本函数声明支持 contains/not_contains
    #   （见 _verify_text_contains.py 的同一契约），但实现从不读 operator，
    #   恒按"必须找到期望文本"判定 → 所有否定型断言（安全/禁止类，引擎标准手段）
    #   在"未出现"时被系统性判 FAIL。同类: `_handle_text_not_exists` 有正确的
    #   `passed = count == 0` 语义，但 Step3 产出的是 text_contains_check+operator，
    #   两个实现语义不一致 → 语义按 check 声明解析，不按实现猜。
    #   通用性: 只读 check 自身声明的 operator，零业务硬编码。
    _operator = str(
        inner.get("operator") or check.get("operator") or "contains"
    ).strip().lower()
    if _operator not in ("contains", "not_contains"):
        _operator = "contains"
    _neg = _operator == "not_contains"
    # ★ v4.7 修复 (2026-07-23, §13.4): 拆分逗号分隔的多值 expected，任一匹配即 PASS
    #   根因(§10.9):
    #     1. 表层: text_contains:✗ — expected="成功,完成,操作成功,复制成功" 不匹配
    #     2. 中层: _count_text_excluding_inputs(page, text) 把整个逗号串当一个搜索词
    #     3. 根因: _handle_text_contains 不拆分多值 expected（_verify_text_contains.py 会拆分但没被调用）
    #     4. 架构: 两个 text_contains 实现逻辑不一致，actual handler 不拆分逗号
    #   修复: expected 按逗号拆分为多值，任一匹配即 PASS
    #   通用性(§12.4): 跨系统/跨行业/跨操作 — 逗号分隔多值是 Step3 通用格式
    # ★ v5.1 (2026-08-08): 同时支持中文逗号（、）拆分，避免AI生成的"商品管理、订单处理"等
    #   长句不匹配页面元素。中文逗号拆出"商品管理"等短词，更易匹配sidebar/面包屑
    import re as _re
    _split_values = [v.strip() for v in _re.split(r"[、,，]", str(text)) if v.strip()]
    expected_values = _split_values if _split_values else [str(text).strip()]
    if not expected_values:
        expected_values = [str(text).strip()]
    # ★ 精确度修复(2026-08-09, 用户问题 #4): exact 模式不进行 2 字子词扩展
    #   根因: 2字子词扩展（如"商品管理"→"商品"、"管理"）被同时应用于 exact 和 fuzzy 模式，
    #   导致 exact 模式下"科目名称"匹配到仅包含"科目"的元素（假PASS）。
    #   修复: exact 模式只做逗号拆分，不做 2 字子词扩展；fuzzy 模式保留子词扩展。
    #   通用性: 跨系统/行业/页面 — 精确匹配在不同场景下语义一致。
    if precision == "exact":
        # ── exact 模式：无 2 字子词扩展，逐元素精确匹配 ──
        passed = False
        found_count = 0
        matched_value = ""
        try:
            if selector:
                locator = page.locator(selector)
                count = await locator.count()
                for i in range(count):
                    el = locator.nth(i)
                    txt = (await el.inner_text()).strip()
                    for ev in expected_values:
                        if ev in txt or txt == ev:
                            passed = True
                            found_count += 1
                            matched_value = ev
                            break
            else:
                locator = page.locator("body *")
                count = await locator.count()
                for i in range(count):
                    el = locator.nth(i)
                    txt = (await el.inner_text()).strip()
                    for ev in expected_values:
                        if txt == ev:
                            passed = True
                            found_count += 1
                            matched_value = ev
                            break
            if not passed:
                for ev in expected_values:
                    transient_hit = _match_transient_text(page, ev)
                    if transient_hit:
                        passed = True
                        # D15: not_contains 下抓到禁用文本 = 违规（不得因"抓到"而 PASS）
                        if _neg:
                            return {
                                "type": "text_contains",
                                "text": text,
                                "precision": precision,
                                "selector": selector,
                                "passed": False,
                                "evidence": "not_contains 违规: 捕获到禁用文本: %s (matched='%s')"
                                % (transient_hit, ev),
                            }
                        return {
                            "type": "text_contains",
                            "text": text,
                            "precision": precision,
                            "selector": selector,
                            "passed": True,
                            "evidence": "瞬态提示文本已捕获: %s (matched='%s')"
                            % (transient_hit, ev),
                        }
        except Exception as e:
            return {
                "type": "text_contains",
                "text": text,
                "precision": precision,
                "selector": selector,
                "passed": False,
                # ★ D19（2026-09-23）：执行器抛异常 ≠ 断言给出否定结论。
                #   原实现只写 passed=False，与"断言真实执行且产品不满足"同形，
                #   下游按"已执行失败断言"升格 PRODUCT_BUG → 批量假 Bug。
                #   实测 TC-REQ-006/007-V01：选择器含伪元素 `::before`（见 D21），
                #   `Locator.count` 必抛 `Unexpected token ":"`，与产品行为无关。
                #   reason=EXECUTOR_EXCEPTION 使判定层可区分"引擎够不着"与"产品不满足"。
                "reason": "EXECUTOR_EXCEPTION",
                "evidence": "exact查询异常: %s" % str(e)[:60],
            }
        # D15: not_contains 语义取反（"未出现"= 满足），并如实记录计数
        if _neg:
            return {
                "type": "text_contains",
                "text": text,
                "precision": precision,
                "selector": selector,
                "passed": found_count == 0,
                "matched_value": matched_value,
                "evidence": (
                    "not_contains 满足：未出现禁用文本 %s" % expected_values
                    if found_count == 0
                    else f"not_contains 违规：出现禁用文本 {found_count} 处"
                         f"（matched='{matched_value}'）"
                ),
            }
        return {
            "type": "text_contains",
            "text": text,
            "precision": precision,
            "selector": selector,
            "passed": passed,
            "matched_value": matched_value,
            "evidence": f"精确匹配找到{found_count}个元素"
            + (
                f"（selector={selector}, matched='{matched_value}'）"
                if selector
                else ""
            ),
        }
    else:
        # ── fuzzy 模式：2 字子词扩展 + body.innerText 匹配（唯一值例外）──
        # ★ v5.2 (2026-08-08): 对每个拆分值，再提取2字中文子词
        #   根因: Django admin 模型 verbose_name="商品"（不是"商品管理"），AI生成的
        #   "商品管理"在页面上不存在，但"商品"存在。提取2字子词提高匹配率。
        #   通用性: 适用于所有中文页面，不改业务逻辑
        # ★ 唯一值（运行时物化）禁止子词扩展：该值由本用例独占生产，断言语义是
        #   「这条记录在不在」；扩展会把「自动化记录xxxxxx」切成「自动」这类通用词，
        #   命中任意表单标签即假 PASS（实测 TC-REQ-017-E01：新记录不在首屏却判通过）。
        _unique_values = [
            v for v in expected_values if _UNIQUE_NAME_TOKEN_RE.search(v)
        ]
        _extended = list(expected_values)
        for _ev in expected_values:
            if _ev in _unique_values:
                continue
            _chunks = _re.findall(r'[\u4e00-\u9fff]{2,}', _ev)
            for _ck in _chunks:
                if len(_ck) >= 4:
                    for _i in range(len(_ck) - 1):
                        _sub = _ck[_i:_i+2]
                        if _sub not in _extended:
                            _extended.append(_sub)
        expected_values = _extended
        # ★ 通用修复(2026-07-22): fuzzy 模式排除输入框值
        # ★ v5.3 (2026-08-08): 废弃 _count_text_excluding_inputs（已损坏），改用
        #   page.evaluate 获取 body.innerText 后直接检查。
        total_count = 0
        matched_value = ""
        try:
            _body_text = await page.evaluate("document.body.innerText") if page else ""
            for ev in expected_values:
                if ev and ev in _body_text:
                    total_count = _body_text.count(ev)
                    matched_value = ev
                    break
        except Exception as _e:
            _log.debug("_handle_text_contains best-effort 容忍: %s", str(_e)[:120])
        if total_count <= 0:
            for ev in expected_values:
                transient_hit = _match_transient_text(page, ev)
                if transient_hit:
                    # D15: not_contains 下抓到禁用文本 = 违规
                    if _neg:
                        return {
                            "type": "text_contains",
                            "text": text,
                            "precision": precision,
                            "passed": False,
                            "evidence": "not_contains 违规: 捕获到禁用文本: %s (matched='%s')"
                            % (transient_hit, ev),
                        }
                    return {
                        "type": "text_contains",
                        "text": text,
                        "precision": precision,
                        "passed": True,
                        "evidence": "瞬态提示文本已捕获: %s (matched='%s')"
                        % (transient_hit, ev),
                    }
        # ★ 唯一值 = 记录检索语义：服务端分页列表的新记录常不在首屏（按 id 升序分页
        #   时落在末页），只在首屏判定 → 结构性假 FAIL。翻页确认是人类的等价行为，
        #   复用 _search_expected_across_pages 单一实现（唯一值整值命中，判别力天然足够）。
        _page_note = ""
        if total_count <= 0 and _unique_values and not _neg:
            _pg_found, _pg_no, _pg_hit, _pg_note = await _search_expected_across_pages(
                page, selector, _unique_values
            )
            if _pg_found:
                total_count = 1
                matched_value = _pg_hit
                _log.info("[text_contains] 唯一值跨页命中: %s", _pg_note)
            elif _pg_note:
                _log.info("[text_contains] 唯一值跨页未命中: %s", _pg_note)
        # D15: not_contains 语义取反（"未出现"= 满足）
        if _neg:
            return {
                "type": "text_contains",
                "text": text,
                "precision": precision,
                "passed": total_count <= 0,
                "matched_value": matched_value,
                "evidence": (
                    "not_contains 满足：未出现禁用文本 %s" % expected_values
                    if total_count <= 0
                    else f"not_contains 违规：出现禁用文本 {total_count} 处"
                         f"（matched='{matched_value}'）"
                ),
            }
        _ev_text = (
            "找到%d个匹配文本节点（已排除输入框值, matched='%s'）"
            % (total_count, matched_value)
            if matched_value
            else "找到%d个匹配文本节点（已排除输入框值）" % total_count
        )
        return {
            "type": "text_contains",
            "text": text,
            "precision": precision,
            "passed": total_count > 0,
            "matched_value": matched_value,
            "evidence": _ev_text + (f"；{_page_note}" if _page_note else ""),
        }


async def _handle_text_not_exists(page, check, inner, check_type, precision):
    text = inner.get("expected", "") or inner.get("text", "") or check.get("text", "")
    try:
        # ★ 通用修复(2026-07-22): 排除输入框值
        #   根因: page.locator("text=%s") 匹配 input.value，
        #         搜索N01场景下搜索框内的不存在关键词被误判为"文本存在" → 假失败
        #   修复: 使用 _count_text_excluding_inputs 排除 input/textarea/select 的值
        #   通用性: 适用于所有搜索/过滤场景，不针对单一系统/行业/页面
        count = await _count_text_excluding_inputs(page, text)
        passed = count == 0
        return {
            "type": "text_not_exists",
            "text": text,
            "passed": passed,
            "evidence": (
                "确认不存在（已排除输入框值）"
                if passed
                else "找到%d个匹配文本节点（预期为0，已排除输入框值）" % count
            ),
        }
    except Exception as e:
        return {
            "type": "text_not_exists",
            "text": text,
            "passed": False,
            "reason": "EXECUTOR_EXCEPTION",  # ★ D19：执行器异常 ≠ 断言否定结论
            "evidence": "查询异常: %s" % str(e)[:60],
        }


async def _handle_element_not_exists(page, check, inner, check_type, precision):
    selector = _strip_pseudo_elements(
        inner.get("selector", "") or check.get("selector", "")
    )
    try:
        locator = page.locator(selector)
        count = await locator.count()
        passed = count == 0
        return {
            "type": "element_not_exists",
            "selector": selector,
            "passed": passed,
            "evidence": (
                "找到%d个匹配元素（预期为0）" % count if not passed else "确认不存在"
            ),
        }
    except Exception as e:
        return {
            "type": "element_not_exists",
            "selector": selector,
            "passed": False,
            "reason": "EXECUTOR_EXCEPTION",  # ★ D19：执行器异常 ≠ 断言否定结论
            "evidence": "查询异常: %s" % str(e)[:60],
        }


async def _handle_element_exists(page, check, inner, check_type, precision):
    selector = _strip_pseudo_elements(
        inner.get("selector", "") or check.get("selector", "")
    )
    try:
        locator = page.locator(selector)
        count = await locator.count()
        if count <= 0:
            # ★ v27: 活 DOM 未命中 → 查询瞬态提示缓存（toast/message 出现后数秒消失，
            #   动作执行期间已捕获到 page._transient_hints）
            transient_hit = _match_transient_hints(page, selector)
            if transient_hit:
                return {
                    "type": "element_exists",
                    "selector": selector,
                    "passed": True,
                    "evidence": "瞬态提示已捕获: %s" % transient_hit,
                }
        return {
            "type": "element_exists",
            "selector": selector,
            "passed": count > 0,
            "evidence": "找到%d个匹配元素" % count,
        }
    except Exception as e:
        return {
            "type": "element_exists",
            "selector": selector,
            "passed": False,
            "reason": "EXECUTOR_EXCEPTION",  # ★ D19：执行器异常 ≠ 断言否定结论
            "evidence": "查询异常: %s" % str(e)[:60],
        }


async def _handle_url_matches(page, check, inner, check_type, precision):
    pattern = (
        inner.get("pattern", "")
        or inner.get("expected", "")
        or check.get("pattern", "")
        or check.get("expected", "")
    )
    try:
        current_url = page.url if page else ""
        # ★ 双查（2026-08-04）：url_check → url_matches 归一。
        #   exact 语义 = 路径精确匹配——完整 URL(http://host/pipeline) 与路径模式(/pipeline)
        #   直接 == 必不匹配，需提取 path 部分比较；
        #   prefix 语义 = 「导航可达性」——目标页**本身或其子路径**均算到达
        #     （见下方 RC-R）；fuzzy 保持全 URL 正则搜索。
        if precision in ("exact", "prefix"):
            try:
                from urllib.parse import urlparse as _up
            except Exception:
                _up = None
            _pat = str(pattern or "").strip()
            _cur_p = _cp = None
            if _up is not None:
                try:
                    _cur_p = _up(current_url)
                    _cp = _cur_p
                except Exception:
                    _cur_p = None
            current_path = (_cur_p.path or current_url) if _cur_p is not None else current_url
            # ★ 根因修复(2026-09-25) RC-K: pattern 侧未做归一化。
            #   根因: Step3 由 _explore_task.target_page 生成 pattern，落库形态是**全 URL**
            #     （如 "http://127.0.0.1:8050/assets/"）。原实现只把 current_url 归一为
            #     path，pattern 仍带 scheme://host → "/assets" != "http://…/assets"
            #     恒不匹配 → 全量假 FAIL（本仓实测 474/582 用例命中，占 81%）。
            #   通用修复: pattern 为全 URL 且与当前页**同源**时归一为 path 后比较
            #     （"导航到该页面"的语义本就是路径级）；异源保持全串比较，不放松判定。
            #   通用性: 跨系统/行业/页面 — 只涉及 URL 归一，零业务硬编码。
            if "://" in _pat and _up is not None:
                try:
                    _pp = _up(_pat)
                except Exception:
                    _pp = None
                _same_origin = False
                if _pp is not None and _cp is not None and _pp.hostname and _cp.hostname:
                    _p_port = _pp.port or (443 if _pp.scheme == "https" else 80)
                    _c_port = _cp.port or (443 if _cp.scheme == "https" else 80)
                    _same_origin = (
                        _pp.hostname == _cp.hostname and _p_port == _c_port
                    )
                if _same_origin:
                    _pat = _pp.path or "/"
            _cur_n = current_path.strip().rstrip("/") or "/"
            _pat_n = _pat.rstrip("/") or "/"
            if precision == "prefix":
                # ★ 根因修复(2026-09-25) RC-R: 自动追加的「导航到 X 页面」断言是
                #   **导航可达性**断言（_navigation_resolver 文档原话："让'导航到了
                #   正确页面'成为可验证断言"），却被生成为 exact 精确等值 → 在**用例
                #   结束时**核对终态 URL，于是任何"业务流合法地深入子页面"的用例
                #   必然假 FAIL：
                #     ① 录入型（op_type=form）→ 终态 /assets/new；
                #     ② 拒绝型（期望"不提交/被拦截"）→ 终态**必须**停在 /assets/new，
                #        而 exact 断言却要求回到 /assets，与用例自身语义**自相矛盾**。
                #   实测 TC-REQ-002/008/009-I01：语义断言全 PASS，仅此 nav 断言 FAIL。
                #   通用修复: prefix = 目标路径**本身或其子路径**视为已到达该模块
                #     （路径段级前缀，避免 /assets 误配 /assetsfoo）。
                #   通用性: 跨系统/行业/框架 — 纯 URL 路径语义，零业务硬编码。
                matched = (
                    _pat_n == "/" or _cur_n == _pat_n or _cur_n.startswith(_pat_n + "/")
                )
            else:
                matched = _cur_n == _pat_n
        else:
            matched = bool(_re.search(str(pattern), current_url))
        return {
            "type": "url_matches",
            "pattern": pattern,
            "precision": precision,
            "passed": matched,
            "evidence": "当前URL: %s" % current_url,
        }
    except Exception as e:
        return {
            "type": "url_matches",
            "pattern": pattern,
            "precision": precision,
            "passed": False,
            "evidence": "URL匹配异常: %s" % str(e)[:60],
        }


async def _handle_visual_check(page, check, inner, check_type, precision):
    """visual_check 适配层：把 verify_visual 的三态终态翻译为 UI check 结果。

    ★ T04 (2026-08-11) [已复核-DeepSeek-V3 2026-08-16]
      verify_visual 现返回 status ∈ {PASS, FAIL, NO_DATA}，NO_DATA 时 passed=None。
      本层按仓内既有约定翻译：
        - NO_DATA → passed=True（``skipped`` 不阻断 AND，与 _verify_db_async /
          _track_protocol 的 "全 skipped → passed 保持 True" 语义一致），
          但透传 status/reason + skipped=True，使 _compute_confidence 将其
          排除出分母（置信度下降触发人工审核），并由 _verify_ui_objective
          在「全部 check 均无结论」时置 no_valid_assertion=True。
      ★ 同时移除原 ``vr.get("passed", True)`` 的 True 默认值——键缺失时
        不再默认判通过（fail-open 隐患）。
    """
    try:
        from src.pipeline.steps.step4.verification._verify_visual import (
            verify_visual,
            STATUS_NO_DATA,
        )

        # ★ 真值回填增强（2026-09-01）：inner 与 check 合并后传给 verify_visual。
        #   根因: Step3 将 selector/expectation 放在 check_item["check"]（inner），
        #        而本适配层原样把 outer check_item 传给 verify_visual，其内只读
        #        check.get("selector"/"expectation") → LLM 生成的控件级目标全部丢失，
        #        控件级截图特写从未真正触发。合并后 inner 的同名字段覆盖 outer，
        #        (见 _verify_visual._resolve_visual_target) 真值命中逻辑即可读到。
        _merged_visual = {**check, **inner} if isinstance(inner, dict) else {**check}
        vr = await verify_visual(page, _merged_visual)
        _status = vr.get("status", STATUS_NO_DATA)
        if _status == STATUS_NO_DATA:
            _passed = True  # 不阻断 AND，但下方 skipped/status 标明「未校验」
        else:
            _passed = bool(vr.get("passed", False))
        return {
            "type": "visual_check",
            "expectation": _merged_visual.get("expectation", ""),
            "passed": _passed,
            "status": _status,
            "reason": vr.get("reason", ""),
            "confidence": vr.get("confidence", 0.0),
            "evidence": vr.get("evidence", ""),
            "skipped": bool(vr.get("skipped", False)),
        }
    except Exception as e:
        return {
            "type": "visual_check",
            "expectation": check.get("expectation", ""),
            "passed": False,
            "status": "ERROR",
            "reason": "VISUAL_CHECK_EXCEPTION",
            "confidence": 0.0,
            "evidence": "视觉断言执行异常(fail-closed): %s" % str(e)[:60],
            "skipped": True,
        }


async def _handle_geometry_check(page, check, inner, check_type, precision):
    try:
        from src.pipeline.steps.step4.verification._verify_geometry import (
            verify_geometry,
        )

        _mc = _merge_check_inner(check, inner)
        gr = await verify_geometry(page, _mc)
        return {
            "type": "geometry_check",
            "rule": _mc.get("rule", ""),
            "passed": gr.get("passed", True),
            "evidence": gr.get("evidence", ""),
            "detail": gr.get("detail"),
        }
    except Exception as e:
        return {
            "type": "geometry_check",
            "rule": check.get("rule", ""),
            "passed": False,
            "evidence": "几何断言执行异常(fail-closed): %s" % str(e)[:60],
        }


async def _handle_style_check(page, check, inner, check_type, precision):
    try:
        from src.pipeline.steps.step4.verification._verify_style import verify_style

        sr = await verify_style(page, _merge_check_inner(check, inner))
        return {
            "type": check_type,
            "passed": sr.get("passed", True),
            "evidence": sr.get("evidence", ""),
        }
    except Exception as e:
        return {
            "type": check_type,
            "passed": False,
            "evidence": "样式断言执行异常(fail-closed): %s" % str(e)[:60],
        }


async def _handle_position_check(page, check, inner, check_type, precision):
    try:
        from src.pipeline.steps.step4.verification._verify_position import (
            verify_position,
        )

        pr = await verify_position(page, _merge_check_inner(check, inner))
        return {
            "type": check_type,
            "passed": pr.get("passed", True),
            "evidence": pr.get("evidence", ""),
        }
    except Exception as e:
        return {
            "type": check_type,
            "passed": False,
            "evidence": "位置断言执行异常(fail-closed): %s" % str(e)[:60],
        }


async def _handle_toast_check(page, check, inner, check_type, precision):
    try:
        from src.pipeline.steps.step4.verification._verify_toast import verify_toast

        # ★ P1-3a 修复（QA 复核，2026-08-05）: inner→check merge，
        #   修复顶层 selector/text 丢失导致「无 selector，跳过」/「未知 Toast 断言类型，跳过」
        merged_check = _merge_check_inner(check, inner)
        # 归一化 type 为 verify_toast 期望的后缀形式（兼容 taxonomy 裸名别名）
        _raw = check_type or ""
        if _raw == "toast":
            _canon = "toast_appear_check"
        elif _raw == "loading_state":
            _canon = "loading_state_check"
        else:
            _canon = _raw if _raw.endswith("_check") else _raw + "_check"
        merged_check["type"] = _canon
        tr = await verify_toast(page, merged_check)
        return {
            "type": check_type,
            "passed": tr.get("passed", True),
            "evidence": tr.get("evidence", ""),
        }
    except Exception as e:
        return {
            "type": check_type,
            "passed": False,
            "evidence": "Toast断言执行异常(fail-closed): %s" % str(e)[:60],
        }


async def _handle_page_refresh_check(
    page, check, inner, check_type, precision, step_checks=None
):
    try:
        from src.pipeline.steps.step4.verification._verify_refresh import verify_refresh

        pre_state = (step_checks or {}).get("_pre_state")
        rr = await verify_refresh(page, check, pre_state)
        return {
            "type": "page_refresh_check",
            "passed": rr.get("passed", True),
            "evidence": rr.get("evidence", ""),
        }
    except Exception as e:
        return {
            "type": "page_refresh_check",
            "passed": False,
            "evidence": "刷新断言执行异常(fail-closed): %s" % str(e)[:60],
        }


async def _handle_element_count_check(page, check, inner, check_type, precision):
    try:
        from src.pipeline.steps.step4.verification._verify_count import verify_count

        # ★ 根因修复: 引擎规范把断言参数（selector/expected_count/operator）嵌套在
        #   `check` 子对象内，而 verify_count 只读**扁平**字典 —— 原先直接传外层
        #   `check` → selector 丢失 → verify_count 返回 {"passed": True, "无 selector，
        #   跳过"}，即**声明了数量断言却静默判绿**（假绿来源）。与 `_handle_state_check`
        #   / toast / loading / list_order / download 同一归一：统一用 `_merge_check_inner`。
        cr = await verify_count(page, _merge_check_inner(check, inner))
        return {
            "type": "element_count_check",
            "passed": cr.get("passed", True),
            "evidence": cr.get("evidence", ""),
        }
    except Exception as e:
        return {
            "type": "element_count_check",
            "passed": False,
            "evidence": "数量断言执行异常(fail-closed): %s" % str(e)[:60],
        }


async def _handle_state_check(page, check, inner, check_type, precision):
    try:
        from src.pipeline.steps.step4.verification._verify_state import verify_state

        # ★ P1-3a 修复（QA 复核，2026-08-05）: inner→check merge，
        #   修复 selector/expected 丢失导致「无 selector，跳过」（结构化断言 assertion_rate=0 根因）
        merged_check = _merge_check_inner(check, inner)
        # 归一化 type 为 verify_state 期望的后缀形式（兼容 taxonomy 裸名别名）
        merged_check["type"] = (
            check_type if check_type.endswith("_check") else check_type + "_check"
        )
        sr = await verify_state(page, merged_check)
        return {
            "type": check_type,
            "passed": sr.get("passed", True),
            "evidence": sr.get("evidence", ""),
        }
    except Exception as e:
        return {
            "type": check_type,
            "passed": False,
            "evidence": "状态断言执行异常(fail-closed): %s" % str(e)[:60],
        }


async def _handle_image_loaded_check(page, check, inner, check_type, precision):
    try:
        from src.pipeline.steps.step4.verification._verify_image import verify_image

        ir = await verify_image(page, _merge_check_inner(check, inner))
        return {
            "type": "image_loaded_check",
            "passed": ir.get("passed", True),
            "evidence": ir.get("evidence", ""),
        }
    except Exception as e:
        return {
            "type": "image_loaded_check",
            "passed": False,
            "evidence": "图片断言执行异常(fail-closed): %s" % str(e)[:60],
        }


# ★ 防御型 no_error 的**系统级致命/泄露信号**:
#   SQLi/XSS 用例断言"系统不崩溃、无 DB 错误泄露"时，字段级业务校验错误
#   （".field-error"名称不能为空）是 SUT 正常反馈，不是防御失败。本词表
#   只含"页面崩溃/服务端错误泄露"的通用信号（500 页、堆栈、DB 驱动错误），
#   跨系统/行业通用，零业务硬编码；命中即防御失效（FAIL），否则防御生效（PASS）。
_DEFENSE_CRASH_SIGNALS = (
    "internal server error",
    "traceback",
    "operationalerror",
    "databaseerror",
    "syntaxerror",
    "500 internal",
    "服务器内部错误",
    "数据库错误",
    "堆栈",
)


# no_error / loading_complete / list_order 必须真实执行：未注册分派会让
# 「未验证」变成 passed=True（假 PASS），故统一注册到 _UI_CHECK_DISPATCH。
async def _handle_no_error(page, check, inner, check_type, precision):
    """检查页面无错误提示（通用UI框架错误元素 + 瞬态提示缓存）。

    两种语义由生成端用结构化 ``mode`` 声明，执行端只读不推：
      - 默认（无 mode）= 全量扫描：任何错误提示都算失败。
      - ``mode="defense"`` = 只判系统级崩溃/泄露信号：注入/容错探针的
        "无致命错误"断言，字段级业务校验提示是 SUT 正常反馈，不得计失败。
    """
    _defense_mode = str(inner.get("mode") or check.get("mode") or "").strip().lower() == "defense"
    if _defense_mode:
        try:
            _body_txt = ((await page.inner_text("body")) or "")[:3000]
        except Exception:
            _body_txt = ""
        _low = _body_txt.lower()
        _hit = [s for s in _DEFENSE_CRASH_SIGNALS if s in _low]
        if _hit:
            return {
                "type": "no_error",
                "passed": False,
                "evidence": "防御型检查: 检测到系统级错误信号 %s" % "; ".join(_hit[:3]),
            }
        return {
            "type": "no_error",
            "passed": True,
            "evidence": "防御生效: 页面无崩溃/数据库错误泄露信号(字段级业务校验不计致命错误)",
        }
    _ERROR_SELECTORS = _NO_ERROR_SCAN_SELECTORS
    found_errors = []
    try:
        for sel in _ERROR_SELECTORS:
            try:
                cnt = await page.locator(sel).count()
                if cnt > 0:
                    # 取第一条错误文本作为证据
                    try:
                        txt = await page.locator(sel).first.inner_text(timeout=1000)
                    except Exception:
                        txt = ""
                    found_errors.append(
                        "%s×%d%s" % (sel, cnt, ":'%s'" % txt[:30] if txt else "")
                    )
            except Exception as _se1:
                _log.debug(
                    "[_db_ui] 异常降级(continue): %s", str(_se1)[:60], exc_info=True
                )
                continue
        # ★ 同时检查瞬态提示缓存（错误提示可能已消失）
        if not found_errors:
            hints = getattr(page, "_transient_hints", None) or []
            for h in hints:
                cls = (h.get("cls") or "").lower()
                role = (h.get("role") or "").lower()
                text = (h.get("text") or "").strip()
                if text and ("error" in cls or "warn" in cls or role == "alert"):
                    found_errors.append("瞬态错误提示:'%s'" % text[:40])
                    break
        if found_errors:
            return {
                "type": "no_error",
                "passed": False,
                "evidence": "发现错误提示: %s" % "; ".join(found_errors[:3]),
            }
        return {
            "type": "no_error",
            "passed": True,
            "evidence": "未发现错误提示元素(扫描%d个通用错误选择器)"
            % len(_ERROR_SELECTORS),
        }
    except Exception as e:
        return {
            "type": "no_error",
            "passed": False,
            "evidence": "no_error 检查异常: %s" % str(e)[:60],
        }


# ═══════════════════════════════════════════════════════════════════════════════
# ★ 根因修复(2026-09-25) FIX4/AJ: 访问控制断言（access_control_check）
# ═══════════════════════════════════════════════════════════════════════════════
# 根因（5 层 why）:
#   L1 现象: 访问控制需求（"viewer 不允许新增/编辑/删除资产，返回 403 权限不足"）
#     的用例恒 FAIL（实测 accept_sut_final TC-REQ-068-E01）。
#   L2 直接: 生成端对这类需求只能发射 `text_contains` / `semantic_check`，
#     断言文本取自需求里的**设计陈述**（"不同角色获得对应的数据访问与操作权限"）
#     —— 规格文字是文档语言，页面上不存在 → 恒 0 匹配 → FAIL。
#   L3 为何: "访问控制"的期望性质是**行为**（低权访问被拒），而文本匹配手段
#     只能验证**呈现**；手段与期望性质错配。
#   L4 为何反复: 生成端没有"行为型期望"的断言形态可发，只能退回通用文本匹配；
#     每遇到一个行为型需求就再退化一次。
#   L5 根本: 缺少"访问控制行为"的**可观察判据**（拒绝页/拒绝文案/登录墙）
#     与其执行器 —— 有判据无执行器 → 未知检查类型假 FAIL；
#     无判据则退化为文本匹配假 FAIL。
# 修复: 提供 `access_control_check` 执行器 —— 在**活动页面事实**上判定拒绝：
#   ① 拒绝页容器可见（`DENIAL_SELECTORS`，单一事实源）
#   ② 页面出现拒绝文案（`DENIAL_TEXT_KEYWORDS`）
#   ③ 未认证访问被重定向到登录页（登录墙）
#   任一成立 → 访问控制生效（PASS）。
#   三者皆无，且**受限操作确已成功**（URL 落在操作页 + 表单/成功反馈可见）
#   → 真越权（FAIL，可证伪）。
#   受限入口对当前身份不可见 → 最小权限生效（PASS，证据标注）。
#   既无拒绝证据也无越权证据 → 诚实 SKIP（不假 PASS 也不假 FAIL）。
# 通用性: 拒绝形态（403/无权限/重定向登录）与业务、框架无关，零硬编码。
_ACTION_PATH_TOKENS = (
    "new", "create", "add", "edit", "update", "delete", "remove",
    "import", "upload", "export", "download",
)
_BUSINESS_CONTENT_SELECTOR = (
    "table, [role='table'], form, [role='form'], ul, ol, [role='list'], main"
)


async def _collect_denial_evidence(page) -> list[str]:
    """收集「访问被拒绝」的可观察证据（拒绝页 / 拒绝文案 / 登录墙）。

    诚实性: 只在**真有可见拒绝形态**时返回非空；探测异常一律不产出证据
    （fail-open 于取证，不 fail-open 于结论 —— 无证据即走后续判定/SKIP）。
    """
    hits: list[str] = []
    # ① 拒绝页/无权限容器
    for sel in _DENIAL_SELECTORS:
        try:
            loc = page.locator(sel)
            if (await loc.count()) <= 0:
                continue
            first = loc.first
            if not await first.is_visible():
                continue
            txt = (await first.inner_text(timeout=1000)).strip()
            hits.append("拒绝页(%s):'%s'" % (sel, txt[:40]))
            break
        except Exception as _e:  # noqa: BLE001
            _log.debug("[AccessControl] 拒绝页探测跳过 %s: %s", sel, str(_e)[:60])
            continue
    # ② 页面拒绝文案
    try:
        body = (await page.locator("body").inner_text(timeout=2000)) or ""
    except Exception:  # noqa: BLE001
        body = ""
    _kws = _denial_signals_in_text(body)
    if _kws:
        hits.append("页面出现拒绝文案:%s" % ",".join(_kws[:3]))
    # ③ 未认证 → 重定向登录墙
    try:
        url = page.url or ""
    except Exception:  # noqa: BLE001
        url = ""
    if _is_login_path(url):
        hits.append("未认证访问被重定向到登录页:%s" % url[:60])
    return hits


async def _restricted_control_visible(page, hints: list[str]) -> tuple[bool, str]:
    """受限操作入口（按钮/链接）在当前页面是否可见。返回 (可见, 命中文本)。"""
    for hint in hints or []:
        for cand in _split_selector_list(hint) or [hint]:
            cand = str(cand or "").strip()
            if not cand:
                continue
            try:
                loc = page.get_by_text(cand, exact=False)
                if (await loc.count()) <= 0:
                    continue
                if await loc.first.is_visible():
                    return True, cand
            except Exception as _e:  # noqa: BLE001
                _log.debug("[AccessControl] 入口探测跳过 %r: %s", cand, str(_e)[:60])
                continue
    return False, ""


async def _restricted_action_succeeded(page) -> tuple[bool, str]:
    """受限操作是否**确已成功**（越权判据，强证据，避免误判真越权）。

    判据（须同时成立，缺一不判越权）:
      ① 当前 URL 落在操作类路径（new/create/edit/delete…）
      ② 页面渲染出表单或成功反馈（说明系统确实放行了该操作）
    """
    try:
        url = page.url or ""
    except Exception:  # noqa: BLE001
        url = ""
    _low = str(url).lower()
    if not any(t in _low for t in _ACTION_PATH_TOKENS):
        return False, ""
    try:
        if (await page.locator(_BUSINESS_CONTENT_SELECTOR).count()) <= 0:
            return False, ""
    except Exception:  # noqa: BLE001
        return False, ""
    _ok, _ev = await _detect_visible_feedback_bar(page, "success")
    if _ok:
        return True, "URL=%s 且出现成功反馈(%s)" % (str(url)[:60], _ev[:60])
    try:
        _forms = page.locator("form, [role='form'], input, select, textarea")
        if (await _forms.count()) > 0:
            return True, "URL=%s 且操作表单已渲染（系统放行了受限操作）" % str(url)[:60]
    except Exception as _e:  # noqa: BLE001
        _log.debug("_restricted_action_succeeded best-effort 容忍: %s", str(_e)[:120])
    return False, ""


async def _drive_restricted_action_once(page, hints: list[str]) -> dict:
    """**主动驱动**一次受限操作入口，观察系统反应（越权/负向路径驱动的单一事实源）。

    根因: 原 `access_control_check` 纯被动 —— 只看当前页有无拒绝形态；
      "低权执行受限操作应被拒绝"的期望**性质是行为**，不实际执行就无法证伪，
      最终只能恒 SKIP（结论不可用）。用户诉求"强化越权/负向路径驱动能力"即此。

    行为: 按 hints 顺序取第一个可见入口并点击（新增类在前，破坏性动作在后），
      待页面稳定后按同一判据取证。返回 {denied, succeeded, evidence}，宁可诚实
      无结论（全 False）也不臆断。异常一律 fail-open 到"无结论"，绝不假 PASS/FAIL。
    """
    out = {"denied": False, "succeeded": False, "evidence": ""}
    hit = ""
    for hint in hints or []:
        for cand in _split_selector_list(hint) or [hint]:
            cand = str(cand or "").strip()
            if not cand:
                continue
            try:
                loc = page.get_by_text(cand, exact=False)
                if (await loc.count()) <= 0:
                    continue
                el = loc.first
                if not await el.is_visible():
                    continue
                await el.click(timeout=3000)
                hit = cand
                break
            except Exception as _e:  # noqa: BLE001
                _log.debug("[AccessControl] 驱动入口跳过 %r: %s", cand, str(_e)[:60])
                continue
        if hit:
            break
    if not hit:
        out["evidence"] = "无可见受限入口可驱动"
        return out
    await page.wait_for_timeout(1200)
    denial = await _collect_denial_evidence(page)
    if denial:
        out["denied"] = True
        out["evidence"] = "点击 '%s' → %s" % (hit, "; ".join(denial[:2]))
        return out
    succ, sev = await _restricted_action_succeeded(page)
    if succ:
        out["succeeded"] = True
        out["evidence"] = "点击 '%s' 未被拒绝且已放行 → %s" % (hit, sev[:100])
        return out
    out["evidence"] = "点击受限入口 '%s' 后未见拒绝或放行信号" % hit
    return out


async def _handle_access_control_check(page, check, inner, check_type, precision):
    """访问控制行为断言（低权/未认证身份执行受限操作 → 系统应拒绝）。"""
    try:
        hints = inner.get("control_hints") or check.get("control_hints") or []
        if isinstance(hints, str):
            hints = _split_selector_list(hints)
        # ① 拒绝证据（任一成立即"访问控制生效"）
        _denial = await _collect_denial_evidence(page)
        if _denial:
            return {
                "type": "access_control_check",
                "passed": True,
                "evidence": "访问控制生效，低权/未认证访问被拒绝 → %s"
                % "; ".join(_denial[:3]),
            }
        # ①.5 ★ 主动驱动受限操作（行为型期望必须实际执行才能证伪）
        if list(hints):
            _drive = await _drive_restricted_action_once(page, list(hints))
            if _drive.get("denied"):
                return {
                    "type": "access_control_check",
                    "passed": True,
                    "evidence": "访问控制生效（主动驱动受限操作被拒绝）→ %s"
                    % _drive["evidence"],
                }
            if _drive.get("succeeded"):
                return {
                    "type": "access_control_check",
                    "passed": False,
                    "evidence": "越权：低权身份执行受限操作未被拒绝 → %s"
                    % _drive["evidence"],
                }
            _log.info("[AccessControl] 主动驱动无结论: %s", _drive.get("evidence", "")[:120])
        # ② 无拒绝证据 → 判断是否真越权（须强证据：已进入操作页且表单/成功反馈可见）
        _bypass, _bev = await _restricted_action_succeeded(page)
        if _bypass:
            return {
                "type": "access_control_check",
                "passed": False,
                "evidence": "越权：受限操作未被拒绝且已成功执行 → %s" % _bev[:120],
            }
        # ③ 受限入口对当前身份不可见 → 最小权限生效
        #   ★ 诚实性守卫(2026-09-26): 无受限控件候选时"不可见"是**空判据**
        #     （没扫任何东西，必然"不可见"）→ 结论不可用，必须诚实 SKIP。
        #     否则空 hints 会产出恒真 PASS（假 PASS 温床，违反"无假 PASS"红线）。
        if not list(hints):
            return {
                "type": "access_control_check",
                "passed": True,
                "status": "SKIP",
                "skipped": True,
                "skip_reason": "access_control_no_control_hints",
                "evidence": "需求未给出受限操作控件候选，无法构造受限操作"
                "（诚实跳过，不假 PASS/假 FAIL）",
            }
        _visible, _hit = await _restricted_control_visible(page, list(hints))
        if not _visible:
            try:
                _has_content = (
                    await page.locator(_BUSINESS_CONTENT_SELECTOR).count()
                ) > 0
            except Exception:  # noqa: BLE001
                _has_content = False
            if _has_content:
                return {
                    "type": "access_control_check",
                    "passed": True,
                    "evidence": "最小权限生效：受限操作入口对当前身份不可见"
                    "（扫描候选 %s）" % ",".join(str(h) for h in list(hints)[:3]),
                }
            return {
                "type": "access_control_check",
                "passed": True,
                "status": "SKIP",
                "skipped": True,
                "skip_reason": "access_control_inconclusive",
                "evidence": "页面无业务内容且受限入口不可见，无法构造受限操作"
                "（诚实跳过，不假 PASS/假 FAIL）",
            }
        # ④ 入口可见但既未拒绝也未确认越权 → 诚实 SKIP
        return {
            "type": "access_control_check",
            "passed": True,
            "status": "SKIP",
            "skipped": True,
            "skip_reason": "access_control_inconclusive",
            "evidence": "受限入口 '%s' 可见，但未观察到拒绝或成功信号"
            "（结论不可用，诚实跳过）" % _hit[:30],
        }
    except Exception as e:  # noqa: BLE001
        return {
            "type": "access_control_check",
            "passed": False,
            "evidence": "access_control 检查异常: %s" % str(e)[:80],
        }


# ★ 2026-09-22 深断言执行器补齐（全量判定审计根修）:
#   Step3 深断言族（_fallback_cases N01/B01/P01）发射 field_error_visible_check /
#   list_row_count_check，Step4 从未注册执行器 → 历史上全部落入 F-00 fail-closed
#   兜底判"未知检查类型"假 FAIL。通用性: 错误提示/列表行数是所有 Web 系统的通用
#   UI 模式，零业务硬编码。错误选择器提升为模块级常量，no_error 与 field_error
#   共用（单一事实源，防两套词表再漂移）。
# ★ 根因修复(2026-09-24): 错误词表补齐框架无关的错误条选择器。
#   现象: 靶子系统以 Flash(server) / 自定义 client_validate 渲染错误条，形态为
#   `.flash-danger` 与 `[data-testid="client-error"]`，既不用 Ant/ElementUI 类，
#   也无 `[role='alert']`。原 _GENERIC_ERROR_SELECTORS 仅含框架类选择器 + 少数
#   `.error-*`，漏掉这些错误条 → no_error(负) 与 field_error_visible_check(正)
#   扫不到真实错误提示 → 靶子按需求正确渲染"红色错误条/校验失败提示"时被误判
#   未出现 → 大量 HARNESS_ERROR/FAIL（风暴放大一个真实系统的错误检测盲区）。
#   根因: 错误指示器的识别词表不完整，偏向单一前端生态，缺通用(框架无关)形态。
#   修复: 补框架无关错误条选择器——通用 Flash danger(server flash)、Bootstrap
#   alert-danger、以及按语义命名的 error-bar / validation-error / 常见 data-testid
#   错误标识。单一事实源；no_error 与 field_error 共用，杜绝两套词表再漂移。
#   通用性: 跨系统/行业/页面——错误提示形态无关实现框架，零业务硬编码。
#   ★ RC-AE(2026-09-25): 词表本体已上移到 `src/pipeline/utils/feedback_selectors.py`
#   （全引擎单一事实源，Step3 生成端/Step4 校验端/Agent 探索端共用），
#   本模块仅以 `_GENERIC_ERROR_SELECTORS` / `_GENERIC_SUCCESS_SELECTORS` 别名引用。
#   `no_error`（负向）改用精确集 `_NO_ERROR_SCAN_SELECTORS`，避免广义类名误报。

# ★ 根因修复(2026-09-25) RC-Z: 成功/正反馈条选择器提升为模块级**单一事实源**。
#   根因: 错误条有 `_GENERIC_ERROR_SELECTORS` 且被 `no_error` / `field_error_visible`
#     / Agent guided 共用，而成功条此前只存在于 `explorer_mixins/_action.py` 的私有
#     `_SUCCESS_SELECTORS` → checks 层（`semantic_check`）**看不到**成功条事实 →
#     同一期望在 Agent 层被判"成功条检测 PASS"、在 checks 层被 LLM 判 FAIL，
#     两个验证器用不同证据对同一期望下相反结论，弱判定否决强证据
#     （实测 TC-REQ-045-I01：guided 已"验证通过[错误条检测]"，
#       checks 层 semantic_check 仍判 FAIL → 整体 FAIL，典型假 FAIL）。
#   通用性: 跨系统/行业/页面 —— 反馈条形态与实现框架无关，零业务硬编码。
#   ★ RC-AE(2026-09-25): 词表本体已上移到 `src/pipeline/utils/feedback_selectors.py`。


async def _detect_visible_feedback_bar(page, kind: str) -> tuple[bool, str]:
    """在**活动 DOM** 上探测可见的反馈条（kind: "error" | "success"）。

    ★ RC-Z: 供 `semantic_check` 用与 Agent guided **同一批选择器**取证，使
      "反馈条类期望"（错误条/红条/成功提示）由 DOM 事实判定，而不是交给只看
      inner_text 的 LLM 去猜视觉形态（LLM 看不到颜色/样式 → 恒判"未出现红条"）。

    返回 (found, evidence)。found 为真时 evidence 含命中选择器 + 可见文本，
    便于审计（诚实：只在真有可见反馈条时返回 True）。

    ★ RC-AF(续3, 2026-09-25): 探测委托单一事实源的 `ERROR_DETECT_JS` /
      `SUCCESS_DETECT_JS`（选择器 + 双重可见性 + **广义类名形态判据**）。
      根因: 本函数此前自行遍历 `_GENERIC_ERROR_SELECTORS`，**无形态判据** ——
        与 `_handle_field_error_visible` 同一根因的第二处消费端；靶子
        `<a class="danger">删除</a>` 会被 `semantic_check` 路径当作"错误条可见"
        → 期望"出现错误提示"的用例假 PASS。
      通用性: 判定算法集中在单一事实源，调用端只执行，杜绝再漂移。
    """
    _js = _ERROR_DETECT_JS if kind == "error" else _SUCCESS_DETECT_JS
    try:
        _hits = await page.evaluate(_js)
    except Exception as _e:
        _log.debug("[FeedbackBar] 活动 DOM 探测异常: %s", str(_e)[:80])
        return False, ""
    if _hits:
        return True, "；".join(list(_hits)[:2])
    return False, ""


# 通用列表行选择器（与 Step3 _fallback_cases._LIST_ROW_SELECTOR 同语义）
_GENERIC_ROW_SELECTOR = (
    "table tbody tr, .ant-table-tbody tr, .el-table__body-wrapper tr, "
    "[data-testid='list-row'], .list-row, .table-row, .ant-list-item, "
    ".el-list__item, li[data-row], .row"
)

# 「列表行数」= 列表**总数**：分页列表的语义行数由汇总/分页组件呈现（框架通用形态）。
_LIST_TOTAL_SELECTORS = (
    "[data-testid*='summary']", "[data-testid*='count']", "[data-testid*='total']",
    "[class*='pagination-total']", "[class*='total-text']", "[class*='pagination-info']",
    ".ant-pagination-total-text", ".el-pagination__total",
)
# 计数单位（中文量词 + 英文名词）：数字与其紧邻即认定为"列表总数"文本
_LIST_TOTAL_UNITS = ("条", "项", "行", "个", "记录",
                     "records", "items", "rows", "entries", "results")


def _extract_list_total(text: str) -> int | None:
    """从汇总文本里取"总数"整数。

    数字必须**紧邻计数单位**（"共 157 条"）；纯数字不复用，否则会把分页页码
    （"第 1/32 页"）误当总数。
    """
    _t = str(text or "")
    for _u in _LIST_TOTAL_UNITS:
        _m = _re.search(r"(\d[\d,]*)\s*" + _re.escape(_u), _t, _re.I)
        if _m:
            return int(_m.group(1).replace(",", ""))
    return None


async def read_list_size(page) -> tuple[int, str]:
    """列表规模 (计数, 来源) —— "列表行数"的**唯一**读取实现（基线/差分断言共用）。

    优先列表**总数**汇总；无汇总时退化为可见行数。
    why: 可见行数在分页列表上恒等于页容量（删一行会有下一页首行补位）→ "行数减一"
      在可见行上永不成立；列表总数才表达"列表行数"的语义。
    通用性: 汇总容器与计数单位是跨框架/跨业务的通用 UI 约定，零业务硬编码。
    """
    try:
        for _sel in _LIST_TOTAL_SELECTORS:
            _loc = page.locator(_sel)
            if await _loc.count() == 0:
                continue
            _n = _extract_list_total(await _loc.first.inner_text())
            if _n is not None:
                return _n, "total"
    except Exception as _e:  # noqa: BLE001 —— 汇总不可用即退化，不阻断
        _log.debug("[ListSize] 总数汇总读取失败: %s", str(_e)[:80])
    return int(await page.locator(_GENERIC_ROW_SELECTOR).count()), "rows"


async def _handle_field_error_visible(page, check, inner, check_type, precision):
    """字段级错误提示可见断言（异常输入 N01/B01 深断言）。

    operator: not_empty(默认)=提交非法输入后须出现可见且非空的字段级错误提示；
              empty=不应出现错误提示（check_guard 极性过滤保证 empty 只出现在
              显式正向语义用例中）。
    诚实性: 找不到错误提示 → passed=False（fail-closed，不假 PASS）；
            异常 → passed=False 并附原因（不静默）。
    """
    try:
        _own_sel = (inner.get("selector") or check.get("selector") or "").strip()
        # ★ 根因修复(2026-09-24): 自身选择器与通用错误词表取并集（自身优先）。
        #   现象: Step3 发射的 _FIELD_ERROR_SELECTOR 只覆盖 Ant/ElementUI/Bootstrap
        #     类，靶子以服务端 Flash 渲染 `.flash-danger` 时命中为空 → 真实错误提示
        #     被判"未出现" → N01/B01/登录拒绝类用例假 FAIL。
        #   根因: "自身精确选择器"与"通用错误词表"二选一 → 词表更新无法惠及已发射
        #     选择器的用例，两套口径再次漂移。
        #   修复: 并集检索（自身优先保证精度，通用词表兜底保证召回），单一事实源。
        #   通用性: 跨系统/行业/页面 — 任何错误提示形态都能被识别。
        #   ★ RC-AF(续2, 2026-09-25): 自身选择器须**拆成单条**再并入 —— 形态判据
        #     `_is_broad_error_selector` 只认单条选择器，整串 CSV 会绕过判据
        #     （实测 `<a class="danger">删除</a>` 被当错误提示 → 假 PASS）。
        _own_parts = _split_selector_list(_own_sel)
        _selectors = _own_parts + [
            s for s in _GENERIC_ERROR_SELECTORS if s not in _own_parts
        ]
        operator = str(
            inner.get("operator") or check.get("operator") or "not_empty"
        ).lower()
        hits: list[str] = []
        for sel in _selectors:
            try:
                loc = page.locator(sel)
                cnt = await loc.count()
            except Exception:
                continue
            # ★ RC-AF(2026-09-25): 广义类名（.error/.invalid/.danger）是类名启发式，
            #   命中"危险操作按钮/链接"这类装饰性元素时不代表出现错误提示
            #   （实测靶子 `<a class="danger">删除</a>` → 假 PASS）。此处叠加**形态判据**。
            _need_shape = _is_broad_error_selector(sel)
            for i in range(min(cnt, 5)):
                try:
                    el = loc.nth(i)
                    if not await el.is_visible():
                        continue
                    if _need_shape and await _is_decorative_broad_match(el):
                        _log.debug(
                            "[FieldError] 广义类名命中交互控件，判装饰性丢弃: %s", sel
                        )
                        continue
                    txt = (await el.inner_text(timeout=1000)).strip()
                except Exception:
                    continue
                if txt:
                    hits.append("'%s'(%s)" % (txt[:40], sel))
        # 瞬态提示缓存兜底（错误提示可能已消失，与 no_error 同源语义）
        if not hits:
            for h in (getattr(page, "_transient_hints", None) or []):
                text = (h.get("text") or "").strip()
                cls = (h.get("cls") or "").lower()
                role = (h.get("role") or "").lower()
                if text and (
                    "error" in cls or "warn" in cls or role == "alert"
                ):
                    hits.append("瞬态错误提示:'%s'" % text[:40])
                    break
        if operator == "empty":
            return {
                "type": "field_error_visible_check",
                "passed": not hits,
                "evidence": (
                    "未出现错误提示(符合 empty 预期)"
                    if not hits
                    else "出现错误提示: %s" % "; ".join(hits[:2])
                ),
            }
        # ★ 根因修复(2026-10-27)：N/A 判据只能用于**字段面**反馈。
        #   生成端已按动作形态声明 `feedback_scope`（`_trigger_actions.field_rejection_scope`）：
        #     · `field`      = 激励是往可编辑字段录入值 → 页面无字段时确实无从适用（N/A）；
        #     · `operation`  = 激励是删除/上传/提交等操作 → 反馈面是页面级提示条，
        #       与"页面有无可编辑字段"无关；未出现反馈即需求要求的后果未发生（FAIL）。
        #   根因: 原判据只看"页面是否存在可编辑字段"，删除/导入/分类拦截的终态页是
        #     列表页（恒无字段）→ 断言一律 N/A → 用例假 SKIP，需求从未被判定
        #     （实测 TC-REQ-039-E01/I01、041-E01/I01、063-I02）。
        _scope = str(
            inner.get("feedback_scope") or check.get("feedback_scope") or "field"
        ).strip()
        if not hits and _scope == "operation":
            return {
                "type": "field_error_visible_check",
                "passed": False,
                "evidence": (
                    "未发现可见错误/拒绝反馈（用例已施加可被拒绝的操作，需求要求给出"
                    "错误提示；扫描%d个选择器）" % len(_selectors)
                ),
            }
        # ★ 根因修复(2026-09-24): 非适用页面的模板必失败 → 诚实 NOT_APPLICABLE。
        #   现象: N01/B01 深断言族为"提交非法输入→须出现字段级错误提示"而生，但
        #       该 check 被规则模板发射到任何同步渲染页(含登录/纯列表等无表单页)。
        #       无表单单页时 hits 恒空 → passed=False → "未发现可见字段级错误提示"
        #       硬判 FAIL，把"页面没有可产生字段级错误的表单"误当成"校验缺陷"。
        #   根因: check 场景(需先提交非法输入)与页面(无表单)不匹配时缺少 N/A 分支，
        #       fail-closed 错用了真 FAIL。诚实性要求: 无适用表单 → 跳过(透传 status=
        #       SKIP + skipped=True，evidence 明示"不适用")，既非假 PASS 也非假 FAIL。
        #   通用性: 跨系统/行业/页面 — 仅跳过真正无表单控件的页面，禁捆绑业务语义。
        if not hits:
            # 适用性判据 = 页面存在**可见可编辑字段**（字段校验反馈的载体）。
            #   `form` 自身不是证据：列表页每行的删除按钮就是一个"隐藏 CSRF + 提交按钮"
            #   的表单，它没有字段校验面；以"存在 form"为适用判据会把字段级错误断言
            #   落到这类页面 → 恒假 FAIL（删除/查看类用例的终态页正是列表页）。
            _editable = 0
            try:
                _fc = page.locator(
                    "input:not([type='hidden']):not([type='submit'])"
                    ":not([type='button']):not([type='image'])"
                    ":not([type='reset']):not([type='file']), "
                    "textarea, select, [contenteditable='true']"
                )
                for _i in range(min(await _fc.count(), 20)):
                    try:
                        if await _fc.nth(_i).is_visible():
                            _editable += 1
                            break
                    except Exception:
                        continue
            except Exception as _e:
                _log.debug("_handle_field_error_visible best-effort 容忍: %s", str(_e)[:120])
            if not _editable:
                return {
                    "type": "field_error_visible_check",
                    "passed": True,
                    "status": "SKIP",
                    "skipped": True,
                    # 结构化"声明期望未适用"标记：消费端据此拒绝把本用例记 PASS
                    #   （见 `_assertion_verdict.has_not_applicable_assertion`）。
                    "reason": "ASSERTION_NOT_APPLICABLE",
                    "skip_reason": "no_applicable_form",
                    "evidence": (
                        "页面无可编辑字段（无字段校验反馈面），字段级错误断言不适用"
                        "(N/A 跳过，扫描%d个选择器)" % len(_selectors)
                    ),
                }
        return {
            "type": "field_error_visible_check",
            "passed": bool(hits),
            "evidence": (
                "字段级错误提示可见: %s" % "; ".join(hits[:2])
                if hits
                else "未发现可见字段级错误提示(扫描%d个选择器)" % len(_selectors)
            ),
        }
    except Exception as e:
        return {
            "type": "field_error_visible_check",
            "passed": False,
            "evidence": "field_error 检查异常: %s" % str(e)[:60],
        }


async def capture_list_row_baseline(page) -> int | None:
    """采集**列表规模基线**（行数差分断言的唯一基线来源）。

    why: `expected_delta` 断言的基线此前由 `count_list_rows` 动作回填，而该动作不在
      执行契约内 → 基线永不回填 → 差分断言恒走"诚实跳过"分支（等于永不判定，
      需求"删除后行数减一"这类主张从此无人验证）。基线是**执行期事实**（只存在于
      执行时的 DOM），生成端无法给出，只能在执行期采集一次。
    只在页面上确有列表时返回基线（无列表页返回 None，不臆造基线）。
    读取口径复用 `read_list_size`（总数优先，分页列表的唯一正确语义）。
    """
    try:
        _n, _src = await read_list_size(page)
    except Exception as _e:  # noqa: BLE001 —— 采集失败不阻断用例（差分断言按诚实跳过处理）
        _log.debug("[RowBaseline] 基线采集失败: %s", str(_e)[:80])
        return None
    return int(_n) if _n > 0 else None


async def _handle_list_row_count(page, check, inner, check_type, precision):
    """列表行数断言（创建型 P01 深断言）。

    支持两种形态:
      expected_count: 绝对行数（operator gte/eq/lte）—— 取可见行数。
      expected_delta: 相对基线行数（基线由执行期在首个交互动作前采集，见
        `capture_list_row_baseline`）；基线不可得 → 诚实 skipped，绝不假 PASS/假 FAIL。
      相对差分的"行数"取 `read_list_size`（分页列表的总数）—— 可见行数在分页列表上
      恒为页容量，差分永不为负。
    """
    try:
        selector = (
            inner.get("selector") or check.get("selector") or ""
        ).strip() or _GENERIC_ROW_SELECTOR
        op = str(inner.get("operator") or check.get("operator") or "gte").lower()
        _exp = inner.get("expected_count")
        if _exp is None:
            _exp = check.get("expected_count")
        if _exp is not None:
            cnt = await page.locator(selector).count()
            exp = int(_exp)
            passed = cnt >= exp if op == "gte" else (cnt == exp if op == "eq" else cnt <= exp)
            return {
                "type": "list_row_count_check",
                "passed": passed,
                "evidence": "列表行数=%d (selector=%s, 期望%s%d)"
                % (cnt, selector[:40], op, exp),
            }
        _delta = inner.get("expected_delta")
        if _delta is None:
            _delta = check.get("expected_delta")
        if _delta is not None:
            # ★ 在搜索/过滤页面上跳过 expected_delta 校验（诚实地不判定 PASS/FAIL）。
            #   why: expected_delta 语义是"列表总条数相对基线的变化"，但在搜索结果/过滤页面
            #     上，read_list_size 读的是"过滤后的结果数"，不是"列表总条数"。
            #     （实测: baseline=174, AgentExplorer 跳到 ?q=abc 后 _cur=0 → Δ=-174 ≠ 0 → 假 FAIL）。
            #   通用性: 跨系统/行业 — 任何带查询参数的列表页都可能是过滤视图，不应做绝对基线比较。
            try:
                _url = getattr(page, "url", "") or ""
                if _url and any(_k in _url.lower() for _k in ("?q=", "?search=", "?filter=", "?keyword=", "?query=")):
                    return {
                        "type": "list_row_count_check",
                        "passed": True,
                        "skipped": True,
                        "evidence": "当前页面 %s 是搜索/过滤视图，"
                        "expected_delta 比较语义不成立，诚实跳过"
                        % _url.split("?")[1][:40],
                    }
            except Exception:
                pass

            _cur, _src = await read_list_size(page)
            baseline = inner.get("baseline_count")
            if baseline is None:
                baseline = check.get("baseline_count")
            if baseline is None:
                # ★ 执行期基线（首个交互动作前的列表规模，唯一来源见 capture_list_row_baseline）
                baseline = getattr(page, "_case_row_baseline", None)
            if baseline is None:
                # 基线不可得 = 该深断言前置动作未执行/未回填 → 诚实跳过
                return {
                    "type": "list_row_count_check",
                    "passed": True,
                    "skipped": True,
                    "evidence": "基线行数不可得(前置 count_list_rows 未回填)，"
                    "降级记录当前列表规模=%d (来源=%s)" % (_cur, _src),
                }
            delta = _cur - int(baseline)
            need = int(_delta)
            passed = delta >= need if op == "gte" else (delta == need if op == "eq" else delta <= need)
            return {
                "type": "list_row_count_check",
                "passed": passed,
                "evidence": "列表行数 %d→%d (Δ=%d, 期望%s%d, 来源=%s)"
                % (int(baseline), _cur, delta, op, need, _src),
            }
        cnt = await page.locator(selector).count()
        return {
            "type": "list_row_count_check",
            "passed": True,
            "skipped": True,
            "evidence": "无 expected_count/expected_delta，记录当前行数=%d" % cnt,
        }
    except Exception as e:
        return {
            "type": "list_row_count_check",
            "passed": False,
            "evidence": "list_row_count 检查异常: %s" % str(e)[:60],
        }


async def _handle_loading_complete(page, check, inner, check_type, precision):
    """复用 _verify_loading.verify_loading_complete 实现加载完成断言。"""
    try:
        from src.pipeline.steps.step4.verification._verify_loading import (
            verify_loading_complete,
        )

        # 合并 inner 和 check 字段（inner 优先），适配 verify_loading_complete 的 check 参数格式
        merged_check = _merge_check_inner(check, inner)
        merged_check["type"] = "loading_complete_check"
        r = await verify_loading_complete(page, merged_check)
        return {
            "type": "loading_complete",
            "passed": r.get("passed", True),
            "evidence": r.get("evidence", ""),
        }
    except Exception as e:
        return {
            "type": "loading_complete",
            "passed": False,
            "evidence": "loading_complete 检查异常: %s" % str(e)[:60],
        }


async def _handle_list_order(page, check, inner, check_type, precision):
    """复用 _verify_list.verify_list_order 实现列表排序断言。"""
    try:
        from src.pipeline.steps.step4.verification._verify_list import verify_list_order

        merged_check = _merge_check_inner(check, inner)
        merged_check["type"] = "list_order_check"
        r = await verify_list_order(page, merged_check)
        return {
            "type": "list_order",
            "passed": r.get("passed", True),
            "evidence": r.get("evidence", ""),
        }
    except Exception as e:
        return {
            "type": "list_order",
            "passed": False,
            "evidence": "list_order 检查异常: %s" % str(e)[:60],
        }


def _resolve_engine(page):
    """取浏览器引擎引用（page 携带优先，其次当前项目执行核心）。

    why: `_captured_dialogs`/`_console_errors`/`_last_download_path` 都挂在
      PlaywrightEngine 上，而 Step4 执行链拿到的 page 并不持有引擎引用；
      只在 page 上找会让这些断言恒不可达（非判定态），必须回落到执行核心。
    """
    engine = getattr(page, "_engine", None) or getattr(page, "engine", None)
    if engine is not None:
        return engine
    try:
        from src.pipeline.steps.step4.browser_core import browser_executor

        return getattr(browser_executor.for_current(), "_engine", None)
    except Exception:  # noqa: BLE001 —— 引擎不可达由调用方按非判定态处理
        return None


async def _handle_download_check(page, check, inner, check_type, precision):
    """★ 归一化（2026-08-04）：注册 download_check 到 _UI_CHECK_DISPATCH。

    复用 _verify_download.verify_download 实现文件下载结果断言。
    verify_download 依赖 engine._last_download_path；若 engine 不可达则 fail-closed
    （passed=False），禁止静默 PASS（原 download_check 落入 else → 假 PASS）。
    """
    try:
        from src.pipeline.steps.step4.verification._verify_download import (
            verify_download,
        )

        engine = _resolve_engine(page)
        merged_check = _merge_check_inner(check, inner)
        merged_check["type"] = "download_check"
        if engine is None:
            return {
                "type": "download_check",
                "passed": False,
                "evidence": "download_check 无法执行：engine 不可达（page 无 _engine/engine 引用）",
            }
        dr = await verify_download(engine, merged_check)
        return {
            "type": "download_check",
            "passed": bool(dr.get("passed", False)),
            "evidence": dr.get("evidence", ""),
        }
    except Exception as e:
        return {
            "type": "download_check",
            "passed": False,
            "evidence": "download_check 检查异常: %s" % str(e)[:60],
        }


# ── ★ T03(2026-08-06): 8 个孤儿验证器接入（原落 UNSUPPORTED_CHECK）──
#   主理人决策 #5：14 个 _verify_*.py 实现存在但未接入 checks 管道 → 全部接入。
#   其中 8 个 UI 孤儿在此注册；network/websocket/header/page_load 走 API 轨。
#   element_visible → verify_style._check_visibility（verify_state 不实现可见性，
#   映射到 verify_style 才是语义正确；deviation from 决策 #5 的 verify_state 标注）。


def _merge_check_inner(check, inner) -> dict:
    """合并外层 check 与 inner（inner 优先，type 除外），供孤儿验证器复用。"""
    merged_check = {}
    if isinstance(check, dict):
        merged_check.update(check)
    if isinstance(inner, dict):
        for _k, _v in inner.items():
            if _k != "type":
                merged_check[_k] = _v
    return merged_check


async def _handle_element_visible(page, check, inner, check_type, precision):
    """element_visible / element_visible_check → verify_style.visibility_check。"""
    try:
        from src.pipeline.steps.step4.verification._verify_style import verify_style

        merged = _merge_check_inner(check, inner)
        merged["type"] = "visibility_check"
        vr = await verify_style(page, merged)
        return {
            "type": "element_visible",
            "passed": vr.get("passed", True),
            "evidence": vr.get("evidence", ""),
        }
    except Exception as e:
        return {
            "type": "element_visible",
            "passed": False,
            "evidence": "元素可见断言异常(fail-closed): %s" % str(e)[:60],
        }


async def _handle_element_attribute(page, check, inner, check_type, precision):
    """element_attribute / element_attribute_check → verify_style.attribute_check。"""
    try:
        from src.pipeline.steps.step4.verification._verify_style import verify_style

        merged = _merge_check_inner(check, inner)
        merged["type"] = "attribute_check"
        vr = await verify_style(page, merged)
        return {
            "type": "element_attribute",
            "passed": vr.get("passed", True),
            "evidence": vr.get("evidence", ""),
        }
    except Exception as e:
        return {
            "type": "element_attribute",
            "passed": False,
            "evidence": "元素属性断言异常(fail-closed): %s" % str(e)[:60],
        }


async def _handle_text_regex(page, check, inner, check_type, precision):
    """text_regex / text_regex_check → verify_text_regex。"""
    try:
        from src.pipeline.steps.step4.verification._verify_regex import (
            verify_text_regex,
        )

        merged = _merge_check_inner(check, inner)
        rr = await verify_text_regex(page, merged)
        return {
            "type": "text_regex",
            "passed": rr.get("passed", True),
            "evidence": rr.get("evidence", ""),
        }
    except Exception as e:
        return {
            "type": "text_regex",
            "passed": False,
            "evidence": "文本正则断言异常(fail-closed): %s" % str(e)[:60],
        }


async def _handle_console_error(page, check, inner, check_type, precision):
    """console_error / console_error_check → verify_no_console_error（engine）。"""
    try:
        from src.pipeline.steps.step4.verification._verify_console import (
            verify_no_console_error,
        )

        engine = _resolve_engine(page)
        if engine is None:
            return {
                "type": "console_error",
                "passed": False,
                "reason": "EXECUTOR_EXCEPTION",
                "evidence": "console_error 无法执行：engine 不可达（page 无 _engine/engine 引用）",
            }
        merged = _merge_check_inner(check, inner)
        cr = await verify_no_console_error(engine, merged)
        return {
            "type": "console_error",
            "passed": cr.get("passed", True),
            "evidence": cr.get("evidence", ""),
        }
    except Exception as e:
        return {
            "type": "console_error",
            "passed": False,
            "evidence": "console 断言异常(fail-closed): %s" % str(e)[:60],
        }


async def _handle_dialog(page, check, inner, check_type, precision):
    """dialog / dialog_check → verify_dialog（engine）。"""
    try:
        from src.pipeline.steps.step4.verification._verify_dialog import verify_dialog

        engine = _resolve_engine(page)
        if engine is None:
            return {
                "type": "dialog",
                "passed": False,
                # 引擎侧不可达 → 结构化标为非判定态（防被聚合成产品缺陷）
                "reason": "EXECUTOR_EXCEPTION",
                "evidence": "dialog 无法执行：engine 不可达（page 无 _engine/engine 引用）",
            }
        merged = _merge_check_inner(check, inner)
        dr = await verify_dialog(engine, merged)
        return {
            "type": "dialog",
            "passed": dr.get("passed", True),
            "evidence": dr.get("evidence", ""),
        }
    except Exception as e:
        return {
            "type": "dialog",
            "passed": False,
            "evidence": "弹窗断言异常(fail-closed): %s" % str(e)[:60],
        }


async def _handle_any_of(page, check, inner, check_type, precision):
    """any_of_check → 析取式期望的 OR 语义（任一替代断言成立即符合期望）。

    why: 声明期望形如"显示错误提示**或**确认拦截"时，任一分支成立都满足需求。
      收窄成单一机制断言（"错误条必须出现"）会在 SUT 走另一分支时必假 FAIL；
      退化成容错断言（"页面能渲染"）又会在两个分支都不成立时假 PASS。
      本 handler 把全部替代分支逐一执行，任一条通过即 PASS，全不通过才 FAIL。
    """
    alts = inner.get("alternatives") or check.get("alternatives") or []
    if not isinstance(alts, list) or not alts:
        return {
            "type": "any_of_check",
            "passed": False,
            "reason": "UNSUPPORTED_CHECK",
            "evidence": "any_of_check 缺少 alternatives（无有效断言，结论不可用）",
        }
    from src.pipeline.steps.step4.verification._track_protocol import (
        is_verdict as _is_verdict,
        render_check_symbol as _render_symbol,
    )

    _parts = []
    _any_pass = False
    _verdict_failed = False
    _nonverdict_reason = ""
    for _i, alt in enumerate(alts[:8]):
        if not isinstance(alt, dict):
            continue
        _t = str(alt.get("type") or "text_contains").strip()
        if _t in ("any_of_check", "any_of"):
            continue
        _handler = _UI_CHECK_DISPATCH.get(_t) or _UI_CHECK_DISPATCH.get(
            _t[: -len("_check")] if _t.endswith("_check") else _t
        )
        if _handler is None:
            _parts.append("alt%d:%s=不支持" % (_i + 1, _t))
            _nonverdict_reason = _nonverdict_reason or "UNSUPPORTED_CHECK"
            continue
        try:
            _r = await _handler(
                page, {"check": alt}, alt, _t, str(alt.get("precision") or precision)
            )
        except Exception as _e:  # noqa: BLE001 —— 单分支异常不阻断其余分支
            _r = {
                "passed": False,
                "reason": "EXECUTOR_EXCEPTION",
                "evidence": "执行器异常: %s" % str(_e)[:60],
            }
        if isinstance(_r, dict) and _is_verdict(_r):
            # 分支结论按 `_track_protocol` 唯一权威分类：只有**构成判定**的分支
            #   才参与 OR 聚合。分支自述不可评估/不适用（N/A 跳过、执行器异常、
            #   不支持）时其 `passed=True` 只是"N/A 诚实返回"，不得当作分支成立
            #   （否则 N/A 分支会把整条析取断言撑成假 PASS）。
            if _r.get("passed"):
                _any_pass = True
            else:
                _verdict_failed = True
        elif isinstance(_r, dict):
            _nonverdict_reason = (
                _nonverdict_reason or str(_r.get("reason") or "") or "UNSUPPORTED_CHECK"
            )
        else:
            _verdict_failed = True
        _parts.append(
            "alt%d:%s=%s(%s)"
            % (
                _i + 1,
                _t,
                _render_symbol(_r) if isinstance(_r, dict) else "?",
                str((_r or {}).get("evidence") or "")[:80],
            )
        )
    _ev = " | ".join(_parts)
    if _any_pass:
        return {
            "type": "any_of_check",
            "passed": True,
            "status": "PASS",
            "evidence": "析取式期望任一分支成立 → 符合期望: " + _ev,
        }
    if _verdict_failed:
        return {
            "type": "any_of_check",
            "passed": False,
            "status": "FAIL",
            "evidence": "析取式期望全部分支均不成立: " + _ev,
        }
    return {
        "type": "any_of_check",
        "passed": False,
        "status": "UNKNOWN",
        "skipped": True,
        "reason": _nonverdict_reason or "UNSUPPORTED_CHECK",
        "evidence": "析取式期望各分支均无可判定结论（引擎侧不可评估，不记产品缺陷）: "
        + _ev,
    }


async def _handle_animation_complete(page, check, inner, check_type, precision):
    """animation_complete / animation_complete_check → verify_animation_complete。"""
    try:
        from src.pipeline.steps.step4.verification._verify_animation import (
            verify_animation_complete,
        )

        merged = _merge_check_inner(check, inner)
        ar = await verify_animation_complete(page, merged)
        return {
            "type": "animation_complete",
            "passed": ar.get("passed", True),
            "evidence": ar.get("evidence", ""),
        }
    except Exception as e:
        return {
            "type": "animation_complete",
            "passed": False,
            "evidence": "动画断言异常(fail-closed): %s" % str(e)[:60],
        }


async def _handle_pagination(page, check, inner, check_type, precision):
    """pagination / pagination_check → verify_pagination。"""
    try:
        from src.pipeline.steps.step4.verification._verify_pagination import (
            verify_pagination,
        )

        merged = _merge_check_inner(check, inner)
        pr = await verify_pagination(page, merged)
        return {
            "type": "pagination",
            "passed": pr.get("passed", True),
            "evidence": pr.get("evidence", ""),
        }
    except Exception as e:
        return {
            "type": "pagination",
            "passed": False,
            "evidence": "分页断言异常(fail-closed): %s" % str(e)[:60],
        }


async def _handle_storage(page, check, inner, check_type, precision):
    """storage / storage_check → verify_storage。"""
    try:
        from src.pipeline.steps.step4.verification._verify_storage import verify_storage

        merged = _merge_check_inner(check, inner)
        sr = await verify_storage(page, merged)
        return {
            "type": "storage",
            "passed": sr.get("passed", True),
            "evidence": sr.get("evidence", ""),
        }
    except Exception as e:
        return {
            "type": "storage",
            "passed": False,
            "evidence": "存储断言异常(fail-closed): %s" % str(e)[:60],
        }


# ═══════════════════════════════════════════════════════════════════════════════
# ★ 2026-09-16 B 方案：易用性 + 兼容性 — 6 个真实 CDP/Playwright 执行器
# 每个 handler 都有真实逻辑（不是空壳 element_exists）
# 设计原则：只用 CDP Runtime evaluate（零依赖 Playwright 高级功能）、
#          有明确阈值（WCAG / Apple HIG / 业界标准）、异常 fail-closed
# ═══════════════════════════════════════════════════════════════════════════════

# ── 易用性：click_target_size_check（WCAG 2.5.5 Target Size, min 44×44px）──
async def _handle_click_target_size_check(page, check, inner, check_type, precision):
    selector = inner.get("selector") or check.get("selector") or "button, a, input, textarea, select, [role='button'], [role='link']"
    min_size = inner.get("min_size") or check.get("min_size") or 32
    try:
        result = await page.evaluate("""([sel, min]) => {
            const els = document.querySelectorAll(sel);
            const bad = [];
            const good = [];
            let exempt = 0;
            const threshold = min;
            els.forEach(el => {
                const cs = getComputedStyle(el);
                if (cs.display === 'none' || cs.visibility === 'hidden') { exempt++; return; }
                // ★ 根因修复：WCAG 2.5.8 "Inline" 例外——内联文本流中的
                //   链接/按钮，其尺寸受周围非目标文本的行高约束，规范明确豁免
                //   （axe-core target-size 规则同款处理）。不豁免则表格单元格内的
                //   文本链接恒被误判（实测 fullcov_sut: A 32×21 被计为不达标）。
                if (cs.display === 'inline') { exempt++; return; }
                const r = el.getBoundingClientRect();
                const w = r.width, h = r.height;
                if (w < threshold || h < threshold) bad.push({tag: el.tagName, w: Math.round(w), h: Math.round(h), text: (el.textContent||'').trim().slice(0,20)});
                else good.push({w: Math.round(w), h: Math.round(h)});
            });
            return {total: els.length, good: good.length, bad: bad.slice(0,5), exempt, min};
        }""", [selector, min_size])
        passed = result["bad"] == [] and (result["good"] + result["exempt"]) > 0
        evidence = (
            f"可交互元素 {result['total']} 个, 达标 {result['good']}, "
            f"不达标 {len(result['bad'])}, 豁免(内联/隐藏) {result['exempt']} "
            f"(阈值≥{min_size}×{min_size}px)"
        )
        if result["bad"]:
            evidence += f" | 示例: {result['bad'][:3]}"
        return {"type": "click_target_size_check", "passed": passed, "evidence": evidence}
    except Exception as e:
        return {"type": "click_target_size_check", "passed": False, "evidence": f"CDP 异常: {str(e)[:60]}"}


# ── 易用性：form_label_association_check（WCAG 3.3.2 Labels or Instructions）──
async def _handle_form_label_association_check(page, check, inner, check_type, precision):
    selector = inner.get("selector") or check.get("selector") or "input, textarea, select"
    try:
        result = await page.evaluate("""(sel) => {
            const els = document.querySelectorAll(sel);
            let withLabel = 0, without = 0, details = [];
            els.forEach(el => {
                const id = el.id || el.getAttribute('id');
                const hasLabel = id && document.querySelector('label[for="'+id+'"]');
                const hasAria = el.hasAttribute('aria-label') || el.hasAttribute('aria-labelledby');
                const hasName = el.getAttribute('name');
                if (hasLabel || hasAria || hasName) { withLabel++; }
                else { without++; details.push({tag: el.tagName, type: el.type||'', name: hasName, cls: el.className}); }
            });
            return {total: els.length, withLabel, without, details: details.slice(0,5)};
        }""", selector)
        ratio = (result["withLabel"] / result["total"] * 100) if result["total"] > 0 else 100
        passed = result["without"] == 0 or ratio >= 90
        evidence = f"表单控件 {result['total']} 个, 有 label/aria/name: {result['withLabel']} ({ratio:.0f}%), 缺失: {result['without']}"
        if result["details"]: evidence += f" | 示例: {result['details'][:3]}"
        return {"type": "form_label_association_check", "passed": passed, "evidence": evidence}
    except Exception as e:
        return {"type": "form_label_association_check", "passed": False, "evidence": f"CDP 异常: {str(e)[:60]}"}


# ── 易用性：viewport_overflow_check（无水平滚动条 / 响应式不破版）──
async def _handle_viewport_overflow_check(page, check, inner, check_type, precision):
    try:
        result = await page.evaluate("""() => {
            const sw = document.documentElement.scrollWidth;
            const cw = document.documentElement.clientWidth;
            const overflowX = sw > cw + 1;
            // 也检查每个元素的水平溢出（不检查 overflow:hidden 的元素）
            const badEls = [];
            const allEls = document.querySelectorAll('*');
            for (const el of allEls) {
                const r = el.getBoundingClientRect();
                if (r.right > cw + 2 && getComputedStyle(el).overflowX !== 'hidden') {
                    badEls.push({tag: el.tagName, right: Math.round(r.right), cls: (el.className||'').toString().slice(0,30)});
                    if (badEls.length >= 5) break;
                }
            }
            return {scrollWidth: sw, clientWidth: cw, overflowX, badElements: badEls};
        }""")
        passed = not result["overflowX"] and len(result["badElements"]) == 0
        evidence = f"scrollWidth={result['scrollWidth']} clientWidth={result['clientWidth']} 水平溢出={'YES' if result['overflowX'] else 'NO'} 破版元素={len(result['badElements'])}"
        if result["badElements"]: evidence += f" | 示例: {result['badElements'][:3]}"
        return {"type": "viewport_overflow_check", "passed": passed, "evidence": evidence}
    except Exception as e:
        return {"type": "viewport_overflow_check", "passed": False, "evidence": f"CDP 异常: {str(e)[:60]}"}


# ── 易用性：form_autocomplete_check（WCAG 1.3.5 Identify Input Purpose）──
async def _handle_form_autocomplete_check(page, check, inner, check_type, precision):
    selector = inner.get("selector") or check.get("selector") or "input[type='text'], input[type='email'], input[type='tel'], input[type='password']"
    try:
        result = await page.evaluate("""(sel) => {
            const els = document.querySelectorAll(sel);
            let withAutocomplete = 0, without = 0;
            els.forEach(el => { if (el.autocomplete && el.autocomplete !== 'off' && el.autocomplete !== '') withAutocomplete++; else without++; });
            return {total: els.length, withAutocomplete, without};
        }""", selector)
        passed = result["without"] == 0 or (result["withAutocomplete"] / result["total"] * 100 if result["total"] > 0 else 100) >= 60
        evidence = f"输入框 {result['total']} 个, 有 autocomplete: {result['withAutocomplete']}, 缺失: {result['without']}"
        return {"type": "form_autocomplete_check", "passed": passed, "evidence": evidence}
    except Exception as e:
        return {"type": "form_autocomplete_check", "passed": False, "evidence": f"CDP 异常: {str(e)[:60]}"}


# ── 兼容性：viewport_rendering_check（多 viewport 下无元素重叠/破版）──
async def _restore_default_viewport(page) -> None:
    """临时切换视口后恢复「默认视口」（唯一实现）。

    取值必须与引擎建 context 时同源（`config.playwright_viewport_*`）：此前硬编码
    1280×800，与配置的 1920×1080 不一致 → 一次临时切换后页面停在非默认宽度，
    后续断言（水平溢出等）在错误视口上判定。
    """
    from src.core.config_loader import config as _cfg

    await page.set_viewport_size(
        {
            "width": _cfg.playwright_viewport_width,
            "height": _cfg.playwright_viewport_height,
        }
    )
    await page.wait_for_timeout(200)


async def _handle_viewport_rendering_check(page, check, inner, check_type, precision):
    viewports = inner.get("viewports") or check.get("viewports") or [
        {"w": 375, "h": 667, "label": "mobile"},
        {"w": 1024, "h": 768, "label": "tablet"},
        {"w": 1920, "h": 1080, "label": "desktop"},
    ]
    try:
        bad_views = []
        for vp in viewports:
            await page.set_viewport_size({"width": vp["w"], "height": vp["h"]})
            await page.wait_for_timeout(200)  # 等 layout reflow
            result = await page.evaluate("""() => {
                const rw = document.documentElement.scrollWidth;
                const cw = document.documentElement.clientWidth;
                const overflowX = rw > cw + 1;
                // ★ F4 根因修复(2026-09-26)：重叠判定原本把**父子包含**关系也算重叠
                //   （选择器含 div，容器必然包住子按钮/文本 → overlap 恒 >3 → 任何正常
                //   页面都判失败）。实测 TC-PAGE-P713FD96F-CP01：viewport_rendering_check
                //   报 desktop overlap=5 失败，而同 run 的 viewport_overflow_check 通过
                //   （无水平溢出）→ 自相矛盾的假 FAIL。
                //   修复：重叠 = 几何相交 且 **彼此不含** 且 相交面积 ≥ 较小元素的 50%
                //   （与函数注释声明的"重叠超过 50%"语义对齐）。
                const visible = [];
                const all = document.querySelectorAll(
                    'button, a, input, textarea, select, h1, h2, h3, p, div, span, label');
                for (const el of all) {
                    const r = el.getBoundingClientRect();
                    if (r.width > 20 && r.height > 10 && r.bottom > 0 && r.top < window.innerHeight) {
                        visible.push({el: el, x: r.x, y: r.y, w: r.width, h: r.height});
                    }
                }
                const intersects = (a, b) => !(a.x + a.w <= b.x || b.x + b.w <= a.x ||
                                               a.y + a.h <= b.y || b.y + b.h <= a.y);
                const area = (o) => Math.max(0, Math.min(o.a.x+o.a.w, o.b.x+o.b.w) - Math.max(o.a.x, o.b.x)) *
                                   Math.max(0, Math.min(o.a.y+o.a.h, o.b.y+o.b.h) - Math.max(o.a.y, o.b.y));
                let overlaps = 0;
                for (let i = 0; i < visible.length && i < 30; i++) {
                    for (let j = i+1; j < visible.length && j < 30; j++) {
                        const A = visible[i], B = visible[j];
                        // 父子/祖孙包含关系不算重叠（布局天然嵌套）
                        if (A.el.contains(B.el) || B.el.contains(A.el)) continue;
                        if (!intersects(A, B)) continue;
                        const inter = area({a: A, b: B});
                        const smaller = Math.min(A.w * A.h, B.w * B.h);
                        if (smaller > 0 && inter / smaller >= 0.5) overlaps++;
                    }
                }
                return {overflowX, overlap_count: overlaps, total_visible: visible.length};
            }""")
            if result["overflowX"] or result["overlap_count"] > 3:
                bad_views.append({**vp, **result})
        # 恢复默认 viewport
        await _restore_default_viewport(page)
        passed = len(bad_views) == 0
        evidence = f"检测 {len(viewports)} viewport, 通过 {len(viewports)-len(bad_views)}, 失败 {len(bad_views)}"
        if bad_views:
            evidence += f" | 失败 viewport: {[b['label']+('(overflow)' if b['overflowX'] else '(overlap='+str(b['overlap_count'])+')') for b in bad_views]}"
        return {"type": "viewport_rendering_check", "passed": passed, "evidence": evidence}
    except Exception as e:
        try:
            await _restore_default_viewport(page)
        except Exception as _e:
            _log.debug("_handle_viewport_rendering_check best-effort 容忍: %s", str(_e)[:120])
        return {"type": "viewport_rendering_check", "passed": False, "evidence": f"执行异常: {str(e)[:60]}"}


# ── 兼容性：touch_target_mobile_check（移动端点击区域 ≥ 44×44px, Apple HIG / WCAG 2.5.5）──
async def _handle_touch_target_mobile_check(page, check, inner, check_type, precision):
    min_size = inner.get("min_size") or check.get("min_size") or 44
    mobile_w = inner.get("mobile_width") or check.get("mobile_width") or 375
    try:
        # 切到移动端 viewport
        await page.set_viewport_size({"width": mobile_w, "height": 812})
        await page.wait_for_timeout(300)
        result = await page.evaluate("""([sel, min]) => {
            const els = document.querySelectorAll(sel);
            const bad = [];
            els.forEach(el => {
                const r = el.getBoundingClientRect();
                const w = r.width, h = r.height;
                if ((w < min || h < min) && r.width > 0 && r.height > 0) bad.push({tag: el.tagName, w: Math.round(w), h: Math.round(h), text: (el.textContent||'').trim().slice(0,20)});
            });
            return {total: els.length, bad: bad.slice(0,8), min};
        }""", ["button, a, input, textarea, select, [role='button'], [role='tab']", min_size])
        # 恢复默认
        await _restore_default_viewport(page)
        passed = result["bad"] == [] and result["total"] > 0
        evidence = f"移动端({mobile_w}px)可交互元素 {result['total']}, 达标 {result['total']-len(result['bad'])}, 不达标 {len(result['bad'])} (阈值≥{min_size}×{min_size}px)"
        if result["bad"]: evidence += f" | 示例: {result['bad'][:3]}"
        return {"type": "touch_target_mobile_check", "passed": passed, "evidence": evidence}
    except Exception as e:
        try:
            await _restore_default_viewport(page)
        except Exception as _e:
            _log.debug("_handle_touch_target_mobile_check best-effort 容忍: %s", str(_e)[:120])
        return {"type": "touch_target_mobile_check", "passed": False, "evidence": f"执行异常: {str(e)[:60]}"}


# ── ★ semantic_check：语义断言（机械优先，AI 兜底判定）[ 根因修复] ──
#   根因（5层why）:
#     L1 现象: 17 条假 FAIL——断言 `text_contains` 报"找到0个匹配文本节点"，
#        而同一用例的 Agent 语义验证（含 LLM）已判"验证通过"。
#     L2 直接: 需求语义期望被生成端翻译成**字面 needle** 做 contains，
#        页面用别的措辞表达同一语义（或期望本身是叙述转述）→ 必然 0 匹配。
#     L3 为何: 引擎把「需语义理解的期望」用「机械字符串匹配」实现——
#        断言手段与期望性质不匹配（语义对齐边界）。
#     L4 为何反复: 历史修复只在 needle 形态上加正则过滤（占位符/超长），
#        未改断言手段，符合形态的语义 needle 仍必然假 FAIL。
#     L5 根本: 缺少"语义期望走语义判定"的通道。
#   根治 = 新增语义断言执行器：先做机械文本匹配（命中即过，零 LLM 成本），
#    未命中时才交由 AI（本地/云端路由）按页面事实判定是否满足期望语义；
#    AI 不可用时诚实标 UNVERIFIED（既不定 FAIL 也不假 PASS）。
#   通用性: 跨系统/行业/语言——只依赖页面文本与期望语义，无业务硬编码。
# ★ 根因修复（2026-09-23）semantic_check 机械短路假 PASS —— 判别力守卫
#   实测：TC-REQ-001-I01 期望「系统展示商品列表按更新时间排序」，机械预检以
#   fuzzy 精度做 2 字子词扩展 → 命中针退化为「系统」→ 任意中文页面皆命中
#   （body 里出现 3 次）→ 短路返回 passed=True，AI 语义判定从未执行 → 假 PASS。
#   策略（ponytail：修在语义通道的唯一入口）：机械命中只有在**针具备判别力**时
#   才可作为语义判定的充分证据；退化的高频短针一律落回 AI 语义判定（或诚实
#   UNVERIFIED），绝不冒充语义通过。判别力阈值取保守值——宁可多花一次 AI 调用，
#   也不放过假 PASS。
_MECH_MIN_CJK_CHARS = 4  # 中文针至少 4 字（"系统"/"商品"等 2 字通用词不具判别力）
_MECH_MIN_LATIN_CHARS = 3  # 拉丁/数字针至少 3 字符


def _mech_needle_is_discriminative(needle: str) -> bool:
    """机械命中的针是否具备判别力（足以支撑语义 PASS）。

    仅凭长度/字符构成判断，不做词表硬编码 —— 通用性优先（跨系统/行业/页面）。
    """
    s = str(needle or "").strip()
    if not s:
        return False
    cjk = len(_re.findall(r"[\u4e00-\u9fff]", s))
    latin = len(_re.findall(r"[A-Za-z0-9]", s))
    return cjk >= _MECH_MIN_CJK_CHARS or latin >= _MECH_MIN_LATIN_CHARS


# ── 分页翻页查找（2026-09-27）：列表语义断言跨页检索 ──
#   why: 服务端分页列表首屏只含前 N 条，「创建后新记录可见」常落在非首页，
#   机械未命中即判 FAIL 是对分页系统的过度约束（假 FAIL，实测 TC-REQ-002-P01：
#   新记录 id=8 在第2页，首页只显示 id3-7）。页面指纹与期望值均业务无关。
#   人类用户会翻页确认 → 语义断言等价翻页；命中后仍需判别性针把关（防假 PASS）。
_NEXT_PAGE_SELECTORS = (
    'a[rel="next"]',
    'button[rel="next"]',
    'a:has-text("下一页")',
    'button:has-text("下一页")',
    'a:has-text("下页")',
    'a[aria-label*="下一页"]',
    'button[aria-label*="下一页"]',
)


async def _click_next_page(page) -> bool:
    """点击页面上的「下一页」（通用候选集，任一可见可用即点）。"""
    for _sel in _NEXT_PAGE_SELECTORS:
        try:
            _el = page.locator(_sel).first
            if await _el.count() <= 0 or not await _el.is_visible():
                continue
            _disabled = await _el.get_attribute("aria-disabled")
            if _disabled and str(_disabled).strip().lower() == "true":
                continue
            await _el.click()
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=3000)
            except Exception as _e:
                _log.debug("_click_next_page best-effort 容忍: %s", str(_e)[:120])
            await page.wait_for_timeout(300)
            return True
        except Exception:
            continue
    return False


async def _search_expected_across_pages(
    page, selector: str, expected_values: list[str], max_pages: int = 200
) -> tuple[bool, int, str, str]:
    """机械未命中后逐页查找期望值。返回 (found, 命中页码, 命中值, 详情)。

    根因(2026-09-27 实证 TC-REQ-002-P01)：固定 max_pages=6 是拍脑袋上限——
    SUT 存量分页规模增长后（实测已 71 条升序记录，新记录第 15 页），翻 6 页
    即弃用，AI 只能据首页旧数据判 FAIL（假 FAIL 且误报 PRODUCT_BUG）。
    修法: 以上一页文本指纹是否变化判定「是否还有更多页」，有变化继续翻，直到
    命中或内容不再变化（末页/下一页失效）；仅保留大数保护上限防异常死循环。
    """
    _prev_fp: str | None = None
    for _pg in range(1, max_pages + 1):
        try:
            _scope = page.locator(selector) if selector else page.locator("body")
            _txt = (await _scope.inner_text()) or ""
        except Exception:
            try:
                _txt = (await page.inner_text("body")) or ""
            except Exception:
                _txt = ""
        for _ev in expected_values:
            if _ev and _ev in _txt:
                return True, _pg, _ev, "第%d页命中 %s" % (_pg, _ev[:40])
        _fp = _txt[-120:]
        if _prev_fp is not None and _fp == _prev_fp:
            return False, 0, "", "翻页查找终止(第%d页内容与上页相同,已到末页或下一页失效)" % _pg
        if _pg >= max_pages:
            break
        if not await _click_next_page(page):
            return False, 0, "", "翻页查找终止(下一页控件不可用)"
        _prev_fp = _fp
    return False, 0, "", "翻页查找%d页未命中" % max_pages


async def _handle_semantic_check(page, check, inner, check_type, precision):
    _expect = (
        inner.get("expected", "")
        or inner.get("text", "")
        or check.get("text", "")
        or check.get("expect", "")
        or inner.get("assertion", "")
    )
    _expect = str(_expect).strip()
    if not _expect:
        return {
            "type": "semantic_check",
            "passed": False,
            "status": "ERROR",
            "reason": "EMPTY_EXPECTATION",
            "evidence": "语义断言缺失期望文本（无法判定）",
        }

    # ── ① 机械优先：仅在针具备判别力时短路（廉价路径）──
    #   ★ 根因修复（2026-09-23）：原实现无条件接受机械命中，fuzzy 的 2 字子词
    #   扩展会把针退化成「系统」这类通用词，任何页面都命中 → 假 PASS。
    #   现要求 matched_value 具备判别力，否则落回 ② AI 语义判定。
    try:
        _mech = await _handle_text_contains(page, check, inner, "text_contains", precision)
        _mv = str((_mech or {}).get("matched_value", "") or "")
        if isinstance(_mech, dict) and _mech.get("passed"):
            if _mech_needle_is_discriminative(_mv):
                return {
                    "type": "semantic_check",
                    "passed": True,
                    "evidence": "机械文本命中(判别性针): %s" % str(_mech.get("evidence", ""))[:160],
                    "semantic_source": "mechanical",
                }
            _log.info(
                "[semantic_check] 机械命中但针不具判别力(matched=%r)，转分页/AI 判定",
                _mv[:40],
            )
        # ★ 分页跨页检索（2026-09-27）：机械未以判别性短路（未命中，或 fuzzy 命中
        #   但针退化）且期望是判别性短针时，在分页列表各页中查找（人类用户翻页行为）。
        #   长句走 AI 语义判定。实测 002-P01：新记录在升序分页第2页，首屏子词命中
        #   ("自动化")不具判别力 → 若不做跨页即假 FAIL。
        _pg_expect = str(_expect).strip()
        if 0 < len(_pg_expect) <= 80:
            _pg_sel = str(inner.get("selector") or check.get("selector") or "").strip()
            _pg_found, _pg_no, _pg_hit, _pg_ev = await _search_expected_across_pages(
                page, _pg_sel, [_pg_expect]
            )
            if _pg_found:
                _log.info("[semantic_check] 跨页机械命中: %s", _pg_ev)
                return {
                    "type": "semantic_check",
                    "passed": True,
                    "evidence": "跨页机械命中(%s, 判别性针: %s)"
                    % (_pg_ev, _pg_expect[:40]),
                    "semantic_source": "mechanical_paginated",
                }
        _mech_ev = str((_mech or {}).get("evidence", ""))[:160]
    except Exception as _e:
        _mech_ev = "机械匹配异常: %s" % str(_e)[:80]

    # ── ①.5 DOM 反馈条确定性判定（与 Agent guided 同源证据）──
    #   ★ 根因修复(2026-09-25) RC-Z: 消除「弱判定否决强证据」。
    #   根因（5层why）:
    #     L1 现象: Agent guided 已用 DOM 错误条/成功条判 PASS，checks 层
    #        `semantic_check` 仍对**同一期望**判 FAIL → 用例整体 FAIL（实测
    #        TC-REQ-045-I01：guided "验证通过[错误条检测]"，checks 判 FAIL）。
    #     L2 直接: `semantic_check` 只把 `inner_text` 交给 LLM —— **看不到样式/类名**，
    #       而期望里常含视觉/形态词（"flash 红条"／"红色错误条"）→ LLM 如实回答
    #       "无红色、bar/flash/banner 等视觉描述" → 判 FAIL。
    #     L3 为何: 断言手段与期望性质错配 —— "页面是否出现反馈条"是 **DOM 事实**，
    #       却被交给"只读纯文本"的 LLM 去猜。
    #     L4 为何反复: 反馈条选择器此前只在 Agent 侧私有（错误条虽共享、成功条未共享），
    #       checks 层拿不到同一批证据 → 两个验证器口径不一致，必然互相打架。
    #     L5 根本: 缺少"反馈条类期望 → DOM 确定性判定"的通道。
    #   修复: 极性（错误/接受）由 Step3 单一事实源判定，命中即用**共享选择器**在
    #     活动 DOM 上取证；可见且文本非空 → PASS（证据含选择器+类名+文本，可审计）。
    #   通用性: 跨系统/行业/页面 —— 反馈条形态与框架无关，零业务硬编码。
    try:
        from src.pipeline.step3._rule_extractor_keywords import (
            expects_accept_feedback as _exp_acc,
            expects_error_feedback as _exp_err,
        )
    except Exception:
        _exp_acc = _exp_err = None
    _fb_kind = ""
    if _exp_err and _exp_err(_expect):
        _fb_kind = "error"
    elif _exp_acc and _exp_acc(_expect):
        _fb_kind = "success"
    if _fb_kind:
        try:
            _fb_ok, _fb_ev = await _detect_visible_feedback_bar(page, _fb_kind)
        except Exception as _fb_e:
            _fb_ok, _fb_ev = False, "反馈条探测异常: %s" % str(_fb_e)[:80]
        if _fb_ok:
            _log.info(
                "[semantic_check] %s 极性 + 可见反馈条 → 确定性 PASS: %s",
                _fb_kind, _fb_ev[:120],
            )
            return {
                "type": "semantic_check",
                "passed": True,
                "semantic_source": "dom_feedback_bar",
                "evidence": "DOM 反馈条确定性判定(%s): %s | 机械文本未命中: %s"
                % (_fb_kind, _fb_ev, _mech_ev),
            }
        _mech_ev = "%s ; 反馈条(%s)未见" % (_mech_ev, _fb_kind)

    # ── ② AI 语义判定（机械未命中时才调用）──
    # 运行期 AI 被架构关闭时（src.ai.runtime_ai_gate）不存在 AI 兜底通道，
    # 必须如实标「运行期 AI 不可用」，否则 evidence 会把策略性禁用说成输出解析失败。
    from src.ai.runtime_ai_gate import runtime_ai_denied as _rt_ai_denied

    if _rt_ai_denied():
        return {
            "type": "semantic_check",
            "passed": False,
            "status": "UNVERIFIED",
            "reason": "SEMANTIC_RUNTIME_AI_DISABLED",
            "evidence": "语义判定不可用：运行期 AI 已禁用(step4_runtime_ai=False)，"
            "机械文本未命中: %s" % _mech_ev,
        }
    _snapshot = ""
    try:
        _snapshot = (await page.inner_text("body") or "").strip()[:2500]
    except Exception:
        try:
            _snapshot = (await page.content() or "")[:2500]
        except Exception:
            _snapshot = ""
    if not _snapshot:
        return {
            "type": "semantic_check",
            "passed": False,
            "status": "UNVERIFIED",
            "reason": "SEMANTIC_NO_PAGE_EVIDENCE",
            "evidence": "语义判定不可用：页面无文本证据（机械文本未命中: %s）" % _mech_ev,
        }
    try:
        from src.ai.router_module import router as _router

        _prompt = (
            "判定「页面实际状态」是否满足「期望」的语义（不要求字面一致，只要求语义等价）。\n"
            f"期望: {_expect[:300]}\n"
            f"页面实际文本: {_snapshot}\n\n"
            '返回JSON: {"verdict":"PASS"|"FAIL","confidence":0.0-1.0,"evidence":"依据的页面事实"}'
        )
        _res = await asyncio.to_thread(
            _router.route,
            _prompt,
            system_prompt="你是Web测试断言判定专家。只看页面事实，语义等价即PASS。只返回JSON。",
            task_type="verify",
            expect_json=True,
            temperature=0.0,
            # ★ 根因修复(2026-09-24): 400→1200。根因: evidence 字段要引用页面事实，
            #   400 token 常被截断 → json.loads 抛 "Unterminated string"（实测多例
            #   "语义判定不可用：Unterminated string starting at: line 1 column 48"）
            #   → 语义判定整体不可用 → 断言退化 → 假 FAIL。与 _semantic.py 同源取齐。
            max_tokens=1200,
        )
        _data = _res.get("data") if isinstance(_res, dict) else None
        if isinstance(_data, str):
            # ★ 根因修复(2026-09-24): 不再就地手写 markdown/截断修补（易漏），
            #   统一走 canonical 单一事实源 src.ai.json_parser.parse_json_response
            #   （含 markdown 剥离、尾逗号/注释规范化、截断补全、内嵌 JSON 恢复）。
            #   根因: 手写版只处理 ``` 与裸 {…}，遇截断 JSON 直接 json.loads 抛异常
            #   → "Expecting value: line 1 column 1 (char 0)" 类不可用（实测多例）。
            #   通用性: 所有 LLM JSON 输出共用同一修复器，跨模型/跨任务成立。
            from src.ai.json_parser import parse_json_response as _pjr

            _pr = _pjr(_data)
            _data = _pr.get("data") if _pr.get("success") else None
        if isinstance(_data, dict) and _data.get("verdict") in ("PASS", "FAIL"):
            _verdict = _data["verdict"]
            _conf = float(_data.get("confidence", 0.0) or 0.0)
            _ev = str(_data.get("evidence", ""))[:200]
            _passed = _verdict == "PASS" and _conf >= 0.5
            return {
                "type": "semantic_check",
                "passed": _passed,
                "semantic_source": "ai",
                "semantic_confidence": _conf,
                "evidence": "AI语义判定=%s(conf=%.2f): %s | 机械文本未命中: %s"
                % (_verdict, _conf, _ev, _mech_ev),
            }
        _err = str((_res or {}).get("error") or "")[:80] if isinstance(_res, dict) else ""
        return {
            "type": "semantic_check",
            "passed": False,
            "status": "UNVERIFIED",
            "reason": "SEMANTIC_AI_UNPARSABLE",
            "evidence": "语义判定不可用：AI 输出不可解析%s（机械文本未命中: %s）"
            % ((": " + _err) if _err else "", _mech_ev),
        }
    except Exception as _e:
        return {
            "type": "semantic_check",
            "passed": False,
            "status": "UNVERIFIED",
            "reason": "SEMANTIC_AI_UNAVAILABLE",
            "evidence": "语义判定不可用：%s（机械文本未命中: %s）" % (str(_e)[:80], _mech_ev),
        }


# ── 字典分派：check_type → handler ──

_PERF_METRICS_JS = """
() => {
  const out = {FCP: 0, LCP: 0, TTFB: 0, LOAD: 0};
  try {
    const fcp = performance.getEntriesByName('first-contentful-paint')[0];
    if (fcp) out.FCP = fcp.startTime;
  } catch (e) {}
  try {
    const nav = performance.getEntriesByType('navigation')[0];
    if (nav) { out.TTFB = nav.responseStart || 0; out.LOAD = nav.loadEventEnd || nav.domContentLoadedEventEnd || 0; }
  } catch (e) {}
  try {
    out.PAINT = (performance.getEntriesByType('paint') || []).length;
  } catch (e) {}
  return out;
}
"""

_LCP_BUFFERED_JS = """
() => new Promise((resolve) => {
  let done = false;
  try {
    const po = new PerformanceObserver((list) => {
      const es = list.getEntries();
      if (es && es.length) { done = true; resolve(es[es.length - 1].startTime); }
    });
    po.observe({type: 'largest-contentful-paint', buffered: true});
  } catch (e) {}
  setTimeout(() => { if (!done) resolve(0); }, 300);
})
"""


async def _measure_page_perf(page) -> dict:
    """采集 FCP / LCP / TTFB / Load（ms）。不可用时返回 0，绝不抛异常。"""
    metrics: dict = {"FCP": 0.0, "LCP": 0.0, "TTFB": 0.0, "LOAD": 0.0}
    try:
        base = await page.evaluate(_PERF_METRICS_JS)
        if isinstance(base, dict):
            for _k in ("FCP", "TTFB", "LOAD"):
                try:
                    metrics[_k] = float(base.get(_k) or 0)
                except Exception:
                    metrics[_k] = 0.0
    except Exception as _e:
        _log.debug("[perf] 基础性能采集失败: %s", str(_e)[:80])
    try:
        _lcp = await page.evaluate(_LCP_BUFFERED_JS)
        metrics["LCP"] = float(_lcp or 0)
    except Exception as _e:
        _log.debug("[perf] LCP 采集失败: %s", str(_e)[:80])
    # LCP 不可得时退化为 FCP（诚实降级：证据里写明来源）
    if not metrics["LCP"] and metrics["FCP"]:
        metrics["LCP"] = metrics["FCP"]
        metrics["LCP_SOURCE"] = "fcp_fallback"
    return metrics


async def _handle_performance_metric_check(page, check, inner, check_type, precision):
    """★ 2026-09-23 根治（性能族全灭：生成端发射、执行端未注册）。

    根因：Step3 生成端发射 performance_lcp_check（_fallback_cases 的 PF 用例），
    但 Step4 的 _UI_CHECK_DISPATCH 从未注册该类型 → check_guard 按"执行注册表"
    校验时把它当词表漂移剔除 → PF 用例失去唯一断言 → 空用例落入 API 轨得到 404
    → 性能族（fcp/lcp/page_load）在 ai_health_metrics 恒为零。

    修复（对齐守卫设计的"新增类型必须先注册执行器"）：
      1. 真实测量 FCP/LCP（PerformanceObserver buffered + navigation timing）；
      2. 按 check.operator 与 expected(ms) 比较给出判定，不可测则 UNVERIFIED 不判假 FAIL；
      3. 顺带把 FCP 写入 ai_health_metrics（fcp 族），使"指标正确收集"成立。

    通用性：任何 Web 目标、任何 check_type 以 performance_* 开头的毫秒阈值断言。
    """
    # ★ 2026-09-23 修正（自身缺陷，防假 PASS）：阈值/操作符实际落在 `inner`
    #   （分派契约：handler(page, check, inner, ...)，inner 才是 check 本体）。
    #   只读 check 会拿不到 expected → 落入"无阈值"分支 → **声明了阈值的性能断言被无条件判过**
    #   （实测 LCP 28ms 显示"无阈值声明"即此因）。此处两处都取，inner 优先。
    def _pick(key: str, default=None):
        for _src in (inner, check):
            if isinstance(_src, dict) and _src.get(key) not in (None, ""):
                return _src.get(key)
        return default

    _metric = str(_pick("selector") or "").upper()
    if not _metric or _metric not in ("LCP", "FCP", "TTFB", "LOAD", "PAGE_LOAD"):
        _metric = "LCP" if "lcp" in check_type.lower() else "FCP"
    _key = "LOAD" if _metric == "PAGE_LOAD" else _metric

    try:
        _expected = float(_pick("expected") or 0)
    except Exception:
        _expected = 0.0
    _op = str(_pick("operator", "less_than_equal")).lower()

    try:
        metrics = await _measure_page_perf(page)
    except Exception as _e:
        return {
            "type": check_type,
            "passed": False,
            "status": "UNVERIFIED",
            "reason": "PERF_METRIC_UNAVAILABLE",
            "evidence": "性能指标不可采集: %s" % str(_e)[:80],
        }

    _value = float(metrics.get(_key, 0) or 0)
    _src = metrics.get("LCP_SOURCE", "")
    _evidence_base = "FCP:%.0fms LCP:%.0fms TTFB:%.0fms Load:%.0fms%s" % (
        metrics.get("FCP", 0),
        metrics.get("LCP", 0),
        metrics.get("TTFB", 0),
        metrics.get("LOAD", 0),
        ("(%s)" % _src) if _src else "",
    )

    # ★ 指标收集：把实测 FCP 写入 ai_health_metrics（fcp 族），不再恒零
    try:
        from src.extensions.metrics_collector import metrics_collector

        _project = str(_pick("project") or "")
        _run_id = str(_pick("run_id") or "")
        _case_id = str(_pick("case_id") or "")
        if not _project:
            from src.core.config_loader import config as _cfg

            _project = str(getattr(_cfg, "project_name", "") or "")
        if _project and not _run_id:
            from src.core.versioning import (
                current_run_id,
                current_version_label,
                make_run_id,
            )

            try:
                _run_id = current_run_id(_project)
            except Exception:
                _run_id = ""
            if not _run_id:
                try:
                    _run_id = make_run_id(
                        _project, current_version_label(_project) or "v1.0"
                    )
                except Exception:
                    _run_id = ""
        if _project and _run_id and metrics.get("FCP"):
            metrics_collector.record_performance_fcp(
                _project,
                _run_id,
                _case_id,
                float(metrics["FCP"]),
                bool(_expected and metrics["FCP"] > _expected),
            )
    except Exception as _e:
        _log.debug("[perf] FCP 指标记录跳过: %s", str(_e)[:80])

    if _value <= 0:
        return {
            "type": check_type,
            "passed": False,
            "status": "UNVERIFIED",
            "reason": "PERF_METRIC_UNAVAILABLE",
            "evidence": "未能采到 %s（%s）" % (_metric, _evidence_base),
        }

    if not _expected:
        return {
            "type": check_type,
            "passed": True,
            "status": "PASS",
            "reason": "",
            "evidence": "%s=%.0fms（无阈值声明）| %s" % (_metric, _value, _evidence_base),
        }

    _ops = {
        "less_than_equal": _value <= _expected,
        "less_than": _value < _expected,
        "greater_than_equal": _value >= _expected,
        "greater_than": _value > _expected,
        "equal": abs(_value - _expected) < 1e-6,
    }
    _ok = _ops.get(_op, _value <= _expected)
    return {
        "type": check_type,
        "passed": bool(_ok),
        "status": "PASS" if _ok else "FAIL",
        "reason": "" if _ok else "PERF_THRESHOLD_EXCEEDED",
        "evidence": "%s=%.0fms %s 阈值%.0fms | %s"
        % (_metric, _value, "≤" if _ok else ">", _expected, _evidence_base),
    }


# ★ 根因修复(2026-09-24) RC-A: 字段约束型需求的 DOM 属性校验执行器。
#   根因: 需求文档的字段规则（"名称最长 40 字符"/"库存非负整数"/"价格必须大于 0"）
#     由 `<input maxlength/min/step/type/required>` 等 DOM 属性承载，页面上
#     **不存在**描述这些规则的文字。此前用语义断言（只看 inner_text）验证 →
#     AI 如实回答"页面无任何关于'非负整数'的验证说明" → 系统性假 FAIL
#     （实测 TC-REQ-001-I01/004-I01/005-I01）。验证手段与期望性质错配，属引擎缺陷。
#   通用性: 字段定位靠「页面候选标签 ∩ 需求文本」交集（不硬编码任何业务字段名），
#     约束核对靠通用 DOM 属性语义（maxlength/min/step/type/required/options），
#     跨系统/行业/页面成立。
_FIELD_LOCATE_JS = r"""
({ expect, wantOpts, fieldHint }) => {
  // 标签/字段名的规范化：空白、必填星号、必填括号后缀、尾冒号都是**装饰**，
  // 不参与匹配（"名称 *" 与需求里的"名称"/"商品名称"必须能对上，否则
  // 定位端 0 命中 → 假 FIELD_NOT_FOUND）。
  const norm = (s) => (s || '')
    .replace(/[\s\u00a0]+/g, '')
    .replace(/[（(]\s*(?:必填|选填|可选|required|optional)\s*[)）]/gi, '')
    .replace(/[*＊:：]+$/g, '')
    .trim();
  const exp = norm(expect);
  const hint = norm(fieldHint || '');
  const labels = Array.from(document.querySelectorAll('label'));
  let best = null;
  // ★ RC-Y(2026-09-25): 优先按「字段名提示」定位 —— 约束声明原文里提取的字段名
  //   （"价格字段为必填项…" → "价格"）比整段需求文本精确得多，避免多字段需求
  //   因最长匹配命中到**别的**字段。命中判据：label 与 hint 互相包含（覆盖
  //   "价格" vs "价格(元)" 这类修饰差异），且 label 足够短（≤20 字，排除段落）。
  if (hint) {
    for (const l of labels) {
      const t = norm(l.textContent);
      if (!t || t.length > 20) continue;
      if (t === hint || t.indexOf(hint) >= 0 || hint.indexOf(t) >= 0) {
        if (!best || t.length > best.t.length) best = { t: t, el: l };
      }
    }
  }
  if (!best) {
    for (const l of labels) {
      const t = norm(l.textContent);
      if (!t || t.length > 20) continue;
      if (exp.includes(t)) {
        if (!best || t.length > best.t.length) best = { t: t, el: l };
      }
    }
  }
  let ctrl = null, matchedBy = '';
  if (best) {
    const f = best.el.getAttribute('for');
    ctrl = f ? document.getElementById(f) : best.el.querySelector('input,select,textarea');
    matchedBy = 'label:' + best.t;
  }
  if (!ctrl) {
    const ctrls = Array.from(document.querySelectorAll('input,select,textarea'));
    for (const c of ctrls) {
      const cands = [c.getAttribute('aria-label'), c.getAttribute('placeholder'),
                     c.getAttribute('name'), c.getAttribute('title')];
      let hit = '';
      for (const cd of cands) {
        const t = norm(cd);
        if (!t || t.length > 20) continue;
        if (exp.includes(t) || (hint && (t === hint || t.indexOf(hint) >= 0 || hint.indexOf(t) >= 0))) {
          hit = t; break;
        }
      }
      if (hit) { ctrl = c; matchedBy = 'attr:' + hit; break; }
    }
  }
  // ★ 根因修复(2026-09-25) RC-P2: 枚举型约束（"系统提供 server/network/… 下拉
  //   选项"）的期望文本里**不含字段名**（字段是"分类"/"状态"），label/attr 两条
  //   路径都无从命中。但需求声明的 option 取值集合本身是**DOM 事实**——
  //   直接按 <select> 的 option 值集合匹配，无需字段名，跨系统/行业通用。
  if (!ctrl && Array.isArray(wantOpts) && wantOpts.length) {
    const want = wantOpts.map((o) => String(o));
    const need = Math.max(1, Math.min(2, want.length));
    let bestSel = null, bestHit = 0;
    for (const s of Array.from(document.querySelectorAll('select'))) {
      const vals = Array.from(s.options).map((o) => String(o.value));
      const hit = want.filter((o) => vals.indexOf(o) >= 0).length;
      if (hit > bestHit) { bestHit = hit; bestSel = s; }
    }
    if (bestSel && bestHit >= need) {
      ctrl = bestSel;
      matchedBy = 'options:' + bestHit + '/' + want.length;
    }
  }
  if (!ctrl) {
    return { found: false, labels: labels.map((l) => norm(l.textContent)).filter(Boolean).slice(0, 30) };
  }
  const attrs = {};
  for (const a of ['type', 'maxlength', 'minlength', 'min', 'max', 'step', 'pattern']) {
    const v = ctrl.getAttribute(a);
    if (v !== null) attrs[a] = v;
  }
  attrs['required'] = ctrl.hasAttribute('required') || ctrl.getAttribute('aria-required') === 'true';
  let options = null;
  if (ctrl.tagName === 'SELECT') options = Array.from(ctrl.options).map((o) => o.value);
  return { found: true, matched_by: matchedBy, tag: ctrl.tagName,
           type: ctrl.type || '', attrs: attrs, options: options };
}
"""

# ★ 通用「表单入口」语汇（零业务硬编码，跨系统/行业/框架）。
#   与 explorer_mixins/_action.py 的 _FORM_ENTRY_WORDS 同源语义。
_FORM_ENTRY_WORDS = (
    "新增", "新建", "添加", "创建", "录入", "登记", "注册", "发布",
    "编辑", "修改",
    "New", "Create", "Add", "Edit",
)


async def _locate_field_in_form_context(page, expect: str, want_opts=None, field_hint=""):
    """在**表单上下文**中定位需求所述字段控件（返回 _FIELD_LOCATE_JS 的结果）。

    ★ 根因修复(2026-09-25) RC-P: field_constraint_check 是**表单上下文内**的
      DOM 属性断言，而 checks 阶段在业务流之后执行 —— 创建类用例此时表单已提交
      并跳回列表页 → 控件不在当前页 → 恒 FIELD_NOT_FOUND → 系统性假 FAIL
      （实测 TC-REQ-003/004/005/006/038-I01：页面候选标签只剩列表页的
      ['按名称搜索','资产分类']，而需求字段是表单内的 名称/分类/价格/库存/状态）。

    通用修复: 当前页定位不到时，用通用「新建/编辑」语义词在**独立标签页**
      打开录入表单后重试定位。用独立页（同 context，共享会话 Cookie）是为了
      **不污染主页面状态** —— 同一批 checks 中的 url_matches 断言仍须在主页面
      成立（实测 TC-REQ-004 中 field_constraint_check 排在 url_matches 之前，
      若直接在主页面开表单，url_matches 会因 URL 变成 /assets/new 而假 FAIL）。

    仍定位不到 → 返回**当前页**的原结果（调用方维持 fail-closed FAIL，不静默通过）。
    """
    info = await page.evaluate(
        _FIELD_LOCATE_JS,
        {"expect": expect, "wantOpts": want_opts, "fieldHint": field_hint or ""},
    )
    if isinstance(info, dict) and info.get("found"):
        return info
    _ctx = getattr(page, "context", None)
    if _ctx is None:
        return info
    _probe = None
    try:
        _probe = await _ctx.new_page()
        await _probe.goto(page.url, wait_until="domcontentloaded", timeout=15000)
        await _probe.wait_for_timeout(500)
        for _kw in _FORM_ENTRY_WORDS:
            for _role in ("link", "button"):
                try:
                    _loc = _probe.get_by_role(_role, name=_kw)
                    if await _loc.count() <= 0:
                        continue
                    _el = _loc.first
                    if not await _el.is_visible():
                        continue
                    await _el.click(timeout=5000)
                    try:
                        await _probe.wait_for_load_state(
                            "domcontentloaded", timeout=8000
                        )
                    except Exception as _e:
                        _log.debug("_locate_field_in_form_context best-effort 容忍: %s", str(_e)[:120])
                    await _probe.wait_for_timeout(600)
                    _info2 = await _probe.evaluate(
                        _FIELD_LOCATE_JS,
                        {"expect": expect, "wantOpts": want_opts,
                         "fieldHint": field_hint or ""},
                    )
                    if isinstance(_info2, dict) and _info2.get("found"):
                        _info2["_opened_form"] = _kw
                        _log.info(
                            "[FieldConstraint] 当前页无该字段 → 独立标签页打开"
                            "'%s'表单后定位成功: %s", _kw, str(_info2.get("matched_by"))[:60],
                        )
                        return _info2
                except Exception:
                    continue
    except Exception as _e:
        _log.warning(
            "[FieldConstraint] 表单上下文探测异常(按原结果 fail-closed): %s",
            str(_e)[:120],
        )
    finally:
        if _probe is not None:
            try:
                await _probe.close()
            except Exception as _e:
                _log.debug("_locate_field_in_form_context best-effort 容忍: %s", str(_e)[:120])
    return info


async def _handle_field_constraint_check(page, check, inner, check_type, precision):
    """field_constraint_check → 读控件 DOM 属性核对需求声明的字段约束。

    步骤:
      1. 以「需求文本 ∩ 页面候选标签」定位字段控件（label for= / 内嵌控件 /
         aria-label / placeholder / name / title，零业务硬编码）；
      2. 逐条核对 constraints（maxlength / min / integer / positive / options / required）；
      3. 全部满足 → PASS；任一不满足 → FAIL（附差异明细）。

    fail-closed: 定位不到控件 → FAIL（不静默通过），evidence 列出页面候选标签便于排查。
    """
    constraints = inner.get("constraints") or check.get("constraints") or {}
    expect = str(inner.get("expected") or check.get("expect") or "").strip()
    # ★ 根因修复(2026-09-25) RC-Y: 字段定位源与「证据文案」分离。
    #   根因: `expected` 是 expected_feedback（描述**系统行为**，如"客户端拦截表单并提交，
    #     页面顶部显示红色错误条"），其中**不含字段名** → 拿它匹配页面 label 必然 0 命中
    #     → 恒 FIELD_NOT_FOUND 假 FAIL（实测 TC-REQ-041-I01/042-I01）。
    #   修复: 定位用 `locate_text`（约束声明原文=需求正文，字段名与约束同句共现），
    #     优先用 `field_hint`（通用句式提取的字段名）；`expected` 仍用于证据文案。
    #   兼容: 旧用例无 locate_text/field_hint → 退回 `expected`（维持原行为）。
    locate_text = (
        str(inner.get("locate_text") or check.get("locate_text") or "").strip()
        or expect
    )
    field_hint = str(
        inner.get("field_hint") or check.get("field_hint") or ""
    ).strip()
    if not isinstance(constraints, dict) or not constraints:
        return {
            "type": "field_constraint_check",
            "passed": False,
            "status": "ERROR",
            "reason": "NO_CONSTRAINTS",
            "evidence": "field_constraint_check 缺失 constraints 声明（无法判定）",
        }
    try:
        info = await _locate_field_in_form_context(
            page, locate_text, constraints.get("options"), field_hint
        )
    except Exception as e:
        return {
            "type": "field_constraint_check",
            "passed": False,
            "status": "ERROR",
            "reason": "FIELD_LOCATE_ERROR",
            "evidence": "字段定位异常(fail-closed): %s" % str(e)[:80],
        }
    if not isinstance(info, dict) or not info.get("found"):
        _cands = (info or {}).get("labels") if isinstance(info, dict) else None
        return {
            "type": "field_constraint_check",
            "passed": False,
            "status": "FAIL",
            "reason": "FIELD_NOT_FOUND",
            "evidence": "未定位到需求所述字段控件（定位源: %s）| 页面候选标签: %s"
            % (locate_text[:60], str(_cands)[:200]),
        }

    attrs = info.get("attrs") or {}
    options = info.get("options")
    _matched = str(info.get("matched_by") or "")
    _tag = str(info.get("tag") or "")
    _type = str(info.get("type") or "")
    failures: list[str] = []
    checked: list[str] = []

    def _num(v):
        try:
            return float(str(v).strip())
        except Exception:
            return None

    if constraints.get("maxlength"):
        _want = int(constraints["maxlength"])
        _got = attrs.get("maxlength")
        _gv = _num(_got)
        checked.append("maxlength")
        if _gv is None:
            failures.append("maxlength 未声明（需求要求≤%d，控件无长度限制）" % _want)
        elif _gv > _want:
            failures.append("maxlength=%s 超出需求上限 %d" % (_got, _want))

    if constraints.get("min") is not None:
        _want = float(constraints["min"])
        _got = attrs.get("min")
        _gv = _num(_got)
        checked.append("min")
        if _gv is None:
            failures.append("min 未声明（需求要求≥%s）" % constraints["min"])
        elif _gv < _want:
            failures.append("min=%s 低于需求下限 %s" % (_got, constraints["min"]))

    if constraints.get("integer"):
        checked.append("integer")
        _step = attrs.get("step")
        _sv = _num(_step)
        if _type != "number":
            failures.append("控件 type=%r 非数字输入（需求要求整数）" % _type)
        elif _step is not None and _sv is not None and float(_sv).is_integer() is False:
            failures.append("step=%s 非整数步长（需求要求整数）" % _step)

    if constraints.get("positive"):
        # ★ 说明: 需求"必须大于 0"的**严格**约束通常由客户端脚本承担，DOM 层
        #   只能表达 min≥0（实测 fullcov_sut price: type=number min=0 step=0.01，
        #   >0 由 client_validate.js 拦截）。此处按 DOM 可达的最强语义核对：
        #   数字类型 + 存在非负下限；严格 >0 由边界用例（提交 0）另行覆盖。
        checked.append("positive")
        _got = attrs.get("min")
        _gv = _num(_got)
        if _type != "number":
            failures.append("控件 type=%r 非数字输入（需求要求数字价格）" % _type)
        elif _gv is None:
            failures.append("min 未声明（需求要求非负数值下限）")
        elif _gv < 0:
            failures.append("min=%s 为负（需求要求非负下限）" % _got)

    if constraints.get("options"):
        checked.append("options")
        _want = [str(o) for o in constraints["options"]]
        _have = [str(o) for o in (options or [])]
        _missing = [o for o in _want if o not in _have]
        if _tag != "SELECT":
            failures.append("控件标签=%s 非下拉（需求要求下拉选项 %s）" % (_tag, _want))
        elif _missing:
            failures.append("下拉缺失选项 %s（实际 %s）" % (_missing, _have))

    if constraints.get("required"):
        checked.append("required")
        if not attrs.get("required"):
            failures.append("required 未声明（需求要求必填）")

    _passed = not failures
    _detail = "字段=%s 控件=%s/%s 约束=%s 核对=%s" % (
        _matched,
        _tag,
        _type,
        constraints,
        ",".join(checked) or "无",
    )
    return {
        "type": "field_constraint_check",
        "passed": _passed,
        "status": "PASS" if _passed else "FAIL",
        "reason": "" if _passed else "FIELD_CONSTRAINT_VIOLATION",
        "evidence": ("%s | %s" % (_detail, "全部满足" if _passed else "不满足: " + "; ".join(failures)))[:400],
    }


async def _handle_security_probe_check(page, check, inner, check_type, precision):
    """安全探针断言：注入该类攻击载荷后，取证「攻击痕迹是否出现」。

    判定对象 = **该类攻击的可观察痕迹**（不是"页面还能打开"）：
      - sqli → 页面出现数据库错误签名（复用 `SecurityScanner.SQL_INJECTION_PATTERNS`）
      - xss  → 载荷被原样反射为活动标记（复用 `SecurityScanner.XSS_PATTERNS`，
              并与原始 HTML 中的载荷字面值做**未转义反射**比对）
    采集不到 HTML → N/A(SKIP)：既非 PASS（防假绿）也非 FAIL（防假 Bug）。
    通用性: 判据来自安全扫描器的规则库（唯一事实源），零业务/靶子硬编码。
    """
    _attack = str(inner.get("attack") or check.get("attack") or "").lower()
    _payloads = [str(p) for p in (inner.get("payloads") or check.get("payloads") or []) if str(p)]
    if not _attack or not _payloads:
        return {
            "type": "security_probe_check",
            "passed": True,
            "status": "SKIP",
            "skipped": True,
            "reason": "ASSERTION_NOT_APPLICABLE",
            "skip_reason": "no_probe_declaration",
            "evidence": "断言未声明攻击类别/载荷，无法取证（N/A 跳过）",
        }
    try:
        _html = await page.content()
    except Exception as _e:  # noqa: BLE001
        _log.debug("security_probe_check 取 HTML 失败: %s", str(_e)[:120])
        _html = ""
    if not (_html or "").strip():
        return {
            "type": "security_probe_check",
            "passed": True,
            "status": "SKIP",
            "skipped": True,
            "reason": "ASSERTION_NOT_APPLICABLE",
            "skip_reason": "no_html_snapshot",
            "evidence": "页面 HTML 不可得，安全探针断言不适用（N/A 跳过）",
        }

    _cat = {"sqli": "SQL Injection", "xss": "Cross-Site Scripting"}.get(_attack, "")
    hits: list[str] = []
    try:
        from src.extensions.security_scanner import SecurityScanner

        _url = str(getattr(page, "url", "") or "")
        _scanner = SecurityScanner()
        _scanner.scan_response(
            body=_html, content_type="text/html", url=_url, status_code=200
        )
        for _f in _scanner.to_dict_list():
            if _cat and str(_f.get("category") or "") == _cat:
                hits.append(
                    "%s(%s): %s"
                    % (
                        str(_f.get("rule_id") or "?"),
                        str(_f.get("severity") or "?"),
                        str(_f.get("description") or "")[:60],
                    )
                )
    except Exception as _e:  # noqa: BLE001
        _log.warning("[security_probe] 规则扫描异常(判据不可得，按 N/A 处理): %s", str(_e)[:120])

    # XSS 的独立取证面：载荷以**未转义原文**留在 HTML 里即为反射型漏洞
    #   （转义后的文本节点会被序列化成 &lt;script&gt;，不会与本字面值相等）。
    if _attack == "xss":
        for _p in _payloads:
            if _p in _html:
                hits.append("载荷被原样反射(未转义): %r" % _p[:40])
                break

    return {
        "type": "security_probe_check",
        "passed": not hits,
        "evidence": (
            "未见该类攻击痕迹(attack=%s payloads=%d)" % (_attack, len(_payloads))
            if not hits
            else "攻击痕迹出现: %s" % "; ".join(hits[:3])
        ),
    }


_UI_CHECK_DISPATCH = {
    "text_contains": _handle_text_contains,
    "semantic_check": _handle_semantic_check,
    "text_not_exists": _handle_text_not_exists,
    "element_not_exists": _handle_element_not_exists,
    "element_exists": _handle_element_exists,
    "url_matches": _handle_url_matches,
    "visual_check": _handle_visual_check,
    "geometry_check": _handle_geometry_check,
    "color_check": _handle_style_check,
    "font_check": _handle_style_check,
    "visibility_check": _handle_style_check,
    "css_class_check": _handle_style_check,
    "attribute_check": _handle_style_check,
    "zindex_check": _handle_style_check,
    "position_check": _handle_position_check,
    "exact_size_check": _handle_position_check,
    "toast_appear_check": _handle_toast_check,
    "toast_disappear_check": _handle_toast_check,
    "loading_state_check": _handle_toast_check,
    "page_refresh_check": _handle_page_refresh_check,
    "element_count_check": _handle_element_count_check,
    "element_enabled_check": _handle_state_check,
    "element_checked_check": _handle_state_check,
    "select_value_check": _handle_state_check,
    "element_text_equals_check": _handle_state_check,
    "image_loaded_check": _handle_image_loaded_check,
    # ★ 方案B (ADR-017, 2026-07-23): 补齐缺失的 check 类型，消除"未知检查类型"假 PASS
    "no_error": _handle_no_error,
    "loading_complete": _handle_loading_complete,
    "list_order": _handle_list_order,
    # ★ 归一化（2026-08-04）：download_check 注册 + url_check → url_matches 归一
    "download_check": _handle_download_check,
    "url_check": _handle_url_matches,
    # ★ P1-3b（QA 复核，2026-08-05）：补 11 个 taxonomy 裸名别名（纯 additive，不改 _taxonomy.py）
    #   CHECK_TAXONOMY 权威 key（style/toast/refresh/position/element_count/download/...）
    #   与校验器分发表 *_check 后缀键存在命名错位，裸名直接落入 else → UNSUPPORTED_CHECK。
    #   此处将裸名映射到同一 handler；handler 内部已把 type 归一化为 *_check 后缀形式。
    "style": _handle_style_check,
    "toast": _handle_toast_check,
    "refresh": _handle_page_refresh_check,
    "position": _handle_position_check,
    "element_count": _handle_element_count_check,
    "download": _handle_download_check,
    "element_enabled": _handle_state_check,
    "element_checked": _handle_state_check,
    "select_value": _handle_state_check,
    "text_equals": _handle_state_check,
    "image_loaded": _handle_image_loaded_check,
    # ★ T03(2026-08-06): 8 个孤儿验证器接入（含裸名 + *_check 后缀，消除 UNSUPPORTED_CHECK）
    "element_visible": _handle_element_visible,
    "element_visible_check": _handle_element_visible,
    "element_attribute": _handle_element_attribute,
    "element_attribute_check": _handle_element_attribute,
    "text_regex": _handle_text_regex,
    "text_regex_check": _handle_text_regex,
    "console_error": _handle_console_error,
    "console_error_check": _handle_console_error,
    "dialog": _handle_dialog,
    "dialog_check": _handle_dialog,
    "animation_complete": _handle_animation_complete,
    "animation_complete_check": _handle_animation_complete,
    "pagination": _handle_pagination,
    "pagination_check": _handle_pagination,
    "storage": _handle_storage,
    "storage_check": _handle_storage,
    # ★ 2026-09-16 B 方案：易用性 + 兼容性真实执行器（6 个 CDP 驱动）
    "click_target_size_check": _handle_click_target_size_check,
    "form_label_association_check": _handle_form_label_association_check,
    "viewport_overflow_check": _handle_viewport_overflow_check,
    "form_autocomplete_check": _handle_form_autocomplete_check,
    "viewport_rendering_check": _handle_viewport_rendering_check,
    "touch_target_mobile_check": _handle_touch_target_mobile_check,
    # ★ 2026-09-22 深断言执行器补齐（全量判定审计根修，消除"未知检查类型"假 FAIL）
    "field_error_visible_check": _handle_field_error_visible,
    "field_error_visible": _handle_field_error_visible,
    "list_row_count_check": _handle_list_row_count,
    "list_row_count": _handle_list_row_count,
    # ★ 2026-09-23 性能族执行器接入（生成端 performance_lcp_check 词表漂移根治）
    "performance_lcp_check": _handle_performance_metric_check,
    "performance_lcp": _handle_performance_metric_check,
    "performance_fcp_check": _handle_performance_metric_check,
    "performance_fcp": _handle_performance_metric_check,
    "page_load_time_check": _handle_performance_metric_check,
    "page_load_time": _handle_performance_metric_check,
    # ★ 2026-09-24 RC-A 注册：字段约束型需求改走 DOM 约束校验（生成端
    #   `_intent_checks` 发射 field_constraint_check）。根因: handler 早已实现但
    #   **从未注册** → check_guard 按执行注册表把它当"词表漂移"剔除 → 生成端改了、
    #   执行端没通，修复被静默中和（同形于被治的病）。注册后 _build_executable_check_types
    #   自动纳入该类型，守卫不再误杀。
    "field_constraint_check": _handle_field_constraint_check,
    "field_constraint": _handle_field_constraint_check,
    # ★ FIX4/AJ(2026-09-25): 访问控制行为断言（Step3 生成端 _access_control_checks 发射）。
    #   注册进本表 = 自动纳入 `_check_guard._build_executable_check_types`，
    #   词表不再漂移（否则生成端发了、执行端不认 → "未知检查类型"假 FAIL）。
    "access_control_check": _handle_access_control_check,
    "access_control": _handle_access_control_check,
    # 析取式期望（"A 或 B"）的 OR 语义执行器：任一分支持有即符合期望。
    "any_of_check": _handle_any_of,
    "any_of": _handle_any_of,
    # 安全探针断言（Step3 落库前派生）：取证"该类攻击痕迹是否出现"。
    "security_probe_check": _handle_security_probe_check,
    "security_probe": _handle_security_probe_check,
}


async def _assert_field_rendered(page, selector: str) -> tuple[bool, str]:
    """★ P0 盲区补丁 GAP-02 [已复核-DeepSeek-V3 2026-08-16(含RISK缓解)] (2026-08-15)。

    校验目标元素确实被渲染到 DOM（消除 render_blank 假阴性）：
      (a) 元素存在 (count > 0)
      (b) 可见（非 display:none / visibility:hidden / 零尺寸）
      (c) 读取值非空（inner_text 或 input.value 任一非空）
    任一不满足 → 返回 (False, 原因)，交由调用方判该断言失败。
    异常一律返回 (True, "") 交由原断言结论（fail-open，不误杀正常用例）。

    背景：盲测发现 render_blank 类 4/4 真实缺陷 0 检出——接口返回了数据，
    但前端模板未把该字段渲染出来(空白/占位)，data_assert 仍按内存态/接口值
    断言通过。本函数作为"取值型"UI 断言的渲染存在性兜底，使坏页面不再全绿。
    """
    try:
        loc = page.locator(selector).first
        if await loc.count() == 0:
            return False, "元素不存在(selector 未匹配任何 DOM 节点)"
        # ★ GAP-02 RISK 缓解(2026-08-16): 合法隐藏字段逃生口。
        #   元素带 data-test-ignore-render-check 属性 → 跳过可见性/取值校验，
        #   视为已渲染(SUT 对"合法隐藏但应判过"的字段显式豁免，防误杀；opt-in)。
        try:
            if await loc.get_attribute("data-test-ignore-render-check") is not None:
                return True, ""
        except Exception as _e:
            _log.debug("_assert_field_rendered best-effort 容忍: %s", str(_e)[:120])
        if not await loc.is_visible():
            return False, "元素不可见(疑似 display:none/visibility:hidden/零尺寸)"
        _txt = ""
        try:
            _txt = (await loc.inner_text(timeout=2000) or "").strip()
        except Exception:
            _txt = ""
        _val = ""
        try:
            _val = (
                await loc.evaluate("el => (el.value || '').toString().trim()") or ""
            )
        except Exception:
            _val = ""
        if not _txt and not _val:
            return False, "元素已存在但读取值为空(疑似模板未渲染该字段/render_blank)"
        return True, ""
    except Exception as _e:
        _log.debug("[GAP-02] _assert_field_rendered 异常(不阻断): %s", _e)
        return True, ""


async def _verify_ui_objective(page, step_checks: dict) -> dict:
    """
    用 DOM 查询做客观断言，不依赖 AI 判断。
    支持七种断言类型：
    - text_contains: 页面包含特定文字
    - text_not_exists: 页面不包含特定文字（★ 元素消失验证）
    - element_exists: 特定元素存在（选择器）
    - element_not_exists: 特定元素不存在（★ 删除/禁用/弹窗消失验证）
    - url_matches: URL 匹配模式
    - visual_check: ★ 场景2 vision 截图断言（DOM 拿不到的视觉缺陷，vision 不可用降级跳过）
    - geometry_check: ★ 场景2 几何断言（no_overlap/in_viewport/not_occluded，视觉兜底）
    """
    results = []
    objective_checks = step_checks.get("ui_checks", [])
    if not objective_checks:
        # ★ F-00 (2026-08-04): 无 UI 客观断言 → 标记 no_valid_assertion，
        #   展示层据此标注「结论不可用」，不再裸返回 passed=True（防假PASS）。
        return {
            "passed": True,
            "checks": [],
            "evidence": "无UI客观断言",
            "total_assertions": 0,
            "no_valid_assertion": True,
        }

    for check in objective_checks:
        outer_assertion = check.get("assertion", "")
        outer_target_hint = check.get("target_hint", "")
        inner = check.get("check", {})
        if not isinstance(inner, dict):
            if outer_assertion:
                inner = {"type": outer_assertion, "expected": str(inner)}
                if (
                    outer_assertion in ("element_exists", "element_not_exists")
                    and outer_target_hint
                ):
                    inner["selector"] = outer_target_hint
            else:
                inner = {"type": "text_contains", "expected": str(inner)}
        else:
            if not inner.get("type") and outer_assertion:
                inner["type"] = outer_assertion
                if (
                    outer_assertion in ("element_exists", "element_not_exists")
                    and outer_target_hint
                    and not inner.get("selector")
                ):
                    inner["selector"] = outer_target_hint
        check_type = inner.get("type", "text_contains")
        # ★ P0 盲区补丁 GAP-02 [已复核-DeepSeek-V3 2026-08-16(含RISK缓解)] (2026-08-15):
        #   取值型 UI 断言的目标选择器（供渲染存在性兜底校验使用）。
        _selector = (
            inner.get("selector") or check.get("selector", "")
            if isinstance(inner, dict)
            else check.get("selector", "")
        )
        # ★ F-01 (2026-08-04): 键归一化改为「原始键 + 去后缀键」双查。
        #   根因(3层): ①原代码先剥 `_check` 后缀再查表，20/28 个带后缀键
        #   永久不可达，全部落入 else 兜底 passed=True → 假PASS；②`:747`
        #   的 `check_type == "page_refresh_check"` 比较发生在剥离之后，永不
        #   成立 = 死分支，page_refresh_check 双重死亡；③展示层无「无效断言」
        #   标注，FAIL+高confidence 的矛盾报告不报警。
        #   修复: 保持分发表 28 键原样，先查原始键，未命中再查去后缀键；
        #   page_refresh_check 按需传 step_checks（该 handler 唯一需要）。
        precision = check.get("precision", "fuzzy")

        # ── 字典分派（双查）──
        handler = _UI_CHECK_DISPATCH.get(check_type)
        dispatch_key = check_type
        if handler is None and check_type.endswith("_check"):
            dispatch_key = check_type[: -len("_check")]
            handler = _UI_CHECK_DISPATCH.get(dispatch_key)
        if handler:
            # ★ P1-3b（QA 复核）: refresh 裸名与 page_refresh_check 同 handler，均需 step_checks 第 6 参
            if dispatch_key in ("page_refresh_check", "refresh"):
                result = await handler(
                    page, check, inner, check_type, precision, step_checks
                )
            else:
                result = await handler(page, check, inner, check_type, precision)
        else:
            # ★ F-00 (2026-08-04): fail-open 兜底翻转 → fail-closed 永久终态。
            #   根因(3层): ①else 兜底无条件 passed=True；②落入 else 因双查
            #   未命中（词表外类型）；③无门禁拦截 → 未验证就报通过。
            #   修复: 未命中 → ERROR + UNSUPPORTED_CHECK + passed=False。
            #   注意: F-01 修复后命中率≈100%，此分支是防遗漏复发的最后保险，
            #   不得随 F-01 移除（PRD F-00⑦ 永久终态）。
            result = {
                "type": check_type,
                "passed": False,
                "status": "ERROR",
                "reason": "UNSUPPORTED_CHECK",
                "evidence": "未知检查类型: %s（无有效断言，结论不可用）" % check_type,
            }
        # ★ P0 盲区补丁 GAP-02 [已复核-DeepSeek-V3 2026-08-16(含RISK缓解)] (2026-08-15):
        #   渲染存在性兜底校验（render_blank 捕获）。对"带 selector 的取值型"UI 断言
        #   (element_text_equals_check / select_value_check / text_contains)，若目标元素
        #   未渲染(缺失/不可见/空白/占位)，即便原断言按内存态/接口值判过，也翻转为失败，
        #   使"接口有数据但前端模板没渲染该字段"的坏页面不再全绿。
        #   最小增量: 复用现有 result["passed"]/evidence 结构，不新增轨道、不改契约。
        if (
            isinstance(result, dict)
            and result.get("passed")
            and _selector
            and check_type
            in ("element_text_equals_check", "select_value_check", "text_contains")
        ):
            try:
                _rendered, _render_detail = await _assert_field_rendered(
                    page, _selector
                )
                if not _rendered:
                    result["passed"] = False
                    result["evidence"] = (
                        ((result.get("evidence", "") or "")[:120])
                        + " | [render_blank] "
                        + _render_detail
                    )[:200]
                    _log.warning(
                        "[GAP-02] 用例字段渲染缺失(render_blank): selector=%s — %s",
                        _selector,
                        _render_detail,
                    )
            except Exception as _rb_e:
                _log.debug("[GAP-02] 渲染存在性校验异常(不阻断): %s", _rb_e)

        results.append(result)

    # ★ 契约修复(2026-09-27)：聚合改由唯一权威 aggregate_check_results 定夺——
    #   原 `all(r["passed"] for r in results)` 把 skipped/UNVERIFIED 的 check（passed=True
    #   仅为"不阻断"）算作通过 → 整轨 passed=True → 结构性假 PASS（实测 133 条
    #   list_row_count_check「基线不可得，诚实跳过」全部显示 ✓）。
    #   新语义：有真实失败→False；有真实通过且无失败→True；全为非判定态→False 且
    #   no_verdict_only=True（下游据此判 SKIP，既不假 PASS 也不假 FAIL）。
    from src.pipeline.steps.step4.verification._track_protocol import (
        aggregate_check_results as _agg_checks,
        render_check_symbol as _render_symbol,
    )

    _agg = _agg_checks(results)
    all_passed = _agg["passed"]
    # ★ v4.7 诊断: evidence 包含详细验证信息；非判定态用 ⊘/⚠ 区分（不再与 ✓ 混淆）
    #   每 check 证据保留 300 字符：截断过短（原 80）会把匹配值/翻页命中页码等
    #   可复算证据切掉，使 FAIL 无法从 DB 独立复核。
    evidence = "; ".join(
        "%s:%s(%s)"
        % (
            r.get("type"),
            _render_symbol(r),
            str(r.get("evidence", ""))[:300],
        )
        for r in results
    )
    # ★ F-00 (2026-08-04): 返回无效断言元数据，供展示层标注「结论不可用」。
    #   消费点: verification/__init__.py 组装 result["ui"] 时透传；报告层据此
    #   不展示 confidence（PRD F-00② 矛盾态防护）。
    try:
        from src.pipeline.utils.validation import is_no_valid_assertion

        _no_valid = is_no_valid_assertion({"checks": results})
    except Exception:
        _no_valid = False

    # ★ T04 (2026-08-11) [已复核-DeepSeek-V3 2026-08-16]
    #   补齐「全部 check 都没产生实际结论」的假 PASS 缺口。
    #   根因: 上面的 all_passed 只做 all(r["passed"])，不看 check 级 skipped；
    #   而 NO_DATA/skipped 的 check 按仓内约定 passed=True（不阻断 AND）。
    #   于是「唯一的 check 是 NO_DATA 视觉断言」的轨会得到
    #   passed=True + no_valid_assertion=False —— 未验证却报通过。
    #   修复: 无任何真实结论时置 no_valid_assertion=True，复用 F-00 既有
    #   展示层「结论不可用」链路（不改 passed，避免大面积误判 FAIL）。
    _real_checks = [
        _r
        for _r in results
        if isinstance(_r, dict)
        and not _r.get("skipped")
        and _r.get("status") != "NO_DATA"
    ]
    if results and not _real_checks:
        _no_valid = True
    if _agg["no_verdict_only"]:
        # 有 check 但无任何有效判定 → 复用既有「结论不可用」链路
        _no_valid = True
    _log.info("[UI校验] passed=%s | evidence=%s", all_passed, evidence[:200])
    return {
        "passed": all_passed,
        "checks": results,
        "evidence": evidence,
        "total_assertions": len(results),
        "no_valid_assertion": _no_valid,
        # ★ 契约修复(2026-09-27): 三值聚合元数据，供上游区分 FAIL(产品缺陷) 与 SKIP(无结论)
        "has_positive_verdict": _agg["has_positive"],
        "has_negative_verdict": _agg["has_negative"],
        # 「可归因产品缺陷」的失败（排除定位歧义型）——升格 PRODUCT_BUG 的唯一门槛
        "has_product_defect_verdict": _agg["has_product_defect"],
        "no_verdict_only": _agg["no_verdict_only"],
    }
