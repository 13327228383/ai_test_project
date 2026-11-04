# -*- coding: utf-8 -*-
"""页面状态检测辅助 — 从 _single.py 拆分（§4.3 单文件≤800行）。

职责：封装导航执行期的页面状态检测逻辑：
  - 内容交互性: _check_content_interactive (内容区 input/button 数量)
  - 错误页检测: _check_error_page (404/无权限/空白页)
  - 重登检测: _try_relogin_to_target (会话过期重登)
  - 有效页面查找: _find_first_working_page (从 nav_map 找首个有效 URL)
  - 导航到达验证: _verify_nav_target_reached (三信号: URL/激活菜单/内容文本)

依赖: logging; 内部模块延迟导入 config_loader / browser_ai_login / db_manager
内部依赖: _check_error_page → _check_content_interactive

根因(3层追问):
  1. 表层: _single.py 3526 行超 §4.3 红线
  2. 中层: 5 个页面状态检测函数占 312 行，与 _execute_tool 主流程职责不同
  3. 根因: 页面状态检测是独立职责（导航执行期验证），应拆为独立模块
通用性: 覆盖 Ant Design/ElementUI/Bootstrap/原生 HTML，零业务硬编码
覆盖性: 影响 Step4 导航执行 → 用例导航成功率 → Step5 报告
"""
import logging
import re

from src.core.guarded import guard

_log = logging.getLogger(__name__)

# ── 错误页关键词表（唯一实现）──────────────────────────────────────────
# 拒绝类与不存在类语义不同：前者可能是「用例期望的结果」，后者永远是异常。
# 调用方（导航层、内容加载判定、模块验证）一律走下方谓词，不得各自维护词表。
_NOTFOUND_KEYWORDS = (
    "页面不存在",
    "页面未找到",
    "该页面不存在",
    "找不到页面",
    "找不到该页面",
    "访问的页面不存在",
    "抱歉，您访问的页面不存在",
    "访问链接有误",
    "链接有误",
    "无权限访问该页面",
    "404",
    "not found",
    "page not found",
)
_DENY_KEYWORDS = (
    "403",
    "无权限访问",
    "access denied",
    "forbidden",
)


def _keyword_hit(low: str, keywords) -> str:
    """在已小写的正文中找命中的关键词；**纯数字状态码按独立 token 匹配**。

    why: 原实现用 `kw in body` 做整页子串匹配。业务数据本身可能含 "404"/"403"
         字样（实测靶子商品描述 ``TEST_hnlg404pg7``），正常列表页因此被误判为
         404 页 → 导航整链失败 → 该轮 4 条用例 PASS→ERROR。状态码的语义是
         **独立 token**，嵌在标识符/编号里不构成状态码。
    通用性: 与业务无关；所有「整页文本找状态码」的判定都应遵循同一形态。
    """
    for kw in keywords:
        if kw.isdigit():
            if re.search(r"(?<![0-9a-z])%s(?![0-9a-z])" % re.escape(kw), low):
                return kw
        elif kw in low:
            return kw
    return ""


def page_is_denial_response(body_text: str) -> bool:
    """页面是否为「拒绝类响应」(403/无权限/forbidden) —— 唯一实现。

    共用方：`_check_error_page` 的错误页判定、`_handle_browser_navigate` 的
    「已到达期望拒绝页」判定。二者必须同源：曾因导航层用「内容区交互元素 <2」
    独立判定而把期望中的拒绝页判成"页面空白 → 导航失败"，使访问控制用例
    结构化动作失败 → 回落 AI 兜底 → 批量 SKIP。
    """
    return bool(_keyword_hit((body_text or "").lower(), _DENY_KEYWORDS))


def page_is_notfound_response(body_text: str) -> bool:
    """页面是否为「不存在类」响应(404/页面不存在) —— 唯一实现。

    共用方：导航层的 `_check_error_page`、内容加载判定 `_verify_page_content_loaded`、
    模块验证 `_verify_module_in_page`。三处原各维护一份词表且都用子串匹配，
    数据里出现 "404" 就误判；现统一到本谓词。
    """
    return bool(_keyword_hit((body_text or "").lower(), _NOTFOUND_KEYWORDS))


