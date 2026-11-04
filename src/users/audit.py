# -*- coding: utf-8 -*-
"""
用户审计模块 — 执行跟踪
从 manager.py 拆分。
"""

import logging
from src.core.guarded import guard
from datetime import datetime
from src.users.user_db import _conn

_log = logging.getLogger(__name__)


def _active_version_id(project: str) -> int:
    try:
        from src.core.versioning import current_version_id

        return current_version_id(project)
    except Exception as e:
        _log.warning("获取活跃版本ID异常: %s", e, exc_info=True)
        return 0


def _ensure_execution_user(conn, user_id: str) -> str:
    """确保 executions.user_id 外键指向的用户存在。

    执行记录可能在无登录上下文（脚本/后台任务）中生成，此时 user_id 可能为空
    或为 'anonymous'/'system' 等兜底值。若该用户不存在，自动创建 'system'
    系统兜底账号，避免外键约束导致执行记录丢失。
    """
    user_id = str(user_id or "").strip() or "system"
    with guard("_ensure_execution_user: row = conn.execute('SELECT 1 FROM us", log=_log):
        row = conn.execute("SELECT 1 FROM users WHERE id = %s", (user_id,)).fetchone()
        if row:
            return user_id

    # 传入用户不存在，fallback 到 system
    if user_id != "system":
        with guard("_ensure_execution_user: row = conn.execute( 'SELECT 1 FROM u", log=_log):
            row = conn.execute(
                "SELECT 1 FROM users WHERE id = %s", ("system",)
            ).fetchone()
            if row:
                return "system"

    # 创建 system 兜底账号（占位密码，不可登录）
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO users(id, name, password, status, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            ("system", "系统账号", "0" * 64, "active", now, now),
        )
        _log.info("[audit] 自动创建 system 兜底用户")
        return "system"
    except Exception as e:
        _log.warning("[audit] 创建 system 兜底用户失败: %s", e)
        return user_id


# ★ 根因修复（2026-09-23）run_id 空串静默写失败 —— 单一收口点
#   问题：browser_core 的 serial.py 4 处用 `getattr(self, "_current_run_id", "")`
#     取 run_id，但全仓从未给 BrowserCore 赋该实例属性 → 恒为空串。结果 executions
#     里写入的 BLOCKED/PASS 等行 run_id=''，所有按 run_id 的查询（Step5 报告、
#     完成判定、指标聚合）全部看不见 → 实测「登录失败批量写 BLOCKED: 32/32」日志
#     声称成功、DB 却零命中，形成「假完成」。
#   策略（ponytail：只改共享函数，不逐调用点打补丁）：record_execution 作为所有
#     执行记录的唯一写入口，在 run_id 为空时按权威来源自愈，并**显式告警**，
#     绝不再静默写入空 run_id。
_run_id_heal_logged: set = set()


def _resolve_run_id(project: str, run_id: str) -> str:
    """转调全仓唯一实现 src.core.versioning.resolve_run_id（避免第三份重复逻辑）。"""
    try:
        from src.core.versioning import resolve_run_id

        return str(resolve_run_id(project, run_id) or "").strip()
    except Exception as e:  # noqa: BLE001
        _log.warning("[audit] resolve_run_id 调用失败，退回显式值: %s", e)
        return str(run_id or "").strip()


def _upsert_execution(conn, cols: str, ph: str, params: list, run_id: str,
                      project: str, case_id: str, check_type: str) -> int:
    """幂等写执行记录：同一 run+case 已存在则 UPDATE 覆盖（后者胜出），否则 INSERT。

    ★ 流程层去重（2026-08-24）：同一用例在一次 run 内可能经多条策略路径执行
      （如 SKIP→SKIP→PASS），若每次 INSERT 会残留失败态，污染分布与 KPI
      （实测 SmartShop TC-REQ-001-I01 三行）。改为按 (run_id, case_id) 取最终态。

    边界：run_id 为空（脚本/无 run 上下文）时不幂等，退化为普通 INSERT，
    避免跨 run 的同 case 记录被意外覆盖。
    """
    cur = conn.execute(
        "SELECT id FROM executions "
        "WHERE run_id=%s AND project=%s AND case_id=%s AND check_type=%s",
        (run_id, project, case_id, check_type),
    ).fetchone()
    if cur and run_id:
        existing_id = cur["id"] if isinstance(cur, dict) else cur[0]
        # UPDATE SET 各列显式绑定参数（列名即 cols 用逗号切出的字段名，值取 params 顺序）；
        # executed_at 随最近一次写入更新为最新，保证"后者胜出"语义。
        col_names = [col.split(" ")[0] for col in cols.split(",")]
        set_clause = ", ".join("%s=%%s" % col for col in col_names)
        conn.execute(
            "UPDATE executions SET %s WHERE id=%%s" % set_clause,
            tuple(params) + (existing_id,),
        )
        conn.commit()
        return existing_id
    cur = conn.execute(
        "INSERT INTO executions(%s) VALUES(%s)" % (cols, ph), tuple(params)
    )
    eid = cur.lastrowid
    conn.commit()
    return eid


