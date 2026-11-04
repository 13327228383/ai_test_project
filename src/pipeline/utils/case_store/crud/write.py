# -*- coding: utf-8 -*-
"""
CaseStore 写入 mixin

职责：
- upsert_intents / delete_intent / update_intent / clear_project_intents
"""

import json
from src.core.guarded import guard
import logging
from datetime import datetime
from typing import Any

from .._helpers import _to_json

_log = logging.getLogger(__name__)

# ★ 通用修复(2026-07-19): 缓存 nav_map 合法 URL 集合，避免每次校验都查库
_VALID_URLS_CACHE = None


def _derive_expected_outcome(intent: dict) -> str:
    """从意图取「真实期望结果」（D10 根因修复，2026-09-23）。

    背景：原实现 `exp = str(intent.get("title") or "")` 把 expected_outcome 写死为
    用例标题，Step3 生成的真实期望（`test_method.verifications[*].expect`，来自
    `case_rules/base.py` 的场景期望三级推导）在持久化边界被整体丢弃 —— 实测
    98/98 条用例 expected_outcome 恒等于标题，报告「预期结果」无意义，
    Step3 few_shot 的少样本期望样本也退化为标题。

    优先级：显式期望 > 意图期望 > test_method.verifications 期望 > 标题兜底。
    通用性：不依赖业务/模块名，任何携带工程期望字段的意图均适用。
    """
    for _k in ("expected_outcome", "expected", "expect"):
        _v = intent.get(_k)
        if _v is not None and str(_v).strip():
            return str(_v).strip()
    _tm = intent.get("test_method")
    if isinstance(_tm, str) and _tm.strip():
        try:
            _tm = json.loads(_tm)
        except Exception:
            _tm = None
    _exps: list = []
    if isinstance(_tm, dict):
        _top = str(_tm.get("expected", "") or "").strip()
        if _top:
            _exps.append(_top)
        for _v in _tm.get("verifications") or []:
            if not isinstance(_v, dict):
                continue
            _e = str(_v.get("expect", "") or _v.get("expected", "") or "").strip()
            if _e and _e not in _exps:
                _exps.append(_e)
    if _exps:
        return "；".join(_exps)
    # 最后兜底：标题（保留原有语义，仅在无任何期望时使用）
    return str(intent.get("title") or "")


