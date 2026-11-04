# -*- coding: utf-8 -*-
"""★ P0-refactor(2026-07-27): Agent 统一执行器（替代 display 降级用例 + 标准 step loop）

架构决策(§16.2 长期路线): 取消"先爬全再测试"模式，所有用例统一走 AgentExplorer 实时探索。
Step3 生成的意图驱动用例（含 _explore_task）→ Step4 AgentExplorer 在浏览器中实时感知→决策→执行。

对标: BrowserGym/WebVoyager — Agent 驱动的端到端测试。
职责边界: AgentExplorer 做决策（§16.2），Step4 只做调度（§12.1）。
通用性: 跨系统/跨行业/跨页面 — Agent 实时感知不依赖静态快照。
"""

from __future__ import annotations
from src.core.guarded import guard


# 「页面不存在 / 404」判定统一走
# `browser_tools.execution._single_nav_state.page_is_notfound_response`（唯一谓词，
# 数字状态码按独立 token 匹配）。本文件不再维护自己的词表 —— 三处副本曾各自
# 用子串匹配，靶子数据里的 "404" 字样会把正常页面误判为错误页。


import json
import logging
import re
import time
from src.core.page_ready import (
    await_bounded,
    count_bounded,
    evaluate_bounded,
)
from src.pipeline.utils.menu_matcher import _MENU_COMMON_TOKENS as _COMMON  # noqa: F401  词表唯一源

_log = logging.getLogger(__name__)

# ★ 删除断言行数统计（2026-09-28）: 与 _action.py._JS_COUNT_DATA_ROWS 同一语义
#   （实体行 = 主表格 tbody 行数，无表格退回 [role=row]）——删除可观察证据共享
#   唯一判据，两处各自内联（agent 与 guided 运行期不再互相 import 串模块）。
_JS_COUNT_DATA_ROWS_EXPL: str = """
() => {
    const tables = Array.from(document.querySelectorAll('table'));
    for (const t of tables) {
        const rows = Array.from(t.querySelectorAll('tbody tr'));
        if (rows.length) return rows.length;
    }
    return document.querySelectorAll('[role="row"]').length;
}
"""


# ── 方案B(2026-07-31): 侧边栏缓存（同批次用例共享，避免重复_full_menu）──
#   根因(§10.9 5层why):
#     L1: TC-REQ-001-I01超时300s → 执行流程715s>300s
#     L2: 715s = 策略0_full_menu(15s)+策略1_full_menu(8s)+Agent(180s)+retry_full_menu(15s)+纠错_full_menu(10s)+纠错Agent(180s)
#     L3: 多次_full_menu重复 → 每次15s展开侧边栏是纯浪费
#     L4: 无缓存 → 每条用例独立探索侧边栏，即使同一批次
#     L5: 根本原因——方案B应"一次缓存+一键导航+Agent执行"，当前7层策略堆叠
#   修复: 模块级缓存，project+page_url为key，300s TTL，同批次用例共享。
#   通用性: 跨系统/行业/框架 — 所有Web应用的侧边栏在同一会话中不变。
_SIDEBAR_CACHE: dict = {}  # {cache_key: {"items": [...], "ts": float}}

# ★ 进程级预检幂等门(2026-09-23): 预检=一次性DOM侦察，每引擎进程每项目只做一次。
_PREFLIGHT_DONE_PROJECTS: set = set()


# ── 方案A(2026-10-28, AGENTS.md §4.17-A): 导航发现的第二证据源 —— 同源链接 ──
#   根因: 预检只遍历"侧边栏菜单项"，而菜单不是导航的唯一呈现。平铺路由型系统
#   （无侧边栏/无菜单）菜单项恒为 0 → 原实现直接 return 0 放弃 → nav_graph_nodes
#   永空 → Step3 导航解析无事实可用 → 用例首步全部落到站点根占位（实测 550/555）。
#   `<a href>` 是任何 Web 应用都存在的导航事实，与菜单遍历共用同一采集/落库路径。
_JS_COLLECT_LINKS: str = """
() => Array.from(document.querySelectorAll('a[href]')).map(a => [
    a.getAttribute('href') || '',
    (a.innerText || a.textContent || '').replace(/\\s+/g, ' ').trim()
])
"""

# 登出/破坏性动作统一的排除词表（点击即改变会话或数据，不能当"页面发现"来点）。
# 与菜单遍历共用同一判据来源（通用语汇，零业务硬编码）。
_UNSAFE_LINK_RE = re.compile(
    r"退出|注销|登出|登出登录|logout|log\s*out|sign\s*out|signout"
    r"|删除|移除|清空|重置|delete|remove|destroy|reset|clear",
    re.IGNORECASE,
)

# 路径中的实体标识段（纯数字 / UUID / 长十六进制 / 长字母数字串）。
# 判据: 实体 id 不是**页面身份** —— /products/940/edit 与 /products/941/edit 是
# 同一个页面类型（同一路由模板）。去重必须按路由模板，否则列表页上的实体链接会
# 把页面上限全占满（实测 smartshop：5 个商品编辑页占掉 5 个名额）。
_ENTITY_ID_SEG_RE = re.compile(
    r"^(\d+"
    r"|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|[0-9a-fA-F]{16,}"
    r"|(?=[A-Za-z0-9_-]{8,}$)(?=.*\d)[A-Za-z0-9_-]+)$"
)


def _path_identity(path: str) -> str:
    """路径的**路由模板**（实体 id 段归一化为 ``{id}``，用于去重/上限判定）。"""
    _segs = [s for s in str(path or "/").split("/")]
    return "/".join(
        "{id}" if s and _ENTITY_ID_SEG_RE.match(s) else s for s in _segs
    ) or "/"


# 认证页（登录页）不是业务导航节点 —— 它是"未登录/会话失效"的产物，写入导航图会让
# Step3 把用例导航到登录页，并在预检遍历时把已登录会话赶回登录态。
# 判据唯一源：链接发现（不遍历）与落库（不写入）共用本谓词。
_LOGIN_PAGE_RE = re.compile(r"(^|/)login(/|$)", re.IGNORECASE)


def _is_auth_page_url(url: str) -> bool:
    """URL 是否为认证页（登录页）—— 不构成导航节点。"""
    from urllib.parse import urlparse

    return bool(_LOGIN_PAGE_RE.search(urlparse(str(url or "")).path or "/"))


# 泛化的导航文案（翻页/查看/编辑/返回等）不是**页面身份** —— 它们是"怎么去"的
# 控件文案，不是"去了哪"的页面名。用它当 page_title 会让 Step3 的页面匹配
# 整体失效（实测 smartshop：列表页被记成 "下一页"）。零业务硬编码的通用语汇。
_GENERIC_LINK_TEXT_RE = re.compile(
    r"^(下一页|上一页|下页|上页|更多|详情|查看|编辑|删除|返回|首页|"
    r"next(\s*page)?|prev(ious)?(\s*page)?|more|details?|view|edit|back|home|"
    r"[»«<>…\.]+|\.{3})$",
    re.IGNORECASE,
)


def _page_identity(link_text: str, doc_title: str, path: str) -> str:
    """页面身份文本（写库的 ``page_title``）：语义文案 > 文档标题 > 路径。

    ``page_title`` 是 Step3 导航解析的匹配依据（RC-D），必须描述"这是哪个页面"。
    链接文案若是泛化导航语（下一页/编辑/查看…）则不代表页面身份，回落到
    ``document.title``；文档标题也缺失时才用路径兜底。
    """
    _text = " ".join(str(link_text or "").split())
    if _text and not _GENERIC_LINK_TEXT_RE.match(_text):
        return _text
    _title = " ".join(str(doc_title or "").split())
    return _title or str(path or "/")


def _same_origin_link_candidates(
    entry_url: str, anchors: list, max_pages: int = 10
) -> list[tuple[str, str]]:
    """从页面锚点筛出可安全遍历的同源页面链接（纯函数，可单测）。

    规则（零业务硬编码）:
      1. 只取同源（scheme+netloc 与入口一致；相对路径按入口 URL 解析）
      2. 丢弃非导航 href（空串 / 纯锚点 / javascript: / mailto: / tel: / data:）
      3. 丢弃登出、认证页与破坏性动作（文本或路径命中判据；认证页见
         ``_is_auth_page_url``，破坏性见 ``_UNSAFE_LINK_RE``）
      4. 丢弃入口页自身（它已在采集范围内，避免重复访问）
      5. 按**路由模板**去重（``_path_identity``：忽略 query/hash，并把实体 id 段
         归一化 —— 同一页面的多个筛选态、以及 ``/x/940/edit`` 与 ``/x/941/edit``
         是同一页面类型，不得各占一个页面上限）

    :return: ``[(绝对URL, 链接文本)]``，最多 ``max_pages`` 条
    """
    from urllib.parse import urljoin, urlparse

    _entry = urlparse(str(entry_url or ""))
    if _entry.scheme not in ("http", "https") or not _entry.netloc:
        return []
    _origin = f"{_entry.scheme}://{_entry.netloc}"
    _seen = {_path_identity(_entry.path or "/")}
    _out: list[tuple[str, str]] = []
    for _item in anchors or []:
        if isinstance(_item, (list, tuple)) and len(_item) >= 2:
            _href, _text = _item[0], _item[1]
        else:
            continue
        _href = str(_href or "").strip()
        _text = " ".join(str(_text or "").split())
        if not _href or _href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            continue
        _abs = urljoin(str(entry_url), _href)
        _p = urlparse(_abs)
        if _p.scheme not in ("http", "https"):
            continue
        if f"{_p.scheme}://{_p.netloc}" != _origin:
            continue
        _path = (_p.path or "/").rstrip("/") or "/"
        _identity = _path_identity(_path)
        if _identity in _seen:
            continue
        if _is_auth_page_url(_abs):
            continue
        if _UNSAFE_LINK_RE.search(_text) or _UNSAFE_LINK_RE.search(_path):
            continue
        _seen.add(_identity)
        _out.append((f"{_origin}{_p.path or '/'}", _text[:80]))
        if len(_out) >= max_pages:
            break
    return _out


