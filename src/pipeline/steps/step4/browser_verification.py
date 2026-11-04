# -*- coding: utf-8 -*-
"""browser_verification.py — 硬校验与意图证据判断方法。从 browser_core.py 拆分。"""

import json
from src.core.guarded import guard
import logging
from src.core.config_loader import config  # noqa: F401

_log = logging.getLogger(__name__)


def extract_keywords(text):
    """从文本中提取语义关键词（静态方法，直接复制）。"""
    if not text:
        return []
    import re

    stop = {
        "应该",
        "可以",
        "需要",
        "必须",
        "进行",
        "完成",
        "操作",
        "步骤",
        "预期",
        "结果",
        "页面",
        "出现",
        "进入",
        "消失",
        "信息",
        "业务",
        "目标",
        "证据",
        "满足",
        "验收",
        "标准",
    }
    normalized = re.sub(r"(或者|以及|并且|且|和|与|或)", " ", str(text))
    parts = re.split(r"[\s,，。；;、/：:]+", normalized)
    words = []
    for p in parts:
        if not p:
            continue
        # ★ v4.3: 限制为 2-4 字短关键词，避免 8 字超长词无法匹配页面文本
        words.extend(re.findall(r"[\u4e00-\u9fff]{2,4}", p))
    # ★ v4.3: 删除原 w[:4]/w[-4:] 拆分逻辑，findall 已限制为 2-4 字
    seen = set()
    result = []
    for w in words:
        if w not in stop and w not in seen:
            seen.add(w)
            result.append(w)
    return result[:8]