def record_execution(
    user_id: str,
    project: str,
    case_id: str,
    status: str,
    case_asset_file: str = "",
    case_row=None,
    screenshot: str = "",
    failure_reason: str = "",
    run_id: str = "",
    failure_category: str = "",
    failure_chain: str = "",
    check_type: str = "",
    expected: str = "",
    actual: str = "",
    details: str = "",
    confidence: float = None,
    verification_track: str = "",
    cross_cut_dims: str = "",
) -> int:
    """记录一次执行（原子写入），返回记录 ID。

    参数:
        failure_category: 失败归属分类
            PRODUCT_BUG  = 被测系统bug（元素不存在/位置错误/行为不符合需求）
            HARNESS_ERROR = 执行系统bug（定位失败/浏览器异常/框架错误）
            ENVIRONMENT_ERROR = 环境问题（网络/服务不可用）
            DATA_ERROR = 数据问题（测试数据异常）
        failure_chain: 失败链路JSON，记录策略执行序列和每步结果
        check_type: 检查类型（pre_gate/browser_exec/api_exec/step_exec）
        expected: 期望值（断言期望，用于失败可分析性）
        actual: 实际值（断言实际结果）
        details: 详情JSON（check 级别明细，补充 failure_chain）
    """
    # 防御性转换：case_row 可能是空字符串或 None
    try:
        case_row = int(case_row) if case_row not in (None, "") else 0
    except (ValueError, TypeError):
        case_row = 0
    # ★ 修复割裂：case_id strip 防御，避免带空格的 case_id 写入导致与 test_cases 不匹配
    case_id = str(case_id or "").strip()
    user_id = str(user_id or "").strip()
    project = str(project or "").strip()
    # ★ 根因修复（2026-09-23）: run_id 空串自愈 + 显式告警（详见 _resolve_run_id 注释）
    _raw_run_id = str(run_id or "").strip()
    run_id = _resolve_run_id(project, run_id)
    if not _raw_run_id and run_id:
        if run_id not in _run_id_heal_logged:
            _run_id_heal_logged.add(run_id)
            _log.warning(
                "[audit] 调用方未传 run_id，已按权威来源归一为 %s（project=%s）；"
                "修复前该记录会以 run_id='' 落库，对一切按 run_id 的查询不可见",
                run_id,
                project,
            )
    elif not run_id:
        _log.error(
            "[audit] run_id 无法解析（project=%s case=%s check=%s）→ 执行记录将以 "
            "run_id='' 落库，run 维度查询不可见，请检查 run 上下文是否丢失",
            project,
            case_id,
            check_type,
        )
    c = None
    try:
        c = _conn()
        # ★ 通用修复：确保 executions.user_id 外键指向的用户存在（脚本/后台任务无登录上下文时）
        user_id = _ensure_execution_user(c, user_id)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            from src.core.versioning import current_version_id

            active_version_id = current_version_id(project)
        except Exception:
            _log.warning("获取当前版本ID失败", exc_info=True)
            active_version_id = 0
        cols = "run_id,version_id,user_id,project,case_asset_file,case_row,case_id,status,screenshot,failure_reason,failure_category,failure_chain,check_type,expected,actual,details,executed_at"
        ph = "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s"
        params: list = [
            run_id,
            active_version_id,
            user_id,
            project,
            case_asset_file,
            case_row,
            case_id,
            status,
            screenshot,
            failure_reason,
            failure_category,
            failure_chain,
            check_type,
            expected,
            actual,
            details,
            now,
        ]
        # ★ 根因修复（2026-08-24）：record_execution 原不落 confidence，
        #   导致 PASS 落库后 confidence=0.0 被假PASS审计误报。现若显式传入
        #   confidence 则一并落库，保持既有调用（不传）行为不变。
        if confidence is not None:
            cols += ",confidence"
            ph += ",%s"
            params.append(float(confidence))
        # ★ 根因修复（2026-09-15）：executions 表 verification_track 和 cross_cut_dims
        #   两列此前 record_execution 未覆盖（全表 DEFAULT='' 污染 D6M 轨道统计），
        #   导致 engine_eval consistency / cross_cut 得分被稀释。显式写入，保持既有
        #   调用（不传则空字符串）行为不变。
        # ★ 统一口径：多执行通道（api_only/browser 各子路径）此前漏传
        #   verification_track 落空串，与 empirical 的 'api_track'/'ui_track' 枚举割裂。
        #   此处按 check_type 派生默认轨道：api_exec→api_track、browser_exec→ui_track。
        #   调用方显式传参优先；诊断类（如 FLAKY, check_type=''）维持空串，不误标。
        if not verification_track:
            verification_track = (
                {"api_exec": "api_track", "browser_exec": "ui_track", "empirical": ""}.get(
                    check_type, ""
                )
            )
        if verification_track:
            cols += ",verification_track"
            ph += ",%s"
            params.append(str(verification_track).strip())
        if cross_cut_dims:
            cols += ",cross_cut_dims"
            ph += ",%s"
            params.append(str(cross_cut_dims).strip())
        # ★ 流程层幂等去重（2026-08-24）：同一 run_id+case_id 重复执行时，只保留最后一
        #   次结果，避免失败态残留（如 SmartShop TC-REQ-001-I01 连续 SKIP→SKIP→PASS
        #   三行）污染分布/KPI。同一条执行更新（后者覆盖前者），原子性由事务保证。
        return _upsert_execution(
            c, cols, ph, params, run_id, project, case_id, check_type
        )
    finally:
        if c:
            try:
                c.close()
            except Exception as _pass_e:
                _log.warning("非关键异常: %s", _pass_e, exc_info=True)


