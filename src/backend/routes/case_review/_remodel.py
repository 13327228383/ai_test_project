# -*- coding: utf-8 -*-
"""测试用例重新建模（AI 调用 + 替换） — 从 case_review.py 拆出。

v2: 加入去重检查 — AI 返回多条时，过滤与同需求已有用例高度相似的条目，
    只保留不重复的。全部重复时不替换，返回提示信息。
"""

import logging
from src.core.guarded import guard
from src.pipeline.step3.case_rules.button_resolver import _SUBMIT_VERBS as _SUBMIT_VERBS  # noqa: F401  词表唯一源
from src.pipeline.step3.case_rules.button_resolver import _TRIGGER_VERBS as _TRIGGER_VERBS  # noqa: F401  词表唯一源

_log = logging.getLogger(__name__)


def _is_executable_intent(intent: dict) -> bool:
    """用例是否**可执行**：至少含 1 条断言（checks）或 1 条动作（actions）。

    ★ 根因修复(2026-09-25) RC-M 配套判据（见 ``_remodel_intent`` 内说明）。
    兼容 AI 输出的两种字段形态：``checks``/``actions`` 与 ``case_actions_json``
    （字符串或列表），任一非空即视为可执行。缺字段/空列表 → 不可执行。
    """
    if not isinstance(intent, dict):
        return False
    _checks = intent.get("checks")
    if isinstance(_checks, list) and len(_checks) > 0:
        return True
    for _k in ("actions", "case_actions_json"):
        _acts = intent.get(_k)
        if isinstance(_acts, str):
            try:
                import json as _json

                _acts = _json.loads(_acts)
            except Exception:
                _acts = []
        if isinstance(_acts, list) and len(_acts) > 0:
            return True
    return False


