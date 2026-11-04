# -*- coding: utf-8 -*-
"""
导航路径查找模块（从 _single.py 拆分）
======================================
★ 架构债清理(2026-07-25): 导航关键词提取 + nav_map 查找 + Confluence 兜底 + Tab 点击
  拆分至本文件（§4.3 单文件≤800行）。

职责边界：
  - _extract_nav_keywords: 从 URL/hint 提取导航关键词（含 intent: 协议解析）
  - _lookup_nav_path: 从 nav_graph_nodes/edges 查找导航路径 + direct_url
  - _fallback_confluence_nav_path: nav_map 为空时从 Confluence 文档提取路径
  - _click_page_tab: 通用页面内 Tab 点击（覆盖主流 UI 框架）

通用性：零业务硬编码，纯基于通用 UI 框架选择器 + 文本回退 + 关键词匹配。
"""

import logging
from src.core.guarded import guard

_log = logging.getLogger(__name__)


def _extract_nav_keywords(url: str, hint: str) -> list:
    """从 URL 和 hint 提取导航关键词。

    例如：
    - /dataManagement/auxiliary/list → ["辅材", "auxiliary", "list"]
    - /dataManagement/auxiliary/recipeFlow → ["配方", "辅材", "auxiliary", "recipeFlow"]
    - /dataManage/portraitData?tab=叶组配方 → ["叶组配方", "画像", "配方"]
    - intent:辅材配方管理 → ["辅材配方管理", "辅材", "配方", "管理", "辅材配方", "配方管理"]

    ★ 通用修复 (2026-07-18): 中文关键词（suffix_map映射）优先排在前面，
    确保更精准的匹配先被尝试。同时避免 noise 关键词（如 "list", "edit"）干扰匹配。

    ★ 通用修复 (2026-07-18 v2): 提取URL查询参数中的中文文本作为最高优先级关键词。
    例如 tab=叶组配方 → "叶组配方"，避免菜单导航映射到错误页面。

    ★ 方案B (ADR-017, 2026-07-23): 识别 intent: 语义前缀协议。
      根因(§10.9 3层追问):
        1. 表层: element_exists:✗ — navigate没到正确页面
        2. 中层: intent:辅材配方管理 被当作整体英文关键词，匹配 nav_map 菜单项"辅材配方"失败
        3. 根因: Step3 生成 intent: 协议，Step4 未实现协议解析器
      通用性(§12.4 9维度):
        1. 跨系统: intent: 协议是通用语义层，适用于所有SPA系统
        2. 跨行业: 中文关键词提取适用于任何行业
        3. 跨页面: 列表/详情/表单/Tab页都适用
        4. 跨Tab: 长文本分词能匹配 Tab 菜单项（辅材配方管理 → 辅材配方）
        5. 数据完整性: 即使 nav_map 为空也能用关键词在DOM中找菜单
        6. 根因溯源: 补齐协议解析器，非打补丁
        7. 覆盖性: 所有调用 _extract_nav_keywords 的路径都受益
        8. 全局观: 解锁方案B整条流水线（Step3生成→Step4解析）
        9. 职责边界: Step4在自己职责内解析 intent: 协议
      自我反驳三次:
        1) 只剥离前缀不做分词？→ 不够。辅材配方管理匹配不到菜单项"辅材配方"（精确匹配优先，子串匹配得1分太低）
        2) 在 _handle_browser_navigate 入口处理？→ 增加间接性，_extract_nav_keywords本就是关键词提取职责
        3) Step3生成时附带keywords数组？→ 违反Step3/Step4数据简洁性，且Step3无nav_map对照能力弱
    """
    import re

    chn_keywords = []  # 中文关键词（优先匹配）
    eng_keywords = []  # 英文关键词（兜底匹配）

    target = hint or url
    if not target:
        return []

    # ★ 方案B (ADR-017, 2026-07-23): 识别 intent: 语义前缀协议
    #   Step3 生成 intent:<语义文本>，Step4 剥离前缀后提取中文关键词
    #   通用性: 纯协议解析，零业务硬编码，适用于所有 intent: 用例
    _INTENT_PREFIX = "intent:"
    if isinstance(hint, str) and hint.lower().startswith(_INTENT_PREFIX):
        _intent_text = hint[len(_INTENT_PREFIX) :].strip()
        if _intent_text:
            # 提取所有中文连续子串（最高优先级）
            _intent_cn_blocks = re.findall(r"[\u4e00-\u9fff]+", _intent_text)
            for _block in _intent_cn_blocks:
                if _block and _block not in chn_keywords:
                    chn_keywords.append(_block)
                # ★ 长文本(>=4字符)做2字符滑动窗口分词，增加子串匹配机会
                #   通用性: "辅材配方管理" → "辅材"、"配方"、"管理"、"辅材配方"、"配方管理"
                #   覆盖性: 兼容 nav_map 中可能用短词命名的菜单项
                if len(_block) >= 4:
                    for _w in range(2, len(_block)):
                        for _i in range(0, len(_block) - _w + 1):
                            _sub = _block[_i : _i + _w]
                            if _sub and _sub not in chn_keywords:
                                chn_keywords.append(_sub)
            _log.info(
                "[MenuNav] ★ 方案B intent: 协议解析: hint=%s → 中文关键词=%s",
                hint[:60],
                chn_keywords[:8],
            )

    # ★ 通用修复 (2026-07-18 v2): 从URL查询参数中提取中文文本（最高优先级）
    #   例如: /dataManage/portraitData?tab=叶组配方&menuKey=formula
    #   提取 "叶组配方" 作为关键词，确保菜单导航定位到正确的tab页面
    _query_part = ""
    if "?" in target:
        _query_part = target.split("?", 1)[1]
    elif "?" in url:
        _query_part = url.split("?", 1)[1]
    if _query_part:
        for _param in _query_part.split("&"):
            if "=" in _param:
                _k, _v = _param.split("=", 1)
                # URL解码（处理 %E5%8F%B6 等编码）
                try:
                    from urllib.parse import unquote

                    _v = unquote(_v)
                except Exception as _se14:
                    _log.debug(
                        "[_single] 异常降级(pass): %s", str(_se14)[:60], exc_info=True
                    )
                    pass
                # 提取中文文本
                _chn = re.findall(r"[\u4e00-\u9fff]+", _v)
                for _c in _chn:
                    if _c and _c not in chn_keywords:
                        chn_keywords.append(_c)

    # 提取路径中的最后一段（先去?后的查询参数）
    _clean_path = target.split("?")[0].rstrip("/")
    path_parts = _clean_path.split("/")
    if path_parts:
        last_part = path_parts[-1]
        if last_part and last_part not in (
            "list",
            "edit",
            "add",
            "create",
            "delete",
            "get",
            "post",
        ):
            eng_keywords.append(last_part)
        # 也取倒数第二段
        if len(path_parts) >= 2:
            second_last = path_parts[-2]
            if second_last and second_last not in (
                "api",
                "dataManagement",
                "dataManage",
            ):
                eng_keywords.append(second_last)

    # 从 URL 提取更多关键词
    url_lower = url.lower()
    # ★ 通用修复 (2026-07-18): 扩展常见业务关键词映射
    # 覆盖更多 AI 编造的英文URL路径 → 中文页面名映射
    suffix_map = {
        # 物料/辅材相关
        "auxiliary": "辅材",
        "material": "物料",
        # 配方相关
        "formula": "配方",
        "recipe": "配方",
        # 产品/烟叶相关
        "product": "产品",
        "tobacco": "烟叶",
        "flavor": "香精",
        # 用户/角色/任务
        "user": "用户",
        "role": "角色",
        "task": "任务",
        # 字段/配置相关
        "field": "字段",
        "config": "配置",
        "string": "字符串",
        # 数据/画像相关
        "portrait": "画像",
        "data": "数据",
        # 模型相关
        "model": "模型",
        # 导入/看板相关
        "import": "导入",
        "dashboard": "看板",
        # 品吸相关
        "tasting": "品吸",
        "evaluate": "评定",
        # 通用管理/列表相关
        "manage": "管理",
        "management": "管理",
    }
    for eng, chn in suffix_map.items():
        if eng in url_lower and chn not in chn_keywords:
            chn_keywords.append(chn)

    # ★ 中文关键词优先（查询参数中文 > suffix_map中文），英文关键词兜底
    keywords = chn_keywords + eng_keywords
    _log.debug(
        "[MenuNav] 提取关键词: %s (中文:%s 英文:%s)",
        keywords,
        chn_keywords,
        eng_keywords,
    )
    return keywords