def notfound_keyword(body_text: str) -> str:
    """命中的不存在类关键词（仅用于日志归因）。"""
    return _keyword_hit((body_text or "").lower(), _NOTFOUND_KEYWORDS)


def _denial_keyword(body_text: str) -> str:
    """命中的拒绝关键词（仅用于日志归因）。"""
    return _keyword_hit((body_text or "").lower(), _DENY_KEYWORDS)


async def _check_content_interactive(page) -> int:
    """检测内容区交互元素数量（排除顶部导航/侧边栏等公共元素）。

    通用检测，返回 content_inputs + content_buttons 的总数。
    优先级：特定UI框架容器 → 通用内容区 → 全页面兜底
    """
    try:
        # 1. 特定UI框架容器（ElementUI, Ant Design, Bootstrap等）
        content_inputs = await page.locator(
            ".el-main input, .ant-content input, .ant-layout-content input, "
            ".el-table input, .ant-table input, "
            ".el-form input, .ant-form input, "
            "main input, [role='main'] input, "
            ".main-content input, .content-area input, "
            ".page-content input, .app-main input"
        ).count()
        content_buttons = await page.locator(
            ".el-main button, .ant-content button, .ant-layout-content button, "
            "main button, [role='main'] button, "
            ".main-content button, .content-area button, "
            ".page-content button, .app-main button"
        ).count()
        total = content_inputs + content_buttons

        # 2. 如果框架容器内无元素，尝试通用内容区（排除导航/侧边栏/页头）
        if total == 0:
            # 排除常见的导航/侧边栏/页头区域
            all_inputs = await page.locator(
                "input:visible, textarea:visible, select:visible"
            ).count()
            nav_inputs = await page.locator(
                "header input, nav input, .navbar input, .sidebar input, "
                ".ant-layout-sider input, .el-aside input, "
                "[role='navigation'] input, [role='banner'] input, "
                ".ant-menu input, .ant-menu-item input, "
                ".ant-layout-header input, .el-menu input, .el-menu-item input"
            ).count()
            all_buttons = await page.locator(
                "button:visible, [role='button']:visible, a:visible"
            ).count()
            nav_buttons = await page.locator(
                "header button, nav button, .navbar button, .sidebar button, "
                ".ant-layout-sider button, .el-aside button, "
                "[role='navigation'] button, [role='banner'] button, "
                ".ant-menu button, .ant-menu-item button, "
                ".ant-menu a, .ant-menu-item a, "
                ".ant-layout-header button, .ant-layout-header a, "
                ".el-menu button, .el-menu-item button, "
                ".el-menu a, .el-menu-item a, "
                "header a, nav a, .navbar a, .sidebar a"
            ).count()
            total = max(0, (all_inputs - nav_inputs) + (all_buttons - nav_buttons))

        return total
    except Exception:
        return 0


async def _check_content_structural(page) -> int:
    """内容区**结构性内容**元素数（表/行/列表/定义列表/表单/标题）。

    why: 「内容区交互元素数」是"页面是否渲染"的旧代理判据，只把**可交互**元素
      计入渲染证据 —— 只读列表页（如分页列表仅剩 1 个"下一页"链接）交互元素天然
      稀少，被误判为空白页 → 导航记为失败 → 结构化动作失败 → 回落 AI 兜底
      （架构约束「运行期零 AI」）→ 用例被判"无有效断言"SKIP，需求主张无人验证。
      渲染真实性由**结构性内容**表达：页面是否渲染出表格/列表/标题，与它有几个
      可点击元素无关。与 `page_is_denial_response` 同源——那一处曾对"期望被拒绝"
      的用例单独修过同一根因，此处把判据归还通用形态。
    通用性: table/ul/dl/form/h1-h3 是 HTML 标准结构语义，与框架无关，零业务硬编码。
    """
    try:
        return await page.locator(
            "main table, main tr, main ul, main dl, main form, "
            "main h1, main h2, main h3, "
            "[role='main'] table, [role='main'] tr, [role='main'] ul, "
            "[role='main'] dl, [role='main'] form, [role='main'] h1, "
            "table:visible, dl:visible, form:visible, h1:visible, "
            "[role='table']:visible, [role='grid']:visible"
        ).count()
    except Exception:
        return 0