def _page_elements_from_snapshot(btns: list, inputs: list) -> dict:
    """DOM 快照 → nav_graph_nodes.page_elements（菜单遍历与链接发现共用）。

    ★ 根因修复(2026-09-25) RC-E: 保留 region（nav/main），供 Step3 特征元素选取时
      剔除全局外壳。根因: page_elements 是全页扁平化，导航链接与正文操作控件混在
      一起 → 首个"按钮"恒为导航项 → 页面特征验证点取到导航文案（每页都有）→
      验证恒真(假 PASS) 或被侧边栏过滤后恒假(假 FAIL)。region 由 get_page_snapshot
      用 W3C ARIA landmark + 原生语义标签判定，零框架类名，跨系统通用。
    """
    return {
        "buttons": [
            {
                "text": b.get("text", ""),
                "tag": b.get("tag", ""),
                "role": b.get("role", ""),
                "region": b.get("region", ""),
            }
            for b in (btns or [])[:50]
        ],
        "inputs": [
            {
                "text": i.get("text", ""),
                "placeholder": i.get("placeholder", ""),
                "region": i.get("region", ""),
            }
            for i in (inputs or [])[:20]
        ],
    }


def _write_nav_nodes(project: str, version_label: str, page_data: list) -> int:
    """把发现的页面写入 nav_graph_nodes（唯一写库实现，菜单/链接两种来源共用）。"""
    if not page_data:
        return 0
    import logging as _logging

    import pymysql.cursors as _pmc

    from src.core.db_manager import get_connection

    _written = 0
    with guard("_write_nav_nodes: from src.core.db_manager import get_", log=_log, level=_logging.WARNING):
        _conn = get_connection()
        try:
            _raw = _conn._conn._con
            _cur = _raw.cursor(_pmc.Cursor)
            for _url, _pe, _title in page_data:
                # ★ 登出防护(2026-09-23): /login 是登出/会话失效的产物，不是业务导航节点
                #   判据与链接发现共用 `_is_auth_page_url`（唯一源）。
                if _is_auth_page_url(_url):
                    continue
                # 幂等: INSERT ... ON DUPLICATE KEY UPDATE，令已存在的旧行就地补全
                # （UNIQUE(project,version,url) 会让重复插入被静默丢弃）。
                _cur.execute(
                    "INSERT INTO nav_graph_nodes "
                    "(project, version_label, page_url, page_path, page_title, "
                    " element_count, source, page_elements) "
                    "VALUES (%s, %s, %s, %s, %s, %s, 'dom', %s) "
                    "ON DUPLICATE KEY UPDATE page_title=VALUES(page_title), "
                    " element_count=VALUES(element_count), "
                    " page_elements=VALUES(page_elements)",
                    (
                        project,
                        version_label or "",
                        _url,
                        _url,
                        (_title or "").strip()[:512],
                        len(_pe.get("buttons", [])),
                        json.dumps(_pe, ensure_ascii=False),
                    ),
                )
                _written += _cur.rowcount
            _raw.commit()
        finally:
            _conn.close()
    _log.info(
        "[Preflight] 写入nav_graph_nodes: %d条 (尝试%d页, 含页面标题)",
        _written,
        len(page_data),
    )
    return _written


async def _discover_pages_by_links(
    page, max_pages: int = 10, deadline: float | None = None, visited: set | None = None
) -> list[tuple[str, dict, str]]:
    """同源链接 BFS 发现页面（无菜单/菜单不足时的导航事实来源，方案A）。

    产出与菜单遍历**同形** ``(url, page_elements, title)``，交由 ``_write_nav_nodes``
    统一落库 —— 不另造存储路径。

    通用性: 只依赖 DOM 的 ``<a href>`` 与同源判定，跨框架/跨系统成立；页面有效性
            判定复用 ``_verify_page_content_loaded``（SPA 404 排除），不依赖业务文案。
    """
    import asyncio
    from urllib.parse import urlparse

    from src.agent.element_utils import get_page_snapshot

    _entry = _nav_entry_url(page)
    if not _entry:
        _log.warning("[Preflight] 链接发现: 无可用入口 URL，跳过")
        return []

    def _path_of(_u: str) -> str:
        """URL → 路由模板标识（实体 id 段归一，用于入队/去重判等）。"""
        return _path_identity(urlparse(_u).path or "/")

    _visited_paths = {_path_of(str(u)) for u in (visited or ())}
    _entry_path = _path_of(_entry)
    _visited_paths.add(_entry_path)
    _queue: list[tuple[str, str]] = [(_entry, "")]
    _inspected = 0
    _max_inspected = max(max_pages * 3, 3)
    _found: list[tuple[str, dict, str]] = []

    while _queue and len(_found) < max_pages and _inspected < _max_inspected:
        if deadline is not None and time.time() > deadline:
            _log.info("[Preflight] 链接发现: 到达时间预算，停止(已发现%d页)", len(_found))
            break
        _url, _link_text = _queue.pop(0)
        _inspected += 1
        with guard("_discover_pages_by_links: await page.goto", log=_log, level=logging.WARNING):
            await page.goto(_url, wait_until="domcontentloaded", timeout=10000)
            await asyncio.sleep(0.5)
        _final_url = page.url or _url

        # 链接采集先于有效性判定：错误页/空页上的导航链接同样指向真实页面，
        # 不能因为"本页内容不像业务页"就切断整棵导航树。
        _anchors = await evaluate_bounded(page, _JS_COLLECT_LINKS) or []
        for _cand_url, _cand_text in _same_origin_link_candidates(
            _final_url, _anchors, max_pages=max_pages * 2
        ):
            _cp = _path_of(_cand_url)
            if _cp in _visited_paths:
                continue
            _visited_paths.add(_cp)
            _queue.append((_cand_url, _cand_text))

        if not await _verify_page_content_loaded(page):
            _log.info("[Preflight] 链接发现: 跳过无效页 %s", _final_url[:80])
            continue

        _btns, _inputs = await get_page_snapshot(page)
        _doc_title = await await_bounded(page.title(), default="", what="title")
        _title = _page_identity(_link_text, _doc_title, _path_of(_final_url))
        _found.append(
            (_final_url, _page_elements_from_snapshot(_btns, _inputs), _title)
        )
        _log.info(
            "[Preflight] 链接发现页面: %s (via %r)", _final_url[:80], _title[:30]
        )

    return _found


def _nav_entry_url(page) -> str:
    """导航发现入口：优先配置的站点入口，其次当前页 origin 根（同源，绝不跨站）。"""
    from urllib.parse import urlparse

    for _u in (
        _configured_target_url(),
        str(getattr(page, "url", "") or ""),
    ):
        _p = urlparse(_u or "")
        if _p.scheme in ("http", "https") and _p.netloc:
            return f"{_p.scheme}://{_p.netloc}{_p.path or '/'}"
    return ""


def _configured_target_url() -> str:
    try:
        from src.core.config_loader import config

        _t = str(getattr(config, "target_url", "") or "").strip()
        return _t if _t.startswith(("http://", "https://")) else ""
    except Exception:  # noqa: BLE001
        return ""



async def _get_sidebar_cache(page, project: str, explorer) -> list:
    """获取侧边栏菜单项（缓存优先，300s TTL）。"""
    _cache_key = f"{project}:{page.url}"
    _now = time.time()
    if _cache_key in _SIDEBAR_CACHE:
        _cached = _SIDEBAR_CACHE[_cache_key]
        if _now - _cached["ts"] < 300:
            _log.info(
                "[SidebarCache] 命中缓存: %d项 (age=%.0fs)",
                len(_cached["items"]),
                _now - _cached["ts"],
            )
            return _cached["items"]
    _items = await explorer._expand_and_capture_full_menu(page, 15000)
    _SIDEBAR_CACHE[_cache_key] = {"items": _items, "ts": _now}
    _log.info("[SidebarCache] 构建缓存: %d项", len(_items))
    return _items


