# -*- coding: utf-8 -*-
"""上下文构建（UI / Trace / 数据字典 / 禁止词 / Few-shot） — 从 case_generator.py 拆出，零业务逻辑变更。"""

from src.core.config_loader import config
from src.core.guarded import guard
from src.pipeline.steps.step3.case_quality import (
    _load_few_shot_examples,
    _format_few_shot,
)

from ._helpers import _clip_context, _CTX_LIMITS
import logging

_log = logging.getLogger(__name__)


def _build_context_from_nav_graph(project: str, version_label: str) -> str:
    """★ P0-3 3b: 从 nav_graph_nodes 构建 AI 导航上下文（替 system_nav_maps 纸地图）。

    nav_graph_nodes 来自真实 DOM 爬取（source='dom'），只含确认存在的页面。
    返回空串则降级到其它数据源（§12.4 第5条：数据不完整时降级而非崩溃）；
    旧的 system_nav_maps 降级分支已于 2026-08-05 随该表 DROP 一并移除。

    通用性：零业务术语，基于结构化节点字段（page_url/page_title/element_count/source）。
    ★ v5.0.2 修复(2026-07-27, §10.9): 注入 page_elements（buttons/inputs/tabs/links），
      根因: P0-3b 迁移时遗漏元素注入逻辑，导致 AI 看不到按钮，所有用例降级。
      通用性: 与 system_nav_maps 分支元素注入逻辑一致，适用于所有项目。
    """
    from src.pipeline.utils.nav_graph_store import read_nav_graph_nodes

    nodes = read_nav_graph_nodes(project, version_label)
    if not nodes:
        return ""

    _page_lines: list[str] = []
    _elem_lines: list[str] = []
    _explore_lines: list[str] = []
    _seen: set[str] = set()
    # ★ v5.1(2026-08-04): 全局 UI 噪音词——sidebar 常驻按钮，对页面特征无区分度，
    #   混入会让 LLM 误以为每个页面都有这些功能。通用适用于任何 Web UI。
    _GLOBAL_UI_NOISE = frozenset({"一键执行", "退出登录", "刷新", "刷新列表"})
    _all_titles: set[str] = {(n.get("page_title") or "").strip() for n in nodes}

    for n in nodes:
        title = (n.get("page_title", "") or "").strip()
        if not title or title in _seen:
            continue
        _seen.add(title)
        url = (n.get("page_url", "") or "").strip()
        elem_cnt = int(n.get("element_count") or 0)
        pe = n.get("page_elements") or {}
        if isinstance(pe, str):
            try:
                import json as _json

                pe = _json.loads(pe)
            except Exception:
                pe = {}
        _has_elements = bool(
            pe and (pe.get("inputs") or pe.get("buttons") or pe.get("tabs"))
        )

        nav_path = title
        if url:
            nav_path += f"  | URL: {url}"

        if _has_elements:
            _page_lines.append(f"  - {nav_path}")
            _elem_parts = []
            _inputs = pe.get("inputs", [])
            if _inputs:
                _inp_strs = []
                _seen_inp: set[str] = set()
                for inp in _inputs[:10]:
                    _pl = inp.get("placeholder", "") or inp.get("label", "")
                    _inp_key = (
                        f"placeholder='{_pl}'"
                        if _pl
                        else f"type={inp.get('type','text')}"
                    )
                    if _inp_key not in _seen_inp:
                        _seen_inp.add(_inp_key)
                        _inp_strs.append(_inp_key)
                _elem_parts.append(f"输入框: {', '.join(_inp_strs)}")
            _btns = pe.get("buttons", [])
            if _btns:
                _btn_strs: list[str] = []
                _seen_btn: set[str] = set()
                for b in _btns[:15]:
                    _bt = b.get("text", "")
                    if _bt and _bt not in _seen_btn and _bt not in _GLOBAL_UI_NOISE:
                        _seen_btn.add(_bt)
                        _btn_strs.append(_bt)
                _elem_parts.append(f"按钮: [{', '.join(_btn_strs)}]")
            _tabs = pe.get("tabs", [])
            if _tabs:
                _tab_strs: list[str] = []
                _seen_tab: set[str] = set()
                for t in _tabs:
                    _tt = t.get("text", "")
                    if _tt and _tt not in _seen_tab:
                        _seen_tab.add(_tt)
                        _tab_strs.append(_tt)
                _elem_parts.append(f"Tab: [{', '.join(_tab_strs)}]")
            _links = pe.get("links", [])
            if _links:
                _link_strs: list[str] = []
                _seen_link: set[str] = set()
                for l in _links[:10]:
                    _lt = l.get("text", "")
                    # 过滤 sidebar 全局菜单链接（与页面清单重复，无页面区分度）
                    if _lt and _lt not in _seen_link and _lt not in _all_titles:
                        _seen_link.add(_lt)
                        _link_strs.append(_lt)
                _elem_parts.append(f"操作: [{', '.join(_link_strs)}]")
            if _elem_parts:
                _elem_lines.append(f"  - {title}: {'; '.join(_elem_parts)}")
        elif elem_cnt > 0:
            _page_lines.append(f"  - {nav_path}")
        else:
            label = nav_path
            if not url:
                label += " [元素未知，仅限导航]"
            _explore_lines.append(f"  - {label}")

    if not _page_lines and not _explore_lines:
        return ""

    _context = ""
    if _page_lines:
        _context += (
            "\n## ★ 可用页面路径清单（来自 nav_graph 真实爬取）\n"
            "以下页面已在目标系统中确认存在（DOM爬取），navigate 的 target_hint 必须使用以下名称：\n"
            "%s\n"
        ) % "\n".join(_page_lines)

    if _elem_lines:
        _context += (
            "\n## ★ 页面元素清单（来自 nav_graph 真实爬取）\n"
            "以下页面已爬取到具体元素，click 的 target_hint 必须使用以下按钮名：\n"
            "%s\n"
        ) % "\n".join(_elem_lines)

    if _explore_lines:
        _context += (
            "\n## ⚠️ 探索模式页面（页面存在但元素信息未知，禁止编造具体元素名）\n"
            "以下页面确认存在，但 DOM 爬取未能获取其内部元素。\n"
            "只能使用 navigate + verify 动作，严禁生成 click/fill/select_option：\n"
            "%s\n"
        ) % "\n".join(_explore_lines)

    _context += (
        "\n★ 强制约束（nav_graph）：\n"
        "1. navigate 的 target_hint 必须使用上方页面名称，禁止编造。\n"
        "2. navigate 的 url 字段必须填清单中标注的 URL（含 query 参数），严禁编造。\n"
        "3. 探索模式页面禁止 click/fill/select_option，只能 navigate + verify。\n"
        "4. click 的 target_hint 必须使用页面元素清单中的按钮名，禁止编造。\n"
    )

    _log.info(
        "[_contexts] ✓ nav_graph 注入: %d 白名单 + %d 元素 + %d 探索 (来源=nav_graph_nodes)",
        len(_page_lines),
        len(_elem_lines),
        len(_explore_lines),
    )
    return _context


