# -*- coding: utf-8 -*-
"""意图定位数据层模块 — 从 locator.py 拆分（§4.3 单文件≤800行）。

职责：域关键词提取 + rag_chunks/nav_map 数据查询（纯同步函数，无 Playwright）。
被 _intent_executor.py 依赖。
"""
import logging
from src.core.guarded import guard

_log = logging.getLogger(__name__)


# ── 方案B (ADR-017): intent: 语义意图实时感知 ──


def _extract_domain_keywords(operation: str) -> list[str]:
    """★ v4 通用修复 (ADR-017, 2026-07-23): 从operation提取域关键词。

    根因(§10.9 4层追问):
      1. 表层: '用户点击复制版本' → 关键词缺 '复制'/'版本'
      2. 中层: '复制'被_action_words静态词表过滤
      3. 根因: n-gram滑动窗口不识别词边界，静态词表无法处理一词多义
         ("复制"既是UI动作又是业务操作名)
      4. 架构: 域关键词应从operation语义提取，而非盲猜n-gram+静态词表

    修复(v4): 操作前缀剥离 + 域文本提取
      1. 剥离开头主语(用户/系统/管理员/操作员)
      2. 剥离紧跟动词(点击/执行/查询/导入/加载/查看等)
      3. 剥离中间修饰(列表中的/页面中的/对应的/某条/批量等)
      4. 剥离结尾动作后缀(操作/导入/导出/对接/管理/查询等)
      5. 剩余=域文本，生成完整词+2-4gram

    通用性(§12.4):
      - 跨系统: 剥离规则基于通用中文操作句式，非业务特定
      - 跨行业: 无行业词库依赖，域文本从operation语义提取
      - 跨页面: 完整域文本做精确子串匹配，优于n-gram碎片
      - 根因溯源: 修复"一词多义"根因，而非维护静态词表
      - 覆盖性: 函数签名不变，6个调用点无需改动
      - 全局观: 关键词准确→页面评分准→导航对Tab→点击对按钮→用例通过
    """
    import re as _re

    if not operation:
        return []
    _text = operation.strip()

    # 1. 剥离开头主语（用户/系统/管理员/操作员/前端/后台/客户端）
    _text = _re.sub(
        r"^(用户|系统|管理员|操作员|用户角色|前端|后台|客户端|平台)[之的]?", "", _text
    )

    # 2. 剥离紧跟动词（点击/执行/查询/导入/加载/查看/操作等，含双动词组合）
    _text = _re.sub(
        r"^(点击|执行|查询|导入|加载|查看|操作|访问|进入|选择|输入|上传|下载|导出|"
        r"提交|保存|确认|取消|删除|编辑|修改|新建|新增|添加|创建|启用|停用|刷新|重置|"
        r"触发|调用|打开|关闭|展示|显示|返回|完成|支持|需要|允许|浏览|检索|筛选|查找|"
        r"点击查看|点击编辑|点击删除|点击新建|点击添加|点击查询|点击操作|点击进入|点击访问|点击选择|"
        r"可进行|可执行|可查看|可操作)",
        "",
        _text,
    )

    # 3. 剥离中间修饰（列表中的/页面中的/对应的/某条/批量/单个等）
    _text = _re.sub(
        r"(列表中的|页面中的|对应的|某条|某个|该条|这条|一条|当前|所有|全部|"
        r"批量|单个|快速|定时|自动|手动|本地|远程|在线|离线)",
        "",
        _text,
    )

    # 4. 剥离结尾动作后缀（操作/导入/导出/对接/管理/查询等）
    _text = _re.sub(
        r"(操作|导入|导出|对接|管理|查询|加载|展示|维护|初始化|处理|流程|功能|"
        r"数据|信息|记录|结果|列表|明细|场景|能力|行为|动作|步骤)$",
        "",
        _text,
    )

    _text = _text.strip()
    # 剥离后为空 → 回退到原文本（保证总有关键词）
    if len(_text) < 2:
        _text = operation.strip()

    cn_chars = _re.findall(r"[\u4e00-\u9fff]", _text)
    if len(cn_chars) < 2:
        return [_text] if _text else []

    domain_kws = []
    # 完整域文本（最高优先级，精确匹配，区分度最高）
    _full = "".join(cn_chars)
    if _full and _full not in domain_kws:
        domain_kws.append(_full)
    # 4-gram → 3-gram → 2-gram（长词优先，减少碎片噪音）
    for length in (4, 3, 2):
        for i in range(len(cn_chars) - length + 1):
            _gram = "".join(cn_chars[i : i + length])
            if _gram not in domain_kws:
                domain_kws.append(_gram)

    # 过滤纯停用字组合（的/了/是/在等无意义组合）
    _stop_chars = {
        "的",
        "了",
        "是",
        "在",
        "为",
        "与",
        "和",
        "或",
        "及",
        "等",
        "之",
        "其",
        "该",
    }
    domain_kws = [kw for kw in domain_kws if not all(c in _stop_chars for c in kw)]

    return domain_kws