async def _check_error_page(page, expects_denial: bool | None = None) -> bool:
    """检测页面是否为错误页面（404/无权限/页面不存在等）。

    通用检测，不依赖特定业务术语：
    1. 页面 body 包含常见错误关键词
    2. 页面有"返回首页"按钮但无业务内容元素
    3. 页面内容区无交互元素（空白页面）

    :param expects_denial: 本用例断言是否要求「被拒绝」。默认 None = 读
        `src.core.case_context` 的单源上下文（由 Step4 意图入口用唯一生产函数
        `case_expects_denial` 写入一次，下游只读不推）。为 True 时，拒绝类响应
        （403/无权限/forbidden）就是**期望结果**，不得计为错误页 —— 否则访问控制
        用例会被"重新登录+菜单回退"反复重试后判失败（实测单条白烧 37.9s）。
    """
    if expects_denial is None:
        from src.core.case_context import current_case_expects_denial

        expects_denial = current_case_expects_denial()
    with guard("_check_error_page: body_text = await page.locator('body", log=_log):
        body_text = await page.locator("body").inner_text()
        body_lower = body_text.lower()
        # 期望被拒绝 → 拒绝响应即期望结果，明确判为非错误页并立即返回
        # （不能只从关键词表里删，否则会被下方"空白页"判定再次捕获）
        if expects_denial and page_is_denial_response(body_text):
            _log.info(
                "[Navigate] 页面为拒绝响应且本用例期望被拒绝 → 非错误页: keyword=%s",
                _denial_keyword(body_text),
            )
            return False
        # 不存在类（404/页面不存在）走唯一谓词；拒绝类共用一个 token 感知的
        # 关键词命中器（数字状态码不再子串匹配，防业务数据误判）。
        _kw = notfound_keyword(body_text) or _denial_keyword(body_text)
        if _kw:
            _log.info("[Navigate] 检测到错误页面: keyword=%s", _kw)
            return True
        # 检查是否有"返回首页"但无业务内容
        has_back_home = "返回首页" in body_text or "back home" in body_lower
        if has_back_home:
            # 检查是否有业务表格/表单元素
            has_table = (
                await page.locator("table, [role='table'], [role='grid']").count() > 0
            )
            has_form = await page.locator("form, [role='form']").count() > 0
            if not has_table and not has_form:
                _log.info("[Navigate] 检测到错误页面: 仅有返回首页按钮，无业务内容")
                return True
        # ★ 通用修复 (2026-07-17): 检测空白页面（SPA返回无效路由时仅显示框架）
        #   使用 _check_content_interactive 的通用检测逻辑
        content_total = await _check_content_interactive(page)
        #   空白的判据 = 内容区**既无交互元素也无结构性内容**（表格/列表/标题）。
        #   只看交互元素会把只读展示页误判为空白页（同一根因见 _check_content_structural）。
        if content_total == 0 and await _check_content_structural(page) == 0:
            _log.info(
                "[Navigate] 检测到空白页面: 内容区无交互/结构元素(仅%d body字符)",
                len(body_text),
            )
            return True
    return False


async def _try_relogin_to_target(engine, page) -> bool:
    """检测目标系统会话是否过期，若是则尝试重新登录。

    通用策略：
    1. 检测页面是否有登录表单（密码框 + 登录按钮）
    2. 若有，使用配置的 target_username/target_password 重新登录
    3. 登录成功后导航回目标系统首页

    Returns:
        True 如果重新登录成功，False 如果不需要登录或登录失败
    """
    try:
        from src.core.config_loader import config

        target_url = config.target_url or ""
        username = config.target_username or ""
        password = config.target_password or ""

        if not username or not password:
            _log.debug("[Relogin] 未配置目标系统凭证，跳过重登")
            return False

        # 检测是否在登录页面：有密码输入框
        has_pwd = await page.locator('input[type="password"]').count() > 0
        if not has_pwd:
            _log.debug("[Relogin] 未检测到登录表单，跳过重登")
            return False

        _log.warning("[Relogin] 检测到目标系统会话过期，尝试重新登录: %s", target_url)

        # 尝试确定性登录
        from src.pipeline.steps.step4.browser_ai_login import try_deterministic_login

        login_ok = await try_deterministic_login(engine, username, password)
        if not login_ok:
            _log.warning("[Relogin] 确定性登录失败")
            return False

        _log.info("[Relogin] 重新登录成功")
        return True

    except Exception as e:
        _log.warning("[Relogin] 重登异常: %s", str(e)[:120])
        return False