def _build_nav_map_context(project: str, version_label: str = "v1.0") -> str:
    """从 nav_graph_nodes 构建 AI 导航上下文（唯一数据源）。

    解决 SPA 菜单项无 URL 时 Step3 AI 编造 URL 的根因问题。
    nav_graph_nodes 来自真实 DOM 爬取（source='dom'）。

    ★ P0-1 读侧收口(2026-08-05): 删除 system_nav_maps 降级分支（约 230 行）。
      根因: 该表 0 行且**无 nav_json 列**，原 `SELECT nav_json ... AND
      version_label=%s` 双重不存在（version_label 列同样不存在），恒抛
      Unknown column 并被 `except: _log.debug` 吞掉 —— 纯死代码。
      合规: AGENTS.md §10.5 零兼容负担，废弃表不保留兼容读取。
    """
    return _build_context_from_nav_graph(project, version_label)


def _build_common_contexts(
    requirement,
    ui_context,
    rag_context,
    trace_context,
    rules_context,
    feedback_context,
    previous_intents=None,
):
    """构建各个上下文片段（UI / Trace / 数据字典 / 禁止词 / Few-shot）

    ★ 优化6: previous_intents 为旧版本用例列表（modified 需求 few-shot 注入），
             为 None 或空时走原逻辑（模块级 few-shot）。
    """

    # ★ 从trace_context提取目标页面路径，用于限定DOM检索范围
    target_pages = []
    if trace_context:
        import re as _re

        _page_matches = _re.findall(r"Page:\s*([^\n,]+)", trace_context)
        target_pages = [x for p in _page_matches if (x := p.strip())]

    if rag_context:
        rag_context = _clip_context("RAG", rag_context, _CTX_LIMITS["ui"])
        ui_section = (
            "\n## 相关上下文（v4 语义检索 + 源数据精确查询）\n"
            "%s\n\n★ 请优先参考以上信息。\n" % rag_context
        )
    elif ui_context:
        ui_context = _clip_context("UI", ui_context, _CTX_LIMITS["ui"])
        ui_section = (
            "\n## 目标系统 UI 结构（Figma 设计稿）\n"
            "%s\n\n★ 请基于此设计意图。\n" % ui_context
        )
    else:
        ui_section = ""

    trace_section = ""
    if trace_context:
        trace_context = _clip_context("Trace", trace_context, _CTX_LIMITS["trace"])
        trace_section = (
            "\n## ★ 追溯链路\n%s\n\n★ 以上供参考，确保意图覆盖正确的 API/DB/Page。\n"
            % trace_context
        )

    data_dict_context = ""
    # ★ 2026-07-12: 纯 UI 操作（点击/输入/选择）不需要数据字典，跳过 2200 字符噪音
    _op_type_ui = False
    if isinstance(requirement, dict):
        _op = (requirement.get("operation", "") or "").strip()
        if _op:
            from src.pipeline.step3._modeling_engine import (
                _UI_OPERATIONS,
                _DATA_OPERATIONS,
            )

            _op_lower = _op.lower()
            _op_type_ui = any(kw in _op_lower for kw in _UI_OPERATIONS)
            _op_type_data = any(kw in _op_lower for kw in _DATA_OPERATIONS)
            _op_type_ui = _op_type_ui and not _op_type_data  # 纯 UI，非混合

    if not _op_type_ui:
        try:
            if config.system_knowledge_inject_prompt:
                from src.extensions.system_data_resolver import system_data

                # ★ 两层注入：紧凑目录（全局完整）+ 按需详情（按需求相关性筛选）
                req_text = (
                    requirement.get("description", "")
                    if isinstance(requirement, dict)
                    else ""
                )
                module_name = (
                    requirement.get("module", requirement.get("module_name", ""))
                    if isinstance(requirement, dict)
                    else ""
                )
                data_dict_context = _clip_context(
                    "系统数据字典",
                    system_data.format_data_dict_two_layer(
                        requirement_text=req_text,
                        module_name=module_name,
                        top_n=3,  # 只取最相关的 3 张表，节省 token
                    ),
                    _CTX_LIMITS["data_dict"],
                )
        except Exception as _e:
            _log.debug("[_contexts] 非关键异常: %s", _e, exc_info=True)
    else:
        _log.info(
            "[_contexts] 纯UI操作，跳过数据字典注入（节省 ~%d 字符）",
            _CTX_LIMITS["data_dict"],
        )

    forbidden_context = ""
    try:
        from src.extensions.quality.test_rules_kb import test_rules_kb

        forbidden_context = _clip_context(
            "禁止规则",
            test_rules_kb.format_forbidden_context(),
            _CTX_LIMITS["rules"],
        )
    except Exception as _e:
        _log.debug("[_contexts] 非关键异常: %s", _e, exc_info=True)

    # ★ 优化5: 敏感数据脱敏规则（复用 Step2 contains_sensitive_data 标记）
    # 当需求命中敏感数据检测时，强制 AI 生成脱敏测试数据，避免真实敏感信息进入用例
    try:
        _is_sensitive = False
        if isinstance(requirement, dict):
            _raw = requirement.get("contains_sensitive_data", 0)
            _is_sensitive = bool(int(_raw or 0))
        if _is_sensitive:
            _sensitive_ctx = (
                "\n## 敏感数据脱敏规则（需求命中敏感数据检测）\n"
                "★ 测试数据必须使用脱敏值，禁止使用真实敏感信息：\n"
                "- 手机号: 138****0000 / 139****1234\n"
                "- 身份证: 110************0000 / 310***********1234\n"
                "- 邮箱: test***@example.com / user***@test.com\n"
                "- IP: 192.168.*.* / 10.0.*.*\n"
            )
            forbidden_context = (forbidden_context or "") + _sensitive_ctx
            _log.info("[_contexts] ✓ 需求命中敏感数据检测，已注入脱敏规则")
    except Exception as _e:
        _log.debug("[_contexts] 敏感数据脱敏规则构建失败: %s", _e, exc_info=True)

    few_shot_context = ""
    # ★ 优化6: 优先使用旧版本用例作为 few-shot（modified 需求增量继承）
    if previous_intents:
        try:
            _prev_lines = []
            for _pi in previous_intents[:3]:  # 最多 3 条，控制 token
                _title = str(_pi.get("title", "") or "")[:80]
                _ctype = str(_pi.get("case_type", "") or "")[:20]
                _actions = str(_pi.get("actions", "") or "")[:300]
                if _title and _actions:
                    _prev_lines.append(
                        "- 类型:%s | 标题:%s | 操作:%s" % (_ctype, _title, _actions)
                    )
            if _prev_lines:
                _prev_text = (
                    "\n## 旧版本参考用例（modified 需求，保持操作连贯性）\n"
                    "★ 以下为旧版本同类用例，请保持操作步骤的连贯性，但 checks 必须根据新需求重新生成。\n"
                    + "\n".join(_prev_lines)
                    + "\n"
                )
                few_shot_context = _clip_context(
                    "旧版本Few-shot", _prev_text, _CTX_LIMITS["few_shot"]
                )
                _log.info(
                    "[_contexts] ✓ 优化6: 注入 %d 条旧版本用例作为 few-shot",
                    len(_prev_lines),
                )
        except Exception as _e:
            _log.debug("[_contexts] 旧版本 few-shot 构建失败: %s", _e, exc_info=True)

    if not few_shot_context:
        try:
            module = requirement.get("module", requirement.get("module_name", ""))
            examples = _load_few_shot_examples(module)
            few_shot_context = _clip_context(
                "Few-shot", _format_few_shot(examples), _CTX_LIMITS["few_shot"]
            )
        except Exception as _e:
            _log.debug("[_contexts] 非关键异常: %s", _e, exc_info=True)

    # ★ 2026-07-06 扩展：按控件类型注入允许操作清单（带具体操作名）
    # 有 operation 时按控件类型精确过滤；无 operation 时回退到需求文本场景筛选
    action_library_context = ""
    try:
        from ._action_injection import (
            build_control_action_prompt,
            build_action_injection_prompt,
        )

        _req_text = (
            requirement.get("description", "") if isinstance(requirement, dict) else ""
        )
        _req_op = (
            requirement.get("operation", "") if isinstance(requirement, dict) else ""
        ).strip()
        _req_eb = (
            requirement.get("expected_feedback", "")
            if isinstance(requirement, dict)
            else ""
        ).strip()
        if _req_op:
            action_library_context = build_control_action_prompt(_req_op, _req_eb)
            _log.info("[_contexts] ✓ 控件类型操作注入: op=%s", _req_op[:60])
        else:
            _injection_text = (_req_text + " " + _req_op).strip()
            action_library_context = build_action_injection_prompt(
                requirement_text=_injection_text
            )
    except Exception as _e:
        _log.debug("[_contexts] 操作清单注入失败: %s", _e, exc_info=True)

    # ★ 2026-07-03 扩展：目标系统 DOM 快照 RAG 检索（Step1 爬取，供 AI 生成 selector_hint）
    # ★ 2026-07-12 重构：RAG 查询改用 operation（更精准定位控件），
    #   预验证命中时用精确映射替换通用 DOM dump（减少信息稀释）。
    import re as _re2

    dom_context = ""
    _pages = {}
    _op = ""
    # ★ 2026-07-14: 预初始化 _matched_proto，避免 try 块内 rag_engine.retrieve
    #   抛异常时，line 354 `if _matched_proto:` 触发 UnboundLocalError
    _matched_proto = []
    try:
        from src.extensions.rag_engine import rag_engine

        _req_text = (
            requirement.get("description", "") if isinstance(requirement, dict) else ""
        )
        _req_module = (
            requirement.get("module", "") if isinstance(requirement, dict) else ""
        )
        _op = (
            requirement.get("operation", "") if isinstance(requirement, dict) else ""
        ).strip()
        # ★ 用 operation 做 RAG 查询（比 description 更精准定位控件所在页面）
        _rag_query = (_op or _req_text).strip()

        if _rag_query and rag_engine.chunk_count > 0:
            _dom_results = rag_engine.retrieve(
                requirement={"description": _rag_query, "module": _req_module},
                top_k=20,
                retrieve_types=["dom_snapshots"],
            )
            _matched_dom = _dom_results.get("dom_snapshots", [])

            # ★ 2026-07-13: 检索原型图识别结果（视觉模型提取的 UI 结构）
            _proto_results = rag_engine.retrieve(
                requirement={"description": _rag_query, "module": _req_module},
                top_k=5,
                retrieve_types=["prototype_images"],
            )
            _matched_proto = _proto_results.get("prototype_images", [])

            if _matched_dom:
                # ★ S3-012: 记录 RAG 使用日志
                with guard("_build_common_contexts: _req_id = ( requirement.get('req_id'", log=_log):
                    _req_id = (
                        requirement.get("req_id", "")
                        if isinstance(requirement, dict)
                        else ""
                    )
                    from src.extensions.rag_engine.rag_usage_logger import (
                        log_rag_retrieval,
                    )

                    log_rag_retrieval(_dom_results, req_id=str(_req_id))
                _filtered_elements = []

                for _dom in _matched_dom:
                    _meta = _dom.get("meta", "{}")
                    if isinstance(_meta, str):
                        import json as _json

                        try:
                            _meta = _json.loads(_meta)
                        except Exception:
                            _meta = {}

                    _page_path = _meta.get("page_path", "")
                    _page_title = _meta.get("page_title", "")
                    _element_line = _meta.get("element_line", "")

                    if target_pages:
                        _is_target = False
                        for _tp in target_pages:
                            if (
                                _tp in _page_path
                                or _page_path in _tp
                                or _tp in _page_title
                                or _page_title in _tp
                            ):
                                _is_target = True
                                break
                        if not _is_target:
                            continue

                    _page_key = _page_path or _meta.get("page_url", "")
                    if _page_key not in _pages:
                        _pages[_page_key] = {"title": _page_title, "elements": []}
                    if _element_line:
                        _pages[_page_key]["elements"].append(_element_line)
                        _filtered_elements.append(_element_line)

                if _pages:
                    _log.info(
                        "[_contexts] ✓ DOM RAG 检索: query='%s' → %d 页 %d 元素",
                        _rag_query[:60],
                        len(_pages),
                        len(_filtered_elements),
                    )
    except Exception as _e:
        _log.debug("[_contexts] DOM 快照 RAG 检索失败: %s", _e, exc_info=True)

    # ★ 控件存在性预验证（2026-07-12）：在 AI 生成前，从 DOM 快照匹配控件→页面映射
    #   解决问题：Step2 三元组只有抽象操作（如"点击保存按钮"），没有页面/控件定位信息。
    #   AI 被迫从海量稀释上下文中推断空间坐标 → 幻觉。
    #   前置注入硬约束（已知事实），替代后置补救（猜完再纠正）。
    #   通用性：中文 n-gram + 去动词前缀，纯文本匹配，零业务硬编码。
    _control_pre_valid = ""
    _ctrl_matched = False
    try:
        if _pages and _op:
            # ── 中文关键词提取：n-gram 拆解 + 去动词前缀 ──
            # 问题：中文无空格，"用户点击保存按钮" 整句无法匹配 DOM 元素 "text=保存"
            # 方案：逐字滑动 2-3 gram + 去掉常见动词前缀
            _cleaned = _re2.sub(r"[（(].*?[）)]", "", _op).strip()
            # 常见中文动词前缀（通用，无业务硬编码）
            _verb_prefixes = [
                "用户",
                "系统",
                "管理员",
                "点击",
                "双击",
                "右键",
                "输入",
                "选择",
                "上传",
                "下载",
                "勾选",
                "取消勾选",
                "删除",
                "添加",
                "新增",
                "修改",
                "查询",
                "搜索",
                "导出",
                "导入",
                "保存",
                "提交",
                "确认",
                "取消",
                "关闭",
                "打开",
                "查看",
                "浏览",
                "滚动",
                "拖拽",
                "切换",
                "切换至",
                "退出",
                "进入",
                "清空",
                "设置",
                "按下",
                "释放",
                "悬停",
                "聚焦",
                "失焦",
                "长按",
                "滑动",
                "扫码",
                "刷新",
                "重置",
                "启用",
                "禁用",
                "审批",
                "驳回",
                "通过",
                "分配",
                "指派",
                "配置",
                "安装",
                "卸载",
            ]
            _core = _cleaned
            for _vp in sorted(_verb_prefixes, key=len, reverse=True):
                if _core.startswith(_vp):
                    _core = _core[len(_vp) :].lstrip("的")
                    break
            # 再次尝试去掉剩余动词前缀
            for _vp in sorted(_verb_prefixes, key=len, reverse=True):
                if _core.startswith(_vp):
                    _core = _core[len(_vp) :].lstrip("的")
                    break

            # 生成 2-3 字 n-gram（核心文本 n-gram 不过滤，原文本 n-gram 排除纯动词）
            _ctrl_keywords = []
            _seen = set()
            _verb_set = set(_verb_prefixes)
            if len(_core) >= 2:
                _ctrl_keywords.append(_core)
                _seen.add(_core)
            # 核心文本 n-gram：控件名本身，不过滤（"保存"即使碰巧是动词，也是控件名的一部分）
            for _n in (2, 3):
                for _i in range(len(_core) - _n + 1):
                    _ngram = _core[_i : _i + _n]
                    if _ngram not in _seen:
                        _seen.add(_ngram)
                        _ctrl_keywords.append(_ngram)
            # 原文本 n-gram：排除纯动词前缀，避免"用户"匹配"用户名"等假阳性
            for _n in (2, 3):
                for _i in range(len(_cleaned) - _n + 1):
                    _ngram = _cleaned[_i : _i + _n]
                    if _ngram not in _seen and _ngram not in _verb_set:
                        _seen.add(_ngram)
                        _ctrl_keywords.append(_ngram)

            if _ctrl_keywords:
                # 在 DOM 快照中逐页搜索控件关键词
                _matched_pages = []
                for _page_key, _page_data in _pages.items():
                    _page_title = _page_data.get("title", "")
                    _elements = _page_data.get("elements", [])
                    _matched_els = []
                    for _el in _elements:
                        _el_lower = _el.lower()
                        for _kw in _ctrl_keywords:
                            if _kw.lower() in _el_lower:
                                _matched_els.append(_el)
                                break
                    if _matched_els:
                        _matched_pages.append((_page_key, _page_title, _matched_els))

                if _matched_pages:
                    _ctrl_matched = True
                    # 精确约束：只注入匹配的页面和控件，不 dump 全部 DOM
                    _lines = []
                    for _pk, _pt, _els in _matched_pages[:3]:
                        _lines.append(f"  - 页面: {_pk} ({_pt})")
                        for _el in _els[:3]:
                            _lines.append(f"    控件: {_el}")
                    _control_pre_valid = (
                        "\n## ★ 控件定位约束（DOM 预验证已确认，必须遵守）\n"
                        "以下控件已在 DOM 快照中找到，请使用这些页面和控件描述：\n"
                        "%s\n"
                        "★ 强制约束：\n"
                        "1. navigate target_hint 必须使用上方列出的页面路径。\n"
                        "2. click/fill/select_option 等操作的 target_hint 必须来自上方控件描述。\n"
                        "3. 禁止编造不存在的页面路径或控件名。\n"
                    ) % "\n".join(_lines)
                    _log.info(
                        "[_contexts] ✓ 控件预验证命中: op='%s' → core='%s' → %d 页",
                        _op[:60],
                        _core,
                        len(_matched_pages),
                    )
                else:
                    _log.info(
                        "[_contexts] ⚠ 控件预验证未命中: op='%s' core='%s' keywords=%s",
                        _op[:60],
                        _core,
                        _ctrl_keywords[:5],
                    )
    except Exception as _e:
        _log.debug("[_contexts] 控件预验证失败(不阻塞): %s", _e, exc_info=True)

    # ── 上下文注入策略 ──
    # 命中时：用精确控件→页面映射替换通用 DOM dump（减少信息稀释）
    # 未命中时：保留通用 DOM dump 作为 AI 探索的 fallback
    if _ctrl_matched and _control_pre_valid:
        ui_section = (ui_section or "") + _control_pre_valid
    elif _pages:
        # 未命中 → 注入通用 DOM 元素（限制 5 个/页，2200 字符上限）
        dom_context = "\n## 目标系统 DOM 元素（RAG匹配，参考探索）\n"
        for _page_key, _page_data in _pages.items():
            dom_context += f"\n### [{_page_data['title']}] {_page_key}\n"
            for _el in _page_data["elements"][:5]:
                dom_context += f"- {_el}\n"
        dom_context = _clip_context("DOM快照", dom_context, _CTX_LIMITS["ui"])
        ui_section = (ui_section or "") + "\n" + dom_context

    # ★ 2026-07-13: 注入原型图识别结果（视觉模型提取的 UI 结构）
    if _matched_proto:
        _proto_lines = []
        for _p in _matched_proto[:3]:  # 最多 3 张原型图
            _proto_text = _p.get("text", "")
            if _proto_text:
                _proto_lines.append(_proto_text)
        if _proto_lines:
            _proto_section = (
                "\n## ★ 原型图识别结果（视觉模型提取）\n"
                "以下 UI 结构信息来自需求文档中的原型图，供参考：\n"
                "%s\n"
            ) % "\n\n---\n\n".join(_proto_lines)
            _proto_section = _clip_context("原型图", _proto_section, 1500)
            ui_section = (ui_section or "") + _proto_section
            _log.info("[_contexts] ✓ 原型图上下文注入: %d 张", len(_proto_lines))

    # ★ 通用修复(2026-07-18): 注入导航地图"可用页面路径清单"
    # 解决 SPA 菜单项无 URL 时 Step3 AI 编造 URL 的根因问题
    _project_name = None
    try:
        if isinstance(requirement, dict):
            _project_name = requirement.get("project", "")
        if not _project_name:
            _project_name = getattr(config, "project_name", "")
    except Exception as _r31:
        _log.debug("[_contexts] 异常降级(pass): %s", str(_r31)[:60], exc_info=True)
        pass
    _log.info(
        "[_contexts] 导航地图注入: requirement_keys=%s project_name=%s",
        (
            list(requirement.keys())
            if isinstance(requirement, dict)
            else type(requirement)
        ),
        _project_name,
    )
    if _project_name:
        # ★ 通用修复(2026-07-20): requirements.version_id 是整数ID（如1），
        #   不是 version_label（如"v1.0"）。修复前直接 str(version_id) → "1"
        #   → _build_nav_map_context 查不到 nav_map → AI 完全看不到导航地图
        #   → 编造菜单路径（如"数据管理→物料管理→辅材管理"）。
        #   修复后: 通过 project_versions 表把 version_id 转成 version_label。
        _version_label = "v1.0"
        if isinstance(requirement, dict):
            _vid = requirement.get("version_id", "")
            if _vid and isinstance(_vid, int):
                with guard("_build_common_contexts: from src.core.db_manager import get_", log=_log):
                    from src.core.db_manager import get_connection, release_connection

                    _conn_v = get_connection()
                    try:
                        _cur_v = _conn_v.cursor()
                        _cur_v.execute(
                            "SELECT version_label FROM project_versions WHERE id=%s LIMIT 1",
                            (_vid,),
                        )
                        _row_v = _cur_v.fetchone()
                        _cur_v.close()
                        if _row_v and _row_v.get("version_label"):
                            _version_label = _row_v["version_label"]
                    finally:
                        release_connection(_conn_v)
            elif _vid and isinstance(_vid, str) and _vid.startswith("v"):
                _version_label = _vid
        _nav_map_ctx = _build_nav_map_context(str(_project_name), str(_version_label))
        if _nav_map_ctx:
            ui_section = (ui_section or "") + _nav_map_ctx

    # ★ P2-11 接入(2026-07-25): MultiModalRAG 跨模态语义检索（需求+DOM+figma 联合）
    #   防幻觉：注入外部真值，禁止 AI 编造与下方不符的元素/接口/字段
    #   通用性：query 用 operation+object，不写死业务术语；跨系统/行业/页面通用
    #   互补性：DOM 由 rag_engine 精确匹配（line 478-556），retrieve_context 补充需求+figma 语义关联
    #   降级：检索失败或无结果 → 返回空串，不阻断主流程（§12.4 第5条）
    #   职责边界：Step3 用例建模注入 RAG 上下文，属 Step3 职责（§13.3），不越界
    with guard("_build_common_contexts: from src.rag.integration import retr", log=_log, level=logging.WARNING):
        from src.rag.integration import retrieve_context as _retrieve_mm

        _mm_query = ""
        if isinstance(requirement, dict):
            _mm_query = (
                str(requirement.get("operation", "") or "").strip()
                + " "
                + str(
                    requirement.get("object", "")
                    or requirement.get("target", "")
                    or requirement.get("description", "")
                    or ""
                ).strip()
            ).strip()
        if _mm_query and _project_name:
            _mm_ctx = _retrieve_mm(str(_project_name), _mm_query, top_k=5)
            if _mm_ctx:
                ui_section = (ui_section or "") + _mm_ctx
                _log.info(
                    "[_contexts] ✓ P2-11 MultiModalRAG 注入: query='%s' project=%s",
                    _mm_query[:60],
                    str(_project_name)[:30],
                )

    # ★ 通用修复(2026-07-20): 注入系统 base_url（首页URL），用于 navigate 到首页
    # ★ 注意：base_url 不再是 navigate 的唯一合法 URL。导航地图清单中的 URL（如 /xxx?tab=yyy）
    #   也可以填入 navigate 的 url 字段。base_url 仅作为"系统首页"的URL注入。
    _base_url = ""
    try:
        _base_url = str(getattr(config, "target_url", "") or "").strip()
        if not _base_url:
            from src.core.config_loader import config as _cfg

            _base_url = str(getattr(_cfg, "target_url", "") or "").strip()
    except Exception as _r32:
        _log.debug("[_contexts] 异常降级(pass): %s", str(_r32)[:60], exc_info=True)
        pass
    if _base_url:
        _base_url_section = (
            "\n## ★ 系统首页 URL（base_url）\n"
            "当需要导航到系统首页时使用此 URL：\n"
            '  {"type": "navigate", "target_hint": "系统首页", "url": "%s"}\n'
            "★ 注意：base_url 不是 navigate 的唯一合法 URL。\n"
            "  导航地图清单中标注的 URL（如 /xxx?tab=yyy）也可以填入 navigate 的 url 字段，\n"
            "  这样 Step4 可以直接 URL 导航到目标页面（最快最准）。\n"
            "  仅当清单中未标注 URL 时，才用 base_url 导航到首页 + click 菜单项。\n"
        ) % _base_url
        ui_section = (ui_section or "") + _base_url_section
        _log.info("[_contexts] ✓ base_url 注入: %s", _base_url)

    return (
        ui_section,
        trace_section,
        data_dict_context,
        forbidden_context,
        few_shot_context,
        action_library_context,
    )