def update_execution_status(
    execution_id: int, status: str, failure_reason: str = "", failure_category: str = ""
) -> bool:
    """更新执行记录的状态（用于置信度闸门等后置修正）。

    Args:
        execution_id: record_execution 返回的记录 ID
        status: 新状态（FAIL/MANUAL_REVIEW 等）
        failure_reason: 失败原因
        failure_category: 失败归属分类

    Returns:
        True 表示更新成功

    ★ v22 通用修复(2026-07-23): 增加重试机制（同 update_execution_confidence）。
      根因: 假 PASS 拦截时 update_execution_status 也可能 DB 连接超时，
      导致 status 保持 PASS（假 PASS 漏检，违反 AGENTS.md 12.4）。
      通用性+覆盖性: 与 update_execution_confidence 同类路径，统一修复。
    """
    for _attempt in range(2):
        c = None
        try:
            c = _conn()
            c.execute(
                "UPDATE executions SET status=%s, failure_reason=%s, failure_category=%s "
                "WHERE id=%s",
                (status, failure_reason, failure_category, execution_id),
            )
            c.commit()
            return True
        except Exception as _e:
            if _attempt == 0:
                _log.warning(
                    "[update_execution_status] 首次失败，重试: %s", str(_e)[:120]
                )
            else:
                _log.warning("[update_execution_status] 重试仍失败: %s", str(_e)[:120])
        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("[audit] 关闭连接异常: %s", _e, exc_info=True)
    return False


def update_execution_confidence(
    execution_id: int, confidence: float, confidence_factors: str = ""
) -> bool:
    """★ v23 根源修复(2026-07-21): 写入 ConfidenceGate 计算后的置信度到 executions 表。

    根因: ConfidenceGate 在 _execute_one_intent 中已正确计算 confidence（如83%），
    但只写日志不持久化；_run_step 完成后又从 DB 重新构造 block_in（step_results=[]）
    再算一次，导致 confidence 被严重低估为 35%，假 PASS 漏检。

    修复: confidence 在执行时一次计算并通过本方法写入 executions.confidence 字段，
    _run_step 直接读 DB，消除双计算路径（架构红线4：禁止格式转换层）。

    通用性: 适用于所有走 _execute_one_intent → record_execution 的用例。

    ★ v22 通用修复(2026-07-23): 增加重试机制，防止偶发 DB 连接超时导致 confidence 丢失。
      根因(§10.9):
        1. 现象: executions.confidence=0.0 但实际计算值=82%
        2. 为什么1: UPDATE 操作 DB 连接超时(2013, Lost connection during query)
        3. 为什么2: record_execution 的 INSERT 后，ConfidenceCalculator 计算耗时较长(>60s)，
           期间 MySQL 服务端关闭了空闲连接(wait_timeout)
        4. 为什么3: PooledDB ping=1 只在借用时检测，不保证查询期间连接存活
      修复: 首次失败时关闭旧连接，获取新连接(经 ping 检测存活)重试一次
      通用性: 适用于所有偶发 DB 连接超时场景(跨系统/跨用例/跨操作类型)
      覆盖性: 所有走 update_execution_confidence 的路径
    """
    for _attempt in range(2):
        c = None
        try:
            c = _conn()
            c.execute(
                "UPDATE executions SET confidence=%s, confidence_factors=%s WHERE id=%s",
                (float(confidence), confidence_factors, execution_id),
            )
            c.commit()
            return True
        except Exception as _e:
            if _attempt == 0:
                _log.warning(
                    "[update_execution_confidence] 首次失败，重试: %s", str(_e)[:120]
                )
            else:
                _log.warning(
                    "[update_execution_confidence] 重试仍失败: %s", str(_e)[:120]
                )
        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("[audit] 关闭连接异常: %s", _e, exc_info=True)
    return False