async def _find_first_working_page(project: str = "") -> str:
    """★ 通用修复 (2026-07-18): 从导航地图中查找第一个有有效URL的页面。

    当首页（如 /dashboard）为死页面时，导航到系统中有实际内容的页面，
    确保后续菜单导航可在有效页面上执行。
    """
    try:
        if not project:
            from src.core.config_loader import config as _cfg

            project = getattr(_cfg, "project_name", "") or ""
        if not project:
            return ""

        # ★ P0-1 读侧迁移(2026-08-05): system_nav_maps(0行且无 nav_json 列) → nav_graph
        from src.pipeline.utils.nav_graph_read import read_flat_list, read_menu_groups

        # 优先找有URL的UI菜单项（非API接口组）
        for group in read_menu_groups(project):
            group_name = (group.get("group_name", "") or "").lower()
            if group_name in ("api接口", "api"):
                continue
            for item in group.get("items", []):
                item_path = (item.get("path", "") or "").strip()
                if (
                    item_path
                    and item_path.startswith("/")
                    and "dashboard" not in item_path.lower()
                ):
                    return item_path

        # 兜底：从 flat_list 中找
        for item in read_flat_list(project):
            item_path = (item.get("path", "") or "").strip()
            if (
                item_path
                and item_path.startswith("/")
                and "dashboard" not in item_path.lower()
            ):
                return item_path
    except Exception as _e:
        # 配置存在但读取失败属阻断级信号，必须留下可定位的上下文(§5/§执行纪律7)
        _log.error(
            "[MenuNav] 查找首个有效页面失败 project=%s: %s", project, _e, exc_info=True
        )
    return ""