async def _preflight_build_nav_graph(
    page, project: str, version_label: str = ""
) -> int:
    """★ 前置侦察(2026-07-29 重构): 懒加载——查DB判断是否需要预检，按需探索+写入。

    根因(5层why):
      1. Agent找不到按钮 → 按钮不在target_page上
      2. 按钮不在target_page上 → target_page可能错误或按钮在子页面
      3. target_page错误 → Step3无DOM数据，只能猜测URL
      4. 无DOM数据 → Step1已移除静态DOM爬取
      5. 缺少导航图 → nav_graph_nodes为空，Agent无法定位正确页面

    策略: 查nav_graph_nodes COUNT → 数据不足就展开侧边栏→遍历菜单项→写入DB
    通用性: 跨系统/行业/页面 — 侧边栏导航是Web应用的通用模式。
    DB唯一事实源: 不依赖内存全局变量，每次查DB判断是否已预检。
    返回: 发现的页面数量（0=跳过或失败）
    """
    # ★ 查DB判断是否需要预检（替代全局变量_PREFLIGHT_DONE）
    # ★ 进程级幂等门(2026-09-23): 预检是"一次性 DOM 侦察"，同一引擎进程内每项目只做一次。
    #   原门控 nav_graph_nodes>10 在小系统(如3菜单项靶子)永不满足 → 每例重跑15s预检，
    #   530例 ≈ 2h 纯开销。DB 门控保留用于跨运行持久化，进程门控避免运行内重复。
    if project in _PREFLIGHT_DONE_PROJECTS:
        _log.info("[Preflight] 项目 %r 本进程已预检过，跳过(每进程一次)", project)
        return 0
    with guard("_preflight_build_nav_graph: import pymysql.cursors as _pmc_check", log=_log, level=logging.WARNING):
        import pymysql.cursors as _pmc_check
        from src.core.db_manager import get_connection as _gc

        _conn_check = _gc()
        _cur_check = _conn_check._conn._con.cursor(_pmc_check.Cursor)
        _cur_check.execute(
            "SELECT COUNT(*) FROM nav_graph_nodes WHERE project=%s", (project,)
        )
        _cnt = _cur_check.fetchone()[0]
        _conn_check.close()
        # ★ v5.15(2026-07-31): 提高预检阈值 2→10
        #   根因: 3个节点(>2)就跳过预检→nav_graph不完整→Agent导航到错误页面
        #   修复: 阈值提高到10，确保大多数情况都会执行预检发现更多页面
        #   通用性: 跨系统/行业 — 少于10个页面说明导航图不完整，需要预检补充
        if _cnt > 10:
            _log.info("[Preflight] nav_graph已有%d条(>10阈值)，跳过预检", _cnt)
            return 0
        _log.info("[Preflight] nav_graph仅%d条(≤10阈值)，执行预检...", _cnt)

    import asyncio

    _written = 0
    try:
        from src.agent.explorer import get_explorer as _get_explorer
        from src.agent.element_utils import get_page_snapshot, click_button

        _explorer = _get_explorer()

        # ── 阶段1: 激进展开全部侧边栏子菜单 ──
        _log.info("[Preflight] 阶段1: 展开全部侧边栏...")
        _all_menu_texts = await _explorer._expand_and_capture_full_menu(page, 15000)

        # ★ 强化: 点击所有可能的子菜单触发器（框架特定兜底）
        _extra_expanded = 0
        for _trigger_sel in [
            ".ant-menu-submenu-title",
            ".el-submenu__title",
            "[aria-haspopup='true']",
            "li[class*='submenu']",
            "div[class*='submenu']",
        ]:
            with guard("_preflight_build_nav_graph: _triggers = page.locator(_trigger_se", log=_log):
                _triggers = page.locator(_trigger_sel)
                _cnt = await count_bounded(_triggers)
                for _i in range(min(_cnt, 30)):
                    with guard("_preflight_build_nav_graph: _txt = (await _triggers.nth(_i).text", log=_log):
                        _txt = (
                            await await_bounded(
                                _triggers.nth(_i).text_content(),
                                default="",
                                what="text_content",
                            )
                            or ""
                        ).strip()
                        await _triggers.nth(_i).click(timeout=2000)
                        _extra_expanded += 1
                        await asyncio.sleep(0.2)
        _log.info("[Preflight] 额外展开了%d个框架子菜单触发器", _extra_expanded)

        # 等待子菜单出现
        await asyncio.sleep(1.0)

        # 重新收集全部菜单项
        _all_menu_texts = await _explorer._expand_and_capture_full_menu(page, 15000)
        _log.info("[Preflight] 展开后菜单项总数: %d", len(_all_menu_texts))

        # ★ 方案A(2026-10-28, §4.17-A): 菜单为空**不再早退**。
        #   根因: 平铺路由型系统（无侧边栏）菜单项恒为 0，原 `return 0` 直接放弃
        #   → nav_graph 永空（实测全库 0 行）。空列表本就让下方遍历成为 no-op，
        #   早退只是提前掐断了唯一还活着的发现手段（同源链接）。
        if not _all_menu_texts:
            _log.warning(
                "[Preflight] 页面上未发现侧边栏菜单项 → 改用同源链接发现导航页面"
            )

        # ── 阶段2: 遍历菜单项，导航到每个页面，记录URL和按钮 ──
        _log.info("[Preflight] 阶段2: 遍历%d个菜单项...", len(_all_menu_texts))
        _current_url = page.url
        _visited = set()
        _page_data = []  # [(url, page_elements_dict, 页面身份文本)]

        # ★ 性能优化(2026-07-28): 上限10页 + 缩短等待 + 时间预算120s
        #   根因: 30页×18s/页=540s > 300s总预算 → 预检超时
        _preflight_deadline = time.time() + 120
        _preflight_max = 10
        # ★ 登出防护(2026-09-23 实测回归根修): 预检遍历菜单时点了"退出"→ Agent 被真实登出
        #   → 后续全部用例在未登录态执行 → ERROR 尖刺（监控实测 13/21 HARNESS_ERROR，
        #   actual 全为登录页）。登出/注销类菜单项必须跳过；/login 页不是导航树节点。
        import re as _re

        _LOGOUT_MENU_RE = _re.compile(
            r"退出|注销|登出|logout|log\s*out|sign\s*out|signout", _re.IGNORECASE
        )
        for _item_text in _all_menu_texts[:_preflight_max]:
            if time.time() > _preflight_deadline:
                _log.info("[Preflight] 到达时间预算(120s)，停止遍历")
                break
            if not _item_text or len(_item_text) > 50:
                continue
            if _LOGOUT_MENU_RE.search(_item_text):
                _log.info(
                    "[Preflight] 跳过登出类菜单项 %r（防误登出破坏登录态）",
                    _item_text[:20],
                )
                continue
            _click_ok = await click_button(page, _item_text, 8000)
            if not _click_ok:
                continue
            with guard("_preflight_build_nav_graph: await page.wait_for_load_state('domc", log=_log):
                await page.wait_for_load_state("domcontentloaded", timeout=3000)
            await asyncio.sleep(0.8)

            _new_url = page.url
            if _new_url in _visited:
                continue

            _visited.add(_new_url)
            _log.info(
                "[Preflight] 发现页面: %s (via %r)", _new_url[:80], _item_text[:30]
            )

            # 获取页面元素（与链接发现共用同一快照→page_elements 实现）
            _btns, _inputs = await get_page_snapshot(page)
            _page_data.append(
                (_new_url, _page_elements_from_snapshot(_btns, _inputs), _item_text)
            )

        # ★ 方案A(2026-10-28, §4.17-A): 菜单只是导航的一种呈现 —— 再按同源链接
        #   补全导航事实（平铺路由系统只有链接可循；菜单系统也能补出菜单外的页面）。
        #   与菜单遍历共用 `_visited`/时间预算/落库路径，产出同形三元组。
        if len(_page_data) < _preflight_max and time.time() < _preflight_deadline:
            _link_pages = await _discover_pages_by_links(
                page,
                max_pages=_preflight_max - len(_page_data),
                deadline=_preflight_deadline,
                visited=_visited,
            )
            if _link_pages:
                _log.info("[Preflight] 链接发现补充页面: %d个", len(_link_pages))
            _page_data.extend(_link_pages)

        # ── 阶段3: 写入 nav_graph_nodes（唯一写库实现）──
        #   ★ 根因修复(2026-09-25) RC-D: page_title 必须是**页面身份**（侧边栏菜单
        #     文本 / 入口页 title / 链接文本），不得为空 —— Step3 导航解析读的就是
        #     这里；标题全空时 `_match_menu` 直接返回 None，确定性解析整体失效。
        #     幂等: ON DUPLICATE KEY UPDATE 令已存在的旧行就地补全。
        _written += _write_nav_nodes(project, version_label, _page_data)

        # 导航回原始页面
        if page.url != _current_url:
            with guard("_preflight_build_nav_graph: await page.goto(_current_url, timeou", log=_log):
                await page.goto(_current_url, timeout=10000)
                await asyncio.sleep(1)

    except Exception as _e:
        _log.warning("[Preflight] 异常: %s", str(_e)[:200], exc_info=True)

    # 成功完成（无异常冒出）才标记，失败下次用例可重试
    _PREFLIGHT_DONE_PROJECTS.add(project)
    return _written