def is_fake_pass(confidence, expected="", actual="", details="") -> bool:
    """★ 假 PASS 判定口径（AGENTS §13.4 / §12.4）—— 唯一事实源，防口径漂移。

    status=PASS 且 (confidence<0.70 或 expected+actual+details 三列全空) → 假 PASS。
    get_execution_status_map(:257-261) 与 step5/fake_pass_audit.py 共用本函数。
    """
    conf = float(confidence or 0)
    ev_empty = not (expected or actual or details)
    return conf < 0.7 or ev_empty


def get_execution_status_map(case_ids: list, project: str) -> dict:
    """批量查询测试用例的最新执行状态。返回 {case_id: status}。
    用子查询确保每个 case_id 取 executed_at 最大的行的 status。"""
    result = {}
    if not case_ids:
        return result
    c = _conn()
    try:
        try:
            from src.core.versioning import current_version_id

            active_version_id = current_version_id(project)
        except Exception:
            _log.warning("获取当前版本ID失败", exc_info=True)
            active_version_id = 0
        for i in range(0, len(case_ids), 500):
            batch = case_ids[i : i + 500]
            ph = ",".join(["%s"] * len(batch))
            rows = c.execute(
                f"SELECT e.case_id, e.status, e.confidence, e.expected, e.actual, e.details FROM executions e "
                f"INNER JOIN (SELECT case_id, MAX(executed_at) AS max_at FROM executions "
                f"WHERE case_id IN ({ph}) AND project=%s AND version_id=%s GROUP BY case_id) latest "
                f"ON e.case_id = latest.case_id AND e.executed_at = latest.max_at "
                f"WHERE e.project=%s AND e.version_id=%s",
                tuple(batch) + (project, active_version_id, project, active_version_id),
            ).fetchall()
            for row in rows:
                raw = (row["status"] or "").strip().upper()
                # 统一状态映射：passed→PASS, failed→FAIL, skipped→SKIP
                # ★ 修复割裂：ERROR（HARNESS_ERROR/ENVIRONMENT_ERROR）归入 FAIL，
                #   避免执行系统失败的用例既不算 passed 也不算 failed，导致 KPI 显示"未执行"
                status_map = {
                    "PASSED": "PASS",
                    "FAILED": "FAIL",
                    "SKIPPED": "SKIP",
                    "FLAKY": "FLAKY",
                    "ERROR": "FAIL",  # ★ 执行系统失败也计入 failed
                    "PRODUCT_BUG": "FAIL",
                    "HARNESS_ERROR": "FAIL",
                    "ENVIRONMENT_ERROR": "FAIL",
                    "DATA_ERROR": "FAIL",
                    "PASS_WITH_SLOW_BUG": "PASS",  # ★ slow-bug 通过态归 PASS，纳入假 PASS 审计（与 KPI aggregator 口径一致）
                }
                mapped = status_map.get(raw, raw)
                # ★ 通用修复(2026-07-21): 假PASS拦截——status=PASS 但 confidence<0.7 或 evidence 为空，强制显示为 FAIL
                #   根因: AGENTS.md §13.4 规定 confidence<70% 或空 evidence 的 PASS 必须按 FAIL 处理，
                #         否则报告/UI会把不可靠执行计为通过。
                #   通用性: 适用于所有项目、所有用例，不依赖业务逻辑。
                #   职责边界: 状态映射层统一拦截，不污染 Step4 原始记录。
                if mapped == "PASS":
                    if is_fake_pass(
                        row.get("confidence"),
                        row.get("expected"),
                        row.get("actual"),
                        row.get("details"),
                    ):
                        mapped = "FAIL"
                result[row["case_id"]] = mapped
    finally:
        c.close()
    return result


