# -*- coding: utf-8 -*-
"""★ P0-refactor(2026-07-29): 统一 Agent 执行器（从 _exploratory.py 拆分）

职责: 接收 Step3 的 _explore_task，调度 AgentExplorer 在浏览器中实时探索执行。
      不负责导航图构建（_exploratory._preflight_build_nav_graph）和模块导航（_exploratory._navigate_by_module）。

架构决策: 单一职责——Agent执行逻辑独立于预检/导航，可单独测试和复用。
通用性: 跨系统/行业/页面 — Agent实时感知不依赖静态快照。
"""

from __future__ import annotations
from src.core.guarded import guard

import logging
import re
import time
import urllib.parse
from typing import Any

from ._base_classify import _is_authoritative_requirement

_log = logging.getLogger(__name__)


def _is_login_op(operation: str) -> bool:
    """``operation`` 是否为登录动作场景（委托 Step3 单一事实源）。

    ★ RC-AG: 本文件此前自持一份关键词表 ``("登录","login","账号","密码")``
      （与 Step3 的 ``_LOGIN_SUBMIT_KEYWORDS`` 口径不同：多了"账号/密码"、少了
      "凭证/鉴权/认证/口令"）→ 典型的口径漂移。改委托 Step3
      ``_rule_extractor_keywords.is_login_operation``，全链路（身份指派 + 导航）同源。
    """
    try:
        from src.pipeline.step3._rule_extractor_keywords import is_login_operation

        return is_login_operation(operation)
    except Exception:  # noqa: BLE001
        return any(
            k in (operation or "")
            for k in ("登录", "login", "账号", "密码")
        )


def _cell(row, key: str, idx: int = 0):
    """兼容两种游标返回：db_manager 用的是 DictCursor（dict），裸连接是 tuple。"""
    try:
        if isinstance(row, dict):
            return row.get(key)
        return row[idx]
    except Exception:
        return None


def _origin_of(url: str) -> str:
    """把 URL 归一化为 scheme://host:port（用于同源比较）。"""
    try:
        from urllib.parse import urlparse as _up

        u = str(url or "").strip()
        if not u:
            return ""
        p = _up(u if "://" in u else "http://" + u)
        if not p.hostname:
            return ""
        _port = p.port or (443 if p.scheme == "https" else 80)
        return "%s://%s:%s" % (p.scheme, p.hostname, _port)
    except Exception:
        return ""


def _project_target_origin(project: str) -> str:
    """读项目配置的 TARGET_URL 并归一化为 origin。取不到则返回 ""（未知）。"""
    try:
        import json as _json

        from src.core.db_manager import get_connection

        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT config_json FROM project_configs WHERE project=%s LIMIT 1",
                (str(project).strip(),),
            ).fetchall()
        finally:
            with guard("_project_target_origin: conn.close()", log=_log):
                conn.close()
        if not rows:
            return ""
        _cfg = _cell(rows[0], "config_json", 0)
        if not _cfg:
            return ""
        if isinstance(_cfg, (bytes, bytearray)):
            _cfg = _cfg.decode("utf-8", "ignore")
        cfg = _json.loads(_cfg) if isinstance(_cfg, str) else dict(_cfg or {})
        return _origin_of(cfg.get("TARGET_URL") or cfg.get("target_url") or "")
    except Exception as _e:
        _log.warning(
            "[AgentExecutor] 读取项目 TARGET_URL 失败(视为未知): %s", str(_e)[:100]
        )
        return ""


def _project_has_nav_inventory(project: str) -> bool:
    """★ 证据基础守卫(2026-09-22 全量判定审计): 项目是否持有**本系统**页面清单。

    "需求覆盖缺口"FAIL 的证据是"在侧边栏/URL 中无对应实现"。该结论成立的前提
    = 引擎确实摄入过**该系统的**菜单图谱（nav_graph_nodes，Step1/1.5 感知产物）。
    若清单为空：Step3 的确定性导航解析（resolve_intents_batch）会整体跳过，
    module 保持需求文档章节标题原样（如"2. 用户与权限（安全需求）"），拿它对
    真实侧边栏做匹配失败只能证明"章节标题不是菜单名"，不能证明"需求未实现"。
    引擎环境缺口 ≠ 产品缺陷 —— 判决必须建立在证据之上（fail-closed 纪律）。

    ★  根因修复(D22)：**只判存在性是不够的**。
      实测 modsystem 的 nav_graph_nodes 3 行全部指向 http://127.0.0.1:8020/
      （page_title='SmartShop 商品管理'，元素是 SmartShop 的），而该项目的
      TARGET_URL 已是 http://127.0.0.1:8040（8020 早已无监听）。
      一份**陈旧且跨系统**的残留清单被当成"本系统菜单清单"，为"需求未实现"
      提供了伪证据基础 → 12 条假 Bug（REQ-008~011）。
      修复：清单必须与项目 TARGET_URL **同 origin** 才算有效证据。
      取不到 TARGET_URL（配置缺失）时保持原存在性语义，避免过度收窄。
    通用性: 跨系统/行业 — origin 与 nav 图谱都是通用元数据，零业务硬编码。
    """
    if not str(project or "").strip():
        return False
    try:
        # 跨版本直查：清单是否摄入过与版本号无关（read_nav_graph_nodes 需要
        # 精确 version_label，此处只判"存在性 + 同源性"）
        from src.core.db_manager import get_connection

        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT page_url FROM nav_graph_nodes WHERE project=%s",
                (str(project).strip(),),
            ).fetchall()
        finally:
            with guard("_project_has_nav_inventory: conn.close()", log=_log):
                conn.close()
        if not rows:
            return False

        _target_origin = _project_target_origin(project)
        if not _target_origin:
            # 靶子地址未知 → 无从校验同源，保持既有"存在即有效"语义
            return True

        _urls = [str(_cell(r, "page_url", 0) or "").strip() for r in rows]
        _urls = [u for u in _urls if u]
        if not _urls:
            # 清单没有可用 URL → 同样无从校验，保持既有语义
            return True

        _same = [u for u in _urls if _origin_of(u) == _target_origin]
        if not _same:
            _log.warning(
                "[AgentExecutor] ⚠️ nav_graph_nodes 与项目靶子不同源（清单=%s，"
                "TARGET_URL origin=%s，共 %d 行）→ 该清单不可作为'需求未实现'的证据，"
                "按无清单处理（交 Agent 语义自由探索）",
                _origin_of(_urls[0]),
                _target_origin,
                len(_urls),
            )
            return False
        return True
    except Exception as _e:
        _log.warning("[AgentExecutor] nav_graph_nodes 读取失败(视为无清单): %s", str(_e)[:80])
        return False