def _load_valid_urls(project: str, conn) -> set:
    """从 system_nav_maps + page_dom_snapshots 加载当前项目的合法 URL 集合。

    ★ 通用修复(2026-07-19): nav_map 可能不完整（SPA 应用中 DOM 爬取的页面
    如 /dashboard 未被 Confluence 条目覆盖），因此同时从 page_dom_snapshots
    加载实际爬取过的页面 URL 作为合法 URL。通用性：任何被 DOM 爬虫实际访问
    过的页面都是合法导航目标，不依赖 nav_map 完整性。
    ★ 通用修复(2026-07-20): 同时读取 nav_map 中的 path 字段（DOM 爬取器存储
    完整URL路径的地方，含query参数如 /xxx?tab=yyy），之前只读 url 字段导致
    path 字段中的合法URL被忽略，AI填入后被 _validate_action_urls 清空。
    """
    global _VALID_URLS_CACHE
    if _VALID_URLS_CACHE is not None:
        return _VALID_URLS_CACHE
    try:
        urls = set()
        from urllib.parse import urlparse as _up

        # ★ 通用修复(2026-07-20): 统一URL提取函数（与 _contexts.py 保持一致）
        # 优先 path 字段（DOM爬取器存储完整URL），回退 url 字段
        def _extract_url(it: dict) -> str:
            if not it:
                return ""
            _p = (it.get("path", "") or "").strip()
            if _p and (_p.startswith("/") or _p.startswith("http") or "?" in _p):
                return _p
            _u = (it.get("url", "") or "").strip()
            if _u and (_u.startswith("/") or _u.startswith("http") or "?" in _u):
                return _u
            return ""

        def _add_url_to_set(url: str):
            """将URL加入合法集合（同时处理带query和不带query的版本）"""
            if not url:
                return
            _path = url
            if "://" in _path:
                _path = _up(_path).path or _path
            urls.add(_path)
            if "?" in _path:
                urls.add(_path.split("?")[0])

        # 1. 从导航图加载 URL（同时读取 path 和 url 字段）
        # ★ P0-1 读侧迁移(2026-08-05): system_nav_maps.nav_json → nav_graph。
        #   根因(严重): 该表无 nav_json 列 → `SELECT nav_json` 恒抛 Unknown column
        #   → 被本函数外层 except 捕获 → _VALID_URLS_CACHE=set() → 调用方
        #   _validate_action_urls 判定 `not valid_urls` 后**直接放行全部 URL**，
        #   即"URL 幻觉校验"长期处于完全失效状态且无任何告警。
        from src.pipeline.utils.nav_graph_read import read_nav_map

        nav = read_nav_map(project)
        for item in nav.get("flat_list", []):
            _add_url_to_set(_extract_url(item))
        for mg in nav.get("menu_groups", []):
            for item in mg.get("items", []):
                _add_url_to_set(_extract_url(item))
            # ★ 通用修复(2026-07-20): 同时处理 tab_items（Tab 页面URL在path字段）
            for tab_item in mg.get("tab_items", []):
                _add_url_to_set(_extract_url(tab_item))

        # ★ 通用修复(2026-07-19): 从 page_dom_snapshots 加载实际爬取的页面 URL
        #   nav_map 可能不完整（SPA 中 /dashboard 等页面未被 Confluence 覆盖），
        #   但 DOM 爬虫实际访问过的页面一定是合法导航目标。
        # ★ 通用修复(2026-07-20): 同时注入系统 base_url 作为合法导航目标，
        #   Step3 新 prompt 要求首动作 navigate 到首页，base_url 必须被认可。
        # ★ 根因修复(2026-09-27): 同时注入靶子**登录入口**（config.target_login_path，
        #   桥接靶子注册表 auth.login_url）。登录页是系统真实可执行页面，却**没有菜单
        #   节点**（侧边栏无"登录"项）→ 不在 nav_map/nav_graph_nodes 中 → 被
        #   `_validate_action_urls` 误判为幻觉 URL 并把 url 清空（实测日志
        #   `[URL校验] 移除幻觉URL: /login`）→ Step3 导航解析器按 _login_entry_url()
        #   正确写入的 url="/login" 在落库网关被抹掉、只剩 target_hint →
        #   Step4 判"导航失败或元素未找到" → 认证类用例整体 HARNESS_ERROR
        #   （实测 TC-REQ-005-E01/I01、006-I01、045、052、083）。
        #   与 base_url 同源同理：**真实入口 ≠ 幻觉**，判据统一来自配置/靶子注册表，
        #   零业务硬编码，跨系统/行业/页面通用。
        with guard("_load_valid_urls: from src.core.config_loader import c", log=_log):
            from src.core.config_loader import config as _cfg

            for _entry in (
                str(getattr(_cfg, "target_url", "") or "").strip(),
                str(getattr(_cfg, "target_login_path", "") or "").strip(),
            ):
                if not _entry:
                    continue
                _ep = _up(_entry).path if "://" in _entry else _entry
                if _ep:
                    urls.add(_ep)
                    if not _ep.endswith("/"):
                        urls.add(_ep + "/")
                if "://" in _entry:
                    urls.add(_entry)

        # ★ P0-1 读侧迁移(2026-08-05): page_dom_snapshots(0 行) → nav_graph_nodes
        from src.pipeline.utils.nav_graph_read import read_dom_pages

        _dom_rows = read_dom_pages(project)
        _dom_count = 0
        for _dr in _dom_rows:
            _dom_url = (_dr.get("page_url") or "").strip()
            if _dom_url:
                _path = _dom_url
                if "://" in _path:
                    _path = _up(_path).path or _path
                if _path and _path not in urls:
                    urls.add(_path)
                    _dom_count += 1
                if "?" in _path:
                    _base = _path.split("?")[0]
                    if _base not in urls:
                        urls.add(_base)
                        _dom_count += 1

        _VALID_URLS_CACHE = urls
        _log.info(
            "[URL校验] 加载 %d 个合法URL (nav_map=%d, dom=%d, project=%s)",
            len(urls),
            len(urls) - _dom_count,
            _dom_count,
            project,
        )
        return urls
    except Exception as _e:
        # 注意: 返回空集会让 _validate_action_urls 整体放行，等于关闭 URL 幻觉校验。
        # 因此这里必须 error 级 + 堆栈，绝不能静默。
        _log.error(
            "[URL校验] 合法URL集加载失败 project=%s，URL幻觉校验将失效: %s",
            project,
            _e,
            exc_info=True,
        )
        _VALID_URLS_CACHE = set()
        return _VALID_URLS_CACHE