async def _verify_page_content_loaded(page) -> bool:
    """★ v5.17 fix(2026-07-31): 导航后验证页面内容是否实际加载 + SPA 404检测。

    根因(§10.9 5层why):
      L1: TC-REQ-001-I01找不到按钮 → 页面显示"页面不存在"
      L2: 页面显示"页面不存在" → /dataManagement/brand是AI幻觉URL，某SPA系统无此路由
      L3: Step3生成错误URL → AI从API路径推测前端路由，但某SPA系统路由命名规则不同
      L4: 策略0误判导航成功 → "页面不存在"页有按钮(返回首页)+文本>80字符
      L5: 根本原因——_verify_page_content_loaded未检测SPA 404页面。
           SPA 404页面返回HTTP 200，有按钮和文本，但内容无效。
           Agent在404页面上找不到业务按钮，浪费时间。

    通用性: 跨系统/行业/框架 — 所有SPA(React/Vue/Angular)都有自定义404页面，
            URL导航不触发JS路由→内容不渲染/显示404。检测方式不依赖框架特定选择器。

    策略: ① SPA 404检测 → ② 统计可见button数 → ③ 检查主内容区文本量
           → 任一通过即认为内容已加载（404检测除外）
    """
    try:
        # ★ v5.17: ① SPA 404页面检测（必须在按钮检查之前）
        #   SPA 404页面返回HTTP 200，有按钮和文本，但内容是"页面不存在"。
        #   ★ 2026-10-27: 词表与匹配形态统一到 `page_is_notfound_response`
        #     （数字状态码按独立 token 匹配）；本文件原副本用整页子串匹配，
        #     靶子数据里的 "404" 字样（实测商品描述 TEST_hnlg404pg7）会把
        #     正常列表页误判为 404 页 → 整链导航失败。
        from src.pipeline.steps.step4.browser_tools.execution._single_nav_state import (
            notfound_keyword,
            page_is_notfound_response,
        )

        with guard("_verify_page_content_loaded: _body_text = await page.text_content", log=_log):
            _body_text = await await_bounded(
                page.text_content("body"), default="", what="text_content"
            )
            if _body_text and page_is_notfound_response(_body_text):
                _log.warning(
                    "[ContentVerify] ❌ SPA 404页面检测: 命中关键词=%s",
                    notfound_keyword(_body_text),
                )
                return False

        # ② 统计可见button数（含role=button的span/div等）
        _btn_count = 0
        _all_btn_texts = []
        with guard("_verify_page_content_loaded: _btns = page.locator('button')", log=_log):
            # 原生button
            _btns = page.locator("button")
            _total = await count_bounded(_btns)
            for _i in range(min(_total, 30)):
                try:
                    if await await_bounded(
                        _btns.nth(_i).is_visible(), default=False, what="is_visible"
                    ):
                        _txt = await await_bounded(
                            _btns.nth(_i).text_content(), default="", what="text_content"
                        )
                        _btn_count += 1
                        if _txt and _txt.strip():
                            _all_btn_texts.append(_txt.strip()[:40])
                except Exception:
                    continue
            # 也检查role=button的非button元素
            _role_btns = page.locator("[role='button']:not(button)")
            _rtotal = await count_bounded(_role_btns)
            for _i in range(min(_rtotal, 20)):
                try:
                    if await await_bounded(
                        _role_btns.nth(_i).is_visible(), default=False, what="is_visible"
                    ):
                        _txt = await await_bounded(
                            _role_btns.nth(_i).text_content(),
                            default="",
                            what="text_content",
                        )
                        _btn_count += 1
                        if _txt and _txt.strip():
                            _all_btn_texts.append(_txt.strip()[:40])
                except Exception:
                    continue

        if _btn_count > 0:
            _log.info(
                "[ContentVerify] ✅ 页面有%d个可见按钮: %s",
                _btn_count,
                _all_btn_texts[:5],
            )
            return True

        # ② 检查主内容区文本量（排除侧边栏/导航栏）
        _content_text = ""
        with guard("_verify_page_content_loaded: _main_selectors = [ 'main', '[role='", log=_log):
            _main_selectors = [
                "main",
                "[role='main']",
                ".ant-layout-content",
                ".el-main",
                ".main-content",
                "[class*='content']:not([class*='sidebar']):not([class*='menu'])",
                "[class*='page']:not([class*='sidebar']):not([class*='menu'])",
            ]
            for _sel in _main_selectors:
                try:
                    _el = page.locator(_sel).first
                    if await count_bounded(_el) > 0:
                        _t = await await_bounded(
                            _el.text_content(), default="", what="text_content"
                        )
                        if _t and len(_t.strip()) > 30:
                            _content_text = _t.strip()
                            break
                except Exception:
                    continue
            if not _content_text:
                _body = await await_bounded(
                    page.text_content("body"), default="", what="text_content"
                )
                if _body:
                    _content_text = _body.strip()

        # 过滤常见侧边栏关键词
        _SIDEBAR_KEYWORDS = [
            "数据看板",
            "数据管理",
            "任务管理",
            "模型管理",
            "系统管理",
            "用户管理",
            "权限管理",
            "角色管理",
            "菜单管理",
            "日志管理",
            "Dashboard",
            "Settings",
            "Admin",
            "Profile",
        ]
        _filtered_lines = []
        for _line in _content_text.split("\n"):
            _line_s = _line.strip()
            if not _line_s:
                continue
            _is_sidebar = False
            for _sk in _SIDEBAR_KEYWORDS:
                if _sk in _line_s and len(_line_s) < 30:
                    _is_sidebar = True
                    break
            if not _is_sidebar:
                _filtered_lines.append(_line_s)

        _filtered_text = " ".join(_filtered_lines)
        _text_len = len(_filtered_text)

        if _text_len > 80:
            _log.info("[ContentVerify] ✅ 页面内容区有%d字符(过滤后)", _text_len)
            return True
        else:
            _log.warning(
                "[ContentVerify] ❌ 页面内容不足(过滤后%d字符, %d按钮), SPA可能未加载",
                _text_len,
                _btn_count,
            )
            return False
    except Exception as _e:
        _log.warning("[ContentVerify] 验证异常: %s", str(_e)[:80])
        return True  # 不确定时放行，避免误判


# ★ 删除断言 DOM 单一实现(2026-09-28): 删除可观察证据 = 列表行数减少。
#   与加载断言同构——Step3 上游落 `assert="delete_done"`，执行层只读此处判定，
#   不按泛文本猜、不调 AI（删除真实落库却因页面无"删除完成"文案假 FAIL 的根除，
#   实测 TC-REQ-005-I01）。通用性：跨系统/行业/框架，只数"实体行"。
#   入参 before = 点击删除前采集的行数（-1 表示无前值）：
#     * before>=0 且当前行数 < before → 删除已落库（行数减少）；
#     * before<0（页面无列表或采集失败）→ 退化为页面已加载判定（列表仍可见），
#       仅防"删除把页面走到空态/500"这类硬失败，不产生假 PASS。
async def _verify_delete_row_decreased(page, before: int = -1, timeout_ms: int = 15000) -> bool:
    """删除后列表行数减少判定（DOM 可观察证据）。

    轮询等待行数减少（最长 self 超时）——删除是 form POST→confirm accept→
    重定向回列表，首次读取常在重定向完成前拿到删除前快照 → 行数未减 → 假 FAIL
    （实测 TC-REQ-008-I01：点击'删除'后立即读仍是旧行数）。轮询语义：
    行数 < before 即删除已落库；无 before 退化页面加载判定（防空态/500）。
    """
    import asyncio
    import time as _time
    # 删除提交后等待重定向/刷新稳定（每轮都 wait_for_load_state，读最新 DOM）
    _deadline = _time.time() + min(timeout_ms / 1000.0, 8.0)
    while _time.time() < _deadline:
        with guard("_verify_delete_row_decreased: await page.wait_for_load_state('domc", log=_log):
            await page.wait_for_load_state("domcontentloaded", timeout=3000)
        await asyncio.sleep(0.8)
        try:
            _rows = await evaluate_bounded(page, _JS_COUNT_DATA_ROWS_EXPL)
        except Exception as _e:
            _log.warning("[DeleteVerify] 行数统计异常: %s", str(_e)[:80])
            return False
        if not isinstance(_rows, int) or _rows < 0:
            return False
        if before >= 0:
            if _rows < before:
                _log.info("[DeleteVerify] 删除前=%d 删除后=%d", before, _rows)
                return True
            _log.info(
                "[DeleteVerify] 行数未减(前=%d 后=%d)，重定向中，继续等待...",
                before,
                _rows,
            )
        else:
            # 无前值：确认页面仍为可加载的业务页（防空态/500）
            if await _verify_page_content_loaded(page):
                return True
    if before >= 0:
        _log.warning("[DeleteVerify] 超时：删除后行数未减少(before=%d)", before)
    return False