# 文档章节标签的形态特征（与业务无关，纯排版约定）：
#   ① 编号前缀 "2." / "10、" / "3)"  ② 括号后缀 "（安全需求）"/"(创建需求)"
_MODULE_HEADING_PREFIX_RE = re.compile(r"^\s*\d+\s*[.、)）]\s*")
_MODULE_HEADING_SUFFIX_RE = re.compile(r"\s*[（(][^）)]{0,12}[）)]\s*$")


def _module_is_known_nav_target(project: str, module: str) -> bool:
    """★ 根因修复(2026-09-24): module 标签是否**真实存在于系统菜单/页面清单**。

    根因（系统性假 FAIL）:
      Step2 需求拆解按设计把 `module` 落成**需求文档章节标题**
      （`_resolve_module_for_chunk` → `_sanitize_module_name`，实测 accept_sut_final
      68/68 条 module 形如 "2. 用户与权限（安全需求）" / "10. 接口清单"）。
      章节标题 ≠ 菜单名，`_navigate_by_module` 对它做匹配必然失败。
      原逻辑仅校验"项目有无同源菜单清单"（`_project_has_nav_inventory`），
      清单非空即认定"模块在侧边栏/URL 中确无对应项" → 判"需求未实现"。
      **这是把"章节标题不是菜单名"当成"需求未实现"的证据** → 引擎侧误判假 FAIL
      （实测 REQ-035/039/060/062 共 8 条）。

    通用判据（跨系统/行业/页面，零业务硬编码）:
      仅当 module 标签能在系统菜单清单（page_title/page_path）中找到对应项时，
      "导航失败"才具备"需求未实现"的证据力；否则标签只是文档分类，
      不构成任何实现性结论 → 交 Agent(LLM) 按需求语义自由探索判定。

    保守性: 取不到清单 → 返回 True（维持既有语义，不扩大豁免面）。
      空 module 标签 → 返回 **False**（空标签**不可能**存在于菜单清单里，
      不构成任何实现性证据；旧实现返回 True 会让调用方落到"模块 '' 在侧边栏/
      URL 中无对应实现"的 FAIL 分支 → 假 Bug，实测 accept_sut_0923
      TC-REQ-001-P01/B01/N01 三条权威需求被误判为"系统未实现"）。
    """
    _mod = str(module or "").strip()
    if not _mod:
        # ★ 根因修复(2026-09-26): 空标签 → 无证据可判（消息里连模块名都是空的），
        #   绝不能据此下"系统未实现"的结论 → 交 Agent 按需求语义自由探索。
        return False
    if not str(project or "").strip():
        return True
    try:
        from src.core.db_manager import get_connection

        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT page_title, page_path, page_url FROM nav_graph_nodes WHERE project=%s",
                (str(project).strip(),),
            ).fetchall()
        finally:
            with guard("_module_is_known_nav_target: conn.close()", log=_log):
                conn.close()
        if not rows:
            return True  # 无清单 → 不构成证据，但也不额外豁免（上游已按无清单处理）
        # 归一化 module：去编号前缀、去括号后缀
        _norm = _MODULE_HEADING_PREFIX_RE.sub("", _mod)
        _norm = _MODULE_HEADING_SUFFIX_RE.sub("", _norm).strip()
        _hay = " ".join(
            " ".join(
                str(_cell(r, k, i) or "")
                for i, k in enumerate(("page_title", "page_path", "page_url"))
            )
            for r in rows
        ).lower()
        if not _hay.strip():
            return True  # 清单无可用文本 → 无从匹配
        # 命中判据: 归一化标签整体命中，或其 ≥2 字切片命中
        if _norm and _norm.lower() in _hay:
            return True
        for _i in range(max(0, len(_norm) - 1)):
            _tok = _norm[_i : _i + 2]
            if len(_tok) == 2 and _tok.lower() in _hay:
                return True
        return False
    except Exception as _e:
        _log.warning("[AgentExecutor] module↔菜单清单比对失败(视为已知): %s", str(_e)[:80])
        return True