def _find_button_page_in_rag(operation: str):
    """★ v3 通用修复 (ADR-017, 2026-07-23): 收集所有匹配并按域关键词评分。

    根因(§10.9 3层追问):
      1. 表层: "批量导入辅材"匹配到产品页"数据导入"而非辅材配方Tab页"数据导入"
      2. 中层: 原实现返回第一个匹配，无域上下文区分
      3. 根因: 同一按钮文本在多个页面存在，需用operation域关键词区分

    修复: 收集所有匹配 → 用operation域关键词评分页面DOM上下文 → 返回最高分匹配
    通用性(§12.4): 无业务硬编码，域关键词从operation动态提取
    """
    if not operation:
        return None
    try:
        from src.pipeline.step3._rule_extractor import _match_button_by_keywords
    except ImportError as _e:
        _log.debug(
            "[Step4] import _match_button_by_keywords 失败(§5+§12.5 Step4越界引用Step3): %s",
            _e,
        )
        return None

    try:
        from src.core.db_manager import get_connection, release_connection

        _project = ""
        try:
            from src.core.config_loader import config

            _project = getattr(config, "project_name", "") or ""
        except Exception as _se8:
            _log.debug("[locator] 异常降级(pass): %s", str(_se8)[:60], exc_info=True)
            pass
        if not _project:
            try:
                from src.backend.state import _current_project

                _project = _current_project() or ""
            except Exception as _se9:
                _log.debug(
                    "[locator] 异常降级(pass): %s", str(_se9)[:60], exc_info=True
                )
                pass
        if not _project:
            return None

        _conn = get_connection()
        try:
            _rows = _conn.execute(
                "SELECT chunk_id, text FROM rag_chunks "
                "WHERE scope_type='project' AND scope_id=%s AND type='dom' "
                "LIMIT 500",
                (_project,),
            ).fetchall()
        finally:
            release_connection(_conn)

        if not _rows:
            return None

        import re as _re_rag

        _domain_kws = _extract_domain_keywords(operation)

        _pages = {}
        for _row in _rows:
            _chunk_id = _row["chunk_id"] or ""
            _text = _row["text"] or ""
            _page_url = _chunk_id
            if _page_url.startswith("dom:"):
                _page_url = _page_url[4:]
            _page_url = _re_rag.sub(r":\d+$", "", _page_url)
            # ★ v15 修复(2026-07-23): 增强 rag_chunks 按钮提取，兼容3种格式
            #   根因(§10.9 4层追问):
            #     1. 表层: _find_button_page_in_rag 返回 None，找不到按钮
            #     2. 中层: tag=button 正则匹配不到，因为 rag_chunks 的 tag 字段是 span/div/li
            #     3. 根因: Step1 爬取器把 ARIA 按钮(role=button)的 tag 记为原生标签(span)，
            #        且 [PAGE_ELEMENTS] 汇总行被 continue 跳过
            #     4. 架构: 按钮文本只存在于 [PAGE_ELEMENTS] 汇总行的 BUTTONS: 列表
            #   修复(通用性§12.4):
            #     - 不跳过 [PAGE_ELEMENTS] 汇总行，从 BUTTONS: 提取按钮列表
            #     - 增强 role=button 元素提取（text 可能在 role 前/后）
            #     - 保留原 tag=button 提取（兼容标准格式）
            #   跨系统: Ant Design/ElementUI/Naive UI 的按钮多为 <span role=button> 或 <button>
            #   跨行业: 无业务硬编码，纯 DOM 属性提取
            _is_page_elements = ":PAGE_ELEMENTS" in _page_url
            _page_url = _re_rag.sub(r":PAGE_ELEMENTS$", "", _page_url)
            if not _page_url:
                continue
            if _page_url not in _pages:
                _pages[_page_url] = {"buttons": [], "dom_text": ""}
            _pages[_page_url]["dom_text"] += "\n" + _text
            _btn_texts = []
            if _is_page_elements:
                # 格式1: [PAGE_ELEMENTS] ... BUTTONS: btn1, btn2, btn3 | ...
                _m = _re_rag.search(r"BUTTONS:\s*([^|]+)", _text)
                if _m:
                    _btn_texts = [
                        t.strip() for t in _m.group(1).split(",") if t.strip()
                    ]
            else:
                # 格式2: tag=button | text=xxx（标准 button 标签）
                _btn_texts = _re_rag.findall(r"tag=button\s*\|\s*text=([^|\n]+)", _text)
                # 格式3: role=button 且有 text=xxx（ARIA 按钮，Ant Design 常见）
                if not _btn_texts and "role=button" in _text:
                    _btn_texts = _re_rag.findall(r"text=([^|\n]+)", _text)
            _btn_texts = [t.strip() for t in _btn_texts if t.strip()]
            _pages[_page_url]["buttons"].extend(_btn_texts)

        _matches = []
        for _page_url, _data in _pages.items():
            if not _data["buttons"]:
                continue
            _unique_btns = list(set(_data["buttons"]))
            _matched = _match_button_by_keywords(operation, _unique_btns)
            if _matched:
                _domain_score = sum(1 for kw in _domain_kws if kw in _data["dom_text"])
                _matches.append((_page_url, _matched, _domain_score))

        if not _matches:
            return None

        _matches.sort(key=lambda x: x[2], reverse=True)
        _best = _matches[0]
        _log.info(
            "[_find_button_page_in_rag] v3: op=%r → %d匹配, best: page=%r btn=%r score=%d",
            operation[:30],
            len(_matches),
            _best[0][:60],
            _best[1][:30],
            _best[2],
        )
        return (_best[0], _best[1])
    except Exception as _e:
        _log.debug("[_find_button_page_in_rag] 异常: %s", str(_e)[:100])
        return None