def _remodel_intent(case_id: str) -> dict:
    """★ MySQL-only：AI 重新为测试用例关联的需求建模，成功后才替换旧意图资产。

    v2 改进：
    - AI 返回多条时，用 dedup_remodel 与同需求已有用例去重
    - 全部重复时不替换，返回 {"ok": False, "reason": "all_duplicates"}
    - 部分重复时只保留不重复的条目
    - 返回 dict 包含详细结果信息

    Returns:
        {"ok": True/False, "replaced_count": int, "duplicates": [...], "reason": str}
    """
    try:
        from src.pipeline.utils.case_requirement_tracker import case_requirement_tracker
        from src.pipeline.utils.case_store import case_store
        from src.pipeline.utils.review_store import review_store
        from src.pipeline.utils.requirement_store import requirement_store

        req_id = case_requirement_tracker.get_req_id(case_id)
        if not req_id:
            _log.info("    [重生成] 未找到 case_id=%s 的需求映射", case_id)
            return {"ok": False, "reason": "no_req_mapping"}
        # 从 MySQL 需求镜像读取
        req = requirement_store.get_requirement(req_id)
        if not req:
            # 退化：从 list_requirements 找一条作为兜底（避免空需求导致生成失败）
            all_reqs = requirement_store.list_requirements()
            req = next(
                (
                    r
                    for r in all_reqs
                    if str(r.get("req_id", "")).strip() == str(req_id).strip()
                ),
                None,
            )
        if not req:
            _log.info("    [重生成] 未在 MySQL requirements 中找到需求 %s", req_id)
            return {"ok": False, "reason": "req_not_found"}
        # ★ 先调用 AI 生成新的测试用例资产，成功后再删除旧资产（防止 AI 失败导致数据丢失）
        from src.ai.ai_client import ai_brain

        result = ai_brain.model_test_cases(req, enable_validation=True)
        if not result.get("success"):
            _log.error("    [重新建模] AI 生成失败: %s", result.get("error", "未知"))
            return {
                "ok": False,
                "reason": "ai_failed",
                "error": result.get("error", ""),
            }
        intents = result.get("data", [])
        if isinstance(intents, dict):
            intents = [intents]
        if not isinstance(intents, list) or len(intents) == 0:
            return {"ok": False, "reason": "ai_empty"}

        # ★ v2: 去重 — 获取同需求下其他已有用例（排除正在重建的 case_id）
        existing_intents = []
        with guard("_remodel_intent: all_intents = case_store.list_intent", log=_log, level=logging.WARNING):
            all_intents = case_store.list_intents()
            existing_intents = [
                c
                for c in all_intents
                if c.get("req_id") == req_id and c.get("case_id") != case_id
            ]

        if existing_intents:
            from src.pipeline.steps.step3.case_quality import dedup_remodel

            dedup_result = dedup_remodel(intents, existing_intents)
            intents = dedup_result["kept"]
            dup_info = dedup_result["duplicates"]

            if dedup_result["all_duplicates"]:
                _log.info(
                    "    [重新建模] AI 生成 %d 条全部与已有用例重复，不替换",
                    len(dedup_result["kept"]) + len(dup_info),
                )
                return {
                    "ok": False,
                    "reason": "all_duplicates",
                    "duplicates": dup_info,
                }
            if dup_info:
                _log.info(
                    "    [重新建模] 去重: 移除 %d 条重复，保留 %d 条",
                    len(dup_info),
                    len(intents),
                )
        else:
            dup_info = []

        # 取去重后的第一条，保持原 case_id
        new_intent = intents[0]
        new_intent["case_id"] = case_id
        if not new_intent.get("req_id"):
            new_intent["req_id"] = req_id
        # ★ 根因修复(2026-09-25) RC-M: 重新建模不得把**可执行用例**替换成**不可执行用例**。
        #   根因: 本函数把 AI 原始输出直接 upsert 落库，未做可执行性校验。AI 偶尔返回
        #     展示态 schema（顶层只有 test_steps=[{step,action,data}]，缺 checks/actions）
        #     → 落库后 payload 无断言、case_actions_json=[] → 置信度计算置 0 →
        #     case_meta.review_status 落 blocked（confidence<60）→ 该用例既不可执行
        #     又成为高风险未确认项，**把 Step4 闸门整体卡死**（实测 TC-REQ-021-E01
        #     由置信度 85 的合法 P0 安全用例被降级为无断言 blocked）。
        #   后果链: 自修复本意是"修好失败用例"，却制造了更差的数据 —— 与
        #     "自我修复只许变好"的契约相悖。
        #   通用修复: 替换前校验新用例**可执行**（至少含 1 条 check 或 1 条 action）。
        #     不可执行 → 放弃替换、保留原用例（fail-closed：宁可不修，不许改坏）。
        #   通用性: 跨系统/行业/页面 —— 只依赖"用例必须有可执行内容"这一通用不变量，
        #     零业务硬编码；对任何 AI 供应商的 schema 漂移都成立。
        if not _is_executable_intent(new_intent):
            _log.warning(
                "    [重新建模] AI 输出不可执行（无 checks/actions），放弃替换以保留原用例: %s",
                case_id,
            )
            return {
                "ok": False,
                "reason": "degraded_not_executable",
                "replaced_count": 0,
            }
        # ★ Fix(2026-07-26): 触发按钮→提交按钮后处理（§13.3 职责边界）
        #   SelfRepair AI 再生路径不经过 _rule_extractor_actions._infer_submit_button_text，
        #   导致 N01/B01 用例的 click target_hint 仍是触发按钮（如"修改配置"）而非提交按钮（如"保存"）。
        #   根因(§10.9 3层why):
        #     L1: element_exists:✗ → 点击按钮后未显示错误提示
        #     L2: 为什么没显示? → 点击的是触发按钮（打开表单），未提交表单
        #     L3: 为什么再生后还是触发按钮? → AI 直接生成 actions，未经过规则提取器后处理
        #   通用性(§12.4): 适用于所有 CRUD 系统，基于 DOM 按钮匹配，无业务关键词硬编码
        _fix_trigger_to_submit_button(new_intent)
        # 计算置信度/风险等级（用本地 scorer）
        new_confidence = 75
        new_risk = "medium"
        new_risk_embedding = None
        with guard("_remodel_intent: from src.pipeline.utils.confidence_s", log=_log, level=logging.WARNING):
            from src.pipeline.utils.confidence_scorer import score_test_case
            from src.extensions.quality.risk_analyzer import assess_requirement_risk

            new_confidence = score_test_case(new_intent)
            _rl, _ = assess_requirement_risk(req)
            new_risk = _rl or "medium"
            # ★ 计算 embedding 用于 k-NN 自学习
            from src.extensions.semantic_index import embed as _embed_fn

            _emb_text = f"{req.get('module', '')} {req.get('description', '')}".strip()
            if _emb_text:
                new_risk_embedding = _embed_fn(_emb_text)
        # ★ AI 建模成功 → 现在安全删除旧测试用例资产
        case_requirement_tracker.remove_intent(case_id)
        with guard("_remodel_intent: review_store.delete_case_meta(case_i", log=_log, level=logging.WARNING):
            review_store.delete_case_meta(case_id)
        case_store.delete_intent(case_id)
        # 写入新测试用例资产（test_cases + intent→req 映射 + case_meta）
        if not case_store.upsert_intents([new_intent], case_asset_file=""):
            return {"ok": False, "reason": "upsert_failed"}
        case_requirement_tracker.add(case_id, req_id)
        review_store.write_case_meta(
            case_id,
            int(new_confidence),
            str(new_risk),
            risk_embedding=new_risk_embedding,
        )
        _log.info("    [重新建模] 已替换 MySQL test_cases: %s", case_id)
        return {
            "ok": True,
            "replaced_count": 1,
            "duplicates": dup_info,
            "reason": "success",
        }
    except Exception as e:
        import traceback

        _log.error("[重生成] 异常: %s", e)
        traceback.print_exc()
        return {"ok": False, "reason": "exception", "error": str(e)}