async def handle_agent_intent(
    engine,
    intent: dict[str, Any],
    case_meta: dict[str, Any],
    case_id: str,
    step_results: list,
    all_passed: bool,
    intent_t0: float,
    _session_login_failed: bool = False,
) -> dict[str, Any] | None:
    """★ P0-refactor(2026-07-27): 所有意图驱动用例统一走 AgentExplorer 实时探索。

    架构决策:
      Step3 生成 _explore_task（操作描述 + 目标页面 + 期望反馈），
      Step4 AgentExplorer 在浏览器中实时执行，不依赖静态 DOM 快照。
      取消"先爬全再测试"的感知-执行分离模式，改为 Agent 感知→决策→执行闭环。

    参数:
        engine: Playwright 浏览器引擎
        intent: 测试用例意图（含 _explore_task）
        case_meta: 用例元数据
        case_id: 用例ID
        step_results: 步骤结果列表（修改引用）
        all_passed: 当前是否全部通过
        intent_t0: 意图开始时间
        _session_login_failed: 登录是否失败

    返回:
        dict: 如果处理了 agent 意图，返回结果
        None: 不是 agent 意图（无 _explore_task），调用方继续标准流程（遗留兼容）
    """
    _explore_task = intent.get("_explore_task") or case_meta.get("_explore_task") or {}
    if not _explore_task or _session_login_failed:
        return None

    # ★ 声明动作运行期补齐：`_explore_task.declared_actions` 由 Step3 生成，
    #   但历史落库用例的 payload 里没有该字段（生成端彼时未下发）。
    #   用例声明的动作序列本就在同一 case dict 的 `case_actions_json`/`test_steps` 中，
    #   此处同源补齐，使修复对存量用例即时生效，不必重跑 Step3 重生成用例集。
    #   复用 Step3 的归一实现（语义单一实现，执行端不复制规则）。
    if not _explore_task.get("declared_actions"):
        with guard(
            "handle_agent_intent: attach declared actions",
            log=_log,
            level=logging.WARNING,
        ):
            from src.pipeline.steps.step3.case_generator._explore_task_builder import (
                _attach_declared_actions,
            )

            _attach_declared_actions(
                {
                    "_explore_task": _explore_task,
                    "case_actions_json": (
                        intent.get("case_actions_json")
                        or case_meta.get("case_actions_json")
                    ),
                    "test_steps": (
                        intent.get("test_steps") or case_meta.get("test_steps")
                    ),
                }
            )

    _op = _explore_task.get("operation", "") or ""
    _target_page = (_explore_task.get("target_page") or "").strip()
    _desc = _explore_task.get("description", "") or _op
    _module = _explore_task.get("module", "") or ""
    # ★  通用性修复：需求权威性单一事实源（_base_classify）。
    #   提前在导航前计算，供 ① 导航失败判决（FAIL 需求未实现 vs SKIP 非权威）
    #   ② execute_intent 强制实时执行（权威需求绝不允许 replay 跳过验证）。
    _is_authoritative = _is_authoritative_requirement(intent, case_meta)
    # ★ v5.4: 提取结构化测试方法（替代Agent自由探索）
    _test_method = _explore_task.get("test_method") or {}
    # ★ 拒绝型极性（denial）：由 Step3 权威判据**一次**判定后透传，执行端只读不推。
    #   拒绝型用例里"声明动作的目标不存在"是最小权限生效的证据而非 harness 失败，
    #   结论必须交用例自身的结构化 oracle —— 与「非权威信号不得否决权威 oracle」同纪律。
    _expects_denial = False
    with guard(
        "handle_agent_intent: case_expects_denial",
        log=_log,
        level=logging.WARNING,
    ):
        from src.pipeline.step3._identity_assign import case_expects_denial

        _expects_denial = bool(
            case_expects_denial(intent) or case_expects_denial(case_meta)
        )

    _log.info(
        "[AgentExecutor] ★ Agent执行★ case_id=%s op=%r target=%s has_test_method=%s",
        case_id,
        _op[:60],
        _target_page[:80],
        bool(_test_method),
    )

    try:
        from src.agent.explorer import get_explorer as _get_explorer
        from src.core.config_loader import config as _cfg
        from ._exploratory import _navigate_by_module, _verify_page_content_loaded

        _page = getattr(engine, "_page", None) or getattr(engine, "page", None)
        if not _page:
            _err = "浏览器页面对象不可用"
            _log.error("[AgentExecutor] %s", _err)
            step_results.append(
                {
                    "step_idx": 0,
                    "description": _desc,
                    "action": "agent",
                    "passed": False,
                    "error": _err,
                    "elapsed": 0.0,
                    "_hard_verified": False,
                    "_agent_driven": True,
                }
            )
            return {
                "case_id": case_id,
                "passed": False,
                "error": _err,
                "step_results": step_results,
                "elapsed": round(time.time() - intent_t0, 2),
                "_agent_driven": True,
            }

        # ── 0. 项目/版本信息（不再执行预检）──
        # ★ v5.16(2026-07-31): 删除预检调用
        #   根因(§10.9 5层why):
        #     L1: TC-REQ-001-I01超时300s → 预检120s+导航失败+Agent超时
        #     L2: 预检120s → 探索10个菜单项，逐个点击等待加载
        #     L3: 预检仅发现3个页面 → 侧边栏菜单不可点击/SPA路由问题
        #     L4: 预检对导航无帮助 → 发现的3个页面不含/dataManagement/brand
        #     L5: 根本原因——预检是"先爬全再测试"架构残留，与Agent实时探索矛盾。
        #         每条用例承受120s预检成本，但预检结果不可靠。
        #   修复: 删除预检调用。导航策略链已自包含——
        #         策略0(target_page URL)→策略1(实时侧边栏)→策略1b(nav_graph缓存)→智能纠错
        #         每个策略都实时探索侧边栏，不依赖预检的静态缓存。
        #   通用性: 跨系统/行业/框架 — Agent实时感知>静态预检缓存。
        #   _preflight_build_nav_graph函数保留，可被外部显式调用。
        _project = case_meta.get("project", "") or _cfg.project_name or ""
        _version_label = ""
        with guard("handle_agent_intent: from src.users.project_auth import g", log=_log, level=logging.WARNING):
            from src.users.project_auth import get_active_version_label as _gavl

            _version_label = _gavl(_project) or ""

        # ── 1. 导航到目标页面（★ 方案B v6: 侧边栏缓存+一键导航，失败即FAIL）──
        #   根因(§10.9 5层why):
        #     L1: TC-REQ-001-I01超时300s → 7层策略堆叠715s>300s
        #     L2: 策略1/2/3冗余 → 策略0已含_full_menu+关键词匹配，策略1重复_full_menu
        #     L3: retry+智能纠错浪费 → 导航应该一次正确，错了应FAIL而非重试
        #     L4: URL兜底有害 → AI生成的URL是幻觉，导航到404页面浪费Agent时间
        #     L5: 方案B应为"侧边栏缓存→关键词匹配→一键导航→Agent执行→失败即FAIL"
        #   修复: 删除策略1/2/3+retry+智能纠错。只保留策略0（侧边栏导航）。
        #   通用性: 跨系统/行业/框架 — 侧边栏是唯一可靠的导航方式，URL是AI幻觉。
        _navigated = False
        _nav_url = ""
        _explorer = _get_explorer()

        # ── 策略-1: 用例声明的权威导航目标（优先于一切文本推断）──
        #   根因(SSOT 违背): 用例已把动作序列落成结构化声明（case_actions_json），
        #     执行端却从 module/operation 文本重新猜"去哪" —— 声明的是业务页
        #     （target_hint="资产"），猜出来的是文档章节名（module="10. 接口清单"），
        #     两者不同源 → 导航到不存在的菜单 → 后续动作全部找不到元素 → ERROR。
        #   修复: 同一条语义只在上游判定一次。若用例声明了 navigate 动作，其
        #     url 或 target_hint 即权威导航目标，直接消费，跳过所有文本推断。
        #   通用性: 跨系统/行业 — 只依赖动作类型枚举与声明文本，零业务硬编码。
        _declared_nav = None
        for _da in _explore_task.get("declared_actions") or []:
            if isinstance(_da, dict) and _da.get("type") == "navigate":
                _declared_nav = _da
                break
        if _declared_nav and not _session_login_failed:
            _dn_url = str(_declared_nav.get("url") or "").strip()
            _dn_hint = str(_declared_nav.get("target_hint") or "").strip()
            # target_hint 也可能是 URL 形态（Step3 把 target_page 写成绝对 URL 或
            # 站内路径时）。URL 是确定性导航目标，不应交给菜单语义匹配（菜单里
            # 没有叫 "http://host/assets/" 的条目 → 必然未命中 → 白跑一次导航）。
            if not _dn_url and _dn_hint:
                if "://" in _dn_hint:
                    _dn_url = _dn_hint
                elif _dn_hint.startswith("/") and not _dn_hint.startswith("/api/"):
                    _dn_url = _cfg.target_url.rstrip("/") + "/" + _dn_hint.lstrip("/")
            if _dn_url:
                with guard(
                    "handle_agent_intent: declared navigate url",
                    log=_log,
                    level=logging.WARNING,
                ):
                    await engine.goto(_dn_url)
                    await _page.wait_for_load_state("networkidle", timeout=15000)
                    _navigated = True
                    _nav_url = _dn_url
                    _log.info(
                        "[AgentExecutor] ✅ 声明导航(url): %s", _dn_url[:100]
                    )
            elif _dn_hint:
                _dn_ok = await _navigate_by_module(
                    _page,
                    _dn_hint,
                    _cfg,
                    project=_project,
                    operation=_explore_task.get("operation", ""),
                    semantic_hint=_desc,
                )
                if _dn_ok:
                    _navigated = True
                    _nav_url = _page.url
                    _log.info(
                        "[AgentExecutor] ✅ 声明导航(菜单语义 %r): %s",
                        _dn_hint[:30],
                        _page.url[:100],
                    )
                else:
                    _log.warning(
                        "[AgentExecutor] 声明导航未命中(%r) → 回退文本推断",
                        _dn_hint[:30],
                    )

        # ★ v5(2026-08-08): 模块名含括号模型名 → 跳过侧边栏直接URL导航到模型页面
        #   根因(2026-08-08 5层why):
        #     L1: ModuleNav评分0.169（模块名"2.6 操作日志 (OperationLog)"匹配侧边栏"操作日志"）
        #     L2: 侧边栏匹配失败 → 走URL兜底，但每次用例都经历侧边栏失败
        #     L3: Django admin侧边栏(<nav id="nav-sidebar">)只在模型列表页渲染
        #     L4: 侧边栏匹配算法对带编号+括号模型名的模块名匹配不佳
        #     L5: 根本原因——模块名含括号模型名时，URL导航是确定性成功路径，
        #         侧边栏匹配是概率性失败路径，应先尝试确定性路径
        #   修复v5: 从module名中提取括号模型名，直接URL导航到模型页面，
        #     侧边栏导航作为非模型名模块的兜底策略
        #   通用性: 适用于所有Django admin系统，只需目标URL+model名即可
        # ★ : 跨系统通用性修复——admin_app_name 从配置读取，不再硬编码 shop
        #   根因: /admin/shop/{model_name}/ 中的 shop 是 SmartShop 的 Django app 名称，
        #   其他系统的 app 名称不同（如 erp / crm / portal 等），硬编码 shop 导致引擎
        #   无法在非 SmartShop 系统上使用 URL 导航。
        #   修复: 从 config 读取 admin_app_name，默认值 shop 向后兼容。
        #   通用性: 跨系统/行业 — 任何 Django admin 系统只需配置 admin_app_name 即可。
        # ★ 根因修复(2026-09-22): _model_name 原先只在 if _module: 块内赋值，
        #   模块名为空（Step3 现在会把文档标题/章节标题清空为通用导航）时
        #   L275 引用未定义变量 → UnboundLocalError → 整条用例 ERROR。
        #   属潜伏缺陷：旧建模从不产生空模块名，掩盖了它。统一预初始化。
        _model_name = ""
        # ★ 根因修复(2026-09-22 第三层): 模块名为空（文档标题/章节被清洗）时
        #   不得跳过导航——只要 operation/semantic_hint 有语义文本，就进入
        #   _navigate_by_module 的"规则匹配→operation实体匹配→AI语义菜单映射"
        #   三级链路按操作语义找菜单。空模块+空语义才真正无导航线索。
        _nav_semantic = (
            (_module or "").strip()
            or (_explore_task.get("operation") or "").strip()
            or (_desc or "").strip()
        )
        # ★ 根因修复(2026-09-25) RC-AG: 登录动作场景 → **显式导航到登录页**，跳过模块导航。
        #   根因（5 层 why）:
        #     L1 现象: TC-REQ-024-I01 / 058-I01 / 059-I01 报 "登录按钮未找到" HARNESS_ERROR。
        #     L2 直接: 旧"登录豁免"分支只**跳过导航**，假设"当前页就是登录页"；但浏览器
        #       带着默认身份（admin）的已认证会话停在受保护列表页 /assets/ → 页面上没有
        #       登录表单 → 登录按钮定位失败。
        #     L3 为何: 豁免只处理了"登录页无侧边栏 → 导航必失败"，未处理"如何**到达**登录页"。
        #     L4 为何反复: "登录页在哪"从未作为导航目标参与决策 —— 配置层
        #       `target_login_path`（RC-AB 已就绪，取自靶子 auth.login_url）在 Agent
        #       执行层无人消费。
        #     L5 根本: 登录场景缺"显式导航到登录页"的通用前置，凭"当前页=登录页"的假设执行。
        #   修复: 登录动作场景直接用配置的登录路径导航（未配置则维持原行为）；
        #     未认证态由 Step3 身份指派（RC-AG 同源，identity=anonymous）保证 —— 二者
        #     合起来才使"登录表单可达 + 未登录"成立。
        #   通用性: 登录路径来自靶子配置 auth.login_url，零业务硬编码。
        _is_login_scenario = _is_login_op(_explore_task.get("operation") or "")
        if _is_login_scenario:
            _login_path = (getattr(_cfg, "target_login_path", "") or "").strip()
            _login_target = ""
            if _login_path:
                _login_target = (
                    _login_path
                    if "://" in _login_path
                    else (_cfg.target_url.rstrip("/") + "/" + _login_path.lstrip("/"))
                )
            if _login_target:
                with guard("handle_agent_intent: await engine.goto(_login_target)", log=_log, level=logging.WARNING):
                    await engine.goto(_login_target)
                    await _page.wait_for_load_state("networkidle", timeout=15000)
                    _navigated = True
                    _nav_url = _login_target
                    _log.info(
                        "[AgentExecutor] 登录场景 → 显式导航到登录页: %s",
                        _login_target[:80],
                    )
            else:
                _log.info(
                    "[AgentExecutor] 登录场景 → 未配置登录路径(target_login_path)，在当前页执行"
                )
        if _nav_semantic and not _is_login_scenario and not _navigated:
            import re as _model_re
            _model_match = _model_re.search(r"\((\w+)\)", _module)
            _model_name = _model_match.group(1).lower() if _model_match else ""
            if _model_name and _cfg.target_url:
                # ★ v10: 从配置读取 admin_app_name，默认 shop 向后兼容
                _admin_app = getattr(_cfg, 'admin_app_name', 'shop') or 'shop'
                # ★ 模型名存在 → 直接URL导航到模型页面（确定性成功路径）
                _try_admin_url = _cfg.target_url.rstrip("/") + f"/admin/{_admin_app}/{_model_name}/"
                _log.info(
                    "[AgentExecutor] v5: 模块名含模型名=%s → 直接URL导航到 %s",
                    _model_name,
                    _try_admin_url[:80],
                )
                try:
                    await engine.goto(_try_admin_url)
                    await _page.wait_for_load_state("networkidle", timeout=15000)
                    _navigated = True
                    _nav_url = _try_admin_url
                    _log.info(
                        "[AgentExecutor] ✅ URL导航到模型页面成功: %s",
                        _try_admin_url[:80],
                    )
                except Exception as _admin_e:
                    _log.warning(
                        "[AgentExecutor] URL导航到模型页面失败: %s → 尝试侧边栏导航",
                        str(_admin_e)[:80],
                    )
                    # URL失败 → 回退到侧边栏导航
                    _module_nav_ok = await _navigate_by_module(
                        _page,
                        _module,
                        _cfg,
                        project=_project,
                        operation=_explore_task.get("operation", ""),
                        semantic_hint=_desc,
                    )
                    if _module_nav_ok:
                        _navigated = True
                        _nav_url = _page.url
                        _log.info("[AgentExecutor] ✅ 侧边栏导航成功(URL回退): %s", _page.url[:100])
                    else:
                        _log.warning("[AgentExecutor] 侧边栏导航也失败 → 自由探索模式")
                        _explore_task["_fallback_explore"] = True
                        _explore_task["_nav_failed_module"] = _module
            else:
                # ★ 无模型名 → 尝试侧边栏导航
                _log.info("[AgentExecutor] 模块名无括号模型名，尝试侧边栏导航 module=%s", _module[:60])
                _module_nav_ok = await _navigate_by_module(
                    _page,
                    _module,
                    _cfg,
                    project=_project,
                    operation=_explore_task.get("operation", ""),
                    semantic_hint=_desc,
                )
                if _module_nav_ok:
                    _navigated = True
                    _nav_url = _page.url
                    _log.info("[AgentExecutor] ✅ 侧边栏导航成功: %s", _page.url[:100])
                else:
                    # ★ v5.1(2026-08-08): 侧边栏导航失败 → 尝试 target_page URL 回退
                    #   仅当 target_page 是合法浏览器路径（非 API 路径）时使用
                    # ★ 根因修复(2026-09-25) RC-F1: target_page 落库形态是**绝对 URL**。
                    #   根因: Step3 把 _explore_task.target_page 写成 "http://host/path"，
                    #     而原判据要求 `_tp.startswith("/")` → 绝对 URL 被判无效 →
                    #     已知的确定性导航目标被丢弃 → 直接落入"导航失败"分支 →
                    #     被误判"需求未实现"（实测 TC-REQ-019/023/032/049-E01 共 4 条假 FAIL）。
                    #   通用修复: 绝对 URL 直接作为导航目标（同源/异源皆适用）；相对路径
                    #     维持与 TARGET_URL 拼接的原语义。
                    #   通用性: 跨系统/行业/页面 — 纯 URL 归一，零业务硬编码。
                    _tp = str(_target_page or "").strip()
                    _tp_direct = _tp if "://" in _tp else ""
                    _tp_is_valid = bool(_tp_direct) or (
                        bool(_tp) and not _tp.startswith("/api/") and _tp.startswith("/")
                    )
                    if _tp_is_valid and (_tp_direct or _cfg.target_url):
                        _try_url = _tp_direct or (_cfg.target_url.rstrip("/") + _tp)
                        _log.info(
                            "[AgentExecutor] v5.1: 侧边栏失败 → target_page URL回退 %s",
                            _try_url[:80],
                        )
                        try:
                            await engine.goto(_try_url)
                            await _page.wait_for_load_state("networkidle", timeout=15000)
                            # ★ 2026-08-28 BLOCKED/SKIP 语义: 规则导航全失败后，
                            #   URL 回退若仅到达站点根路径（path="/"）→ 未进入任何
                            #   业务功能页 = 需求功能在系统中不存在（无对应菜单/路由）
                            #   → 不视为导航成功（让下方 SKIP 分支标记"功能不存在"），
                            #   避免"在首页验证需求文本"的假 ERROR。
                            #   通用性: 跨系统/行业 — 根路径回退无业务实体。
                            _try_path = urllib.parse.urlparse(_try_url).path or "/"
                            if _try_path.rstrip("/") in ("", "/"):
                                _log.warning(
                                    "[AgentExecutor] 导航失败且URL回退仅到根路径(%s) → 需求功能不存在",
                                    _try_url[:60],
                                )
                                _explore_task["_fallback_explore"] = False
                            else:
                                _navigated = True
                                _nav_url = _try_url
                                _log.info(
                                    "[AgentExecutor] ✅ target_page URL回退成功: %s",
                                    _try_url[:80],
                                )
                        except Exception as _tp_e:
                            _log.warning(
                                "[AgentExecutor] target_page URL回退也失败: %s → 自由探索模式",
                                str(_tp_e)[:80],
                            )
                            _explore_task["_fallback_explore"] = True
                            _explore_task["_nav_failed_module"] = _module
                    else:
                        _log.warning(
                            "[AgentExecutor] ⚠️ 侧边栏导航失败 module=%s target_page=%s → 自由探索模式",
                            _module[:60],
                            _tp[:60] if _tp else "(无)",
                        )
                        _explore_task["_fallback_explore"] = True
                        _explore_task["_nav_failed_module"] = _module

        # ★ : 通用性修复——模块名无括号模型名 + 导航全部失败时，
        #   标记为SKIP而非进入自由探索（自由探索必然失败，浪费资源且产生误报ERROR）。
        #   通用性：任何非Django Admin模块（如需求来源标识符）都适用此逻辑。
        if not _navigated and not _model_name:
            # ★ 2026-08-28 登录类豁免: 登录用例的"页面"就是登录页本身，
            #   无需侧边栏导航（登录页无侧边栏 → 导航必失败 → 误判功能不存在）。
            #   登录操作直接在初始页执行（SessionAware 已处理"当前页面无需登录"）。
            #   通用性: 跨系统 — 登录是任何系统的第一场景。
            # ★ RC-AG(2026-09-25): 判据改委托 Step3 单一事实源 `_is_login_op`
            #   （模块级，见文件头），消除此处第二份关键词表（登录/login/账号/密码）漂移。
            if _is_login_scenario:
                _log.info(
                    "[AgentExecutor] 登录类用例跳过导航，直接在登录页执行: %s",
                    (_explore_task.get("operation") or "")[:40],
                )
            else:
                # ★  需求权威性语义修复（替代旧 v5.3/v SKIP）:
                #   需求比实际系统更权威 —— 用例是否 SKIP 应按"需求权威性"判决，而非
                #   "导航是否成功"。需求已定义(is_implicit=0)但系统未实现/不可达 =
                #   需求满足性缺口 → FAIL（真实缺陷，非引擎问题）。
                #   仅无权威需求背书（无 req_id / is_implicit=1 隐含 / AI 虚构 / 环境
                #   不可达）才 SKIP。
                #   通用性: 跨系统/行业 — req_id + is_implicit 是通用需求元数据。
                # ★  通用性修复：权威性已在导航前经单一事实源
                #   _is_authoritative_requirement 计算（见 L60-63），此处仅取
                #   req_id 供失败文案引用，判决一律复用该结果——杜绝路径间不一致。
                _req_id = str(
                    intent.get("req_id")
                    or _explore_task.get("req_id")
                    or _explore_task.get("_req_id")
                    or case_meta.get("req_id")
                    or ""
                ).strip()
                # ★  行为型安全需求豁免（通用根因修复）:
                #   security_behavior 场景断言的是"访问控制行为"（登录墙/重定向/403/
                #   仅授权角色可见），其可观察性不依赖"模块页是否可达"——任意页面/
                #   HTTP 层都可验证。模块导航失败 ≠ 需求未实现，判"需求覆盖缺口"
                #   属误判。豁免后交 Agent 自由探索 + 行为断言评估（AI 判定）。
                #   通用性: 场景标记由 Step3 结构化写入 test_method.op_type，
                #   Step4 只读标记，零业务硬编码、零关键词漂移。
                _tm = _explore_task.get("test_method") or {}
                _is_security_behavior_case = _tm.get("op_type") == "security_behavior" or any(
                    (v or {}).get("target") == "访问控制行为"
                    for v in (_tm.get("verifications") or [])
                )
                # ★ 证据基础守卫(2026-09-22): 只对权威需求判一次清单存在性
                _has_nav_inventory = (
                    _project_has_nav_inventory(_project) if _is_authoritative else True
                )
                # ★ 根因修复(2026-09-24): 清单存在还不够 —— module 标签必须**本身
                #   是菜单项**，"导航失败"才具备"需求未实现"的证据力。
                #   根因: Step2 按设计把 module 落成文档章节标题（68/68 条），
                #     章节标题永远匹配不上菜单 → 清单非空即误判"需求未实现"（8 条假 FAIL）。
                #   见 _module_is_known_nav_target 文档。
                _module_is_nav = (
                    _module_is_known_nav_target(_project, _module)
                    if (_is_authoritative and _has_nav_inventory)
                    else True
                )
                if _is_security_behavior_case:
                    _log.info(
                        "[AgentExecutor] 行为型安全需求豁免导航判决 → Agent 自由探索评估行为断言: %s",
                        (_explore_task.get("operation") or "")[:40],
                    )
                elif str(
                    intent.get("identity") or case_meta.get("identity") or ""
                ).strip():
                    # ★ 根因修复(2026-09-25) RC-F2: per-case 身份 → 菜单可见性可能不同。
                    #   根因: nav_graph_nodes 由 run 级默认身份（admin）采集；受限身份
                    #     （viewer/anonymous）看不到受限菜单项（如"审计"）→ 侧边栏导航必
                    #     失败 → 被当作"需求未实现"证据 → 假 FAIL（实测 TC-REQ-019/023/
                    #     032/049-E01：需求明确要求"拒绝访问/403"，系统实现正确却判未实现）。
                    #   通用修复: 用例声明了 per-case 身份时，"侧边栏无对应项"不构成实现性
                    #     证据 → 交 Agent 语义探索 + 行为断言评估（行为可观察性不依赖菜单）。
                    #   通用性: 跨系统/行业 — 身份声明是通用元数据，零业务硬编码。
                    _log.warning(
                        "[AgentExecutor] ⚠️ 用例声明 per-case 身份 %r，菜单可见性可能与清单"
                        "采集会话不同 → 模块匹配失败不构成'需求未实现'证据: case=%s module=%r",
                        str(intent.get("identity") or case_meta.get("identity"))[:20],
                        case_id,
                        _module[:40],
                    )
                    _explore_task["_fallback_explore"] = True
                    _explore_task["_nav_failed_module"] = _module
                elif _is_authoritative and (not _has_nav_inventory or not _module_is_nav):
                    # ★ 根因修复(2026-09-22 全量判定审计): 权威需求但项目从未摄入
                    #   nav_graph_nodes —— Step3 导航解析整体跳过，module 还是文档
                    #   章节标题；"侧边栏无对应项"只证明"章节标题≠菜单名"，
                    #   不能证明"需求未实现"。引擎环境缺口 ≠ 产品缺陷 → 不判
                    #   FAIL/SKIP，交 Agent(LLM) 按需求语义自由导航验证（架构铁律:
                    #   理解交给 AI）。不 return，继续走下方 AgentExplorer 执行。
                    # ★ 2026-09-24 扩展: 清单存在但 module 标签不在清单内（文档章节
                    #   分类标签）时同理 —— 标签不构成实现性证据。
                    _log.warning(
                        "[AgentExecutor] ⚠️ %s，模块匹配失败不构成'需求未实现'证据 → "
                        "Agent 语义自由探索: case=%s module=%r",
                        "项目无 nav_graph_nodes(菜单清单未摄入)" if not _has_nav_inventory
                        else "module 标签非系统菜单项(文档章节分类)",
                        case_id, _module[:40],
                    )
                    _explore_task["_fallback_explore"] = True
                    _explore_task["_nav_failed_module"] = _module
                elif _is_authoritative:
                    # 权威需求 + 项目确有页面清单（_has_nav_inventory=True）——
                    # 模块在真实侧边栏/URL 中确无对应项，"需求未实现"有证据基础
                    _fail_reason = (
                        f"需求已定义但系统未实现/不可达: 需求 {_req_id} 功能(模块 "
                        f"'{_module[:40]}') 在侧边栏/URL 中无对应实现，需求未得到满足"
                        "（需求权威>实际系统 → 判定为需求覆盖缺口）。"
                    )
                    _log.error("[AgentExecutor] ❌ FAIL(需求未实现): %s", _fail_reason[:130])
                    return {
                        "case_id": case_id,
                        "passed": False,
                        "error": _fail_reason,
                        "skip_reason": "",
                        "step_results": [
                            {
                                "step_idx": 0,
                                # 报告语义：description 承载"预期操作"，
                                # 判定理由/未实现说明放入 error，避免 expected 被 verdict 文本污染。
                                "description": (
                                    _explore_task.get("description")
                                    or f"执行 {f'{_module[:40]} ' if _module else ''}{_op[:60]}"
                                    or "执行需求操作"
                                ),
                                "action": "assert",
                                "passed": False,
                                "error": _fail_reason,
                                "expected": _req_id,
                                "elapsed": 0.0,
                                "_agent_driven": False,
                                "_skip_target_system_issue": False,
                                "_req_unimplemented": True,
                            }
                        ],
                        "elapsed": round(time.time() - intent_t0, 2),
                        "_agent_driven": False,
                        "_req_unimplemented": True,
                    }
                # 非权威需求（无 req_id / 隐含 / AI 虚构 / 环境不可达）→ 保留 SKIP
                else:
                    _skip_reason = (
                        f"需求功能在系统中不存在或不可达: 模块 '{_module[:60]}' "
                        "在侧边栏/URL 中无对应项（非权威/隐含需求或系统未实现），跳过执行。"
                    )
                    _log.warning("[AgentExecutor] ⚠️ SKIP(非权威需求): %s", _skip_reason[:120])
                    return {
                    "case_id": case_id,
                    "passed": True,
                    "error": "",
                    "skip_reason": _skip_reason,
                "step_results": [
                    {
                        "step_idx": 0,
                        "description": f"SKIP: {_skip_reason[:100]}",
                        "action": "skip",
                        "passed": True,
                        "error": _skip_reason,
                        "elapsed": 0.0,
                        "_agent_driven": False,
                        "_skip_target_system_issue": True,
                    }
                ],
                "elapsed": round(time.time() - intent_t0, 2),
                "_agent_driven": False,
                "_skipped": True,
            }

        # ── 2. AgentExplorer 实时探索执行 ──
        # ★ 方案B v6: 超时从180s降到120s（导航已用缓存，Agent有充足时间）
        _timeout_ms = min(getattr(_cfg, "step4_case_timeout_sec", 180), 120) * 1000

        _guide = _explore_task.copy()
        if _test_method:
            _guide["_execution_guide"] = {
                "mode": "guided",
                "navigation": _test_method.get("navigation", ""),
                "inputs": _test_method.get("inputs", {}),
                "verifications": _test_method.get("verifications", []),
                # ★ 操作类型透传(2026-09-24): guided 执行需据此为"表单录入型"需求
                #   补上进入表单的入口点击（operation 无新增/编辑动词时）。
                "op_type": _test_method.get("op_type", ""),
                # ★ 用例声明的权威动作序列（Step3 `_explore_task.declared_actions`）。
                #   执行端只读不推：声明的 click 目标是「点什么」的唯一事实源，
                #   优先于从 operation 文本推断出的句子片段。
                "declared_actions": _explore_task.get("declared_actions") or [],
                # ★ 用例是否声明了**结构化断言**（checks）= 其权威 oracle。
                #   执行端据此判断：当自然语言验证点是无机器可判期望值的弱 oracle
                #   （仅关键词子词匹配、且运行期零 AI 下语义断言不可用）时，
                #   该验证点降级为非判定态，把结论交给结构化 checks ——
                #   与引擎既有的「弱证据不判 PASS」对称，弱证据同样不判 FAIL。
                "structured_checks": bool(
                    intent.get("checks") or case_meta.get("checks")
                ),
                # ★ 用例是否断言「被拒绝/不可达」（权威判据已在上游判定）。
                "expects_denial": _expects_denial,
            }
            _log.info(
                "[AgentExecutor] 使用test_method引导执行: nav=%s inputs=%d verifications=%d",
                _test_method.get("navigation", "")[:40],
                len(_test_method.get("inputs", {})),
                len(_test_method.get("verifications", [])),
            )
        else:
            _guide["_execution_guide"] = {"mode": "explore"}
            _log.info("[AgentExecutor] 无test_method，Agent自由探索模式")

        _explore_result = await _explorer.execute_intent(
            _page,
            _guide,
            timeout_ms=_timeout_ms,
            project=_project,
            version_id=0,
            # ★  通用性修复：权威需求强制实时执行（skip_replay=True），
            #   绝不允许 op_cache_v2 replay 跳过"成功后断言"→ SKIP(REPLAY_NO_ACTION)
            #   掩盖需求满足性。权威需求必须每次真实执行业务动作并验证 → PASS/FAIL。
            #   仅非权威/隐含/AI 虚构需求允许 replay 优化。
            skip_replay=_is_authoritative,
        )

        # ★ 方案B v6: 不重试、不纠错。Agent失败=FAIL。导航已在策略0完成，Agent应能执行。
        #   如果Agent找不到按钮，说明导航到了错误页面→应该FAIL，暴露问题而非掩盖。

        # ── 3. 记录结果 ──
        _success = _explore_result.get("success", False)
        _evidence = _explore_result.get("evidence", "")
        _actions_taken = _explore_result.get("actions_taken", [])
        _strategy = _explore_result.get("strategy", "explore")
        _error = _explore_result.get("error", "")

        # ★ : 经验复用修复——cache 命中/未命中写回 intent 字典
        #   根因: explorer.execute_intent 内部查 op_cache_v2，命中时返回 strategy="replay"
        #   但命中/未命中计数从未写回 intent 字典。_confidence_gate.py 读 intent.get("op_cache_hits", 0)
        #   永远为 0 → cache_hit_rate 恒为 0.0。
        #   修复: 根据 strategy 判断 cache 是否命中，写回 intent 字典。
        #   通用性: 跨系统/行业 — 所有 Agent 执行路径都经过此函数，不依赖特定系统类型。
        if _strategy == "replay":
            intent["op_cache_hits"] = intent.get("op_cache_hits", 0) + 1
            _log.info("[AgentExecutor] ★ v10: op_cache_v2 命中→intent.op_cache_hits=%d",
                       intent["op_cache_hits"])
        else:
            intent["op_cache_misses"] = intent.get("op_cache_misses", 0) + 1

        _exhaustive_search_failed = (
            not _success
            and _strategy == "explore"
            and ("已遍历" in _error or "无匹配" in _error or "nav_map" in _error)
        )

        if _success:
            _log.info(
                "[AgentExecutor] ✅ 成功: strategy=%s evidence=%s",
                _strategy,
                _evidence[:150],
            )
            # ★ v5.4 R8: 构建_verify_result供evidence_extractor提取三轨evidence
            _vr = _build_verify_result(_evidence, _actions_taken, _strategy, True)
            step_results.append(
                {
                    "step_idx": 0,
                    "description": f"Agent执行: {_desc}",
                    "action": "agent",
                    "passed": True,
                    "error": "",
                    "elapsed": 0.0,
                    "_hard_verified": True,
                    "_agent_driven": True,
                    "_agent_evidence": _evidence,
                    "_agent_actions": _actions_taken,
                    "_agent_strategy": _strategy,
                    "_agent_navigated": _navigated,
                    # ★ 2026-09-28: guided 实时判定结构化记录透传，供 checks 层
                    #   上游一次判定/下游只读采信（防流程结束后重查冷冻字面量假 FAIL）
                    "_guided_verify_records": _explore_result.get("verify_records") or [],
                    "_verify_result": _vr,
                }
            )
            all_passed = True
        else:
            # ★ v5.4: 构建具体失败信息（基于test_method验证点）
            _fail_detail = _error or "Agent执行失败"
            _verify_fails = _explore_result.get("verification_failures") or []
            if _verify_fails:
                _fail_parts = ["Agent执行失败，以下验证点未通过:"]
                for _vf in _verify_fails:
                    _vp = _vf.get("point", "?") if isinstance(_vf, dict) else str(_vf)
                    _fail_parts.append("  - %s" % _vp[:80])
                _fail_detail = "\n".join(_fail_parts)
            elif _test_method:
                _nav = _test_method.get("navigation", "")
                _inputs = _test_method.get("inputs", {})
                _verifs = _test_method.get("verifications", [])
                _fail_detail = (
                    "Agent执行失败: 导航路径=%s, 输入字段=%d个, 验证点=%d个, " "错误=%s"
                ) % (_nav[:40], len(_inputs), len(_verifs), (_error or "未知")[:120])

            _log.warning(
                "[AgentExecutor] ❌ 失败: strategy=%s error=%s exhaustive=%s",
                _strategy,
                _fail_detail[:200],
                _exhaustive_search_failed,
            )
            # ★ v5.4 R8: 构建_verify_result供evidence_extractor
            _vr = _build_verify_result(
                _evidence, _actions_taken, _strategy, False, _fail_detail
            )
            step_results.append(
                {
                    "step_idx": 0,
                    "description": f"Agent执行: {_desc}",
                    "action": "agent",
                    "passed": False,
                    "error": _fail_detail or "Agent执行失败",
                    "elapsed": 0.0,
                    "_hard_verified": False,
                    "_agent_driven": True,
                    "_agent_evidence": _evidence,
                    "_agent_actions": _actions_taken,
                    "_agent_strategy": _strategy,
                    "_agent_navigated": _navigated,
                    "_dom_has_element": False if _exhaustive_search_failed else None,
                    "_verify_result": _vr,
                }
            )
            all_passed = False

    except Exception as _e:
        _log.error("[AgentExecutor] 异常: %s", str(_e)[:200], exc_info=True)
        step_results.append(
            {
                "step_idx": 0,
                "description": f"Agent异常: {_desc}",
                "action": "agent",
                "passed": False,
                "error": f"Agent执行异常: {str(_e)[:150]}",
                "elapsed": 0.0,
                "_hard_verified": False,
                "_agent_driven": True,
            }
        )
        all_passed = False

    # ── 4. 返回统一结果 ──
    _elapsed_total = round(time.time() - intent_t0, 2)
    return {
        "case_id": case_id,
        "passed": all_passed,
        "error": (
            ""
            if all_passed
            else (step_results[-1].get("error", "") if step_results else "")
        ),
        "step_results": step_results,
        "elapsed": _elapsed_total,
        "_agent_driven": True,
    }