def _load_nav_map_pages(project: str):
    """★ v4 通用修复 (ADR-017, 2026-07-23): 从 system_nav_maps 加载所有页面 URL。
    ★ v4.2 修复 (2026-07-23): 用 path 去重而非 URL 去重，保留 Tab/sidebar 变体。

    根因(§10.9):
      1. 表层: _find_button_page_in_rag 找不到"编辑"按钮 → intent: 定位失败
      2. 中层: rag_chunks 只有8种按钮(顶部工具栏)，缺表格行内按钮(编辑/删除)
      3. 根因: Step1爬取遗漏表格行内按钮 + nav_map缺部分Tab(辅材配方)
      4. 架构: rag_chunks 是预爬取数据，天然不完整；nav_map 是页面URL清单，相对完整
      5. v4.2: 原 URL 去重导致 16 页面只剩 3 个(多页面共享 URL 但 path 不同)

    修复: 当 rag_chunks 找不到时，用 nav_map 的页面URL作为导航目标，
          让 _navigate_to_page_and_find_button 实时扫描页面按钮(含表格行内)。
          v4.2: 用 path 去重，保留所有 Tab/sidebar 变体。

    通用性(§12.4):
      - 跨系统: nav_map 结构通用(flat_list含path/url/text)
      - 跨行业: 无业务硬编码
      - 数据完整性: nav_map为空时返回空列表，调用方降级处理
    """
    if not project:
        return []
    try:
        # ★ P0-1 读侧迁移(2026-08-05): system_nav_maps(0行且无 nav_json 列) → nav_graph
        from src.pipeline.utils.nav_graph_read import read_flat_list

        _pages = []
        _seen_paths = set()  # ★ v4.2: 用 path 去重，保留 Tab/sidebar 变体
        for _item in read_flat_list(project):
            _path = _item.get("path", "") or ""
            _url = _item.get("real_url") or _item.get("url", "") or ""
            _text = _item.get("text") or _item.get("title", "") or ""
            if not _url or not _path or _path in _seen_paths:
                continue
            _seen_paths.add(_path)
            _pages.append({"path": _path, "url": _url, "text": _text})
        return _pages
    except Exception as _e:
        _log.error(
            "[_load_nav_map_pages] 导航页面加载失败 project=%s: %s",
            project,
            _e,
            exc_info=True,
        )
        return []