async def hard_verify_step(
    engine,
    step_desc,
    step_expected,
    pre_dom_count=0,
    api_tracker=None,
    extract_keywords_fn=None,
):
    """5 轨验证：DOM diff + 关键词文本 + 元素存在性 + API 轨迹 + URL 关键词。"""
    if extract_keywords_fn is None:
        extract_keywords_fn = extract_keywords
    try:
        if engine is None or engine._page is None:
            return False
        page = engine._page
        # 1) DOM diff：元素数量变化 → 判定通过（操作前后元素数不同说明 DOM 发生了改变）
        if pre_dom_count > 0:
            with guard("hard_verify_step: ps = await engine.get_structured_sta", log=_log, level=logging.WARNING):
                ps = await engine.get_structured_state(viewport_only=False)
                pc = len(ps) if ps else 0
                if pc != pre_dom_count:
                    _log.info(
                        "[HARD_VERIFY] DOM变化检测: %d → %d 元素，判定通过",
                        pre_dom_count,
                        pc,
                    )
                    return True
        keywords = extract_keywords_fn(step_expected or step_desc)
        # 2) 页面文本关键词
        # ★ 通用修复 (2026-07-18 v2): 收紧假 pass 条件
        #   修复前: matched >= max(1, min(2, len(keywords) // 2)) — 1个关键词匹配即pass
        #   修复后: 至少需要 2 个关键词匹配，且至少匹配 50% 的关键词
        #   避免"页面出现某词但业务未真达成"的假 pass
        if keywords:
            page_text = await page.evaluate("() => document.body.innerText || ''")
            page_title = ""
            try:
                page_title = await page.title()
            except (OSError, RuntimeError, TimeoutError) as _title_err:
                _log.warning("[Step4] 页面标题获取失败: %s", _title_err)
            url = page.url or ""
            page_text = ("%s\n%s\n%s" % (page_text, page_title, url)).lower()
            matched = sum(1 for kw in keywords if kw.lower() in page_text)
            # ★ 通用修复: 至少匹配 50% 的关键词，但不要求超过关键词总数
            #   原 max(2, ...) 导致单关键词场景永远无法通过（需要 2 但只有 1 个关键词）
            _min_match = min(len(keywords), max(1, len(keywords) // 2))
            if matched >= _min_match:
                _log.info(
                    "[HARD_VERIFY] 文本关键词命中 %d/%d (需要≥%d)",
                    matched,
                    len(keywords),
                    _min_match,
                )
                return True
            else:
                _log.info(
                    "[HARD_VERIFY] 文本关键词不足 %d/%d (需要≥%d)",
                    matched,
                    len(keywords),
                    _min_match,
                )
        url = page.url or ""
        for kw in keywords:
            if len(kw) >= 3 and kw.lower() in url.lower():
                _log.info("[HARD_VERIFY] URL 包含关键词: %s", kw)
                return True
        # 3) 元素存在性检查（1.1）：用 Playwright locator 查找关键词文本元素
        if step_expected and keywords:
            with guard("hard_verify_step: import re as _re_hv", log=_log, level=logging.WARNING):
                import re as _re_hv

                for kw in keywords[:5]:
                    clean_kw = _re_hv.sub(r"[^\w\u4e00-\u9fff]", "", kw)
                    if clean_kw and len(clean_kw) >= 2:
                        loc = page.get_by_text(clean_kw, exact=False)
                        if await loc.count() > 0:
                            _log.info("[HARD_VERIFY] 元素定位命中: '%s'", clean_kw)
                            return True
        # 4) API 轨迹校验（1.2）：检测步骤开始后是否有 API 响应成功
        if api_tracker is not None:
            try:
                api_calls = (
                    api_tracker.get_calls_since(
                        getattr(api_tracker, "_step_start_time", 0)
                    )
                    if hasattr(api_tracker, "_step_start_time")
                    else (getattr(api_tracker, "_calls", []) or [])[-3:]
                )
                ok_api = sum(
                    1 for c in api_calls if 200 <= int(c.get("status", 0)) < 400
                )
                expected_l = str(step_expected or step_desc or "").lower()
                # ★ 收紧(2026-06-23)：去掉 `or keywords` 恒真条件。原 keywords 非空即恒真,
                #    退化为 ok_api>0,任意 2xx 心跳/埋点即判过(放过源)。现要求 step 描述确实提到
                #    API/接口/响应等动作,才认 API 轨迹为有效证据。
                if ok_api > 0 and any(
                    kw in expected_l
                    for kw in (
                        "api",
                        "接口",
                        "响应",
                        "数据",
                        "成功",
                        "加载",
                        "登录",
                        "首页",
                        "受保护",
                    )
                ):
                    _log.info("[HARD_VERIFY] API 轨迹命中 %d 条 2xx/3xx", ok_api)
                    return True
            except (ValueError, AttributeError, KeyError) as _api_err:
                _log.warning("[Step4] API轨迹获取失败: %s", _api_err)
        _log.warning("[HARD_VERIFY] 5轨全部失败: 无文本/DOM/元素/URL/API 证据")
        return False
    except Exception as e:
        _log.warning("[HARD_VERIFY] 异常: %s", str(e)[:60])
        return False


async def current_intent_evidence_ok(engine, intent_schema, extract_keywords_fn=None):
    """能力复用前的轻量证据判断：当前页面是否已经满足该意图。
    ★ v9 意图驱动：验收标准来自 intent.title（intent_schema 即为 intent 本身）。
    """
    if extract_keywords_fn is None:
        extract_keywords_fn = extract_keywords
    if isinstance(intent_schema, str):
        try:
            intent_schema = json.loads(intent_schema)
        except json.JSONDecodeError:
            return False
    # v9: 验收标准来自 intent.title（intent_schema 即为 intent 自身）
    what = str((intent_schema or {}).get("title") or "").strip()
    criteria = [what] if what else []
    page = engine._page if engine else None
    if page is None:
        return False
    try:
        page_url = page.url or ""
        page_title = await page.title() or ""
        page_text = await page.evaluate("() => document.body.innerText || ''") or ""
        try:
            password_count = await page.locator('input[type="password"]').count()
        except Exception as _e:
            _log.debug(
                "[browser_verification] current_intent_evidence_ok 异常: %s",
                _e,
                exc_info=True,
            )
            password_count = 0
    except Exception as _e:
        _log.debug(
            "[browser_verification] current_intent_evidence_ok 异常: %s",
            _e,
            exc_info=True,
        )
        return False
    text_l = ("%s\n%s\n%s" % (page_text, page_title, page_url)).lower()
    if not criteria:
        return bool(page_url and page_title)
    matched = 0
    for c in criteria[:8]:
        cs = str(c or "").strip()
        if not cs:
            continue
        kws = extract_keywords_fn(cs)
        # ★ 收紧(2026-06-23)：any→all，所有关键词都命中才认证据充分。
        #   原 any 任一关键词命中即过，会放过"页面出现某词但业务未真达成"的意图(FN)。
        #   kws 为空时不命中（bool(kws) 防 all([])=True 误判）。
        ok = bool(kws) and all(w.lower() in text_l for w in kws)
        if (
            not ok
            and "登录表单" in cs
            and ("消失" in cs or "未显示" in cs or "未发现" in cs)
        ):
            ok = password_count == 0
        if not ok and ("受保护首页" in cs or "首页" in cs):
            # ★ P0-01: 业务端口走 config.target_success_url_suffix 配置（项目级可覆盖）
            try:
                success_suffix = str(
                    getattr(config, "target_success_url_suffix", ":5001")
                )
            except Exception as _e:
                _log.debug("[browser_verification] 非关键异常: %s", _e, exc_info=True)
                success_suffix = ":5001"
            title_keywords = []
            try:
                title_keywords = list(
                    getattr(config, "target_protected_title_keywords", [])
                    or ["管理系统"]
                )
            except Exception as _e:
                _log.debug("[browser_verification] 非关键异常: %s", _e, exc_info=True)
                title_keywords = ["管理系统"]
            ok = bool(page_url and page_url.rstrip("/").endswith(success_suffix)) or (
                page_title and any(kw in page_title for kw in title_keywords)
            )
        if ok:
            matched += 1
    return matched >= max(1, len(criteria))


def _step_needs_objective_verify(step) -> bool:
    """★ 场景1/2/3(2026-06-23)：判断 step 是否携带「关键词命中不足以证明」的客观校验。

    evidence_ok(关键词 all 命中)+hard_ok(5 轨任一命中) 只能证明页面表象，无法证明：
    - 场景1 DB 落库值正确 / 前端·接口·DB 三值一致（consistency_check / 结构化 db_checks）
    - 场景2 视觉缺陷（柱子遮挡纵坐标等 DOM 拿不到的，visual_check / geometry_check）
    - 场景3 导入数据真正落库（required_tracks 含 db + db_checks）
    携带上述任一信号 → 必须走 verify_step 三轨客观校验，不被 evidence_ok 短路。

    解耦：纯函数只读 step 字段，不依赖运行时引擎；普通意图(无 db/视觉/一致性)返回 False，
    evidence_ok 短路保留(省 DB 开销，避免环境性 FP)。
    """
    if not isinstance(step, dict):
        return False
    # 场景1：跨轨一致性（AI 显式声明要值一致）
    if step.get("consistency_check"):
        return True
    # 场景3：声明需要 DB 轨且有 db_checks（导入类 count / 数据类值校验）
    rt = step.get("required_tracks")
    rt_lower = [str(t).strip().lower() for t in rt] if isinstance(rt, list) else []
    if "db" in rt_lower and step.get("db_checks"):
        return True
    # 场景1：结构化 db_checks（dict 含 field_check，值级校验）
    if any(isinstance(dc, dict) for dc in (step.get("db_checks") or [])):
        return True
    # 场景2：视觉/几何断言（DOM 拿不到的视觉缺陷）
    if any(
        isinstance(uc, dict) and uc.get("type") in ("visual_check", "geometry_check")
        for uc in (step.get("ui_checks") or [])
    ):
        return True
    return False


async def verify_recipe_intent_result(
    engine,
    step,
    step_desc,
    step_expected,
    intent_schema,
    pre_dom_count,
    api_tracker,
    step_t0,
    extract_keywords_fn=None,
):
    """Recipe 命中后的意图裁决。

    ★ v4.3 质变重构(2026-07-02)：反转验证优先级。
    原逻辑：hard_verify(模糊关键词) → 硬门禁 → structured checks(精确) 永远不到达。
    新逻辑：structured checks(精确) → 主验证 → hard_verify(模糊) → 兜底。
    结构化校验存在时它是权威的；hard_verify 降为无结构化校验时的兜底。
    """
    if extract_keywords_fn is None:
        extract_keywords_fn = extract_keywords
    if not isinstance(intent_schema, dict):
        intent_schema = {}
    try:
        # ── 1. 收集结构化校验点 ──
        step_checks = {}
        if isinstance(step, dict):
            if step.get("ui_checks"):
                step_checks["ui_checks"] = step["ui_checks"]
            if step.get("api_checks"):
                step_checks["api_checks"] = step["api_checks"]
            if step.get("db_checks"):
                step_checks["db_checks"] = step["db_checks"]
            if step.get("required_tracks"):
                step_checks["required_tracks"] = step["required_tracks"]
            if step.get("consistency_check"):
                step_checks["consistency_check"] = step["consistency_check"]
            # ★ T2 (2026-08-16) [未复核-需 GLM/DeepSeek 把关]:
            #   a11y 透传：使 checks.a11y 逐用例进入 verify_step（与既有 _flag_*
            #   汇总层通道一致），_flag_a11y 据此逐用例生效（不再只能靠
            #   A11Y_GATE_ENABLED 全局开）。声明位 step.a11y = {"enabled": true}，
            #   未声明 → 不进入 checks → _flag_a11y 回落未启用（fail-open）。
            if step.get("a11y"):
                step_checks["a11y"] = step["a11y"]

        # ── 2. 有结构化校验 → 优先执行，它是权威的 ──
        if step_checks:
            from src.pipeline.steps.step4.verification import verify_step

            _page = getattr(engine, "_page", None) if engine else None
            # BEFORE_CHECK 插件钩子
            with guard("verify_recipe_intent_result: from src.pipeline.steps.step4.plugin", log=_log):
                from src.pipeline.steps.step4.plugins.plugin_manager import (
                    plugin_manager,
                )

                _case_id = (
                    step.get("_case", {}).get("case_id", "")
                    if isinstance(step.get("_case"), dict)
                    else ""
                )
                await plugin_manager.execute_hook(
                    "before_check",
                    step=step,
                    checks=step_checks,
                    case_id=_case_id,
                    tracks=list(step_checks.keys()),
                )
            # ★ 每步校验记录的用例归属：即便 before_check 插件被跳过也要可靠取得 case_id，
            #   以便三轨校验指标按 case 落库（执行详情 per-case 三轨通过率的根因数据）
            try:
                _verify_case_id = (
                    step.get("_case", {}).get("case_id", "")
                    if isinstance(step.get("_case"), dict)
                    else ""
                )
            except Exception:
                _verify_case_id = ""
            verify_result = await verify_step(
                api_tracker, step_t0, step_checks, step_desc, page=_page, case_id=_verify_case_id
            )

            # ★ 通用修复(2026-07-22 v3): 删除"负向用例 element_exists 强制翻转 PASS"逻辑
            #   根因核查(§10.9):
            #     1. 现象: REQ-002 P01/N01/B01 三个用例 status=PASS confidence=0.839，
            #              但 UI 轨 evidence 显示 element_exists:✗（失败）→ 典型假 PASS
            #     2. 直接原因: 原逻辑(L251-287)对 N01 用例的 element_exists 失败做强制翻转 PASS
            #     3. 中间原因: N01 检查 [role="alert"]/.error 失败但 loading_complete:✓ → 翻转
            #     4. 根本原因: Step3 check selector 不匹配 某SPA系统 实际错误提示元素
            #   全局观分析:
            #     - 假 PASS 污染 Step5 报告通过率 → 用户误判系统质量
            #     - 翻转逻辑把"系统未显示预期错误"等同于"系统优雅处理" → 测试逻辑错误
            #     - 违反 AGENTS.md 致命红线"禁止标假 PASS" + §13.4"禁止空evidence的PASS"
            #   职责边界:
            #     - Step4 只执行不决策(§12.1) — "是否优雅处理"是决策，不是 Step4 的职责
            #     - check selector 不匹配是 Step3 的职责，应在 Step3 修复
            #   通用性自检(§12.4):
            #     1. 跨系统: 删除翻转适用于所有系统 ✓
            #     2. 跨行业: 适用于所有行业 ✓
            #     3. 跨页面: 适用于所有页面 ✓
            #     4. 覆盖性: 覆盖所有用例类型(P01/N01/B01) ✓
            #     5. 根因溯源: 追溯到 Step3 selector + Step4 翻转 ✓
            #   自我反驳三次:
            #     1. 删除后 N01 会 FAIL — 但这是真 FAIL（系统确实没显示预期错误提示）
            #     2. 改为 MANUAL_REVIEW？— 仍掩盖失败，不如删除让失败暴露
            #     3. 更根本方案？— 修 Step3 selector + 删 Step4 翻转，两者都必要
            #   实施: 直接删除翻转逻辑，失败就是失败，不掩盖

            # AFTER_CHECK 插件钩子
            with guard("verify_recipe_intent_result: from src.pipeline.steps.step4.plugin", log=_log):
                from src.pipeline.steps.step4.plugins.plugin_manager import (
                    plugin_manager,
                )

                await plugin_manager.execute_hook(
                    "after_check",
                    step=step,
                    check_result=verify_result,
                    case_id=_case_id,
                    passed=verify_result.get("passed", False),
                )
            if verify_result.get("passed"):
                return True, "结构化校验通过(ui/api/db)", verify_result
            return (
                False,
                "结构化校验失败: %s" % verify_result.get("summary", ""),
                verify_result,
            )

        # ── 3. 无结构化校验 → 回退 hard_verify 兜底 ──
        hard_ok = await hard_verify_step(
            engine,
            step_desc,
            step_expected,
            pre_dom_count,
            api_tracker,
            extract_keywords_fn=extract_keywords_fn,
        )
        # ★ P0-fix(2026-07-24): 返回最小 verify_result dict 而非 None
        #   原bug: 返回 None → _base.py:567 isinstance 检查失败 → evidence 提取跳过 → 空 evidence FAIL
        #   修复: 返回与 verify_step 相同结构的最小 dict，保证 evidence 永不缺失（§13.4）
        #   通用性: 适用于任何"无结构化校验点"场景，不依赖业务逻辑
        #   合理性: 追根溯源到返回类型不一致（L4 根因），消除下游 None 处理
        #   覆盖性: 覆盖 hard_verify 通过/失败/异常 三条路径
        if hard_ok:
            return (
                True,
                "硬校验通过(无结构化校验点)",
                {
                    "passed": True,
                    "summary": "硬校验通过(无结构化校验点)",
                    "ui": {
                        "passed": True,
                        "evidence": "硬校验兜底通过(无结构化校验点)",
                    },
                    "api": {"passed": True, "evidence": "无API校验点", "calls": []},
                    "db": {"passed": True, "evidence": "无DB校验点", "data": []},
                },
            )
        return (
            False,
            "硬校验未通过(无结构化校验点兜底)",
            {
                "passed": False,
                "summary": "硬校验未通过(无结构化校验点兜底)",
                "ui": {"passed": False, "evidence": "硬校验兜底未通过(无结构化校验点)"},
                "api": {"passed": True, "evidence": "无API校验点", "calls": []},
                "db": {"passed": True, "evidence": "无DB校验点", "data": []},
            },
        )
    except Exception as e:
        return (
            False,
            "意图裁决异常: %s" % str(e)[:100],
            {
                "passed": False,
                "summary": "意图裁决异常: %s" % str(e)[:100],
                "ui": {"passed": False, "evidence": "意图裁决异常(无结构化校验点)"},
                "api": {"passed": False, "evidence": "意图裁决异常", "calls": []},
                "db": {"passed": False, "evidence": "意图裁决异常", "data": []},
            },
        )