def _build_verify_result(
    evidence: str,
    actions_taken: list,
    strategy: str,
    passed: bool,
    error: str = "",
) -> dict[str, Any]:
    """★ v5.4 R8: 从Agent执行证据构建_verify_result。

    使evidence_extractor能提取三轨evidence，避免fallback_no_verify。
    通用性: 跨系统/行业/页面 — 将Agent执行证据映射为统一三轨格式。

    ★ : 真实性修复——API/DB 轨不再硬编码 passed=True。
    根因: 原代码硬编码 api.passed=True 和 db.passed=True，即使没有实际验证。
    在 SSR 系统（如 Django Admin）中，API 调用在服务端不可观测，DB 访问需要
    独立连接。硬编码 passed=True 导致 assertion_rate 被虚高，引擎声称验证了
    但实际上没验证。这是"假PASS"的根因。
    修复: 添加 verified 字段标记各轨是否真正得到验证。UI 轨始终可验证（Agent
    执行结果），API 轨标记为 NOT_AVAILABLE（浏览器上下文无 API 调用记录），
    DB 轨标记为 NOT_AVAILABLE（未配置 DB 直接访问）。
    通用性: 跨系统/行业/页面 — 任何 SSR 系统或未配置 DB 的系统都适用此逻辑。
    verified=False 的轨在被置信度计算器消费时跳过，不计入 assertion_rate。
    """
    _actions_summary = (
        "; ".join(
            a.get("type", "") + ":" + a.get("target", a.get("target_hint", ""))[:40]
            for a in (actions_taken or [])[:10]
        )
        if actions_taken
        else "无操作"
    )

    return {
        "ui": {
            "verified": True,  # UI 始终可验证
            "passed": passed,
            "evidence": "[PASS] Agent(%s): %s" % (strategy, evidence[:300])
            if passed
            else "Agent(%s): %s" % (strategy, evidence[:300]),
        },
        "api": {
            "verified": False,  # ★ v9: NOT_AVAILABLE — SSR系统API不可观测
            "passed": False,
            "evidence": "NOT_AVAILABLE: SSR系统, 浏览器上下文无API调用记录",
        },
        "db": {
            "verified": False,  # ★ v9: NOT_AVAILABLE — 未配置DB直接访问
            "passed": False,
            "evidence": "NOT_AVAILABLE: 未配置DB直接访问",
        },
        "summary": "Agent执行%s (%s)"
        % (
            "成功" if passed else "失败: %s" % (error or "未知")[:80],
            strategy,
        ),
    }