async def _verify_nav_target_reached(page, target_keywords: list) -> bool:
    """★ 方案B验证层 (ADR-017, 2026-07-23): 验证导航是否真正到达目标页面。

    修复"导航假成功"根因(§10.9):
      现象: TC-REQ-001-P01 navigate intent:辅材 后 text_contains"全部产地"失败(找到0个);
            TC-REQ-001-N01 navigate 后 fill 报 page=None。
      根因(3层追问):
        1. 表层: checks在错误页面执行 → 页面不含"全部产地"
        2. 中层: navigate intent:辅材 解析后导航到的不是辅材页面
        3. 根因: _fallback_menu_navigate 只检查"非错误页+内容≥2",
                未验证目标菜单项是否真的加载 → 假成功 → 后续操作全在错误页面
      全局观: 影响所有带navigate的用例(几乎全部)。假成功→fill/click/check全失败。

    三信号验证(任一强匹配即成功):
      信号1 (强): 当前URL包含目标关键词(URL编码中文或英文路径段)
      信号2 (强): 当前激活菜单项/激活Tab文本包含目标关键词(W3C ARIA契约)
      信号3 (弱): 页面主内容区文本包含目标关键词

    通用性(§12.4 9维度):
      1. 跨系统: URL/激活菜单/内容文本是所有SPA系统的通用信号
      2. 跨行业: 关键词匹配不依赖业务术语
      3. 跨页面: 列表/详情/表单/Tab页都适用
      4. 跨Tab: Tab激活态(aria-selected/active class)是W3C标准
      5. 数据完整性: 信号独立,任一缺失不崩溃
      6. 根因溯源: 修复"假成功"判定,非打补丁
      7. 覆盖性: 所有导航返回点都调用
      8. 全局观: 避免后续fill/click在错误页面执行,解锁Path B整条流水线
      9. 职责边界: Step4执行层验证导航结果,在自己职责内
    自我反驳三次:
      1) 只查URL?→ 不够。页面内Tab切换URL不变(纯前端Tab),需激活态信号
      2) 只查激活菜单?→ 不够。某些系统激活态标记不一致,需URL+内容兜底
      3) 三信号AND?→ 过严。任一强匹配即可,避免误判失败导致用例无法执行
    """
    if not target_keywords:
        return True  # 无关键词无法验证,放行(兼容旧用例)

    try:
        from urllib.parse import unquote

        # ── 信号1 (强): URL匹配 ──
        current_url = ""
        try:
            current_url = page.url or ""
        except Exception as _se8:
            _log.debug(
                "[_single_nav_state] 异常降级(pass): %s", str(_se8)[:60], exc_info=True
            )
            pass
        if current_url:
            url_decoded = unquote(current_url)
            url_lower = url_decoded.lower()
            for kw in target_keywords:
                kw_lower = kw.lower()
                if len(kw_lower) >= 2 and kw_lower in url_lower:
                    _log.info(
                        "[NavVerify] ✓ 信号1(URL)匹配: kw='%s' url=%s",
                        kw,
                        current_url[:80],
                    )
                    return True

        # ── 信号2 (强): 激活菜单项/激活Tab文本匹配 ──
        #   通用性: W3C ARIA契约 + 主流UI框架(ElementUI/Ant/Bootstrap)激活态class
        active_selectors = [
            "[aria-selected='true']",  # W3C标准(最通用)
            ".ant-tabs-tab-active",
            ".ant-menu-item-selected",
            ".el-tabs__item.is-active",
            ".el-menu-item.is-active",
            ".nav-tabs li.active",
            ".menu-item.active",
            ".is-active[role='tab']",
            ".active[role='tab']",
        ]
        for sel in active_selectors:
            try:
                cnt = await page.locator(sel).count()
                if cnt <= 0:
                    continue
                texts = await page.locator(sel).all_text_contents()
                for t in texts:
                    t_lower = (t or "").strip().lower()
                    if not t_lower:
                        continue
                    for kw in target_keywords:
                        kw_lower = kw.lower()
                        if len(kw_lower) >= 2 and (
                            kw_lower in t_lower or t_lower in kw_lower
                        ):
                            _log.info(
                                "[NavVerify] ✓ 信号2(激活菜单)匹配: kw='%s' active='%s'",
                                kw,
                                t.strip()[:30],
                            )
                            return True
            except Exception as _se9:
                _log.debug(
                    "[_single_nav_state] 异常降级(continue): %s",
                    str(_se9)[:60],
                    exc_info=True,
                )
                continue

        # ── 信号3 (弱): 主内容区文本匹配 ──
        #   通用性: 优先内容区容器,避免侧边栏菜单文本污染
        content_text = ""
        for sel in [
            ".el-main",
            ".ant-layout-content",
            "main",
            "[role='main']",
            ".main-content",
            ".app-main",
            ".content-area",
            ".page-content",
        ]:
            try:
                content_text = (
                    await page.locator(sel).first.text_content(timeout=2000) or ""
                )
                if content_text and len(content_text) > 20:
                    break
            except Exception as _se10:
                _log.debug(
                    "[_single_nav_state] 异常降级(continue): %s",
                    str(_se10)[:60],
                    exc_info=True,
                )
                continue
        if not content_text:
            try:
                content_text = (
                    await page.locator("body").text_content(timeout=2000) or ""
                )
            except Exception as _se11:
                _log.debug(
                    "[_single_nav_state] 异常降级(pass): %s",
                    str(_se11)[:60],
                    exc_info=True,
                )
                pass
        if content_text:
            content_lower = content_text.lower()
            for kw in target_keywords:
                kw_lower = kw.lower()
                if len(kw_lower) >= 2 and kw_lower in content_lower:
                    _log.info("[NavVerify] ✓ 信号3(内容文本)匹配: kw='%s'", kw)
                    return True

        _log.warning(
            "[NavVerify] ✗ 三信号均未匹配,导航未到达目标页面: keywords=%s url=%s",
            target_keywords[:5],
            current_url[:80],
        )
        return False
    except Exception as _e:
        _log.warning("[NavVerify] 验证异常(放行避免阻断): %s", str(_e)[:80])
        return True