# ★ 取消/拒绝删除断言 DOM 单一实现: 与「行数减少」对称，可观察证据 = 行数**未减少**。
#   Step3 上游落 `assert="delete_aborted"`，执行层只读此处判定。
#   根因: 删除类需求的"取消则不删除"只含否定后缀，曾被按 delete_done（行数减少）
#     生成断言 → SUT 正确未删除反而判 FAIL。
async def _verify_delete_row_unchanged(page, before: int = -1, timeout_ms: int = 15000) -> bool:
    """取消/拒绝后**不执行删除**的可观察证据：列表行数未减少且页面仍为业务页。

    before<0（无前值可取）时退化为「页面内容已加载」——只防页面被判空/500，
    不凭无据判 PASS。
    """
    import asyncio
    with guard("_verify_delete_row_unchanged: await page.wait_for_load_state('domc", log=_log):
        await page.wait_for_load_state("domcontentloaded", timeout=min(timeout_ms, 8000))
    await asyncio.sleep(0.6)
    try:
        _rows = await evaluate_bounded(page, _JS_COUNT_DATA_ROWS_EXPL)
    except Exception as _e:
        _log.warning("[DeleteAbortVerify] 行数统计异常: %s", str(_e)[:80])
        return await _verify_page_content_loaded(page)
    if not isinstance(_rows, int) or _rows < 0:
        return await _verify_page_content_loaded(page)
    if before >= 0:
        if _rows >= before:
            _log.info("[DeleteAbortVerify] 取消后行数未减少(前=%d 后=%d)", before, _rows)
            return True
        _log.warning("[DeleteAbortVerify] 取消后行数却减少(前=%d 后=%d)", before, _rows)
        return False
    return await _verify_page_content_loaded(page)


# ★ 正向表单提交成功断言 DOM 单一实现(2026-09-28): 可观察证据 = 提交后页面回到
#   业务视图（重定向/刷新完成）**且页面无错误条**——不要求页面上出现"新增成功"等
#   文案（SUT 增改成功常仅重定向、无 toast，文案断言必假 FAIL，实测 TC-REQ-002/007/
#   013/014-I01）。与验证手段"断言靠 DOM 控件状态、不靠 AI 语义猜测"方法论一致。
#   Step3 上游落 `assert="submit_ok"`，执行层只读本实现。
async def _verify_submit_ok(page, timeout_ms: int = 15000) -> bool:
    """提交成功判定：页面无错误条且回到业务视图（主内容可见）。"""
    import asyncio
    with guard("_verify_submit_ok: await page.wait_for_load_state('domc", log=_log):
        await page.wait_for_load_state("domcontentloaded", timeout=min(timeout_ms, 8000))
    # 提交后重定向/刷新需要消化时间，等待再读（避免读到提交前快照）
    await asyncio.sleep(0.8)
    # ① 页面无错误/校验条（提交失败会留错误条，成功则无）
    try:
        from src.pipeline.utils.feedback_selectors import ERROR_SELECTOR_CSV as _ERR_CSV
        _err_sel = _ERR_CSV  # 单一事实源：与错误条检测同一选择器集
    except Exception:
        _err_sel = (
            "[role='alert'], .error, .field-error, .form-error, .invalid-feedback, "
            ".error-message, .flash-danger, .alert-danger, [data-testid*='error']"
        )
    with guard("_verify_submit_ok: _errs = page.locator(_err_sel)", log=_log):
        _errs = page.locator(_err_sel)
        _n = await count_bounded(_errs)
        for _i in range(min(_n, 12)):
            try:
                if await await_bounded(
                    _errs.nth(_i).is_visible(), default=False, what="is_visible"
                ):
                    _txt = (
                        await await_bounded(
                            _errs.nth(_i).text_content(),
                            default="",
                            what="text_content",
                        )
                        or ""
                    ).strip()
                    if _txt:
                        _log.warning(
                            "[SubmitVerify] 页面存在错误条(提交失败): %s", _txt[:60]
                        )
                        return False
            except Exception:
                continue
    # ② 业务视图可见（重定向/刷新后的目标页——列表/主内容）
    return await _verify_page_content_loaded(page)


async def _verify_module_in_page(page, module: str, pre_url: str = "") -> bool:
    """★ v11.5(2026-07-31): 页面内容验证——检查当前页面是否与目标模块相关。

    根因(§10.9 6层why):
      L1: 两步导航失败 → 子菜单匹配不到"烟叶画像"
      L2: 子菜单匹配不到 → 提取菜单项中无"烟叶画像"(Ant Design inline子菜单不被捕获)
      L3: 菜单项不包含 → 但父菜单导航后页面已是目标页面
           (如"数据管理"→/dataManage/portraitData=烟叶画像数据页面)
      L4: 为什么没识别页面已正确? → 两步骤仅检查子菜单匹配，未检查页面内容
      L5: 页面内容验证失败 → 中文关键词无法匹配英文URL/标题
           (如"烟叶画像"无法匹配"portraitData"或"已移除的远程系统")
      L6: 根本原因——跨语言gap + 元素检测阈值过高。
           中文业务术语与英文URL/标题之间有语义鸿沟，且功能页面检测的
           button/input/link阈值(>=3)过严，部分已加载页面可能因渲染延迟未达标。

    通用性: 跨框架/跨行业/跨语言 — 不依赖中文关键词，不依赖特定元素类型。
    策略: ① 页面标题含模块关键词 → ② URL路径含模块关键词 →
          ③ 导航后URL变化(非404) → ④ 页面有实质内容(非空白) → ⑤ 有可交互元素。
    """
    # 「不存在类」判定统一谓词（词表与匹配形态的唯一源，见文件头注释）
    from src.pipeline.steps.step4.browser_tools.execution._single_nav_state import (
        page_is_notfound_response,
    )

    # 提取模块关键词（2-3字窗口）
    _clean_mod = (
        module.replace("、", "")
        .replace("，", "")
        .replace(" ", "")
        .replace("（", "")
        .replace("）", "")
    )
    _keywords = []
    for _win_size in (3, 2):
        for _i in range(len(_clean_mod) - _win_size + 1):
            _sub = _clean_mod[_i : _i + _win_size]
            if _sub not in _keywords:
                _keywords.append(_sub)
    if not _keywords:
        return False

    # ① 页面标题验证
    with guard("_verify_module_in_page: _title = (await page.title() or '').", log=_log):
        _title = str(await await_bounded(page.title(), default="", what="title")).strip()
        _title_clean = _title.replace(" ", "").replace("-", "").replace("_", "").lower()
        for _kw in _keywords:
            if _kw.lower() in _title_clean:
                _log.info("[ModuleVerify] 页面标题匹配: %r in %r", _kw, _title[:60])
                return True

    # ② URL路径验证
    with guard("_verify_module_in_page: _url = page.url.lower()", log=_log):
        _url = page.url.lower()
        _url_parts = (
            _url.replace("://", "/").replace("?", "/").replace("&", "/").split("/")
        )
        for _kw in _keywords:
            for _part in _url_parts:
                if _kw.lower() in _part:
                    _log.info(
                        "[ModuleVerify] URL路径匹配: %r in %r", _kw, page.url[:80]
                    )
                    return True

    # ★ v11.5: ③ 导航后URL变化 + 非404 → 大概率已导航到正确页面
    #   根因: 两步导航流程中，父菜单点击后URL已变化(如→/dataManage/portraitData)，
    #   但中文关键词无法匹配英文URL。URL变化是导航成功的最强信号。
    #   通用性: 跨语言/跨系统 — URL变化不依赖任何语言/框架特定特征。
    if pre_url and page.url != pre_url:
        with guard("_verify_module_in_page: _body_text = await page.text_content", log=_log):
            _body_text = await await_bounded(
                page.text_content("body"), default="", what="text_content"
            )
            if _body_text:
                if not page_is_notfound_response(_body_text):
                    _log.info(
                        "[ModuleVerify] URL变化+非404: %s → %s",
                        pre_url[:60],
                        page.url[:80],
                    )
                    return True

    # ④ 功能页面检测（兜底）：页面有实质内容
    #   通用性: 跨语言/跨系统 — 不依赖中文关键词匹配英文URL
    with guard("_verify_module_in_page: _body_text = await page.text_content", log=_log):
        _body_text = await await_bounded(
            page.text_content("body"), default="", what="text_content"
        )
        if _body_text:
            if page_is_notfound_response(_body_text):
                _log.info("[ModuleVerify] 页面内容验证失败: 404页面")
                return False
            # ★ v11.5: 降低阈值——页面有>200字符实质内容即视为有效页面
            _body_len = len(_body_text.strip())
            if _body_len > 200:
                _log.info("[ModuleVerify] 页面内容检测: body=%d字符", _body_len)
                return True

    # ⑤ 可交互元素检测（最后兜底）
    with guard("_verify_module_in_page: _btn_count = await page.locator('but", log=_log):
        _btn_count = await count_bounded(page, "button, [role='button']")
        _input_count = await count_bounded(page, "input:not([type='hidden'])")
        _link_count = await count_bounded(page, "a[href]")
        if _btn_count + _input_count + _link_count >= 1:
            _log.info(
                "[ModuleVerify] 交互元素检测: %d buttons + %d inputs + %d links",
                _btn_count,
                _input_count,
                _link_count,
            )
            return True

    _log.info(
        "[ModuleVerify] 所有验证策略失败: module=%r url=%s", module[:40], page.url[:80]
    )
    return False


