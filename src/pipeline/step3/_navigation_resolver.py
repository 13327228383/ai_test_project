# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""Step3 落库前确定性导航解析器（v5.9, 2026-08-04）。

问题背景：LLM 生成导航目标时对 prompt 规则遵循不可靠（实测 8 条用例全选同一菜单
/docs 或 /project-mgmt），需求→菜单语义映射失效，用例导航与需求脱节。

本模块在意图写入 MySQL 前，用确定性规则（关键词→真实菜单匹配）覆写导航目标：
  1. 登录/认证类需求 → 导航到**真实登录入口**（config.target_login_path → "/" 兜底），
     绝不发射空导航目标（空目标 = 不可执行指令，必然导致 Step4 结构化执行失败）
  2. 其余需求 → 需求文本（module+description+req_id）关键词 → 匹配 nav_graph_nodes
     真实菜单（标题/URL 包含关系），覆写 actions[0].url/target_hint
  3. 断言强化 → 追加 url_matches（目标页 URL），让"导航到了正确页面"成为可验证断言

纯规则实现，可测试可复现，不依赖 LLM 对 prompt 的遵循度。

通用性：导航数据源与 _contexts._build_context_from_nav_graph 一致（nav_graph_nodes，
真实 DOM/手动注入），关键词表为通用业务词（登录/流水线/报告/配置/项目/用户...），
适用于任何 Web 系统 UI。
"""

import logging
import time

from src.pipeline.utils.menu_matcher import match_module_to_menu
# 登录/认证类需求判定（无对应菜单，Agent 停留登录场景自主处理）。
# ★ 单一事实源(2026-09-24): 关键词表收敛到 _rule_extractor_keywords.AUTH_KEYWORDS，
#   与用例生成侧（_fallback_cases / case_generator）共用，避免多口径漂移。
from src.pipeline.step3._rule_extractor_keywords import (
    AUTH_KEYWORDS as _AUTH_KEYWORDS,
    is_auth_requirement as _is_auth_requirement,
    is_field_constraint_requirement as _is_field_constraint_requirement,
    parse_field_constraints as _parse_field_constraints,
)

_log = logging.getLogger(__name__)

# 语义模糊的验证点/断言文案标记：页面上不会原样出现，留之必判假 FAIL。
# 生成端（resolver）与清洗端（checks）共用本常量，避免同一判据各持一份而漂移。
_DROP_EXPECT_KW = ("页面显示", "相关功能", "页面应展示", "应显示")

# ★ 根因修复(2026-10-28, AGENTS.md §4.17-B): 导航目标「未解析」必须显式。
#   生成端在拿不到任何导航事实时把 target_hint 兜底成站点根 "/"，评审页于是渲染
#   "导航到 /"、执行端则真的把浏览器带到站点根 —— 站点根不是"目标页"，却因断言
#   只校验"页面加载无 404"而静默通过（实测 550/555 条用例首步 target_hint="/"，
#   同时 nav_graph_nodes 全库 0 行）。
#   判据: navigate 动作既无显式 url，目标文本又是 空/根/登录页 —— 这不是页面身份，
#   只是"没解析出来"的占位。凡占位一律标注 nav_unresolved，目标改写为需求语义
#   页面名（module），绝不保留 "/"（宁可"没有目标"也不伪装）。
#   通用性: 判据只依赖动作/URL 结构，零业务硬编码，适用于任何 Web 系统。
PLACEHOLDER_NAV_TARGETS = ("", "/", "/login")
NAV_UNRESOLVED_KEY = "nav_unresolved"
NAV_UNRESOLVED_REASON_KEY = "nav_unresolved_reason"


def is_placeholder_nav(action: dict) -> bool:
    """navigate 动作是否为「站点根占位」（无显式 url 且目标为 空/根/登录页）。"""
    if not isinstance(action, dict):
        return False
    _t = str(action.get("type") or action.get("action") or "").strip()
    if _t != "navigate":
        return False
    if str(action.get("url") or "").strip():
        return False
    return str(action.get("target_hint") or "").strip() in PLACEHOLDER_NAV_TARGETS


def _semantic_page_name(intent: dict) -> str:
    """用例的语义页面名（module → target_page，排除路径型值）。"""
    for _k in ("module", "target_page"):
        _v = str(intent.get(_k) or "").strip()
        if _v and not _v.startswith(("/", "http://", "https://")):
            return _v
    return ""


def clear_nav_unresolved(intent: dict) -> None:
    """清除「未解析」标注（导航目标被真实事实解析成功时调用，幂等）。"""
    if not isinstance(intent, dict):
        return
    intent.pop(NAV_UNRESOLVED_KEY, None)
    intent.pop(NAV_UNRESOLVED_REASON_KEY, None)
    for _a in intent.get("actions") or []:
        if isinstance(_a, dict):
            _a.pop(NAV_UNRESOLVED_KEY, None)
            _a.pop(NAV_UNRESOLVED_REASON_KEY, None)


def mark_nav_unresolved(intent: dict, reason: str) -> int:
    """把占位导航标注为「目标页未解析」（幂等，返回被标注的步骤数）。

    - 用例级: ``nav_unresolved`` / ``nav_unresolved_reason``（评审页、报告据此提示）
    - 步骤级: 目标改写为需求语义页面名，去掉 url，并打同名字段；判据见
      ``is_placeholder_nav``。执行端读到语义页面名会走真实导航解析，解析不到即
      **如实失败**，不再停在站点根上假装"到达目标页"。
    """
    if not isinstance(intent, dict):
        return 0
    _actions = intent.get("actions")
    if not isinstance(_actions, list):
        return 0
    _semantic = _semantic_page_name(intent)
    _n = 0
    for _a in _actions:
        if not is_placeholder_nav(_a):
            continue
        _a.pop("url", None)
        _a["target_hint"] = _semantic
        _a[NAV_UNRESOLVED_KEY] = True
        _a[NAV_UNRESOLVED_REASON_KEY] = reason
        _n += 1
    if _n:
        intent[NAV_UNRESOLVED_KEY] = True
        intent[NAV_UNRESOLVED_REASON_KEY] = reason
    return _n

# ★ v5.15 可观测化：记录最近一次 LLM 语义匹配的原始响应与解析菜单（供日志落库）
_LLM_LAST_RAW = ""
_LLM_LAST_MENU = ""

# ★ v5.14(2026-08-04) 通用化：移除 _RULE_KEYWORDS 业务词表（原表绑定引擎自身业务词，
#   如"流水线/日志面板/项目管理"，换系统即失效）。改为：
#   L1 纯算法匹配（字符重叠强直接命中）+ L2 LLM 语义单选（语义 gap 兜底）+ L3 兜底。
_LLM_PROVIDER_CACHE = {}

# ★ 效率修复(2026-09-27)：L2 LLM 语义匹配结果进程内 memo。
#   根因：resolve_intents_batch 对全部用例逐条 resolve_intent，同一需求文本
#   （module+description+req_id）在数百条用例中完整重复，但每次都独立发起一次
#   3–9s 的 LLM 网络调用。实测 accept_sut_0923 单次 Step3 导航解析：
#     LLM 调用 564 次 / 累计 3811s，而 distinct 文本仅 82 个
#     → 约 85% 的调用是纯重复，白烧 ~53 分钟墙钟（占 Step3 绝大部分耗时）。
#   菜单匹配是**纯函数**（同一文本 + 同一菜单清单 → 同一结果，含"无结果"），
#   memo 只消除重复网络往返，不改变任何判定。key 含菜单清单签名 → 清单变化自动失效。
#   通用性：跨系统/行业/页面，纯调用去重，零业务硬编码。
_LLM_MENU_MEMO: dict = {}
_LLM_MENU_MEMO_MAX = 2000


def _menu_nodes_sig(nodes: list) -> str:
    """菜单清单签名（标题有序拼接）——清单变化时 memo 自动失效。"""
    return "|".join(
        str(n.get("page_title") or "").strip() for n in nodes if n.get("page_title")
    )


def _get_llm_provider():
    """获取主通道 LLM provider（带缓存）。无 key / 失败返回 None（降级纯算法）。"""
    try:
        from src.core.config_loader import config
        from src.ai.ai_providers import get_provider

        _key = config.ai_api_key
        if not _key:
            return None
        _ck = (config.ai_provider, _key, config.ai_base_url, config.ai_model)
        if _ck not in _LLM_PROVIDER_CACHE:
            _LLM_PROVIDER_CACHE[_ck] = get_provider(
                name=config.ai_provider,
                api_key=_key,
                base_url=config.ai_base_url,
                model=config.ai_model,
                timeout=30,
                max_retries=1,
            )
        return _LLM_PROVIDER_CACHE[_ck]
    except Exception as e:
        _log.warning("[NavResolver] LLM provider 获取失败(降级纯算法): %s", str(e)[:80])
        return None


def _llm_match_menu(text: str, nodes: list) -> dict | None:
    """L2 LLM 语义单选（进程内 memo 包装，见 _LLM_MENU_MEMO）。

    同一 (菜单清单, 文本) 只发一次 LLM 网络调用，其余命中缓存；命中时同步回放
    llm_raw/menu 两个可观测全局，保证 navigation_match_logs 记录不失真。
    """
    if not _get_llm_provider():
        return None
    global _LLM_LAST_RAW, _LLM_LAST_MENU
    _key = (_menu_nodes_sig(nodes), text)
    _cached = _LLM_MENU_MEMO.get(_key)
    if _cached is not None:
        _node, _LLM_LAST_RAW, _LLM_LAST_MENU = _cached
        return _node
    _node = _llm_semantic_pick(text, nodes)
    if len(_LLM_MENU_MEMO) >= _LLM_MENU_MEMO_MAX:
        _LLM_MENU_MEMO.clear()
    _LLM_MENU_MEMO[_key] = (_node, _LLM_LAST_RAW, _LLM_LAST_MENU)
    return _node


def _llm_semantic_pick(text: str, nodes: list) -> dict | None:
    """L2 LLM 语义单选：从菜单清单选与需求语义最匹配的菜单（零业务词，跨系统通用）。

    语义 gap 场景（如"系统状态"→"日志面板"、"项目选择"→"项目管理"）纯算法分数过低，
    LLM 语义理解可正确关联。返回的菜单名必须精确匹配清单标题（归一化后），防幻觉。
    """
    provider = _get_llm_provider()
    if not provider:
        return None
    titles = [
        str(n.get("page_title") or "").strip() for n in nodes if n.get("page_title")
    ]
    if not titles:
        return None
    _menu_list = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))
    _sys = (
        "你是页面导航助手。根据需求描述，从系统菜单清单中选择与需求功能"
        '语义最匹配的一个菜单项。只输出 JSON：{"menu": "菜单名"}。'
        '菜单名必须原样来自清单，禁止编造。若需求是登录/认证类基础功能，输出 {"menu": ""}。'
        "直接输出 JSON 本身，禁止输出任何解释、思考过程或前后缀文字。"
        # ★ 根因修复(2026-09-27)：原角色设定为"你是测试用例生成助手"，与本次任务
        #   （选菜单）语义冲突 → 模型被角色带偏，实测输出 `{"test_cases": [...]}`
        #   而非 `{"menu": ...}` → 正则找不到 JSON → 判为"无 JSON 响应"降级 L3
        #   （实测 accept_sut_0923 该类失败 171 次 / ~1078s 纯浪费）。显式收窄输出域。
        "输出中不得包含 test_cases 或其他任何字段。"
    )
    _user = f"需求：{text[:200]}\n\n系统菜单清单：\n{_menu_list}"
    global _LLM_LAST_RAW, _LLM_LAST_MENU
    _LLM_LAST_RAW = ""
    _LLM_LAST_MENU = ""
    with guard("_llm_semantic_pick: resp = provider.chat( system_prompt=", log=_log, level=logging.WARNING):
        # ★ 根因修复(2026-09-25) RC-N: 导航语义匹配的 max_tokens 过小 → 输出截断 → 导航丢失。
        #   根因: 期望输出仅 `{"menu": "资产"}`（≈10 token），原设 max_tokens=120 看似充裕，
        #     但推理型模型会先输出大段"用户要求我根据需求描述选择…"的思考文本再给 JSON，
        #     120 token 全被思考文本吃掉 → finish_reason=length → 正则找不到 JSON →
        #     返回 None → 需求→菜单映射丢失（实测 run_logs 中该 WARNING 连续数十条）。
        #   后果: 需求被路由到兜底页面（列表页）而非语义目标页（如新增表单页）→
        #     "字段约束/表单校验"类用例在错误页面上找不到控件 → 大面积假 FAIL
        #     （实测 TC-REQ-004-I01 落在列表页，候选标签只有'按名称搜索/资产分类'）。
        #   通用修复: 上限放宽至可容纳"思考+JSON"（512）；并显式禁止输出解释文本。
        #   通用性: 跨系统/行业/页面 —— 纯 LLM 输出预算与提示词约束，零业务硬编码；
        #     对任何带思维链的模型（agnes/deepseek/o1 类）均成立。
        resp = provider.chat(
            system_prompt=_sys, user_message=_user, temperature=0.1, max_tokens=512
        )
        resp_text = resp if isinstance(resp, str) else str(resp.get("content") or resp)
        _LLM_LAST_RAW = resp_text
        import re as _re
        import json as _json

        _m = _re.search(r'\{[^{}]*"menu"[^{}]*\}', resp_text)
        if not _m:
            _log.warning(
                "[NavResolver] LLM 语义匹配无 JSON 响应: %s", str(resp_text)[:60]
            )
            return None
        name = str(_json.loads(_m.group(0)).get("menu", "") or "").strip()
        _LLM_LAST_MENU = name
        if not name:
            return None  # LLM 判定为登录类等无菜单需求
        # 精确匹配清单标题（归一化空格），防幻觉
        for n in nodes:
            t = str(n.get("page_title") or "").strip()
            if t and (t == name or t.replace(" ", "") == name.replace(" ", "")):
                return n
        # 宽松：LLM 返回名包含于清单标题（如返回"项目管理"但清单是"项目管理(测试)"）
        for n in nodes:
            t = str(n.get("page_title") or "").strip()
            if t and (name in t or t in name):
                return n
        _log.warning("[NavResolver] LLM 返回的菜单名不在清单中(丢弃): %r", name)
    return None


def _load_menu_nodes(project: str, version_label: str) -> list:
    """从 nav_graph_nodes 加载真实菜单节点（与 context builder 同源）。"""
    try:
        from src.pipeline.utils.nav_graph_store import read_nav_graph_nodes

        return read_nav_graph_nodes(project, version_label) or []
    except Exception as e:
        _log.warning("[NavResolver] 加载 nav_graph_nodes 失败(不阻塞): %s", e)
        return []


def _match_menu(
    text: str, nodes: list, project: str = "", case_id: str = ""
) -> dict | None:
    """需求文本 → 最佳菜单节点（v5.14 通用化：零业务词表，跨系统通用）。

    L1 纯算法：match_module_to_menu（字符重叠强直接命中）
      exact_contain ≥ 0.8 / lcs_jaccard ≥ 0.45
    L2 LLM 语义单选：语义 gap 兜底（"系统状态"→"日志面板"），结果必须命中清单标题
    L3 兜底：返回 None（导航目标留空，Agent 自主导航）

    ★ v5.15 可观测化：每次匹配写 navigation_match_logs（级别/LLM/耗时/结果），
      失败率统计 + 人工校准（见 nav_match_logger）。
    """
    from src.pipeline.utils import nav_match_logger

    if not nodes:
        return None
    titles = [
        str(n.get("page_title") or "").strip() for n in nodes if n.get("page_title")
    ]
    if not titles:
        return None
    # L1 纯算法
    best, score, stage = match_module_to_menu(text, titles)
    if best:
        _min = 0.8 if stage == "exact_contain" else 0.45
        if score >= _min:
            for n in nodes:
                if str(n.get("page_title") or "").strip() == best:
                    nav_match_logger.log_match(project, case_id, text, "L1")
                    return n
    # L2 LLM 语义单选（计时 + 记录成功/原始响应/菜单名/命中）
    provider = _get_llm_provider()
    _t0 = time.time()
    node = _llm_match_menu(text, nodes) if provider else None
    _elapsed = int((time.time() - _t0) * 1000)
    if node:
        nav_match_logger.log_match(
            project,
            case_id,
            text,
            "L2",
            llm_used=True,
            llm_elapsed_ms=_elapsed,
            llm_success=True,
            llm_raw=_LLM_LAST_RAW,
            llm_menu=node.get("page_title", ""),
            menu_hit=True,
        )
        _log.info(
            "[NavResolver] LLM 语义匹配: 需求→菜单[%s] (%.0fms)",
            node.get("page_title"),
            _elapsed,
        )
        return node
    # L3 降级（LLM 未用 / 失败 / 无 JSON / 菜单名不在清单）
    nav_match_logger.log_match(
        project,
        case_id,
        text,
        "none",
        llm_used=provider is not None,
        llm_elapsed_ms=_elapsed,
        llm_success=provider is not None and not node,
        llm_raw=_LLM_LAST_RAW,
        llm_menu=_LLM_LAST_MENU,
        menu_hit=bool(_LLM_LAST_MENU),
    )
    return None


def _is_auth(text: str) -> bool:
    return _is_auth_requirement(text)


def _login_entry_url() -> str:
    """登录/认证类用例的可执行导航目标（**绝不返回空串**）。

    ★ 根因修复：认证类需求（无对应菜单）此前被"清空导航目标"
      （``url=""``）→ 生成端发射了一条**不可执行指令** → Step4 结构化执行判
      "navigate URL 解析为空" → 降级 AI 自由探索 8 轮不收敛 → HARNESS_ERROR
      （实测 TC-REQ-005-E01/I01）。
    "无对应菜单"≠"无需导航"：登录入口本身就是一个真实页面。

    证据源（单一事实源，零业务硬编码，跨系统通用）：
      1. ``config.target_login_path`` —— 统一桥接"项目配置 TARGET_LOGIN_PATH"
         与"靶子注册表 auth.login_url"（跨项目/改名场景由 match_target_key 兜底）
      2. ``"/"`` —— 根路径，登录表单的通用落点
    """
    with guard("_login_entry_url: from src.core.config_loader import c", log=_log):
        from src.core.config_loader import config

        _p = str(getattr(config, "target_login_path", "") or "").strip()
        if _p:
            return _p
    return "/"


def _menu_mentioned_in_text(text: str, nodes: list) -> dict | None:
    """需求文本是否**明确点名**了系统真实菜单/页面；是则返回该节点，否则 None。

    ★ 根因修复(2026-09-26)：
      现象——REQ-001「在用户登录状态下，系统支持对IT及办公资产进行检索…」被
        `_is_auth` 判定为"认证类需求"（只因文本含"登录"二字）→ 导航被清空、
        module 置空 → Step4 判「模块 '' 在侧边栏/URL 中无对应实现」假 FAIL
        （实测 TC-REQ-001-P01/B01/N01，3 条权威需求被误判为"系统未实现"）。
      根因——`_is_auth` 是**关键词出现性**判定，无法区分"登录是被测对象"
        与"登录只是场景/前置条件"，而菜单清单（nav_graph_nodes）这个
        权威事实源在决策链里完全没有参与。
      通用判据（零业务硬编码）：**权威证据 > 关键词启发**——需求若点名了
        系统里真实存在的页面，它就不可能是"无对应菜单的认证需求"，
        导航不得被关键词清空。菜单标题取自系统摄入的菜单图谱，跨系统通用。
    """
    _t = str(text or "")
    if not _t or not nodes:
        return None
    for _n in nodes:
        _title = str(_n.get("page_title") or "").strip()
        if len(_title) >= 2 and _title in _t:
            return _n
    return None


# 通用全局噪音词（任何 Web 系统的常驻外壳文案：登出/刷新）。
# 注意：这只是**兜底**，主判据是 ``_chrome_texts`` 的跨页共现分析（数据驱动、零业务词）。
_NOISE = (
    "一键执行", "退出登录", "退出", "登出", "注销", "刷新", "刷新列表",
    "logout", "sign out", "signout",
)


def _pe_dict(node: dict) -> dict:
    pe = node.get("page_elements") or {}
    if isinstance(pe, str):
        try:
            import json as _json

            pe = _json.loads(pe)
        except Exception:
            pe = {}
    return pe if isinstance(pe, dict) else {}


def _chrome_texts(nodes: list) -> set:
    """全局外壳文案集合（登出/刷新等噪音 + 各页导航标题）。

    ★ 根因修复(2026-09-25) RC-E: 页面特征元素必须是"本页独有"的元素，
    而 ``page_elements`` 抓取是**全页**扁平化（含顶部导航/侧边栏链接），
    其首个按钮恒为导航链接。

    根因(5层why):
      L1: 断言/验证点取到导航文案（如"资产"）而非页面特征 → 该文案在**每个**
          页面都存在 → 验证恒真（假 PASS）；若 GuidedVerify 侧边栏过滤生效
          则该文案在正文中不存在 → 验证恒假（假 FAIL）。同一根因、两种失真。
      L2: 取的是 ``page_elements.buttons`` 的**第一个**元素，无"页面独有"判据。
      L3: ``page_elements`` 无 nav/main 分区，DOM 抓取把导航与正文混在一起。
      L4: 原 ``_NOISE`` 是**硬编码业务词**（"一键执行"），换系统即失效，
          而导航文案是**任意系统都有**的通用形态。
      L5: 缺"什么算全局外壳"的通用判据。

    通用判据（两层，均为事实源、零业务词）:
      ① 首选 ``region == 'nav'``：DOM 抓取用 W3C ARIA landmark + 原生语义标签
         （nav/header/footer/aside/[role=navigation|banner|contentinfo|complementary]）
         判定的元素归属，是**结构化事实**，与业务/框架无关（见 element_utils）。
      ② 兜底：文案等于任一页面的导航标题（``page_title``）→ 导航身份；
         或命中通用外壳词 ``_NOISE``（登出/刷新）。用于 region 缺失的历史数据。

    注: 不用"跨页共现交集"作判据 —— 实测会误删**共享组件**（分页"下一页"在
      资产页与审计页都存在，但它是页面特征而非外壳），导致特征点被过度剔除。
    """
    chrome = {str(t).strip().lower() for t in _NOISE if str(t).strip()}
    for n in nodes or []:
        t = str(n.get("page_title") or "").strip()
        if t:
            chrome.add(t.lower())
    return chrome


def _pe_items(node: dict) -> list:
    """展平 page_elements 的候选交互元素（按钮优先于输入框），保留 region。"""
    pe = _pe_dict(node)
    out = []
    for key in ("buttons", "inputs"):
        for it in (pe.get(key) or [])[:15]:
            if not isinstance(it, dict):
                continue
            t = str(it.get("text") or "").strip() or str(
                it.get("placeholder") or ""
            ).strip()
            if t:
                out.append({"text": t, "region": str(it.get("region") or "").strip().lower()})
    return out


def _feature_selector(node: dict, chrome: set | None = None) -> str:
    """从 page_elements 构造页面特征元素 selector（Playwright CSS/text 语法）。
    过滤全局外壳元素（导航/侧边栏/登出，见 ``_chrome_texts`` 与 region 判据）。"""
    _skip = chrome if chrome is not None else {t.lower() for t in _NOISE}
    pe = _pe_dict(node)
    for key in ("buttons", "inputs"):
        for it in (pe.get(key) or [])[:15]:
            if not isinstance(it, dict):
                continue
            if str(it.get("region") or "").strip().lower() == "nav":
                continue
            t = str(it.get("text") or "").strip()
            if key == "inputs":
                ph = str(it.get("placeholder") or "").strip()
                if ph and ph.lower() not in _skip:
                    return f'input[placeholder="{ph}"]'
                continue
            if t and t.lower() not in _skip:
                return f'button:has-text("{t}")'
    return ""


def _feature_text(node: dict, chrome: set | None = None) -> str:
    """取页面第一个**独有**特征元素文本（按钮>输入框placeholder），作为 GuidedVerify 验证点。

    过滤全局外壳元素（导航/侧边栏/登出），保证验证点只在本页成立 ——
    否则验证恒真（假 PASS）或被侧边栏过滤逻辑剔除后恒假（假 FAIL）。
    """
    _skip = chrome if chrome is not None else {t.lower() for t in _NOISE}
    for it in _pe_items(node):
        if it["region"] == "nav":
            continue
        if it["text"].lower() in _skip:
            continue
        return it["text"]
    return ""


def _fix_explore_task(
    intent: dict, node: dict | None, auth: bool, chrome: set | None = None
):
    """★ v5.11(2026-08-04): 修正 _explore_task（Agent 实际执行的导航+验证目标）。

    Step4 Agent 执行路径消费 _explore_task.test_method.verifications（GuidedVerify），
    而非顶层 checks——LLM 生成的 verifications 是"页面显示XX相关功能"式语义断言，
    module/navigation/target_page 用需求章节号，导致 Agent 导航失败+验证失败。
    解析器统一修正为真实菜单 + 页面特征验证点。
    """
    et = intent.get("_explore_task")
    if not isinstance(et, dict):
        return
    tm = et.get("test_method")
    if not isinstance(tm, dict):
        tm = {}
        et["test_method"] = tm
    vrs = tm.get("verifications")
    if not isinstance(vrs, list):
        vrs = []
        tm["verifications"] = vrs
    # 1. 删除语义模糊验证点（"页面显示XX相关功能"式 + 长文本叙述式语义断言——
    #    后者如"在X模块中，用户执行XX操作，系统预期反馈：XX"，页面文本不会原样包含，
    #    GuidedVerify 文本包含匹配必失败；仅保留短文本具体断言（≤40字符）或 URL 断言）
    #
    # ★ 根因修复(2026-09-25) RC-S: 字段约束型验证点必须删除（改由 field_constraint_check 承担）。
    #   根因: 字段约束（"系统提供active/maintenance/retired下拉选项"／"系统允许输入最多40
    #     字符的名称"）的性质是**控件 DOM 属性**，页面上不存在描述这些规则的文字；
    #     checks 层已由 `_intent_checks._checks_from_expected` 发 field_constraint_check
    #     （读 maxlength/min/type/options 判定）。但 test_method.verifications 里**残留同一
    #     期望的 verify 步骤**，Step4 guided 会对它做文本/语义判定 → 用错工具 → 恒 FAIL。
    #     原仅靠"长度>40 字符"启发式偶然删除（REQ-003 的 expect 45 字符被删→PASS，
    #     REQ-006 的 expect 34 字符残留→ERROR）——语义问题被长度偶然性掩盖。
    #   通用修复: 用单一事实源（is_field_constraint_requirement + parse_field_constraints）
    #     识别约束型期望并删除对应 verify 步骤，与 checks 层同一判据，确定性、无偶然性。
    #   通用性: 跨系统/行业/页面 —— 判据来自通用约束语汇解析（最多/非负整数/大于0/下拉枚举），
    #    零业务硬编码。
    def _is_constraint_verify(_v: dict) -> bool:
        _exp = _v.get("expect")
        if not isinstance(_exp, str) or not _exp.strip():
            return False
        return bool(
            _is_field_constraint_requirement(_exp) and _parse_field_constraints(_exp)
        )

    vrs[:] = [
        v
        for v in vrs
        if not (
            isinstance(v, dict)
            and (
                _is_constraint_verify(v)
                or (
                    isinstance(v.get("expect"), str)
                    and (
                        any(k in v["expect"] for k in _DROP_EXPECT_KW)
                        or (
                            len(v["expect"]) > 40
                            and not v["expect"].lstrip().startswith("/")
                        )
                    )
                )
            )
        )
    ]
    if auth:
        # 登录类：解析到真实登录入口（**不得清空为不可执行指令**，见 _login_entry_url）
        _login_url = _login_entry_url()
        et["target_page"] = _login_url
        if isinstance(tm.get("navigation"), str):
            tm["navigation"] = _login_url
        return
    if node is None:
        return
    url = str(node.get("page_url") or "")
    title = str(node.get("page_title") or "")
    # 2. 修正 module / target_page / navigation
    et["module"] = title
    if url:
        et["target_page"] = url
    if isinstance(tm.get("navigation"), str):
        tm["navigation"] = f"侧边栏→{title}"
    # 3. 追加页面特征验证点（GuidedVerify 文本包含匹配，特征文本必然出现在目标页）
    feat = _feature_text(node, chrome)
    if feat and not any(v.get("expect") == feat for v in vrs if isinstance(v, dict)):
        vrs.append(
            {
                "step": len(vrs) + 1,
                "value": "",
                "action": "verify",
                "expect": feat,
                "target": f"{title}页面特征",
            }
        )


def _replace_main_assertion(checks: list, feat_sel: str):
    """把 checks 中的通用 main 断言（element_exists main/.content/#content）
    替换为目标页特征元素断言（对无 main 布局的页面必失败的问题）。"""
    if not feat_sel:
        return
    for _c in checks:
        _c_ = _c.get("check", _c) if isinstance(_c, dict) else {}
        if (
            isinstance(_c_, dict)
            and _c_.get("type") == "element_exists"
            and "main" in str(_c_.get("selector", ""))
        ):
            _c_["selector"] = feat_sel
            _c_["expect"] = "存在"


def resolve_intent(intent: dict, nodes: list, project: str = "") -> dict:
    """修正单个 intent 的导航目标 + 断言强化。返回原 intent（原地修改）。"""
    module = str(intent.get("module") or "")
    desc = str(intent.get("description") or "")
    req_id = str(intent.get("req_id") or "")
    case_id = str(intent.get("case_id") or "")
    text = f"{module} {desc} {req_id}"
    # ★ RC-E: 全局外壳文案（导航/侧边栏/登出）——页面特征元素必须排除它们，
    #   否则验证点取到"每个页面都有"的导航文案 → 假 PASS/假 FAIL（见 _chrome_texts）。
    chrome = _chrome_texts(nodes)
    actions = intent.get("actions")
    if not isinstance(actions, list):
        actions = []
        intent["actions"] = actions
    nav_idx = next(
        (
            i
            for i, a in enumerate(actions)
            if isinstance(a, dict) and a.get("type") == "navigate"
        ),
        None,
    )

    # ★ v5.10: 统一删除 LLM 生成的语义模糊断言（"页面显示XX相关功能"式，无法在浏览器
    #   事实验证）——url_matches + element_exists(main) + no_error 已构成可执行断言链。
    #   对所有用例生效（含登录类）。
    checks = intent.get("checks")
    if isinstance(checks, list):
        checks[:] = [
            c
            for c in checks
            if not (
                isinstance(c, dict)
                and isinstance(c.get("check", c), dict)
                and isinstance(c.get("check", c).get("expect"), str)
                and any(k in c["check"]["expect"] for k in _DROP_EXPECT_KW)
            )
        ]

    # 1. 登录/认证类 → 导航到真实登录入口（不做菜单清空，见 _login_entry_url）
    #    ★ 根因修复(2026-09-26): 只有需求**未点名真实菜单**时才按认证类清空导航 ——
    #    需求点名了系统真实页面（如"…对IT及办公资产进行检索…"→资产）即为权威证据，
    #    不得被"文本含登录二字"的关键词启发推翻（见 _menu_mentioned_in_text）。
    _mentioned = _menu_mentioned_in_text(text, nodes)
    if _is_auth(text) and _mentioned is None:
        # ★ 根因修复(2026-09-27)：认证类不再发射空导航目标（不可执行指令）。
        #   "无对应菜单"≠"无需导航"——登录入口是真实页面，必须给出可执行 URL，
        #   否则 Step4 结构化执行必败 → 降级 AI 探索 → HARNESS_ERROR。
        _login_url = _login_entry_url()
        if nav_idx is not None:
            actions[nav_idx] = {
                "type": "navigate",
                "url": _login_url,
                "target_hint": _login_url,
            }
        else:
            actions.insert(0, {
                "type": "navigate",
                "url": _login_url,
                "target_hint": _login_url,
            })
        # ★ v5.13: 登录类 main 断言替换为通用主内容容器选择器（覆盖任意布局：
        #   main / role=main / .content / #content / .main-content）。
        #   登录类用例不绑定具体页面特征（登录后落在哪一页取决于系统默认跳转，
        #   dashboard 特征按钮可能不在），登录成功由 Step4 login 前置保证。
        _replace_main_assertion(
            checks, "main, [role='main'], .content, #content, .main-content"
        )
        # ★ §4.17-B: 登录入口是真实页面 → 占位标注（若有）随之清除
        clear_nav_unresolved(intent)
        _fix_explore_task(intent, None, auth=True, chrome=chrome)
        return intent

    # 2. 其余需求 → 匹配真实菜单并覆写导航
    #    ★ 2026-09-26: 需求已点名真实菜单 → 直接采信该节点（确定性证据），
    #      不再过"文本相似度阈值"（长需求文本 vs 短菜单标题易低于阈值 → 丢导航）。
    #    ★ 根因修复：菜单归属的**第一证据是模块/操作主语**，描述只是叙述 ——
    #      描述里常出现别的页面的词（实测 REQ-025「保存编辑后资产」描述提到
    #      "…生成对应审计记录…" → 整文重叠把需求带到**审计**页，导航、动作、断言
    #      全部落在错误页面 → 结构性假 FAIL）。故先以模块匹配，无命中才回落整文。
    node = _mentioned or (
        _match_menu(module, nodes, project, case_id) if module else None
    ) or _match_menu(text, nodes, project, case_id)
    if node:
        url = str(node.get("page_url") or "")
        title = str(node.get("page_title") or "")
        if url:
            if nav_idx is not None:
                actions[nav_idx]["url"] = url
                actions[nav_idx]["target_hint"] = title
            else:
                actions.insert(
                    0, {"type": "navigate", "url": url, "target_hint": title}
                )
            # ★ v5.10(2026-08-04): 覆写 module 为真实菜单标题——
            #   Step4 Agent 执行走"方案B 侧边栏导航"（用 module 字段匹配侧边栏菜单），
            #   原始 module 带章节号（如"三、流水线执行"）导致匹配失败；
            #   覆写为菜单标题（如"流水线执行"）即可命中侧边栏。
            intent["module"] = title
            # 3. 断言强化：追加 url_matches（标准格式：外层 track/expect + check.pattern）
            checks = intent.get("checks")
            if not isinstance(checks, list):
                checks = []
                intent["checks"] = checks
            has_url = any(
                isinstance(c.get("check", c), dict)
                and c.get("check", c).get("type") == "url_matches"
                for c in checks
            )
            if not has_url and len(checks) < 6:
                checks.append(
                    {
                        "check": {
                            "type": "url_matches",
                            "pattern": url,
                            # ★ 根因修复(2026-09-25) RC-R: 本断言是**导航可达性**
                            #   断言（"让'导航到了正确页面'成为可验证断言"），语义为
                            #   "到达了该模块"。原用 exact 精确等值 → 在用例结束时核对
                            #   终态 URL，导致录入型（终态 /assets/new）与拒绝型
                            #   （期望"被拦截不提交"，终态**必须**停在表单页）用例
                            #   系统性假 FAIL，且与用例自身语义自相矛盾。
                            #   prefix = 目标路径本身或其子路径均视为已到达。
                            "precision": "prefix",
                        },
                        "track": "ui",
                        "expect": url,
                        "precision": "prefix",
                        "description": f"导航到 {title} 页面",
                    }
                )
            # ★ §4.17-B: 已有真实菜单事实解析成功 → 清除历史「未解析」标注
            clear_nav_unresolved(intent)
            # ★ v5.13(2026-08-04): 替换通用 main 断言为目标页特征元素断言——
            #   element_exists(main/.content/#content) 对无 main 布局的页面必失败
            #   （引擎 UI 用 div.main-content），用页面真实特征元素（按钮/输入框）替代。
            _replace_main_assertion(checks, _feature_selector(node, chrome))
    # ★ v5.11: 修正 _explore_task（Agent 实际消费的导航+验证目标）
    _fix_explore_task(
        intent, node if node and node.get("page_url") else None,
        auth=False, chrome=chrome,
    )
    # ★ §4.17-B(2026-10-28): 解析结束仍是"站点根占位" → 显式标注未解析。
    #   无论上游哪条生成路径（LLM/规则/兜底/外部导入）伪造了 "/"，都在此收口，
    #   不依赖各生产者各自记得标注（单一咽喉点，见 sanitize_intents）。
    if mark_nav_unresolved(intent, "no_menu_match"):
        _log.warning(
            "[NavResolver] 导航目标未解析(无菜单匹配): case=%s module=%r → 标注 nav_unresolved",
            case_id or "?",
            module[:30],
        )
    return intent


def resolve_intents_batch(
    intents: list, project: str, version_label: str = "v1.0"
) -> list:
    """批量修正（落库前调用）。

    ★ 2026-10-28(§4.17-B): 无导航数据时不再"静默跳过" —— 占位目标会被显式标注
    为未解析（``nav_unresolved``），使评审页与执行端都能看到"目标页未解析"，
    而不是把站点根当成目标页。
    """
    if not intents:
        return intents
    nodes = _load_menu_nodes(project, version_label)
    if not nodes:
        # 无导航事实 ≠ 无需导航：占位目标必须显式化，否则会原样落库成"导航到 /"。
        _marked = sum(
            1
            for it in intents
            if isinstance(it, dict) and mark_nav_unresolved(it, "nav_graph_empty")
        )
        _log.warning(
            "[NavResolver] 无 nav_graph_nodes，%d/%d 条用例导航目标标记为未解析(project=%s)",
            _marked,
            len(intents),
            project,
        )
        return intents
    fixed = 0
    unresolved = 0
    for it in intents:
        if not isinstance(it, dict):
            continue
        with guard("resolve_intents_batch: before = str(it.get('actions') or ''", log=_log, level=logging.WARNING):
            before = str(it.get("actions") or "")[:60]
            resolve_intent(it, nodes, project)
            after = str(it.get("actions") or "")[:60]
            if before != after:
                fixed += 1
        if it.get(NAV_UNRESOLVED_KEY):
            unresolved += 1
    _log.info(
        "[NavResolver] 导航解析完成: 修正 %d/%d 条意图，未解析 %d 条",
        fixed,
        len(intents),
        unresolved,
    )
    return intents