async def _lookup_nav_path(keywords: list, project: str = "") -> dict:
    """★ v4 (2026-07-20): 从 nav_graph 查找导航路径 + 直接URL。

    通用性：不同系统Tab实现方式不同（URL参数/页面内点击/路由切换），
    返回 direct_url 让调用方优先用URL导航，URL无效时才回退到文本匹配。

    Returns:
        dict: {
            "nav_path": ["数据管理", "辅材配方"],  # 菜单文本路径
            "direct_url": "/dataManage/portraitData?tab=material_formula",  # 完整URL路径(如有)
        }
        空字典 {} 表示未找到
    """

    # ★ v4 (2026-07-20): 辅助函数 — 包装返回值为 dict 结构
    #   通用性: 不同系统Tab实现方式不同（URL参数/页面内点击/路由切换）
    #   direct_url 优先用于URL导航，nav_path 用于文本匹配回退
    def _ret(nav_path: list, direct_url: str = "") -> dict:
        return {"nav_path": nav_path or [], "direct_url": direct_url or ""}

    # ★ v4 (2026-07-20): 从 tab_item 提取完整URL路径作为 direct_url
    #   通用场景: 某SPA系统Tab用URL参数实现（?tab=material_formula）
    #   其他系统可能用路由（/path/tab-name）或页面内点击（无direct_url）
    def _extract_direct_url(item: dict) -> str:
        if not item:
            return ""
        path = (item.get("path", "") or "").strip()
        if not path:
            return ""
        # path 是完整URL路径（含?参数或/path段），可作为 direct_url
        if "?" in path or path.startswith("/"):
            return path
        return ""

    with guard("_lookup_nav_path: from src.core.db_manager import get_", log=_log):
        from src.core.db_manager import get_connection, release_connection

        if not project:
            from src.core.config_loader import config as _cfg

            project = getattr(_cfg, "project_name", "") or ""
        if not project:
            return _ret([])

        conn = get_connection()
        try:
            # ★ P0-1 读侧迁移(2026-08-05): system_nav_maps → nav_graph_nodes/edges
            #   根因(§10.9): 旧表已停写且 0 行，更关键的是它**没有 nav_json/version_label 列**
            #   （实际列名 nav_map_json），原 SQL 每次都抛 Unknown column 被 except 静默吞，
            #   表现为"查无导航"→ Step4 菜单导航永久失效。
            from src.users.project_auth import get_active_version_label
            from src.pipeline.utils.nav_graph_read import read_menu_groups

            _version = get_active_version_label(project) if project else ""
            menu_groups = read_menu_groups(project, _version or None)
            if not menu_groups:
                _log.warning(
                    "[MenuNav] nav_graph 无导航节点 (project=%s, version=%s)，"
                    "回退 Confluence 文档提取",
                    project,
                    _version or "<latest>",
                )
                nav_path = await _fallback_confluence_nav_path(keywords, project, conn)
                if nav_path:
                    return _ret(nav_path)
                return _ret([])
            # ★ 通用修复 v2 (2026-07-20): 精确匹配 + tab_item 优先
            #   通用性: 零业务硬编码，纯基于匹配精度和层级优先级
            best_match = None
            best_score = 0
            best_is_tab = False  # ★ v4: 记录是否为 tab_item
            for group in menu_groups:
                items = group.get("items", [])
                for item in items:
                    item_text = (item.get("text", "") or "").lower()
                    if not item_text:
                        continue
                    score = 0
                    matched_kws = []
                    for kw in keywords:
                        kw_lower = kw.lower()
                        if kw_lower == item_text:
                            score += 1000  # ★ v2: 精确匹配满分
                            matched_kws.append(kw_lower)
                        elif kw_lower in item_text:
                            score += 1  # 包含匹配
                            matched_kws.append(kw_lower)
                    if score > 0:
                        score = score - len(item_text) * 0.01
                        if score > best_score:
                            best_score = score
                            best_match = item
                            best_is_tab = False
                # ★ v2(2026-07-20): tab_item 加 bonus，因为 Tab 比顶级菜单更具体
                for tab_item in group.get("tab_items", []):
                    tab_text = (tab_item.get("text", "") or "").lower()
                    if not tab_text:
                        continue
                    score = 0
                    for kw in keywords:
                        kw_lower = kw.lower()
                        if kw_lower == tab_text:
                            score += 1000  # ★ v2: 精确匹配满分
                        elif kw_lower in tab_text:
                            score += 1
                    if score > 0:
                        score = score + 100  # ★ v2: tab_item bonus (更具体)
                        score = score - len(tab_text) * 0.01
                        if score > best_score:
                            best_score = score
                            best_match = tab_item
                            best_is_tab = True
            if best_match:
                item_text = best_match.get("text", "")
                # ★ 通用修复 (2026-07-18 v2): 质量门禁
                import re as _re

                _chn_kws = [
                    kw
                    for kw in keywords
                    if len(kw) >= 3 and _re.match(r"^[\u4e00-\u9fff]+$", kw)
                ]
                if _chn_kws:
                    _any_matched = False
                    for _ck in _chn_kws:
                        if _ck.lower() in item_text.lower():
                            _any_matched = True
                            break
                    if not _any_matched:
                        _log.info(
                            "[MenuNav] 中文查询参数关键词 %s 不匹配最佳item '%s'，放弃菜单导航，回退直接URL",
                            _chn_kws,
                            item_text,
                        )
                        best_match = None
                        best_score = 0
                        best_is_tab = False

            if best_match:
                item_text = best_match.get("text", "")
                parent_path = best_match.get("parent_path", "")
                # ★ v3 (2026-07-20): tab_item 无 parent_path 时，补充其所属 group 的顶级菜单
                if not parent_path:
                    for _g in menu_groups:
                        _g_tab_items = _g.get("tab_items", [])
                        if any(_t.get("text", "") == item_text for _t in _g_tab_items):
                            _g_items = _g.get("items", [])
                            if _g_items:
                                _parent_text = _g_items[0].get("text", "")
                                if _parent_text:
                                    parent_path = _parent_text
                                    _log.info(
                                        "[MenuNav] tab_item '%s' 补充父菜单: '%s'",
                                        item_text,
                                        _parent_text,
                                    )
                            break
                # ★ v4 (2026-07-20): tab_item 优先提取 direct_url（URL参数Tab的通用解决方案）
                _direct_url = ""
                if best_is_tab:
                    _direct_url = _extract_direct_url(best_match)
                    if _direct_url:
                        _log.info(
                            "[MenuNav] tab_item '%s' 提取 direct_url: %s",
                            item_text,
                            _direct_url[:80],
                        )
                # ★ 方案B (ADR-017, 2026-07-23): 普通item匹配时补充查找tab_item的direct_url
                #   根因(§10.9 3层追问):
                #     1. 表层: direct_url=False → 无法URL直达 → 走菜单导航+Tab点击 → Tab切换失败
                #     2. 中层: best_match是普通item(is_tab=False)，只在best_is_tab=True时提取direct_url
                #     3. 根因: Tab子页面在nav_map中同时作为普通item(无URL)和tab_item(有URL)存在，
                #            匹配到普通item时应补充查找同名/子串匹配的tab_item获取direct_url
                #   通用性(§12.4 9维度):
                #     1. 跨系统: Tab子页面的nav_map双注册模式是通用SPA结构
                #     2. 跨行业: 适用于所有有Tab子页面的系统
                #     3. 跨页面: 覆盖所有"普通item无URL但tab_item有URL"的场景
                #     4. 跨Tab: 直接解决Tab子页面直达问题
                #     5. 数据完整性: tab_item无URL时降级为空，不崩溃
                #     6. 根因溯源: 补齐direct_url提取逻辑，非打补丁
                #     7. 覆盖性: 所有普通item匹配路径都受益
                #     8. 全局观: direct_url直达避免Tab切换，减少失败路径
                #     9. 职责边界: Step4导航层在自己职责内补充URL
                #   自我反驳三次:
                #     1) 只查同名tab_item？→ 不够，Tab文本可能是缩写(辅材配方 vs 辅材配方管理)
                #     2) 遍历所有tab_items子串匹配？→ 是，双向子串匹配覆盖命名差异
                #     3) 从rag_chunks查URL？→ rag_chunks是补充数据源，但nav_map内的tab_item更可靠
                if not _direct_url:
                    for _g in menu_groups:
                        for _ti in _g.get("tab_items", []):
                            _ti_text = (_ti.get("text", "") or "").strip()
                            if not _ti_text:
                                continue
                            # 双向子串匹配：tab_text在item_text中 或 item_text在tab_text中
                            _ti_lower = _ti_text.lower()
                            _item_lower = item_text.lower()
                            if _ti_lower in _item_lower or _item_lower in _ti_lower:
                                _ti_url = _extract_direct_url(_ti)
                                if _ti_url:
                                    _direct_url = _ti_url
                                    _log.info(
                                        "[MenuNav] ★ 方案B 补充tab_item direct_url: item='%s' → tab='%s' url=%s",
                                        item_text,
                                        _ti_text,
                                        _ti_url[:80],
                                    )
                                    break
                        if _direct_url:
                            break
                _log.info(
                    "[MenuNav] 最佳匹配: '%s' (score=%.2f, is_tab=%s, direct_url=%s)",
                    item_text,
                    best_score,
                    best_is_tab,
                    bool(_direct_url),
                )
                if parent_path:
                    path_parts = [
                        p.strip() for p in parent_path.split("/") if p.strip()
                    ]
                    path_parts.append(item_text)
                    return _ret(path_parts, _direct_url)
                return _ret([item_text], _direct_url)
            # 第二轮：模糊匹配
            # ★ v2 (2026-07-20): 模糊匹配也应用质量门禁
            import re as _re_gate

            _chn_kws_gate = [
                kw
                for kw in keywords
                if len(kw) >= 3 and _re_gate.match(r"^[\u4e00-\u9fff]+$", kw)
            ]
            for group in menu_groups:
                # 搜索 items
                items = group.get("items", [])
                for item in items:
                    item_text = (item.get("text", "") or "").lower()
                    if not item_text:
                        continue
                    # ★ v2: 质量门禁 — 如果有中文关键词，item_text 必须匹配至少一个
                    if _chn_kws_gate:
                        _any_chn_match = any(
                            _ck.lower() in item_text for _ck in _chn_kws_gate
                        )
                        if not _any_chn_match:
                            continue  # 跳过不匹配中文关键词的item
                    for kw in keywords:
                        kw_lower = kw.lower()
                        if item_text in kw_lower or kw_lower in item_text:
                            _log.info(
                                "[MenuNav] 模糊匹配: kw=%s ↔ item=%s", kw, item_text
                            )
                            parent_path = item.get("parent_path", "")
                            if parent_path:
                                path_parts = [
                                    p.strip()
                                    for p in parent_path.split("/")
                                    if p.strip()
                                ]
                                path_parts.append(item.get("text", ""))
                                return _ret(path_parts)
                            return _ret([item.get("text", "")])
                # ★ 通用修复(2026-07-18): 同时模糊匹配 tab_items
                for tab_item in group.get("tab_items", []):
                    tab_text = (tab_item.get("text", "") or "").lower()
                    if not tab_text:
                        continue
                    # ★ v2: tab_items 不应用质量门禁
                    for kw in keywords:
                        kw_lower = kw.lower()
                        if tab_text in kw_lower or kw_lower in tab_text:
                            _log.info(
                                "[MenuNav] 模糊匹配Tab: kw=%s ↔ tab=%s", kw, tab_text
                            )
                            parent_path = tab_item.get("parent_path", "")
                            # ★ v3 (2026-07-20): tab_item 无 parent_path 时补充父菜单
                            if not parent_path:
                                _parent_item_text = ""
                                _p_items = group.get("items", [])
                                if _p_items:
                                    _parent_item_text = _p_items[0].get("text", "")
                                if _parent_item_text:
                                    parent_path = _parent_item_text
                                    _log.info(
                                        "[MenuNav] 模糊Tab '%s' 补充父菜单: '%s'",
                                        tab_text,
                                        _parent_item_text,
                                    )
                            # ★ v4: tab_item 提取 direct_url
                            _direct_url = _extract_direct_url(tab_item)
                            if _direct_url:
                                _log.info(
                                    "[MenuNav] 模糊Tab '%s' 提取 direct_url: %s",
                                    tab_text,
                                    _direct_url[:80],
                                )
                            if parent_path:
                                path_parts = [
                                    p.strip()
                                    for p in parent_path.split("/")
                                    if p.strip()
                                ]
                                path_parts.append(tab_item.get("text", ""))
                                return _ret(path_parts, _direct_url)
                            return _ret([tab_item.get("text", "")], _direct_url)
            # 第三轮：URL 路径匹配
            for group in menu_groups:
                items = group.get("items", [])
                for item in items:
                    item_path = (item.get("path", "") or "").lower()
                    if not item_path:
                        continue
                    for kw in keywords:
                        kw_lower = kw.lower()
                        if kw_lower in item_path or item_path in kw_lower:
                            _log.info(
                                "[MenuNav] URL路径匹配: kw=%s ↔ path=%s", kw, item_path
                            )
                            parent_path = item.get("parent_path", "")
                            if parent_path:
                                path_parts = [
                                    p.strip()
                                    for p in parent_path.split("/")
                                    if p.strip()
                                ]
                                path_parts.append(item.get("text", ""))
                                return _ret(path_parts)
                            return _ret([item.get("text", "")])
                # ★ 通用修复(2026-07-18): 同时 URL 路径匹配 tab_items
                for tab_item in group.get("tab_items", []):
                    tab_path = (tab_item.get("path", "") or "").lower()
                    if not tab_path:
                        continue
                    for kw in keywords:
                        kw_lower = kw.lower()
                        if kw_lower in tab_path or tab_path in kw_lower:
                            _log.info(
                                "[MenuNav] URL路径匹配Tab: kw=%s ↔ path=%s",
                                kw,
                                tab_path,
                            )
                            parent_path = tab_item.get("parent_path", "")
                            # ★ v4: tab_item 提取 direct_url
                            _direct_url = _extract_direct_url(tab_item)
                            if _direct_url:
                                _log.info(
                                    "[MenuNav] URL路径Tab '%s' 提取 direct_url: %s",
                                    tab_path,
                                    _direct_url[:80],
                                )
                            if parent_path:
                                path_parts = [
                                    p.strip()
                                    for p in parent_path.split("/")
                                    if p.strip()
                                ]
                                path_parts.append(tab_item.get("text", ""))
                                return _ret(path_parts, _direct_url)
                            return _ret([tab_item.get("text", "")], _direct_url)
            # ★ 通用修复 (2026-07-18): 第四轮 — 按 group_name 匹配
            for group in menu_groups:
                group_name = (group.get("group_name", "") or "").lower()
                if not group_name:
                    continue
                for kw in keywords:
                    kw_lower = kw.lower()
                    if kw_lower in group_name or group_name in kw_lower:
                        _log.info(
                            "[MenuNav] 组名匹配: kw=%s ↔ group=%s", kw, group_name
                        )
                        return _ret([group.get("group_name", "")])
        finally:
            release_connection(conn)
    return _ret([])