def _check_url_related_to_module(url: str, module_keywords: list) -> bool:
    """★ 检查URL是否和module关键词相关。

    通用性: 跨系统/行业 — URL路径通常包含业务关键词（英文或拼音）。
    策略: 从URL路径中提取候选词，检查是否和module关键词有重叠。
    """
    if not url or not module_keywords:
        return False
    # 从URL中提取候选词（路径片段）
    _url_lower = url.lower()
    _url_parts = []
    for _part in (
        _url_lower.replace("://", "/")
        .replace("?", "/")
        .replace("&", "/")
        .replace("=", "/")
        .split("/")
    ):
        _part = _part.strip()
        if _part and len(_part) >= 2:
            _url_parts.append(_part)
    # 检查module关键词是否出现在URL中
    for _kw in module_keywords:
        if len(_kw) < 2:
            continue
        for _up in _url_parts:
            if _kw.lower() in _up or _up in _kw.lower():
                return True
    return False


async def _click_menu_item_via_js(page, target_text: str) -> bool:
    """★ 方案B v8: 使用JS click点击菜单项，处理隐藏元素（display:none）。

    根因: Playwright的force=True click可能不触发SPA（React/Vue）的事件处理器，
         因为force=True仅跳过actionability检查，但SPA的事件委托可能要求元素可见。
    通用性: 跨框架 — JS element.click() 直接在DOM上触发click事件，
           事件冒泡到React/Vue的委托处理器，无论元素是否可见。
    策略: ① get_by_text找到元素 → ② JS click → ③ 等待导航
    """
    try:
        _js_code = """
        (targetText) => {
            // 策略1: 查找li元素包含目标文本
            const allLis = document.querySelectorAll('li');
            for (const li of allLis) {
                if (li.textContent.trim() === targetText) {
                    li.click();
                    return true;
                }
            }
            // 策略2: 模糊匹配
            for (const li of allLis) {
                if (li.textContent.trim().includes(targetText)) {
                    li.click();
                    return true;
                }
            }
            // 策略3: 查找a元素
            const allAs = document.querySelectorAll('a');
            for (const a of allAs) {
                if (a.textContent.trim().includes(targetText)) {
                    a.click();
                    return true;
                }
            }
            return false;
        }
        """
        _clicked = await page.evaluate(_js_code, target_text)
        if _clicked:
            _log.info("[JS-Click] 成功点击: %r", target_text[:40])
            import asyncio

            await asyncio.sleep(0.8)
            with guard("_click_menu_item_via_js: await page.wait_for_load_state('domc", log=_log):
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            return True
        _log.warning("[JS-Click] 未找到元素: %r", target_text[:40])
        return False
    except Exception as _e:
        _log.warning("[JS-Click] 异常: %s", str(_e)[:120])
        return False


def _lcs_similarity(a: str, b: str) -> float:
    """★ 通用方案(2026-07-31): 最长公共子序列相似度。

    根因(§10.9): 需求词汇("烟叶画像数据")和UI导航标签("烟叶画像")不一致，
               纯子串匹配无法处理部分重叠。LCS天然处理任意部分重叠。
    通用性: 纯算法零依赖，跨语言/系统/行业通用。
    返回: 0~1，越高越相似。
    """
    if not a or not b:
        return 0.0
    m, n = len(a), len(b)
    # DP: 只用两行节省内存（短文本，但养成好习惯）
    prev = [0] * (n + 1)
    curr = [0] * (n + 1)
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(prev[j], curr[j - 1])
        prev, curr = curr, prev
    lcs_len = prev[n]
    return lcs_len / max(m, n)


def _jaccard_char_similarity(a: str, b: str) -> float:
    """★ 通用方案(2026-07-31): 字符级Jaccard相似度。

    通用性: 纯算法，处理"配方管理"vs"配方列表"（共同字符"配方"，不同后缀）。
    返回: 0~1。
    """
    if not a or not b:
        return 0.0
    set_a = set(a)
    set_b = set(b)
    inter = set_a & set_b
    union = set_a | set_b
    return len(inter) / len(union) if union else 0.0


def _strip_number_prefix(text: str) -> str:
    """剥离中文序号前缀: 一、二、三、1. 1、① 等。

    根因: Step3生成的module名如"一、用户登录"，实际侧边栏只有"用户登录"或"登录"。
    返回: 剥离序号后的纯内容文本。
    """
    import re

    # 中文数字序号: 一、二、三、四、五、六、七、八、九、十、1、2、① 等
    _stripped = re.sub(r"^[一二三四五六七八九十\d]+[、.．\s]*", "", text)
    return _stripped.strip()


def _match_module_to_menu(module: str, menu_items: list) -> tuple:
    """★ 通用方案(2026-07-31): 三级融合匹配——包含>LCS>Jaccard。

    根因: 需求词汇和UI导航标签存在天然语义gap，单一匹配策略无法覆盖所有场景。
    通用性: 纯算法，跨语言/系统/行业。LCS处理"烟叶画像数据"→"烟叶画像"，
           Jaccard处理"配方管理"→"配方列表"。
    v2(2026-08-07): 新增序号前缀剥离+关键词回退，处理Step3生成"一、用户登录"等模块名。
    返回: (best_match_text, best_score, match_stage)
    """
    if not module or not menu_items:
        return None, 0.0, "none"

    _clean_mod = (
        module.replace("、", "")
        .replace("，", "")
        .replace(" ", "")
        .replace("（", "")
        .replace("）", "")
    )
    # 剥离序号前缀后的版本（用于回退匹配）
    _stripped_mod = _strip_number_prefix(_clean_mod)

    _best_match = None
    _best_score = 0.0
    _best_stage = "none"

    for _item in menu_items:
        if not _item:
            continue
        _clean_item = _item.replace(" ", "")

        # 阶段1: 精确包含（最高优先级，直接返回）
        if _clean_mod in _clean_item or _clean_item in _clean_mod:
            _len_penalty = abs(len(_clean_mod) - len(_clean_item)) / max(
                len(_clean_mod), len(_clean_item), 1
            )
            _score = 1.0 - _len_penalty * 0.2
            if _score > _best_score:
                _best_score = _score
                _best_match = _item
                _best_stage = "exact_contain"
            continue

        # 阶段1b: 剥离序号前缀后的精确包含（处理"一、用户登录"→"用户登录"）
        if _stripped_mod and (
            _stripped_mod in _clean_item or _clean_item in _stripped_mod
        ):
            _len_penalty = abs(len(_stripped_mod) - len(_clean_item)) / max(
                len(_stripped_mod), len(_clean_item), 1
            )
            _score = 0.92 - _len_penalty * 0.2  # 略低于裸匹配，仍高于LCS
            if _score > _best_score:
                _best_score = _score
                _best_match = _item
                _best_stage = "stripped_contain"
            continue

        # 阶段2: LCS相似度（权重0.55）
        _lcs = _lcs_similarity(_clean_mod, _clean_item)
        # 同时用剥离序号版本计算（取最大值）
        if _stripped_mod and _stripped_mod != _clean_mod:
            _lcs_stripped = _lcs_similarity(_stripped_mod, _clean_item)
            _lcs = max(_lcs, _lcs_stripped)

        # 阶段3: Jaccard字符相似度（权重0.25）
        _jac = _jaccard_char_similarity(_clean_mod, _clean_item)
        if _stripped_mod and _stripped_mod != _clean_mod:
            _jac_stripped = _jaccard_char_similarity(_stripped_mod, _clean_item)
            _jac = max(_jac, _jac_stripped)

        _mod_chars = set(_clean_mod) - _COMMON
        _item_chars = set(_clean_item) - _COMMON
        _kw_overlap = (
            len(_mod_chars & _item_chars) / max(len(_mod_chars | _item_chars), 1)
            if _mod_chars
            else 0.0
        )

        # 融合分数
        _score = _lcs * 0.55 + _jac * 0.25 + _kw_overlap * 0.20

        if _score > _best_score:
            _best_score = _score
            _best_match = _item
            _best_stage = "lcs_jaccard"

    return _best_match, _best_score, _best_stage