def _validate_action_urls(actions: list, project: str, conn) -> list:
    """校验 actions 中的 url 字段，不在 nav_map 中的设为空字符串。"""
    if not actions:
        return actions
    with guard("_validate_action_urls: valid_urls = _load_valid_urls(projec", log=_log, level=logging.WARNING):
        valid_urls = _load_valid_urls(project, conn)
        if not valid_urls:
            return actions
        import re as _re_url

        fixed_count = 0
        for action in actions:
            if not isinstance(action, dict):
                continue
            url = (action.get("url") or "").strip()
            if not url:
                continue
            # 中文文本不是 URL（AI 幻觉：将页面名填入 url 字段）
            if _re_url.search(r"[\u4e00-\u9fff]", url):
                _log.warning("[URL校验] 移除中文幻觉URL: %s", url)
                action["url"] = ""
                fixed_count += 1
                continue
            # 检查 URL 是否在合法集合中（支持相对路径和全路径匹配）
            _path = url
            if "://" in _path:
                from urllib.parse import urlparse as _up

                _path = _up(_path).path or _path
            if _path not in valid_urls:
                base_url = _path.split("?")[0] if "?" in _path else _path
                if base_url not in valid_urls:
                    _log.warning("[URL校验] 移除幻觉URL: %s (不在 nav_map 中)", url)
                    action["url"] = ""
                    fixed_count += 1
        if fixed_count:
            _log.info("[URL校验] 修正 %d 个幻觉URL", fixed_count)
    return actions


def _query_execution_fail_count(conn, case_id: str) -> int:
    """★ A4(2026-08-16): 查询 executions 表同 case_id 历史 FAIL 次数。

    复用 upsert_intents 已持有的数据库连接（conn 即游标），避免重复建连。
    任何异常均回退 0，不阻塞落库。
    """
    if not conn or not case_id:
        return 0
    try:
        conn.execute(
            "SELECT COUNT(*) FROM executions WHERE case_id=%s AND status='FAIL'",
            (str(case_id),),
        )
        _row = conn.fetchone()
        return int(_row[0]) if _row else 0
    except Exception as _e:
        _log.debug("[CaseStore] 历史失败计数查询失败(回退0): %s", _e)
        return 0