def _load_rag_pages(project: str):
    """★ v4.4 通用修复 (ADR-017, 2026-07-23): 从 rag_chunks 补充候选页面。

    根因(§10.9 5层追问):
      1. 表层: _find_button_via_nav_map 只从 nav_map 加载页面，找不到 ?menuKey=formula 页面
      2. 中层: nav_map 缺 ?menuKey=formula 子菜单页面（只有16个基础页面）
      3. 根因: Step1 Phase2 _synthetic_tab_url 返回 None（URL不变时）→ Tab快照未写入 page_dom_snapshots → 未加入 nav_map
      4. 架构: rag_chunks 是 Step1 爬取时实时写入的，包含了所有访问过的页面（含 Tab 子页面）
      5. 全局: rag_chunks 数据比 nav_map 更完整，可作为 nav_map 的补充数据源

    修复: 从 rag_chunks 提取所有唯一 page_path，作为 nav_map 的补充候选页面。
          让 _find_button_via_nav_map 能导航到 ?menuKey=formula 等未入 nav_map 的页面。

    通用性(§12.4):
      - 跨系统: rag_chunks chunk_id 格式通用（dom:{path}:{idx}）
      - 跨行业: 无业务硬编码，纯数据提取
      - 跨页面: 覆盖所有被爬取过的页面（含 Tab/sidebar 子页面）
      - 数据完整性: rag_chunks 为空时返回空列表，调用方降级处理
      - 根因溯源: 修复在 Step4 的职责边界内（补充数据源），不越界到 Step1
      - 全局观: 不影响 Step1→Step5 其他步骤，仅增强 Step4 Tier4 的候选页面
    """
    if not project:
        return []
    with guard("_load_rag_pages: from src.core.db_manager import get_", log=_log):
        from src.core.db_manager import get_connection, release_connection
        import re as _re_rag

        _conn = get_connection()
        try:
            _rows = _conn.execute(
                "SELECT chunk_id, text FROM rag_chunks "
                "WHERE scope_type='project' AND scope_id=%s AND type='dom' "
                "LIMIT 500",
                (project,),
            ).fetchall()
        finally:
            release_connection(_conn)

        if not _rows:
            return []

        _pages = {}
        for _row in _rows:
            _chunk_id = _row["chunk_id"] or ""
            _text = _row["text"] or ""
            # chunk_id 格式: dom:{page_path}:{idx} 或 dom:{page_path}:PAGE_ELEMENTS
            _page_path = _chunk_id
            if _page_path.startswith("dom:"):
                _page_path = _page_path[4:]
            _page_path = _re_rag.sub(r":\d+$", "", _page_path)
            _page_path = _re_rag.sub(r":PAGE_ELEMENTS$", "", _page_path)
            if not _page_path or not _page_path.startswith("/"):
                continue
            if _page_path not in _pages:
                _pages[_page_path] = ""
            # 聚合文本用于域关键词评分（限制总长度避免内存问题）
            if len(_pages[_page_path]) < 1000:
                _pages[_page_path] += "\n" + _text[:500]

        _result = []
        for _path, _text in _pages.items():
            _result.append({"path": _path, "url": _path, "text": _text[:500]})
        _log.info(
            "[_load_rag_pages] v4.4: 从 rag_chunks 提取 %d 个候选页面", len(_result)
        )
        return _result