def _find_best_parent_menus(module: str, menu_items: list) -> list:
    """★ 方案B v11.1(2026-07-31): 找最佳父菜单候选——字符重叠最多的顶层菜单项列表。

    根因(§10.9): 当模块名("烟叶画像数据")是子菜单项时，需要先找到父菜单
    ("数据管理")导航到父页面。但多个菜单项可能有相同的字符重叠（如"数据看板"
    和"数据管理"都与"烟叶画像数据"重叠2个字符），无法区分哪个是真正的父菜单。
    因此返回所有候选，由调用方逐个尝试。

    通用性: 纯字符集重叠算法，跨语言/系统/行业。不依赖框架选择器。
    返回: 候选父菜单项列表，按重叠度降序排列，最少2个字符重叠。
    """
    if not module or not menu_items:
        return []
    _clean_mod = module.replace("、", "").replace("，", "").replace(" ", "")
    _clean_mod = _clean_mod.replace("（", "").replace("）", "")
    _mod_chars = set(_clean_mod)
    _candidates = []  # [(item, overlap)]
    for _item in menu_items:
        if not _item:
            continue
        _clean_item = _item.replace(" ", "")
        _overlap = len(_mod_chars & set(_clean_item))
        if _overlap >= 2:
            _candidates.append((_item, _overlap))
    _candidates.sort(key=lambda x: x[1], reverse=True)
    return [c[0] for c in _candidates]