def _fix_trigger_to_submit_button(intent: dict) -> None:
    """★ Fix(2026-07-26): 将 N01/B01 用例的 click target_hint 从触发按钮替换为提交按钮。

    SelfRepair AI 再生路径不经过 _rule_extractor_actions._infer_submit_button_text，
    导致 N01/B01 用例的 click 仍是触发按钮（如"修改配置"）而非提交按钮（如"保存"）。

    根因(§10.9 3层why):
      L1: element_exists:✗ → 点击按钮后未显示错误提示
      L2: 为什么没显示? → 点击触发按钮（打开表单），未提交表单触发校验
      L3: 为什么再生后还是触发按钮? → AI 直接生成 actions，未经过规则提取器后处理

    通用性(§12.4):
      - 跨系统: 基于 DOM 按钮匹配，不依赖任何框架特性
      - 跨行业: 无业务关键词硬编码，纯基于触发/提交动词语义
      - 跨页面: 从 page_dom_snapshots 加载目标页面按钮
      - 数据完整性: DOM 不可用时回退到"保存"（通用兜底）

    自我反驳3次:
      反驳1: 后处理可能误改非 N01 用例 → 只处理 case_type="异常"/"边界"
      反驳2: 应该在 AI prompt 层面修复 → prompt 修复影响面大且不可控，后处理更精准
      反驳3: 应该复用 _infer_submit_button_text → 该函数依赖 _match_button_by_keywords
              和 operation 上下文，在 remodel 路径中不可用
    """
    import json as _json

    case_type = (intent.get("case_type") or "").strip()
    if case_type not in ("异常", "边界"):
        return  # 只处理 N01/B01


    actions = intent.get("case_actions_json", [])
    if isinstance(actions, str):
        try:
            actions = _json.loads(actions)
        except Exception:
            return
    if not isinstance(actions, list) or len(actions) == 0:
        return

    modified = False
    for i, action in enumerate(actions):
        if action.get("type") != "click":
            continue
        hint = str(action.get("target_hint", "") or "")
        if not hint:
            continue
        # 检查是否是触发按钮
        is_trigger = any(v in hint for v in _TRIGGER_VERBS)
        if not is_trigger:
            continue

        # 尝试从 DOM 中查找提交按钮
        submit_hint = _find_submit_in_dom(intent)
        if not submit_hint:
            submit_hint = "保存"  # 通用兜底：中文 CRUD 系统最常见的提交按钮

        actions[i] = {**action, "target_hint": submit_hint}
        modified = True
        _log.info(
            "[重生成] ★ 触发→提交按钮切换: case=%s type=%s '%s' → '%s'",
            intent.get("case_id", "?"),
            case_type,
            hint,
            submit_hint,
        )
        break  # 只修改第一个 click action

    if modified:
        intent["case_actions_json"] = actions