async def _fallback_confluence_nav_path(keywords: list, project: str, conn) -> list:
    """通用回退：nav_graph 无节点时，从 Confluence 文档提取导航路径。

    Confluence 文档中通常包含路径信息，如：
    - 路径：数据管理-物料管理-辅材管理
    - 路径：数据管理-配方管理-辅材配方管理

    根据关键词匹配最合适的路径。
    """
    import re

    with guard("_fallback_confluence_nav_path: row = conn.execute( 'SELECT content ", log=_log):
        # 从 source_documents 读取 Confluence 文档
        row = conn.execute(
            "SELECT content FROM source_documents WHERE project=%s AND doc_type='confluence' ORDER BY updated_at DESC LIMIT 1",
            (project,),
        ).fetchone()
        if not row or not row.get("content"):
            return []

        content = row["content"]
        # 提取所有路径：数据管理-物料管理-辅材管理 或 数据管理→物料管理→辅材管理
        path_pattern = re.findall(
            r"路径[：:]\s*(.+?)(?:\n|$)|([一-鿿\w]+(?:[→>-][一-鿿\w]+)+)", content
        )
        all_paths = []
        for match in path_pattern:
            path_str = match[0] or match[1]
            if path_str:
                # 统一分隔符为 →
                path_str = re.sub(r"[->]", "→", path_str)
                parts = [p.strip() for p in path_str.split("→") if p.strip()]
                if len(parts) >= 2:
                    all_paths.append(parts)

        if not all_paths:
            return []

        _log.info("[MenuNav] Confluence 回退：提取到 %d 条路径", len(all_paths))

        # 按关键词匹配
        for kw in keywords:
            kw_lower = kw.lower()
            for path_parts in all_paths:
                # 检查路径的任意部分是否匹配关键词
                for part in path_parts:
                    if kw_lower in part.lower() or part.lower() in kw_lower:
                        _log.info(
                            "[MenuNav] Confluence 回退匹配: kw=%s → path=%s",
                            kw,
                            " → ".join(path_parts),
                        )
                        return path_parts

        # 如果关键词没匹配到，返回路径中与关键词最相关的
        # 尝试匹配路径最后一级（通常是页面名）
        for kw in keywords:
            kw_lower = kw.lower()
            for path_parts in all_paths:
                last = path_parts[-1].lower()
                for kw_part in kw_lower.split():
                    if kw_part in last:
                        _log.info(
                            "[MenuNav] Confluence 回退末级匹配: kw=%s → path=%s",
                            kw,
                            " → ".join(path_parts),
                        )
                        return path_parts

    return []