def is_executed_by_user(
    user_id: str, project: str, case_asset_file: str, case_row: int
) -> bool:
    """检查某用户是否已执行过某测试用例（按行号精确匹配）。"""
    c = None
    try:
        c = _conn()
        version_id = _active_version_id(project)
        row = c.execute(
            "SELECT 1 FROM executions WHERE user_id=%s AND project=%s AND version_id=%s AND case_asset_file=%s AND case_row=%s",
            (user_id, project, version_id, case_asset_file, case_row),
        ).fetchone()
        return row is not None
    finally:
        if c:
            try:
                c.close()
            except Exception as _pass_e:
                _log.warning("非关键异常: %s", _pass_e, exc_info=True)


def get_executed_rows(user_id: str, project: str, case_asset_file: str) -> set:
    """获取某用户在某项目中已执行过的测试用例行号集合。"""
    c = None
    try:
        c = _conn()
        version_id = _active_version_id(project)
        rows = c.execute(
            "SELECT case_row FROM executions WHERE user_id=%s AND project=%s AND version_id=%s AND case_asset_file=%s",
            (user_id, project, version_id, case_asset_file),
        ).fetchall()
        return {r["case_row"] for r in rows}
    finally:
        if c:
            try:
                c.close()
            except Exception as _pass_e:
                _log.warning("非关键异常: %s", _pass_e, exc_info=True)


def get_failed_rows(user_id: str, project: str, case_asset_file: str) -> set:
    """获取某用户在某项目中失败的测试用例行号集合。"""
    c = None
    try:
        c = _conn()
        version_id = _active_version_id(project)
        rows = c.execute(
            "SELECT case_row FROM executions WHERE user_id=%s AND project=%s AND version_id=%s AND case_asset_file=%s AND status='FAIL'",
            (user_id, project, version_id, case_asset_file),
        ).fetchall()
        return {r["case_row"] for r in rows}
    finally:
        if c:
            try:
                c.close()
            except Exception as _pass_e:
                _log.warning("非关键异常: %s", _pass_e, exc_info=True)


def get_user_executions(
    user_id: str, project: str = "", case_asset_file: str = ""
) -> list[dict]:
    """获取某用户的执行记录。可指定项目和测试用例文件过滤。"""
    c = None
    try:
        c = _conn()
        sql = "SELECT * FROM executions WHERE user_id=%s"
        params = [user_id]
        if project:
            sql += " AND project=%s AND version_id=%s"
            params.extend([project, _active_version_id(project)])
        if case_asset_file:
            sql += " AND case_asset_file=%s"
            params.append(case_asset_file)
        sql += " ORDER BY executed_at DESC"
        rows = c.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        if c:
            try:
                c.close()
            except Exception as _pass_e:
                _log.warning("非关键异常: %s", _pass_e, exc_info=True)


def get_all_executions(project: str = "") -> list[dict]:
    """获取所有用户的执行记录（admin 用）。可指定项目过滤。"""
    c = None
    try:
        c = _conn()
        if project:
            version_id = _active_version_id(project)
            rows = c.execute(
                "SELECT e.*, u.name as user_name FROM executions e JOIN users u ON e.user_id=u.id "
                "WHERE e.project=%s AND e.version_id=%s ORDER BY e.executed_at DESC",
                (project, version_id),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT e.*, u.name as user_name FROM executions e JOIN users u ON e.user_id=u.id ORDER BY e.executed_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if c:
            try:
                c.close()
            except Exception as _pass_e:
                _log.warning("非关键异常: %s", _pass_e, exc_info=True)


def get_project_executors(project: str) -> list[dict]:
    """获取指定项目的所有执行人（去重用户ID+名称）。"""
    c = None
    try:
        c = _conn()
        version_id = _active_version_id(project)
        rows = c.execute(
            "SELECT DISTINCT e.user_id, u.name as user_name FROM executions e "
            "JOIN users u ON e.user_id=u.id WHERE e.project=%s AND e.version_id=%s ORDER BY e.user_id",
            (project, version_id),
        ).fetchall()
        return [{"user_id": r["user_id"], "user_name": r["user_name"]} for r in rows]
    finally:
        if c:
            try:
                c.close()
            except Exception as _pass_e:
                _log.warning("非关键异常: %s", _pass_e, exc_info=True)


def get_execution_records(project: str, user_id: str) -> list[dict]:
    """获取指定项目+用户的所有执行记录。"""
    c = None
    try:
        c = _conn()
        version_id = _active_version_id(project)
        rows = c.execute(
            "SELECT * FROM executions WHERE project=%s AND version_id=%s AND user_id=%s ORDER BY executed_at DESC",
            (project, version_id, user_id),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if c:
            try:
                c.close()
            except Exception as _pass_e:
                _log.warning("非关键异常: %s", _pass_e, exc_info=True)