class _VisWriteMixin:
    """测试用例写入操作。"""

    def upsert_intents(
        self, intents: list[dict[str, Any]], case_asset_file: str = ""
    ) -> bool:
        if not intents:
            return True
        from src.core.config_loader import config

        project = config.project_name or ""
        from src.core.versioning import current_version_id, assert_version_writable

        # ★ PV-012: 版本状态检查，archived/deleted 版本拒绝写入
        if not assert_version_writable(project):
            _log.warning(
                "[CaseStore] 版本不可写入，拒绝 upsert %d 条测试用例", len(intents)
            )
            return False
        active_version_id = current_version_id(project)
        # ★ §4.17-B(2026-10-28): 导航目标解析同样必须在**唯一持久化咽喉点**强制。
        #   根因: 此前只有 Step3 建模出口调用 `resolve_intents_batch` —— 导入/自愈回填/
        #     重试脚本等其它生产者绕过它，就能把占位导航（无 url，target="/"）写进库，
        #     落库后必然渲染/执行成"导航到 /"（实测 550/555 条用例首步如此）。
        #   与指令契约（sanitize_case_checks）同一原则: 边界强制，新增生产者自动受约束。
        #   幂等: 已解析的用例二次通过为零变更；无导航事实时显式标注 nav_unresolved。
        with guard("upsert_intents: resolve_intents_batch", log=_log, level=logging.WARNING):
            from src.core.versioning import current_version_label as _cvl
            from src.pipeline.step3._navigation_resolver import (
                resolve_intents_batch as _rinb,
            )

            _rinb(intents, project, _cvl(project) or "")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c = None
        try:
            with self._lock:
                c = self._get_db()
                try:
                    self._ensure_tables(c)
                    # ★ 极性单一事实源(2026-10-14)：把需求的 expected_feedback 落到用例上。
                    #   下游极性闸门 _check_guard.sanitize_case_checks（no_error 反转）**只读**
                    #   case.expected_feedback，而生成端从不写该字段、expected_result 又由 checks
                    #   反推（恒"系统无报错"）→ 闸门对"需求期望拒绝"失明 → SUT 按需求正确渲染
                    #   拒绝提示反被判 FAIL（实测 REQ-002/003-P01）。
                    #   位置：唯一持久化咽喉点 —— 规则/兜底/Excel 回填/导入等所有生产者自动受约束。
                    _ef_map: dict = {}
                    with guard("upsert_intents: 读 requirements.expected_feedback", log=_log):
                        _cur = c.cursor()
                        try:
                            _cur.execute(
                                "SELECT req_id, expected_feedback, operation, description "
                                "FROM requirements "
                                "WHERE project=%s AND version_id=%s",
                                (project, active_version_id),
                            )
                            # ★ 期望反馈必须经**唯一实现** `resolve_expected_feedback` 收口：
                            #   库里的值可能是旧版本按 operation 关键词推断的产物
                            #   （REQ-040「登录失败」却存"登录成功,欢迎"、REQ-042 只读分页
                            #   却存"操作成功"）→ 原样落到用例上会让断言与需求反向 →
                            #   系统性假 FAIL。此处不得再"自己解释"该字段。
                            from src.pipeline.step3._rule_extractor_keywords import (
                                resolve_expected_feedback as _resolve_ef,
                            )

                            for _row in _cur.fetchall() or []:
                                if isinstance(_row, dict):
                                    _rid2 = _row.get("req_id")
                                    _ef2 = _row.get("expected_feedback")
                                    _op2 = _row.get("operation") or ""
                                    _de2 = _row.get("description") or ""
                                else:
                                    _rid2 = _row[0]
                                    _ef2 = _row[1]
                                    _op2 = _row[2] if len(_row) > 2 else ""
                                    _de2 = _row[3] if len(_row) > 3 else ""
                                if not _rid2:
                                    continue
                                _ef_resolved = _resolve_ef(
                                    str(_op2 or ""), str(_ef2 or ""), str(_de2 or "")
                                )
                                if _ef_resolved:
                                    _ef_map[str(_rid2)] = _ef_resolved
                        finally:
                            _cur.close()
                    for intent in intents:
                        if not isinstance(intent, dict):
                            continue
                        cid = str(intent.get("case_id", "") or "").strip()
                        if not cid:
                            continue
                        req_id = str(intent.get("req_id", "") or "").strip()
                        # 极性前置：闸门只读该字段，生成端不写 → 在落库边界按需求补齐
                        if req_id and not intent.get("expected_feedback"):
                            _ef_v = _ef_map.get(req_id)
                            if _ef_v:
                                intent["expected_feedback"] = _ef_v
                        # ★ 指令契约落库边界强制（2026-09-27 通用化）——
                        #   根因: 该守卫此前只在 Step3 建模出口（_modeling_intents）调用，
                        #     **不是真正的落库边界**：任何其它生产者（自愈修复
                        #     consistency_helpers.repair 的 Excel→MySQL 回填、外部导入、
                        #     重试/修复脚本）直调本函数即可把"执行端无法分派的指令"
                        #     （check 无 type 的非指令 / 契约外 action）写进 test_cases，
                        #     落库后 Step4 必判 UNSUPPORTED_CHECK 或静默略过 → 假 FAIL/假 PASS。
                        #   原则: 契约必须在**唯一持久化咽喉点**强制，而非在某个调用点强制——
                        #     这样新增任何生产者都自动受约束，无需再逐处打补丁。
                        #   守卫幂等（Step3 已清洗过的用例二次通过为零变更），故双端调用安全；
                        #   异常 fail-open 但留痕（不阻断建模主流程，与原 Step3 调用一致）。
                        with guard("upsert_intents: from src.pipeline.step3._check_guard", log=_log, level=logging.ERROR):
                            from src.pipeline.step3._check_guard import (
                                sanitize_case_checks,
                            )

                            sanitize_case_checks(intent)
                        # ★ 根因修复（2026-09-23）D10：expected_outcome 曾被写死为 title
                        #   （原注释："expected_outcome = title（对齐 AI prompt 输出字段）"）
                        #   → Step3 生成的**真实期望**（test_method.verifications[*].expect，
                        #   由 case_rules/base.py 的场景期望三级推导产出）在持久化边界被整体
                        #   丢弃。实测 98/98 条用例的 expected_outcome 恒等于用例标题，
                        #   报告「预期结果」无意义、Step3 few_shot 的期望样本也是标题。
                        #   修复：按 显式期望 > 意图期望 > test_method 内 verify 期望 > 标题
                        #   的优先级取值；标题仅作最后兜底（保留原有兜底语义）。
                        exp = _derive_expected_outcome(intent)
                        # ★ case_actions_json = actions 序列化（对齐 AI prompt 输出，不再写死 "{}"）
                        _actions = intent.get("actions") or []
                        if isinstance(_actions, str):
                            try:
                                _actions = json.loads(_actions)
                            except Exception:
                                _actions = []
                        # ★ 通用修复(2026-07-19): URL 幻觉校验
                        #   Step3 AI 可能生成 nav_map 中不存在的 URL（如 /dataQuality/rule）
                        #   校验每个 action 的 url 字段，不在 nav_map 中的设为空，防止 Step4 导航到 404 页面
                        _actions = _validate_action_urls(_actions, project, c)
                        case_actions_json = json.dumps(_actions, ensure_ascii=False)
                        # ★ 通用修复(2026-07-20): payload_json 必须在 URL 校验后生成，
                        #   确保 payload_json 与 case_actions_json 中的 url 一致。
                        payload_json = _to_json(intent)
                        # ★ A4(2026-08-16): 风险驱动优先级推导并填充 priority
                        #   仅当用例未显式携带 priority 时推导；推导依据（信号命中）
                        #   通过 debug 日志可追溯。history_fail_count 取自 executions 表。
                        _prio = str(intent.get("priority") or "").strip()
                        if not _prio:
                            try:
                                from src.pipeline.steps.step3.case_generator._risk_priority import (
                                    derive_priority,
                                    priority_label,
                                )

                                _fail = _query_execution_fail_count(c, cid) if c else 0
                                _prio_val, _reasons = derive_priority(intent, _fail)
                                _prio = priority_label(_prio_val)
                                _log.debug(
                                    "[CaseStore] 推导 priority: case=%s → %s (reasons=%s)",
                                    cid,
                                    _prio,
                                    ",".join(_reasons) if _reasons else "-",
                                )
                            except Exception as _pe:
                                _log.debug("[CaseStore] 优先级推导失败(回退空): %s", _pe)
                                _prio = ""
                            intent["priority"] = _prio  # 回填便于审计/回溯
                        c.execute(
                            """
                            INSERT INTO test_cases(
                                case_id, project, version_id, origin_case_id, origin_version_id,
                                case_source, valid_status, req_id, case_asset_file, title, case_type, risk_level,
                                case_actions_json, expected_outcome, test_method, control_type,
                                precondition, test_steps, expected_result, priority,
                                payload_json,
                                created_at, updated_at
                            )
                            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            ON DUPLICATE KEY UPDATE
                                version_id=VALUES(version_id),
                                origin_case_id=VALUES(origin_case_id),
                                origin_version_id=VALUES(origin_version_id),
                                case_source=VALUES(case_source),
                                valid_status=VALUES(valid_status),
                                req_id=VALUES(req_id),
                                case_asset_file=VALUES(case_asset_file),
                                title=VALUES(title),
                                case_type=VALUES(case_type),
                                risk_level=VALUES(risk_level),
                                case_actions_json=VALUES(case_actions_json),
                                expected_outcome=VALUES(expected_outcome),
                                test_method=VALUES(test_method),
                                control_type=VALUES(control_type),
                                precondition=VALUES(precondition),
                                test_steps=VALUES(test_steps),
                                expected_result=VALUES(expected_result),
                                priority=VALUES(priority),
                                payload_json=VALUES(payload_json),
                                updated_at=VALUES(updated_at)
                            """,
                            (
                                cid,
                                project,
                                intent.get("version_id", active_version_id) or 0,
                                str(intent.get("origin_case_id", "") or ""),
                                (
                                    intent.get("origin_version_id")
                                    if intent.get("origin_version_id") not in ("", None)
                                    else 0
                                ),
                                str(
                                    intent.get("case_source", "generated")
                                    or "generated"
                                ),
                                str(intent.get("valid_status", "valid") or "valid"),
                                req_id,
                                case_asset_file
                                or str(intent.get("case_asset_file", "") or ""),
                                str(intent.get("title", "") or ""),
                                str(
                                    intent.get("case_type", intent.get("type", ""))
                                    or ""
                                ),
                                str(intent.get("risk_level", "") or ""),
                                case_actions_json,
                                exp,
                                str(intent.get("test_method", "") or ""),
                                str(intent.get("control_type", "") or ""),
                                str(intent.get("precondition", "") or ""),
                                str(intent.get("test_steps", "") or ""),
                                str(intent.get("expected_result", "") or ""),
                                _prio,
                                payload_json,
                                now,
                                now,
                            ),
                        )
                    c.commit()
                    # ★ 清理 case_meta 中不在 test_cases 的孤儿记录（防御性同步）
                    with guard("upsert_intents: c.execute( ''' DELETE cm FROM case_m", log=_log):
                        c.execute(
                            """
                            DELETE cm FROM case_meta cm
                            LEFT JOIN test_cases tc
                              ON cm.case_id = tc.case_id
                              AND cm.project = tc.project
                              AND cm.version_id = tc.version_id
                            WHERE cm.project=%s AND cm.version_id=%s
                              AND tc.case_id IS NULL
                            """,
                            (project, active_version_id),
                        )
                        _orphan = c.rowcount
                        if _orphan > 0:
                            c.commit()
                            _log.info(
                                "[Store] 清理 case_meta 孤儿记录 %d 条 (project=%s v=%s)",
                                _orphan,
                                project,
                                active_version_id,
                            )
                finally:
                    if c:
                        with guard("upsert_intents: c.close()", log=_log, level=logging.WARNING):
                            c.close()
            self._last_error = ""
            return True
        except Exception as e:
            self._last_error = str(e)
            _log.error("[Store] 测试用例写入失败: %s", e)
            return False

    def delete_intent(self, case_id: str) -> bool:
        from src.core.config_loader import config

        project = config.project_name or ""
        from src.core.versioning import current_version_id

        active_version_id = current_version_id(project)
        c = None
        try:
            with self._lock:
                c = self._get_db()
                try:
                    self._ensure_tables(c)
                    c.execute(
                        "DELETE FROM test_cases WHERE case_id=%s AND project=%s AND version_id=%s",
                        (str(case_id), project, active_version_id),
                    )
                    c.commit()
                finally:
                    if c:
                        with guard("delete_intent: c.close()", log=_log, level=logging.WARNING):
                            c.close()
            self._last_error = ""
            return True
        except Exception as e:
            self._last_error = str(e)
            _log.error("[Store] delete_intent 删除失败: %s", e)
            return False

    def update_intent(self, case_id: str, updates: dict[str, Any]) -> bool:
        case_id = str(case_id or "").strip()
        if not case_id:
            self._last_error = "empty case_id"
            return False
        if not isinstance(updates, dict) or not updates:
            return True
        from src.core.config_loader import config

        project = config.project_name or ""
        from src.core.versioning import current_version_id

        active_version_id = current_version_id(project)
        allowed = {
            "title",
            "case_type",
            "risk_level",
            "precondition",
            "test_steps",
            "expected_result",
            "req_id",
            "case_asset_file",
            "checks",
            "actions",
            "intent_summary",
            "priority",
        }
        current = self.get_intent(case_id) or {}
        payload = dict(current.get("payload") or {})
        for k, v in updates.items():
            if k in allowed:
                payload[k] = v
        if "payload_json" in updates:
            with guard("update_intent: payload = ( json.loads(updates['payl", log=_log, level=logging.WARNING):
                payload = (
                    json.loads(updates["payload_json"])
                    if updates["payload_json"]
                    else payload
                )
        payload_json = _to_json(payload)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c = None
        try:
            with self._lock:
                c = self._get_db()
                try:
                    self._ensure_tables(c)
                    c.execute(
                        """
                        INSERT IGNORE INTO test_cases(case_id, project, req_id, case_asset_file, title,
                            version_id, case_type, risk_level, case_actions_json, expected_outcome, payload_json,
                            created_at, updated_at)
                        VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            case_id,
                            project,
                            str(updates.get("req_id", "") or ""),
                            str(updates.get("case_asset_file", "") or ""),
                            str(updates.get("title", "") or ""),
                            active_version_id,
                            str(updates.get("case_type", "") or ""),
                            str(updates.get("risk_level", "") or ""),
                            json.dumps(
                                updates.get("actions") or [], ensure_ascii=False
                            ),
                            str(updates.get("expected_outcome", "") or ""),
                            payload_json,
                            now,
                            now,
                        ),
                    )
                    set_parts = []
                    args = []
                    if "title" in updates:
                        set_parts.append("title=%s")
                        args.append(str(updates["title"] or ""))
                    if "case_type" in updates:
                        set_parts.append("case_type=%s")
                        args.append(str(updates["case_type"] or ""))
                    if "risk_level" in updates:
                        set_parts.append("risk_level=%s")
                        args.append(str(updates["risk_level"] or ""))
                    if "req_id" in updates:
                        set_parts.append("req_id=%s")
                        args.append(str(updates["req_id"] or ""))
                    if "case_asset_file" in updates:
                        set_parts.append("case_asset_file=%s")
                        args.append(str(updates["case_asset_file"] or ""))
                    if "priority" in updates:
                        set_parts.append("priority=%s")
                        args.append(str(updates["priority"] or ""))
                    set_parts.append("payload_json=%s")
                    args.append(payload_json)
                    # ★ 同步更新 case_actions_json（对齐 payload.actions）
                    _act = payload.get("actions") or []
                    if isinstance(_act, str):
                        try:
                            _act = json.loads(_act)
                        except Exception:
                            _act = []
                    set_parts.append("case_actions_json=%s")
                    args.append(json.dumps(_act, ensure_ascii=False))
                    set_parts.append("updated_at=%s")
                    args.append(now)
                    args.extend([case_id, project, active_version_id])
                    _SAFE_COLS = {
                        "title",
                        "case_type",
                        "risk_level",
                        "req_id",
                        "case_asset_file",
                        "precondition",
                        "test_steps",
                        "expected_result",
                        "payload_json",
                        "case_actions_json",
                        "priority",
                        "updated_at",
                    }
                    safe_parts = [p for p in set_parts if p.split("=")[0] in _SAFE_COLS]
                    if not safe_parts:
                        self._last_error = "no valid columns to update"
                        return True
                    sql = (
                        "UPDATE test_cases SET "
                        + ", ".join(safe_parts)
                        + " WHERE case_id=%s AND project=%s AND version_id=%s"
                    )
                    c.execute(sql, tuple(args))
                    c.commit()
                finally:
                    if c:
                        with guard("update_intent: c.close()", log=_log, level=logging.WARNING):
                            c.close()
            self._last_error = ""
            return True
        except Exception as e:
            self._last_error = str(e)
            _log.error("[Store] update_intent 失败: %s", e)
            return False

    def clear_project_intents(self) -> bool:
        from src.core.config_loader import config

        project = config.project_name or ""
        from src.core.versioning import current_version_id

        active_version_id = current_version_id(project)
        c = None
        try:
            with self._lock:
                c = self._get_db()
                try:
                    self._ensure_tables(c)
                    c.execute(
                        "DELETE FROM test_cases WHERE project=%s AND version_id=%s",
                        (project, active_version_id),
                    )
                    c.execute(
                        "DELETE FROM case_meta WHERE project=%s AND version_id=%s",
                        (project, active_version_id),
                    )
                    c.commit()
                finally:
                    if c:
                        with guard("clear_project_intents: c.close()", log=_log, level=logging.WARNING):
                            c.close()
            self._last_error = ""
            return True
        except Exception as e:
            self._last_error = str(e)
            _log.error("[Store] clear_project_intents 清空失败: %s", e)
            return False