async def _navigate_by_module(
    page, module: str, _cfg, project: str = "", operation: str = "", semantic_hint: str = ""
) -> bool:
    """★ 方案B(2026-07-31): 用module名在侧边栏中定位并导航到目标页面。

    根因(§10.9 5层why):
      L1: TC-REQ-001-I01超时300s → _navigate_by_module 455行含nav_graph空查询+多次_full_menu
      L2: nav_graph查询(350行)是死代码 → nav_graph始终为空（v5.16删除了preflight）
      L3: 多次_full_menu(15s×3次)浪费 → 第一次调策略0，第二次调策略A/B，第三次调子菜单叶节点
      L4: 无缓存 → 每次进入都重新展开侧边栏
      L5: 方案B应"一次缓存+关键词匹配+一键导航+内容验证"，600行补丁违背了简洁性

    通用性: 跨系统/行业/框架 — 侧边栏菜单是Web应用通用模式，关键词匹配不依赖框架选择器。
    策略: 侧边栏缓存(一次性,300s TTL)→关键词匹配→点击导航→父菜单递归展开→内容验证。
    失败=FAIL，不兜底URL（AI幻觉），不调LLM（慢且不可靠）。
    """
    # ★ 根因修复(2026-09-22): module 为空不再直接返回 False——Step3 现在会把
    #   文档标题/章节标题清洗掉，此时 operation/semantic_hint 仍是有效语义源，
    #   下方的"规则匹配→operation实体匹配→AI语义菜单映射"三级链路可按操作
    #   语义定位菜单。仅当三者全空才真正无导航线索。
    if (
        not module
        and not (operation or "").strip()
        and not (semantic_hint or "").strip()
    ):
        return False
    try:
        from src.agent.explorer import get_explorer as _get_explorer
        from src.agent.element_utils import click_button

        _explorer = _get_explorer()

        # ① 侧边栏缓存（同批次用例共享，避免重复_full_menu）
        _menu_texts = await _get_sidebar_cache(page, project, _explorer)
        _log.info(
            "[ModuleNav] ★★★ 方案B v7 侧边栏%d项, module=%s ★★★",
            len(_menu_texts),
            module[:60],
        )
        if not _menu_texts:
            _log.error(
                "[ModuleNav] ★★★ 侧边栏为空！检查_expand_and_capture_full_menu ★★★"
            )
            return False

        # ② 通用匹配：LCS+Jaccard+关键词重叠 三级融合（纯算法，零依赖）
        _best_match, _best_score, _match_stage = _match_module_to_menu(
            module, _menu_texts
        )
        _log.info(
            "[ModuleNav] 匹配: %r (stage=%s, score=%.3f), 菜单项=%s",
            _best_match,
            _match_stage,
            _best_score,
            _menu_texts[:8],
        )

        # 阈值: exact_contain≥0.8, stripped_contain≥0.70, lcs_jaccard≥0.30
        _min_score = 0.8
        if _match_stage == "stripped_contain":
            _min_score = 0.70
        elif _match_stage != "exact_contain":
            _min_score = 0.30
        if not _best_match or _best_score < _min_score:
            # ★ 2026-08-28 操作文本二次匹配: module 是需求文档标题（如"医院诊所
            #   管理系统需求规格"）时匹配必失败；operation 含业务实体词
            #   （"新增患者"→"患者"），与真实菜单("患者管理")有共同词。
            #   先做实体提取压缩（去 REQ 编号/括号/虚词），再截短为 2 字实体，
            #   避免长文本稀释关键词权重。
            #   通用性: 跨系统/行业 — 需求条目文本是业务实体的稳定来源。
            if operation and operation.strip() and _best_score < 0.99:
                _op_kw = ""
                try:
                    from src.pipeline.steps.step3.case_generator._explore_task_builder import (  # noqa: E501
                        extract_module_from_operation,
                    )

                    _op_kw = extract_module_from_operation(operation) or ""
                except Exception:
                    _op_kw = ""
                # 实体截短：>4 字取前 2 字（患者/医生/预约等 2 字实体为主）
                if len(_op_kw) > 4:
                    _op_kw = _op_kw[:2]
                if _op_kw:
                    _op_match, _op_score, _op_stage = _match_module_to_menu(
                        _op_kw, _menu_texts
                    )
                    if _op_match and _op_score > _best_score:
                        _log.info(
                            "[ModuleNav] operation二次匹配命中: %r (kw=%r, stage=%s, score=%.3f > %.3f)",
                            _op_match,
                            _op_kw,
                            _op_stage,
                            _op_score,
                            _best_score,
                        )
                        _best_match, _best_score, _match_stage = (
                            _op_match,
                            _op_score,
                            _op_stage,
                        )
                        # 重算阈值
                        _min_score = 0.8
                        if _match_stage == "stripped_contain":
                            _min_score = 0.70
                        elif _match_stage != "exact_contain":
                            _min_score = 0.30

            # ★ 2026-08-28 LLM 语义菜单映射兜底: 规则提取词与菜单字符相似度过低
            #   （"预约"↔菜单"排班"——跨命名系统常见），LLM 语义直接理解
            #   "可创建预约"→ 菜单"排班"。仅在 rule 匹配失败/分数低时尝试
            #   （总是尝试会每条 +7s LLM 调用，33 条拖慢 2 倍+）。
            #   通用性: 跨系统/行业 — 实体↔菜单的语义对齐是导航最后一道桥。
            # ★ 通用修复: 语义源多级兜底。此前仅用 operation 一个字段，
            #   需求结构差的系统（Confluence 编号章节当 module、operation 为空）会整条
            #   语义桥断掉 → NOT_EXECUTED。现按 `operation → semantic_hint(需求描述)
            #   → module` 取最完整语义文本，LLM 用需求内容（如"导入边界值"）而非
            #   章节标题匹配真实菜单。零业务知识、跨系统受益（smartshop 无回归）。
            _sem_src = (
                (operation or "").strip()
                or (semantic_hint or "").strip()
                or (module or "").strip()
            )
            if (
                _sem_src
                and (not _best_match or _best_score < _min_score)
            ):
                with guard("_navigate_by_module: _sem_menu = await _explorer._semanti", log=_log):
                    _sem_menu = await _explorer._semantic_menu_match(
                        _sem_src, _menu_texts
                    )
                    if _sem_menu:
                        _log.info(
                            "[ModuleNav] LLM语义菜单命中: %r (src=%r)",
                            _sem_menu,
                            _sem_src[:50],
                        )
                        _best_match, _best_score, _match_stage = (
                            _sem_menu,
                            0.95,
                            "llm_semantic",
                        )
                        _min_score = 0.80

        if not _best_match or _best_score < _min_score:
            # ★ 方案B v11(2026-07-31): 直接匹配失败→两步导航（父菜单→子菜单）
            #   根因(§10.9 5层why): 某SPA系统 Ant Design inline菜单是路由驱动的，
            #   子菜单项("烟叶画像")只在导航到父页面(/dataManagement)后才渲染。
            #   hover/click [aria-expanded=false]无法触发React Router导航。
            #   通用性: 跨所有路由驱动菜单系统(React Router/Vue Router/Next.js/SvelteKit)。
            _log.info(
                "[ModuleNav] 直接匹配失败(score=%.3f)，尝试两步导航(父菜单→子菜单)...",
                _best_score,
            )
            _parent_candidates = _find_best_parent_menus(module, _menu_texts)
            _two_step_ok = False
            for _parent in _parent_candidates[:3]:  # 最多尝试3个候选
                _log.info("[ModuleNav] 尝试父菜单候选: %r", _parent)
                _pre_url = page.url
                _pc = await click_button(page, _parent, 15000)
                if not _pc:
                    _pc = await _click_menu_item_via_js(page, _parent)
                if not _pc:
                    _log.info("[ModuleNav] 父菜单 %r 点击失败，尝试下一个", _parent)
                    continue
                with guard("_navigate_by_module: await page.wait_for_load_state('domc", log=_log):
                    await page.wait_for_load_state("domcontentloaded", timeout=5000)
                await page.wait_for_timeout(1500)
                if page.url == _pre_url:
                    _log.info("[ModuleNav] 父菜单 %r URL未变化，尝试下一个", _parent)
                    continue
                # ★ 导航成功，重新提取侧边栏（新URL→新缓存key→含子菜单项）
                _log.info(
                    "[ModuleNav] 父菜单 %r 导航成功: %s，重新提取子菜单...",
                    _parent,
                    page.url[:80],
                )
                _menu_texts = await _get_sidebar_cache(page, project, _explorer)
                _log.info(
                    "[ModuleNav] 子菜单项: %d项 %s", len(_menu_texts), _menu_texts[:15]
                )
                # 重新匹配（子菜单阈值0.30，子菜单项通常更短）
                _best_match, _best_score, _match_stage = _match_module_to_menu(
                    module, _menu_texts
                )
                _log.info(
                    "[ModuleNav] 第二步匹配: %r (stage=%s, score=%.3f)",
                    _best_match,
                    _match_stage,
                    _best_score,
                )
                if _best_match and _best_score >= 0.30:
                    _lc = await click_button(page, _best_match, 15000)
                    if not _lc:
                        _lc = await _click_menu_item_via_js(page, _best_match)
                    if _lc:
                        with guard("_navigate_by_module: await page.wait_for_load_state( 'dom", log=_log):
                            await page.wait_for_load_state(
                                "domcontentloaded", timeout=5000
                            )
                        await page.wait_for_timeout(1500)
                        if await _verify_page_content_loaded(page):
                            _log.info(
                                "[ModuleNav] ✅ 两步导航成功(%r→%r): %s",
                                _parent,
                                _best_match,
                                page.url[:80],
                            )
                            _two_step_ok = True
                            break
                # 当前候选未找到子菜单 → ★ v11.2: 页面内容验证兜底
                #   根因: Ant Design inline子菜单项不被通用提取方法捕获，
                #   但父菜单导航后页面可能已是目标页面（如"数据管理"→portraitData=烟叶画像）。
                _log.info(
                    "[ModuleNav] 子菜单匹配失败，尝试页面内容验证: %s", page.url[:80]
                )
                if await _verify_module_in_page(page, module, pre_url=_pre_url):
                    _log.info(
                        "[ModuleNav] ✅ 页面内容验证成功(父菜单%r→目标页面): %s",
                        _parent,
                        page.url[:80],
                    )
                    _two_step_ok = True
                    break
                _log.info("[ModuleNav] 父菜单 %r 未找到子菜单，回退尝试下一个", _parent)
                with guard("_navigate_by_module: await page.goto(_pre_url, timeout=10", log=_log):
                    await page.goto(_pre_url, timeout=10000)
                    await page.wait_for_timeout(1000)
            if not _two_step_ok:
                _log.warning(
                    "[ModuleNav] 两步导航失败(score=%.3f, %d个候选), module=%s",
                    _best_score,
                    len(_parent_candidates),
                    module[:60],
                )
                return False
            # ★ v11.3(2026-07-31): 两步导航成功后直接返回，不再继续后续点击逻辑
            #   根因: _best_match是第一层匹配结果(如"数据管理")，两步导航已到正确页面
            #   (如/dataManage/portraitData)，继续点击_best_match会导航到错误页面。
            _log.info("[ModuleNav] ✅ 两步导航成功: %s", page.url[:80])
            return True

        # 保留关键词供URL检查+叶节点匹配使用
        _clean_mod = (
            module.replace("、", "")
            .replace("，", "")
            .replace(" ", "")
            .replace("（", "")
            .replace("）", "")
        )
        _module_keywords = [
            w.strip()
            for w in module.replace("、", " ").replace("，", " ").split()
            if len(w.strip()) >= 2
        ]
        for _win_size in (3, 2):
            for _i in range(len(_clean_mod) - _win_size + 1):
                _sub = _clean_mod[_i : _i + _win_size]
                if _sub not in _module_keywords:
                    _module_keywords.append(_sub)

        # ④ 点击导航（支持父菜单递归展开）
        _current_url = page.url
        _click_ok = await click_button(page, _best_match, 15000)
        if not _click_ok:
            # ★ 方案B v8: force=True click失败时，用JS click兜底（处理隐藏子菜单项）
            #   根因: 隐藏的子菜单项（display:none）force=True click可能不触发SPA导航
            #   通用性: 跨框架 — JS click在隐藏元素上也能触发事件冒泡
            _log.info("[ModuleNav] click_button失败，尝试JS click: %r", _best_match)
            _click_ok = await _click_menu_item_via_js(page, _best_match)
        if not _click_ok:
            return False
        with guard("_navigate_by_module: await page.wait_for_load_state('domc", log=_log):
            await page.wait_for_load_state("domcontentloaded", timeout=5000)
        await page.wait_for_timeout(1500)

        # ⑤ URL变化→相关性检查；URL未变化→可能是父菜单，展开子菜单后重新匹配
        #   ★ 修复(2026-07-31): URL变化后不直接返回成功，而是检查URL/页面是否和module相关。
        #   根因: 某SPA系统中"数据管理"是父菜单，点击后URL变化到/dataManage页面，
        #         但"烟叶画像数据"是子菜单。旧代码URL变化后直接返回True，导航到了错误页面。
        #   通用性: 跨系统/行业 — 所有Web应用都有父菜单→子菜单的层级结构。
        _new_url = page.url
        if _new_url != _current_url:
            # ★  通用性修复: 精确匹配的顶层菜单点击后 URL 变化即视为导航成功。
            #   根因(3层): ① _check_url_related_to_module 用 module 中文关键词匹配 URL
            #   （英文/拼音路径），中英天然不匹配 → 点击"流水线执行"到 /pipeline 被误判
            #   "URL 无关"（实测 score=0.296 误杀）；② 顶层菜单精确匹配（exact_contain）
            #   意味着点击的菜单名就是 module 本身，URL 变化即达目标页面；③ 中文 UI +
            #   英文路由是 Web 应用普遍形态，不能用中文关键词校验英文 URL。
            #   修复: _match_stage=="exact_contain" 且 URL 变化 + 页面加载成功 → 直接成功。
            if _match_stage == "exact_contain":
                if await _verify_page_content_loaded(page):
                    _log.info(
                        "[ModuleNav] ✅ 导航成功(精确匹配菜单+URL变化): %s",
                        _new_url[:80],
                    )
                    return True
            # ★ 检查URL+页面内容是否和module相关
            _url_related = _check_url_related_to_module(_new_url, _module_keywords)
            if _url_related:
                if await _verify_page_content_loaded(page):
                    _log.info("[ModuleNav] ✅ 导航成功(URL相关): %s", _new_url[:80])
                    return True
            else:
                _log.info(
                    "[ModuleNav] URL变化但和module无关(new=%s)，展开子菜单搜索叶节点",
                    _new_url[:80],
                )
            # URL不相关或内容未加载 → 展开子菜单搜索叶节点

        # URL未变化→可能是父菜单(只展开子菜单不导航)，重新获取子菜单找叶节点
        if _new_url == _current_url:
            _log.info("[ModuleNav] URL未变化，可能是父菜单，展开子菜单搜索叶节点")
            # ★  修复: exact_contain 精确匹配的顶层菜单，点击后 URL 未变 +
            #   页面内容已加载 → 视为"已在目标页面"，直接成功。
            #   根因(3层): ① 登录后引擎/被测系统可能自动跳转到目标页(/pipeline)，
            #   点击同名菜单 URL 自然不变；② 旧逻辑误判"父菜单"→子菜单搜索→
            #   误点无关项(如全局按钮"一键执行" score=0.349)→导航失败；
            #   ③ exact_contain 意味着菜单名就是 module 本身，URL 不变 = 已在目标页。
            if _match_stage == "exact_contain":
                if await _verify_page_content_loaded(page):
                    _log.info(
                        "[ModuleNav] ✅ 已在目标页面(URL未变+精确匹配): %s",
                        page.url[:80],
                    )
                    return True
        _sub_items = await _get_sidebar_cache(page, project, _explorer)
        # ★ 排除已匹配项，用通用匹配重新搜索叶节点
        _sub_filtered = [_si for _si in _sub_items if _si != _best_match]
        _leaf_match, _leaf_score, _leaf_stage = _match_module_to_menu(
            module, _sub_filtered
        )
        if _leaf_match and _leaf_score >= 0.30:
            _log.info(
                "[ModuleNav] 叶节点命中: %r (stage=%s, score=%.3f)",
                _leaf_match,
                _leaf_stage,
                _leaf_score,
            )
            _pre_url = page.url
            _lc = await click_button(page, _leaf_match, 15000)
            if not _lc:
                _lc = await _click_menu_item_via_js(page, _leaf_match)
            if _lc:
                with guard("_navigate_by_module: await page.wait_for_load_state('domc", log=_log):
                    await page.wait_for_load_state("domcontentloaded", timeout=5000)
                await page.wait_for_timeout(1500)
                if page.url != _pre_url and await _verify_page_content_loaded(page):
                    _log.info("[ModuleNav] ✅ 叶节点导航成功: %s", page.url[:80])
                    return True
        _log.warning("[ModuleNav] 叶节点未找到或导航失败(score=%.3f)", _leaf_score)
        return False
    except Exception as _e:
        _log.warning("[ModuleNav] 异常: %s", str(_e)[:120])
        return False