def _find_submit_in_dom(intent: dict) -> str:
    """从 page_dom_snapshots 中查找目标页面的提交按钮文本。

    通过 navigate action 的 target_hint 定位目标页面，从 DOM 按钮列表中
    匹配提交按钮（保存/提交/确定/确认）。通用性：不硬编码任何业务术语。

    Returns:
        提交按钮文本，或空字符串（表示未找到，调用方回退到"保存"）
    """
    try:
        actions = intent.get("case_actions_json", [])
        if isinstance(actions, str):
            import json as _json

            actions = _json.loads(actions)
        # 从 navigate action 获取目标页面
        nav_target = ""
        for a in actions or []:
            if a.get("type") == "navigate":
                nav_target = str(a.get("target_hint", "") or "")
                break
        if not nav_target:
            return ""

        from src.core.config_loader import config

        project = getattr(config, "project_name", "") or ""
        if not project:
            return ""

        # ★ P0-1 读侧迁移(2026-08-05): page_dom_snapshots → nav_graph（read_dom_pages）。
        #   该表 0 行且全仓无写入方，原查询恒返回空 → 跨页面提交按钮永远查不到。
        from src.pipeline.utils.nav_graph_read import read_dom_pages

        rows = [
            p
            for p in read_dom_pages(project)
            if int(p.get("element_count") or 0) > 0
            and str(p.get("page_path", "") or "") != "/login"
        ]
        rows.sort(key=lambda r: int(r.get("element_count") or 0), reverse=True)
        for row in rows[:20]:  # 等价原 SQL 的 LIMIT 20
            ds = str(row.get("dom_summary", "") or "")
            pp = str(row.get("page_path", "") or "")
            # 匹配页面：nav_target 出现在 page_path 或 dom_summary 中
            if nav_target not in pp and nav_target not in ds:
                continue
            # 在 dom_summary 中搜索提交按钮
            for sv in _SUBMIT_VERBS:
                if sv in ds:
                    _log.debug(
                        "[重生成] DOM 提交按钮匹配: nav='%s' page='%s' submit='%s'",
                        nav_target[:40],
                        pp[:60],
                        sv,
                    )
                    return sv
    except Exception as e:
        _log.error(
            "[重生成] _find_submit_in_dom 失败(不阻塞) nav=%r: %s",
            nav_target[:40],
            e,
            exc_info=True,
        )
    return ""


def batch_remodel_intents(case_ids: list) -> dict:
    """★ E3 (2026-07-10): 批量重新生成失败用例（增量重生核心）。

    对每个 case_id 调用 _remodel_intent，收集成功/失败统计。
    用于"上传补充文档 → 重跑失败用例"场景：
      上传文档 → Step1/Step2 更新 requirements+RAG → 本函数批量重生 → Step4 执行

    Returns:
        {"total": N, "success": N, "failed": N, "details": [...]}
    """
    if not case_ids:
        return {"total": 0, "success": 0, "failed": 0, "details": []}

    results = []
    success_count = 0
    failed_count = 0

    for cid in case_ids:
        try:
            r = _remodel_intent(cid)
            if r.get("ok"):
                success_count += 1
                results.append(
                    {"case_id": cid, "ok": True, "reason": r.get("reason", "")}
                )
            else:
                failed_count += 1
                results.append(
                    {
                        "case_id": cid,
                        "ok": False,
                        "reason": r.get("reason", ""),
                        "error": r.get("error", ""),
                    }
                )
        except Exception as e:
            failed_count += 1
            results.append(
                {"case_id": cid, "ok": False, "reason": "exception", "error": str(e)}
            )

    _log.info(
        "[批量重生] 完成: %d 成功, %d 失败, 共 %d",
        success_count,
        failed_count,
        len(case_ids),
    )
    return {
        "total": len(case_ids),
        "success": success_count,
        "failed": failed_count,
        "details": results,
    }