async def _click_page_tab(page, tab_name: str) -> bool:
    """★ v3 (2026-07-20): 通用页面内Tab点击 — 覆盖主流UI框架。

    通用Tab选择器策略（按优先级）：
      1. Ant Design: .ant-tabs-tab (文本匹配)
      2. ElementUI: .el-tabs__item (文本匹配)
      3. 通用 [role="tab"] (ARIA标准)
      4. 通用 .tab-item / .nav-link (Bootstrap等)
      5. 通用文本回退: 任何含tab_name文本的可点击元素

    通用性：零业务硬编码，纯基于通用UI框架选择器 + 文本回退。
    """
    if not tab_name:
        return False
    try:
        # ★ 方案B (ADR-017, 2026-07-23): Tab元素直接等待（替代容器等待）
        #   根因(§10.9 3层追问):
        #     1. 表层: [PageTab] 未找到Tab容器 → 8个选择器全返回0 → Tab切换失败
        #     2. 中层: 页面点击2级菜单后Tab元素懒加载，容器等待18s期间页面还在导航
        #     3. 根因: 容器检查(.ant-tabs等)不等于Tab元素检查；页面networkidle后Tab才渲染
        #   修复:
        #     1. 先等networkidle（页面加载完成）
        #     2. 直接等Tab元素出现（用首选择器.wait_for，不是等容器）
        #     3. 合并v13选择器(含.ant-tabs-tab-btn/.n-tabs-tab，与click阶段v13一致)
        #   通用性(§12.4):
        #     1. 跨系统: networkidle是通用加载状态，适用于所有Web系统
        #     2. 跨框架: 选择器覆盖AntDesign/ElementUI/NaiveUI/Bootstrap/ARIA
        #     3. 数据完整: 不依赖nav_map数据，纯DOM感知
        #     4. 根因溯源: 修Tab加载时序问题，非打补丁
        #   自我反驳三次:
        #     1) 只加长容器超时？→ 不够。容器不一定存在(某SPA系统用非标准容器)
        #     2) 只加networkidle？→ 不够。networkidle后Tab可能仍需JS渲染
        #     3) networkidle+直接等Tab元素？→ 是。双保险覆盖加载时序
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception as _se15:
            _log.debug("[_single] 异常降级(pass): %s", str(_se15)[:60], exc_info=True)
            pass  # networkidle超时不阻塞，继续尝试找Tab

        # 通用Tab选择器列表（覆盖主流UI框架，合并v13选择器）
        _tab_selectors = [
            ".ant-tabs-tab",  # Ant Design
            ".ant-tabs-tab-btn",  # Ant Design tab button (v13)
            ".el-tabs__item",  # ElementUI
            '[role="tab"]',  # ARIA标准
            ".n-tabs-tab",  # Naive UI (v13)
            ".nav-tabs .nav-item",  # Bootstrap
            ".tab-item",  # 通用
            ".nav-link",  # 通用
            ".ant-tabs-tab-active",  # Ant Design active
            ".el-tabs__nav > div",  # ElementUI nav
        ]

        # ★ 方案B: 直接等Tab元素出现（首选择器wait_for，最多8秒）
        _tab_elem_found = False
        for _sel_wait in _tab_selectors[:5]:  # 只等前5个常用选择器
            try:
                await page.locator(_sel_wait).first.wait_for(
                    state="attached", timeout=4000
                )
                _tab_elem_found = True
                _log.info("[PageTab] ★ 方案B Tab元素已出现: %s", _sel_wait)
                break
            except Exception as _se16:
                _log.debug(
                    "[_single] 异常降级(continue): %s", str(_se16)[:60], exc_info=True
                )
                continue
        if not _tab_elem_found:
            _log.info("[PageTab] ★ 方案B 未直接等到Tab元素，尝试所有选择器匹配")
            await page.wait_for_timeout(1000)
        for selector in _tab_selectors:
            try:
                tabs = page.locator(selector)
                count = await tabs.count()
                if count == 0:
                    continue
                # 按文本匹配
                for i in range(count):
                    tab = tabs.nth(i)
                    text = (await tab.text_content() or "").strip()
                    if text == tab_name or tab_name in text:
                        await tab.click()
                        _log.info(
                            "[PageTab] 点击Tab成功: '%s' (selector=%s, text='%s')",
                            tab_name,
                            selector,
                            text,
                        )
                        return True
                # 模糊匹配：tab_name 是 text 的子串
                for i in range(count):
                    tab = tabs.nth(i)
                    text = (await tab.text_content() or "").strip()
                    if text and (tab_name in text or text in tab_name):
                        await tab.click()
                        _log.info(
                            "[PageTab] 模糊匹配Tab: '%s' ↔ '%s' (selector=%s)",
                            tab_name,
                            text,
                            selector,
                        )
                        return True
            except Exception as _e:
                _log.debug("[PageTab] selector=%s 异常: %s", selector, str(_e)[:80])
                continue

        # ★ v3.1 (2026-07-20): 文本回退 — 任何含tab_name文本的可点击元素
        #   通用场景: 某些系统Tab不用标准class，用div/span/a等
        #   策略: 用 text= 选择器找所有含tab_name的元素，过滤可见且可点击的
        with guard("_click_page_tab: text_locator = page.locator(f'text={", log=_log):
            text_locator = page.locator(f"text={tab_name}")
            text_count = await text_locator.count()
            for i in range(text_count):
                el = text_locator.nth(i)
                try:
                    is_visible = await el.is_visible()
                    if not is_visible:
                        continue
                    await el.click()
                    _log.info("[PageTab] 文本回退点击成功: '%s'", tab_name)
                    return True
                except Exception as _se17:
                    _log.debug(
                        "[_single] 异常降级(continue): %s",
                        str(_se17)[:60],
                        exc_info=True,
                    )
                    continue

        # ★ 方案B (ADR-017, 2026-07-23): 关键词提取匹配 — Tab文本通常是导航意图的缩写
        #   根因(§10.9 3层追问):
        #     1. 表层: [PageTab] 未找到Tab '辅材配方管理' — 8个选择器+文本回退都失败
        #     2. 中层: Tab实际文本是"辅材配方"(无"管理"后缀)，精确匹配和双向子串匹配
        #            ("辅材配方管理" in "辅材配方"=False)都失败；文本回退用text=精确匹配也失败
        #     3. 根因: tab_name是Step3生成的语义意图(含"管理"后缀)，Tab实际文本是缩写，
        #            需要从tab_name提取核心关键词(辅材/配方)匹配Tab元素
        #   通用性(§12.4 9维度):
        #     1. 跨系统: 关键词提取是通用文本处理，适用于所有UI框架的Tab
        #     2. 跨行业: 导航意图命名差异是通用问题(管理/列表/页面后缀)
        #     3. 跨页面: 适用于所有Tab子页面
        #     4. 跨Tab: 直接解决Tab文本与意图文本的命名差异
        #     5. 数据完整性: 不依赖外部数据，纯文本处理
        #     6. 根因溯源: 修Tab文本匹配缺陷，非打补丁
        #     7. 覆盖性: 所有_click_page_tab调用路径都受益
        #     8. 全局观: 与intent:协议解析形成完整链路
        #     9. 职责边界: Step4在自己职责内增强Tab匹配
        #   自我反驳三次:
        #     1) 只去后缀？→ 不够。"辅材配方管理"去"管理"→"辅材配方"，但双向子串已尝试
        #     2) 用所有2字符子串？→ 太宽泛，"配方"会匹配"叶组配方"。需用首个关键词
        #     3) 用首个2字符关键词？→ 是。"辅材配方管理"→"辅材"，唯一性高
        import re as _re_tab_kw

        _tab_kws = _re_tab_kw.findall(r"[\u4e00-\u9fff]{2,4}", tab_name)
        if _tab_kws:
            # 优先用第一个关键词（通常是业务核心词，如"辅材"），唯一性最高
            _primary_kw = _tab_kws[0]
            _log.info(
                "[PageTab] ★ 方案B 关键词匹配: tab_name='%s' → 首个关键词='%s'",
                tab_name,
                _primary_kw,
            )
            for selector in _tab_selectors:
                try:
                    tabs = page.locator(selector)
                    count = await tabs.count()
                    if count == 0:
                        continue
                    for i in range(count):
                        tab = tabs.nth(i)
                        text = (await tab.text_content() or "").strip()
                        # 关键词在Tab文本中（"辅材" in "辅材配方"=True）
                        if text and _primary_kw in text:
                            await tab.click()
                            _log.info(
                                "[PageTab] ★ 方案B 关键词匹配Tab成功: '%s' ↔ '%s' (selector=%s, kw='%s')",
                                tab_name,
                                text,
                                selector,
                                _primary_kw,
                            )
                            return True
                except Exception as _e:
                    _log.debug(
                        "[PageTab] 关键词匹配 selector=%s 异常: %s",
                        selector,
                        str(_e)[:80],
                    )
                    continue

        _log.info(
            "[PageTab] 未找到Tab '%s' (尝试了 %d 个选择器+文本回退+关键词匹配)",
            tab_name,
            len(_tab_selectors),
        )
        return False
    except Exception as e:
        _log.warning("[PageTab] 点击Tab异常: %s", str(e)[:120])
        return False
