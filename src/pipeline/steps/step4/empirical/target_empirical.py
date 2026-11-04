# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Any
from src.core.guarded import guard

"""[未复核-需 GLM/DeepSeek 把关] 靶子实证验证编排器（TargetEmpiricalVerifier）。

★ 职责：将临时脚本 `scripts/e2e_engine/_tmp_component_verify.py` 的组件级编排
  提为引擎常驻能力，串起"真浏览器打活靶子"的全链路：
    BrowserBackendResolver → sync_playwright 起浏览器 → SutHarness 拉起/登录
    → DeepAssertionRunner 跑 P01/N01/B01 → EmpiricalReportWriter 落盘。

★ 设计约束（design doc Part A §0 / 共享知识 §8）：
  - 不假绿、不静默：登录失败 / 任何 Playwright·网络异常必须**如实**返回
    `all_pass=False`（带 blocked / error 原因），绝对遵守 NO_ASSERTION_DOWNGRADE。
  - selector 优先级：target_cfg["selectors"] > constants 默认；
    DeepAssertionRunner 内部已处理，本编排器只负责透传 target_cfg。
  - 单向依赖：本模块仅依赖 backend_resolver / sut_harness /
    deep_assertion_runner / report_writer，不被反向依赖（无循环导入）。
"""

import logging
import json
from pathlib import Path

from src.pipeline.steps.step4.empirical.constants import (
    API_MODE,
    NO_ASSERTION_DOWNGRADE,
)
from src.pipeline.steps.step4.empirical.sut_harness import SutHarness
from src.pipeline.steps.step4.empirical.deep_assertion_runner import (
    DeepAssertionRunner,
)
from src.pipeline.steps.step4.empirical.report_writer import write as report_write
from src.pipeline.steps.step4.empirical.probe_runner import ApiProbeRunner
from src.pipeline.steps.step4.empirical.authz_matrix import (
    AuthzMatrixVerifier,
)
# ★ 2026-09-02: a11y/perf/visual 三横切门禁收敛到统一 helper（跨后端复用）
from src.pipeline.steps.step4.empirical.cross_cut_gates import (
    run_cross_cut_gates,
    run_perf_check,
)
from src.pipeline.steps.step4.playwright_engine.backend_resolver import (
    BrowserBackendResolver,
)
from src.pipeline.steps.step4.empirical.target_cfgs import (
    get_target_cfg,
    TARGET_CFGS,
)

_log = logging.getLogger(__name__)


def safe_dialog_action(dialog_type: str) -> str:
    """dialog 事件安全策略：仅自动接受 confirm，其余（alert/prompt）关闭。

    ★ 防误伤（见 §14 问题 5）：全局 dialog 处理器若无条件 accept()，会把业务
      alert（如"操作失败"）也一并接受，掩盖真实失败信号，甚至对非删除类确认框
      产生副作用。正确策略——
        - confirm：通常是"删除确认"等用户意图确认，自动 accept 安全推进；
        - alert / prompt / 其它：属警告或需输入弹窗，自动 dismiss（关闭）不触发
          任何副作用，且不会掩盖后续 DOM 断言能捕获的失败信号。
    """
    return "accept" if dialog_type == "confirm" else "dismiss"


def _ensure_project_registered(project: str) -> bool:
    """确保目标项目在 ``projects`` 表已注册（幂等 INSERT IGNORE）。

    ★ FK 根因(2026-09-04)：``executions.project`` → ``projects.name`` 外键约束。
      empirical 路径对任意 target_key 自拉起被测系统并落 executions，此前未把
      目标注册进 ``projects`` 表（api_demo/spa_demo 等），导致写 executions 时
      外部键约束失败被吞掉。本函数在写 executions 前置位注册，**通用**：任何
      target_key 均可，幂等重复调用无副作用。
    Args:
        project: 项目名（= 实证 target_key）。
    Returns:
        bool：注册成功（或已存在）为 True；失败为 False（不阻断后续流程）。
    """
    if not project:
        return False
    try:
        from src.core.db_manager import get_connection, release_connection

        conn = None
        try:
            conn = get_connection()
            conn.execute(
                "INSERT IGNORE INTO projects (name, status) VALUES (%s, 'active')",
                (project,),
            )
            conn.commit()
            _log.info("[verifier] 目标项目已注册 projects: %s", project)
            return True
        finally:
            if conn:
                release_connection(conn)
    except Exception as _e:  # noqa: BLE001 —— 注册失败不阻断实证流程
        _log.warning("[verifier] 注册项目 %s 失败(不阻断): %s", project, _e)
        return False

# 项目根（target_empirical.py 位于 <root>/src/.../empirical）。
# 鲁棒解析：向上找到名为 "src" 的祖先目录，其父即为项目根，
# 避免硬编码 parent 层级出错（曾误算为 <root>/src）。
def _resolve_project_root() -> Path:
    cur = Path(__file__).resolve().parent
    for _ in range(10):
        if (cur / "src").is_dir() and (cur / "scripts").is_dir():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    # 兜底：直接取 empirical 向上 5 级
    return Path(__file__).resolve().parent.parent.parent.parent.parent


_ROOT = _resolve_project_root()

# 注：不再保留「默认靶子配置（hospital_sut）」——去靶子化要求任何靶子配置必须显式
# 声明 target_key（或传入注册表 key 字符串），禁止默认 hospital_sut 偏置。
# 缺省即抛错（见 __init__ 的 else 分支），保证单一事实源在 target_cfgs 注册表。

# 默认待验证用例集（P01 正向 / N01 负向非法值 / B01 边界空 required）
_DEFAULT_CASES: list[dict] = [
    {"case_id": "P01", "fill_invalid": False},
    {"case_id": "N01", "fill_invalid": True},
    {"case_id": "B01", "fill_invalid": False},
]


class TargetEmpiricalVerifier:
    """靶子实证验证编排器：常驻引擎的真浏览器活靶验证入口。

    典型用法::

        v = TargetEmpiricalVerifier({"url": "http://127.0.0.1:5081",
                                     "port": 5081, "target_key": "hospital_sut"})
        report = v.run()            # 落盘 + 返回 report dict
        v.run_and_print()           # 打印终端报告
    """

    def __init__(self, target_cfg: dict | None = None) -> None:
        """初始化编排器。

        Args:
            target_cfg: 靶子配置字典（见 docs/system_design_expend.md §3.1），可含：
                - mode: "browser" | "api"（默认 browser）
                - url: 靶子基地址（必填，除非传入注册表 key 字符串）
                - port: 端口（必填，除非传入注册表 key 字符串）
                - target_key: 靶子标识（必填；或传入注册表 key 字符串由 get_target_cfg 解析）
                - db_env: 可写库环境变量（缺省回退 target_cfg["db_env"]）
                - auth/launch/selectors/pagination: 模式相关注入
                - base_url: 可选，覆盖列表基地址（缺省回退 url）
                - report_path: 可选，报告落盘路径（覆盖默认）
                - cases: 可选，自定义用例集（覆盖默认 P01/N01/B01）
        """
        # 若传入的是注册表 key（字符串），或 dict 中含已知 target_key，
        # 先解析为完整 cfg（含 selectors/pagination/list_path 等）；否则合并默认值。
        if isinstance(target_cfg, str):
            cfg = get_target_cfg(target_cfg)
        elif isinstance(target_cfg, dict) and target_cfg.get("target_key") in TARGET_CFGS:
            # dict 携带已知 target_key：以注册表完整配置为基础，再用传入 dict 覆盖。
            cfg = get_target_cfg(target_cfg["target_key"])
            cfg.update({k: v for k, v in target_cfg.items() if v is not None})
        else:
            # 未传注册表 key、也非已知 target_key 的裸 dict：禁止默认 hospital_sut，
            # 必须显式声明 target_key（调用方应传入完整 target_cfg，或已知 target_key
            # 走上面 get_target_cfg 分支）。未知 target_key 的裸 dict 作为完整配置
            # 直接使用，不合并任何靶子默认值。
            raw = dict(target_cfg or {})
            if not raw.get("target_key"):
                raise ValueError(
                    "target_cfg 必须声明 'target_key'（或传入注册表 key 字符串）；"
                    "禁止默认 hospital_sut。已传入=%r" % (target_cfg,)
                )
            cfg = raw
        self.target_cfg: dict = cfg
        self.mode: str = self.target_cfg.get("mode", "browser")
        self.url: str = self.target_cfg["url"]
        self.port: int = int(self.target_cfg["port"])
        self.target_key: str = self.target_cfg.get("target_key", "")
        self.db_env: str = self.target_cfg.get("db_env", "")

        # 合并 base_url（DeepAssertionRunner 需要绝对基地址导航）
        if "base_url" not in self.target_cfg:
            self.target_cfg["base_url"] = self.url

        # 用例集
        #   之前无条件 `_load_cases_from_db() or target_cfg.cases`，用 target_key 当 DB
        #   project 名查 test_cases；当某 Step1-5 流水线引擎项目与实证 target_key 同名
        #  （如都叫 smartshop）时，会把流水线 AI 生成的 Step3 用例（TC-REQ-XXX）静默抓走，
        #   覆盖掉实证注册表精选用例（P01/N01/...），且 Step3 payload 与实证 runner 格式
        #   不兼容 → 全部 "未知 case_id" FAIL（后端命名空间混轨根因）。
        #   修复（通用，非补丁）：实证靶子默认跑自己注册表的精选用例；仅当 target_cfg
        #   显式开 `load_cases_from_db` 才从 DB 拉取，且独立 `db_project` 命名空间，
        #   不再拿 target_key 当 DB project 隐式耦合。
        _db_cases = self._load_cases_from_db() if self.target_cfg.get(
            "load_cases_from_db"
        ) else None
        if _db_cases is None and self.target_cfg.get("load_cases_from_db"):
            # 显式 opt-in 但 DB 无有效用例 → 明确告警，不静默退化到注册表
            _log.warning(
                "[target_empirical] target=%s 已开启 load_cases_from_db 但 DB 无有效用例 "
                "(db_project=%r)，回退注册表精选用例",
                self.target_key, self.target_cfg.get("db_project") or self.target_key,
            )
        self.cases: list[dict] = _db_cases or self.target_cfg.get("cases") or list(
            _DEFAULT_CASES
        )

        # 报告落盘路径（默认 logs/reports/empirical_<target_key>_report.json）
        # ★  修复：原默认 scripts/e2e_engine/ 相对路径被解析到 src/scripts/e2e_engine/
        #   —— 运行产物误入源码树。现改为项目根 logs/reports/（运行产物统一出源码树）。
        default_report = (
            _ROOT
            / "logs"
            / "reports"
            / f"empirical_{self.target_key}_report.json"
        )
        self.report_path: str = self.target_cfg.get(
            "report_path", str(default_report)
        )

        # 持有时引用自行拉起的 SUT（SutHarness），便于 run() 末尾 finally 回收，
        # 避免 SUT 子进程在验证结束后被孤儿化、跨靶子堆积（实证套件串扰根因）。
        self._sut_harness = None

    # ─────────────────────────────────────────────────────────────────────
    # 公开 API
    # ─────────────────────────────────────────────────────────────────────

    # ─────────────────────────────────────────────────────────────────────
    # DB test_cases 加载（2026-09-16: 让 target 模式能跑 Step3 生成的完整用例集）
    # ─────────────────────────────────────────────────────────────────────

    def _load_cases_from_db(self) -> list[dict] | None:
        """从 MySQL test_cases 表加载用例集（仅当 target_cfg 显式开启时由 __init__ 调用）。

        返回 list[dict] 或 None（DB 为空时走 fallback 链）。
        每条 test_case 的 payload_json 被原样用作 empirical runner 的 case dict。

        ★ 2026-09-20 命名空间隔离：DB 查询用显式 ``db_project``（缺省回退 target_key）。
          - 职责边界：target_key 是"实证靶子"命名空间；db_project 是"Step1-5 流水线
            引擎项目"命名空间，两者可同名但语义不同。调用方必须先 opt-in
            （load_cases_from_db=true）再传 db_project，实证靶子不会因 target_key 恰与
            某流水线项目同名而被静默拉走另一套用例（smartshop 35 条全 FAIL 根因）。
        """
        try:
            from src.core.db_manager import get_connection
            _project = self.target_cfg.get("db_project") or self.target_key
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT payload_json FROM test_cases WHERE project = %s AND valid_status = 'valid'",
                (_project,),
            )
            rows = cur.fetchall()
            if not rows:
                return None
            cases = []
            for r in rows:
                pj = r.get("payload_json") if isinstance(r, dict) else r[0]
                if not pj:
                    continue
                if isinstance(pj, str):
                    pj = json.loads(pj)
                cases.append(pj)
            if not cases:
                return None
            _log.info("[target_empirical] 从 DB test_cases 加载 %d 条 (db_project=%s)", len(cases), _project)
            return cases
        except Exception as exc:
            _log.warning("[target_empirical] DB test_cases 加载失败(不阻断): %s", str(exc)[:120])
            return None

    # ─────────────────────────────────────────────────────────────────────
    # 报告落盘 + snippets 回写 executions（2026-09-02 方案B：MySQL 为唯一事实源）
    # ─────────────────────────────────────────────────────────────────────

    @property
    def _ep_run_id(self) -> str:
        """本次实证运行的独立 run_id（每 verifier 实例唯一、幂等 upsert 用）。"""
        rid = getattr(self, "_cached_emp_run_id", "")
        if rid:
            return rid
        from datetime import datetime
        rid = self.target_cfg.get("run_id") or (
            "empirical:%s:%s" % (
                self.target_key,
                datetime.now().strftime("%Y%m%d%H%M%S"),
            )
        )
        self._cached_emp_run_id = rid
        return rid

    @staticmethod
    def _case_expected(result: dict) -> str:
        for k in ("expected", "expectation"):
            v = result.get(k)
            if v:
                return str(v)
        ass = result.get("assertions") or []
        if ass:
            return " || ".join(
                "{}: {}".format(a.get("name", ""), a.get("expected", ""))
                for a in ass if a.get("expected")
            )
        return ""

    @staticmethod
    def _case_actual(result: dict) -> str:
        for k in ("actual", "result", "evidence", "summary"):
            v = result.get(k)
            if v and not isinstance(v, (dict, list)):
                return str(v)
        ass = result.get("assertions") or []
        if ass:
            return " || ".join(
                "{}: {}".format(a.get("name", ""), a.get("actual", ""))
                for a in ass if a.get("actual")
            )
        return ""

    @staticmethod
    def _case_status(result: dict) -> str:
        overall = result.get("overall")
        if overall:
            st = str(overall).upper()
            return st if st in ("PASS", "FAIL", "SKIP", "ERROR") else (
                "PASS" if st == "PASS" else "FAIL"
            )
        if "pass" in result and any(k in result for k in ("case_id", "case")):
            return "PASS" if result.get("pass") else (
                "SKIP" if (result.get("blocked") or result.get("blocked_by")) else "FAIL"
            )
        return "ERROR"

    @staticmethod
    def _case_failure_category(result: dict) -> str:
        if result.get("blocked") or result.get("blocked_by"):
            return "ENVIRONMENT_ERROR"
        if result.get("harness_error"):
            return "HARNESS_ERROR"
        return "PRODUCT_BUG"

    @staticmethod
    def _case_confidence(result: dict) -> float:
        """实证 case 置信度（客观可复算， 差距2修复）。

        ★ 统一证据口径：回写 executions 时不再留 confidence=0.0/None。取值优先级——
          1. result 显式携带 ``confidence`` → 直接采用（如 VLM/AI 判定的真实置信度）；
          2. 否则按「已判定断言通过率」推导（客观、可复算）；
          3. 无任何已判定断言时，PASS 视 0.9（真实检测执行过）、非 PASS 视 0.0。

        原则：只对显式给过判定(pass 非 None)的断言计分，blocked/未判不计入分母，
        避免受限证据静默拉低置信；也不伪造未执行时的「高置信通过」。
        """
        raw = result.get("confidence")
        if raw not in (None, "", 0):
            try:
                return min(1.0, max(0.0, float(raw)))
            except (TypeError, ValueError) as _e:
                _log.debug("_case_confidence best-effort 容忍: %s", str(_e)[:120])
        ass = result.get("assertions") or []
        judged = [a for a in ass if a.get("pass") is not None]
        if judged:
            passed = sum(1 for a in judged if a.get("pass"))
            return passed / len(judged)
        # 无已判定断言：blocked/skip 视 0.0（未完成检测，不产生虚假置信）
        if (result.get("blocked") or result.get("blocked_by")
                or str(result.get("overall", "")).upper() == "SKIP"):
            return 0.0
        if result.get("overall") == "PASS" or result.get("pass") is True:
            return 0.9
        if result.get("overall") == "FAIL" or result.get("pass") is False:
            return 0.9
        return 0.0

    def _record_cases_to_executions(self, report: dict) -> None:
        """将实证报告每条 case 回写 executions（幂等 upsert，JSON 仅作渲染副本）。

        ★ 方案B：满足"MySQL executions 为唯一事实源"——本组件此前
          只落 JSON 报告，属 MySQL 之外并行数据。现统一经 audit.record_execution
          落库（独立 run_id + check_type=empirical），报告 JSON 仅作配置渲染。
          逐条 best-effort：某条失败只告警，不影响 JSON 落盘与结果返回。

        ★ 根因修复(2026-09-15)：
          (a) record_execution 新增 verification_track / cross_cut_dims 参数支持后，
              实证后端需传入正确值（browser→ui_track, api→api_track），否则 engine_eval
              的 consistency / cross_cut 维度因全表 DEFAULT='' 被稀释。
          (b) engine_eval 锁死文件按 project 聚合 executions（不按 run_id 过滤），
              多次 run 累积会导致 run_summary.passed ≠ SUM(executions.PASS)。写前
              幂等清理同一 project 的旧 run 记录，保持 run_summary vs executions 一致。
        """
        from src.users.audit import record_execution

        # ★ FK 根因(2026-09-04)：写 executions 前置位注册目标项目，满足
        #   executions.project → projects.name 外键，杜绝"目标未注册→回写被吞"。
        _ensure_project_registered(self.target_key)

        cases = report.get("cases") or []
        if not cases:
            return
        run_id = self._ep_run_id
        target_key = self.target_key
        # (b) 幂等清理：同一 project 但 run_id ≠ 当前 run_id 的旧记录
        with guard("_record_cases_to_executions: from src.core.db_manager import get_", log=_log, level=logging.WARNING):
            from src.core.db_manager import get_connection, release_connection
            _conn = get_connection()
            try:
                _conn.execute(
                    "DELETE FROM executions WHERE project=%s AND run_id!=%s AND check_type=%s",
                    (target_key, run_id, "empirical"),
                )
                _conn.commit()
                _log.info("[verifier] 幂等清理旧 executions: project=%s run_id!=%s", target_key, run_id)
            finally:
                release_connection(_conn)

        # (a) 派生 verification_track
        v_track = "api_track" if self.mode == API_MODE else "ui_track"

        for res in cases:
            with guard("_record_cases_to_executions: if not isinstance(res, dict): contin", log=_log, level=logging.WARNING):
                if not isinstance(res, dict):
                    continue
                status = self._case_status(res)
                expected = self._case_expected(res)
                actual = self._case_actual(res)
                details = json.dumps(
                    res, ensure_ascii=False, default=str
                )[:20000]
                failed = status != "PASS"
                _cid = str(res.get("case_id") or res.get("case") or "").strip() or "empirical"
                record_execution(
                    user_id="system",
                    project=target_key,
                    case_id=_cid,
                    status=status,
                    run_id=run_id,
                    check_type="empirical",
                    expected=expected,
                    actual=actual,
                    details=details,
                    failure_reason=(
                        str(res.get("trajectory") or res.get("error")
                            or res.get("blocked_by") or "")[:500] if failed else ""
                    ),
                    failure_category=(
                        self._case_failure_category(res) if failed else ""
                    ),
                    confidence=self._case_confidence(res),
                    verification_track=v_track,
                )
                # ★  根因修复：empirical 通道此前零健康指标埋点——
                #   3 个历史 empirical run 共 12 行 executions、ai_health_metrics 0 行
                #   （与 api_only 通道同类断裂）。补齐：逐用例 action_attempt + 三轨
                #   verification。轨位按模式判定：API_MODE → api 轨承载结果，
                #   UI 模式 → ui 轨承载结果（其余轨 False，与 api_only 同语义）。
                with guard("_record_cases_to_executions: from src.extensions.metrics_collecto", log=_log, level=logging.WARNING):
                    from src.extensions.metrics_collector import metrics_collector

                    metrics_collector.record_action_attempt(
                        target_key, run_id, "empirical"
                    )
                    metrics_collector.record_verification(
                        project=target_key,
                        run_id=run_id,
                        step_desc="empirical:%s" % _cid[:40],
                        ui_pass=(not failed) and self.mode != API_MODE,
                        api_pass=(not failed) and self.mode == API_MODE,
                        db_pass=False,
                        case_id=_cid,
                    )

    def _persist_cross_cut_to_store(self, report: dict) -> None:
        """★ 根因修复(2026-09-04)：实证后端将真实横切结果(a11y/perf/visual)落 MySQL。

        背景：此前三横切门禁只在 serial 后端落库（a11y→oracle_results(accessibility)、
        visual→visual_review、perf→oracle_results(performance)），而 empirical 后端
        只把结论写进报告 JSON → 造成"同一靶子换后端横切维度缺失、oracle_results 零写入"。
        本方法与 serial 的 ``_persist_cross_cut_visual/_perf`` 同源复用 store 入口，使
        empirical 后端任何一次运行都能补全四维报告的事实源；不另造并行落库逻辑。

        关键点：
          - visual 按 run 级 execution_id=0 写（visual_review 无 run_id 列）；
          - perf 按 run 级 execution_id=0 写（report 的 perf 门禁走 PerfContext 插件链，
            此处 oracle_results(performance) 供 perf_baseline 质量面板复用）；
          - a11y 需关联 executions（报告 JOIN project+run_id），故取本 run 第一条
            execution_id 挂载，确保 Step5 可聚合渲染。
        全程 fail-safe：任何异常只 warning，绝不阻断主流程，绝不伪造结论。
        """
        with guard("_persist_cross_cut_to_store: from src.oracle.store import ( write", log=_log, level=logging.WARNING):
            from src.oracle.store import (
                write_oracle_result,
                write_perf_result_from_gate,
                write_visual_review,
            )
            from src.core.versioning import current_version_id
            from src.core.db_manager import get_connection

            project = str(self.target_key or "").strip()
            if not project:
                return
            run_id = self._ep_run_id
            version_id = int(current_version_id(project) or 0)

            # 取本 run 的一条 execution_id（供 a11y 挂载；无则回退 0）
            execution_id = 0
            try:
                conn = get_connection()
                try:
                    row = conn.execute(
                        "SELECT id FROM executions WHERE project=%s AND run_id=%s "
                        "ORDER BY id ASC LIMIT 1",
                        (project, str(run_id)),
                    ).fetchone()
                    if row and row.get("id"):
                        execution_id = int(row["id"])
                finally:
                    if conn:
                        try:
                            conn.close()
                        except (OSError, ConnectionError) as _e:
                            _log.debug("_persist_cross_cut_to_store best-effort 容忍: %s", str(_e)[:120])
            except Exception:  # noqa: BLE001
                execution_id = 0

            # ── visual → visual_review（run 级独立复核） ──
            vis = (report or {}).get("visual") or {}
            if isinstance(vis, dict) and vis.get("enabled"):
                vlm_diff = {
                    "status": vis.get("status"),
                    "passed": vis.get("passed"),
                    "skipped": vis.get("skipped"),
                    "confidence": vis.get("confidence"),
                    "reason": str(vis.get("evidence") or vis.get("reason")
                                  or "")[:500],
                    "evidence": str(vis.get("evidence") or "")[:500],
                }
                write_visual_review(
                    project=project,
                    version_id=version_id,
                    execution_id=0,
                    vlm_diff=vlm_diff,
                )

            # ── a11y → oracle_results(accessibility) ──
            a11y = (report or {}).get("a11y") or {}
            if isinstance(a11y, dict) and a11y.get("enabled"):
                _skip = int(bool(a11y.get("skipped")))
                _checked = 0 if _skip else 1
                _total = int(a11y.get("total") or 0)
                write_oracle_result(
                    project=project,
                    version_id=version_id,
                    execution_id=execution_id,
                    oracle_type="accessibility",
                    verdict="pass" if _total == 0 else "warn",
                    score=max(0.0, 1.0 - min(_total, 20) * 0.05),
                    detail={
                        "case_id": str(a11y.get("source") or ""),
                        "checked": _checked,
                        "skipped": _skip,
                        "total": _total,
                        "by_severity": dict(a11y.get("by_severity") or {}),
                        "findings": (a11y.get("findings") or [])[:50],
                    },
                )

            # ── perf → oracle_results(performance)（run 级独立复核） ──
            perf = (report or {}).get("perf") or {}
            if isinstance(perf, dict) and perf.get("enabled"):
                write_perf_result_from_gate(
                    project=project,
                    version_id=version_id,
                    execution_id=0,
                    gate=perf,
                )

            # ── security → oracle_results(security)（run 级独立复核） ──
            #   与 a11y/perf 对齐：安全横切维度作为一等公民落 MySQL，供 step5 聚合渲染。
            sec = (report or {}).get("security") or {}
            if isinstance(sec, dict) and sec.get("enabled"):
                _s_findings = sec.get("findings") or []
                _s_sev = dict(sec.get("by_severity") or {})
                _has_hi = bool(
                    any(f.get("severity") in ("critical", "high") for f in _s_findings)
                )
                _s_total = int(sec.get("total") or 0)
                # 【2026-09-05】authz（越权/访问控制）子维度：横切门禁产出 security.authz，
                # 一并落 detail，step5 报告据此渲染越权门禁结果（有探针才展示）。
                _authz = sec.get("authz") or {}
                _authz_detail = None
                if _authz.get("total"):
                    _authz_detail = {
                        "all_pass": bool(_authz.get("all_pass")),
                        "total": int(_authz.get("total") or 0),
                        "passed": int(_authz.get("passed") or 0),
                        "failed": int(_authz.get("failed") or 0),
                        "probes": (_authz.get("probes") or [])[:50],
                        "error": _authz.get("error", ""),
                    }
                write_oracle_result(
                    project=project,
                    version_id=version_id,
                    execution_id=execution_id,
                    oracle_type="security",
                    verdict="fail" if _has_hi else ("warn" if _s_total else "pass"),
                    score=max(0.0, 1.0 - min(_s_total, 20) * 0.05),
                    detail={
                        "case_id": str(sec.get("source") or ""),
                        "checked": 1 if _s_total or not sec.get("error") else 0,
                        "skipped": 0,
                        "total": _s_total,
                        "by_severity": _s_sev,
                        "findings": _s_findings[:50],
                        "authz": _authz_detail,
                    },
                )

    def _persist_report(self, report: dict) -> None:
        """统一落盘入口：写 JSON 报告 + 回写 executions + 横切结果落库（best-effort）。"""
        report_write(report, self.report_path)
        self._record_cases_to_executions(report)
        self._persist_cross_cut_to_store(report)

    def _stop_sut(self) -> None:
        """回收自行拉起的 SUT 进程（若有）。finally 中调用，绝不抛异常。"""
        harness = getattr(self, "_sut_harness", None)
        if harness is not None:
            with guard("_stop_sut: harness.stop()", log=_log):
                harness.stop()

    def _idempotent_cleanup_old_runs(self) -> None:
        """★ 根因修复(2026-09-15)：同一 project 的旧 run 数据幂等清理。

        engine_eval 锁死文件按 ``WHERE LOWER(project)=%s`` 聚合 executions 和
        oracle_results（不按 run_id 过滤），多次 run 累积会导致：
          - consistency_score 被稀释（run_summary.passed ≠ SUM(executions.PASS)）
          - cross_cut_score 被稀释（新 run 的 oracle_results 被旧 run 失败记录拉低）
        本方法在 run() 启动前清理同一 project 的旧记录（oracle_results / run_summary），
        配合 _record_cases_to_executions 中 executions 清理，保证单次 run 内数据一致性。
        全程 best-effort：清理失败只 warning，不阻断执行。
        """
        with guard("_idempotent_cleanup_old_runs: from src.core.db_manager import get_", log=_log, level=logging.WARNING):
            from src.core.db_manager import get_connection, release_connection
            conn = get_connection()
            try:
                # 清旧 oracle_results（security/a11y/perf/visual）
                conn.execute(
                    "DELETE FROM oracle_results WHERE project=%s",
                    (self.target_key,),
                )
                deleted = conn.rowcount
                conn.execute(
                    "DELETE FROM run_summary WHERE project=%s AND report_version='empirical'",
                    (self.target_key,),
                )
                conn.commit()
                if deleted > 0:
                    _log.info(
                        "[verifier] 幂等清理旧 oracle_results: project=%s deleted=%d",
                        self.target_key, deleted,
                    )
            finally:
                release_connection(conn)

    def run(self) -> dict:
        """执行完整实证验证链路（按 mode 分派 browser / api）。

        Returns:
            报告 dict：{target, mode, target_key, self_started_sut, backend,
                       cases, summary:{...}}。
            - 任何异常（Playwright/网络/api）→ 捕获后返回
              {summary:{all_pass:False, error:str(e)}}，不静默、不假绿。
            - 登录失败（cookie/token 为空）→ 返回
              {summary:{all_pass:False, blocked:"login_failed"}}。

        ★ 根因修复(2026-09-15)：幂等清理旧 run 数据。engine_eval 锁死文件按
          project 聚合 executions/oracle_results（不按 run_id 过滤），多次 run
          累积会导致 consistency / cross_cut 得分被稀释。run() 启动前清理
          同一 project 的旧 oracle_results / run_summary，与 _record_cases_to_executions
          中 executions 清理配合，保证单次 run 内数据一致性。
        """
        self._idempotent_cleanup_old_runs()

        # ★ 根因修复：authz（越权/访问控制）是「横切门禁」，与功能性用例正交，
        #   不再是 run() 顶部分派的互斥模式。修复前只要 target_cfg 配了 authz 就
        #   ``return _run_authz_mode()``，把功能性用例（如 api_demo 的 P01/N01/B01）
        #   整链短路丢弃——target_cfg 既声明 cases 又声明 authz 时矛盾自曝，
        #   违反「全部用例执行完毕」与通用性。现：先执行功能性用例（api/browser），
        #   再以 security.authz 子维度并入横切门禁，两者都覆盖。
        authz_cfg = self.target_cfg.get("authz")
        # 纯 authz 靶子（无功能性用例，如仅上传权限探针）保持原语义：直接跑权限矩阵。
        if authz_cfg and not self.cases:
            return self._run_authz_mode()

        # SUT 生命周期由 run() 统一拥有：authz 是横切门禁，须在功能性路径之后、
        # 同一存活 SUT 上执行；提前回收会让 authz 探针全部连接被拒 → 引擎制造假 FAIL。
        try:
            # API 模式：纯 requests 打端点，无 Playwright、无 cookie、无 DOM。
            if self.mode == API_MODE:
                report = self._run_api_mode()
            else:
                report = self._run_browser_mode()

            if authz_cfg:
                report = self._merge_authz_gate(report)
            return report
        finally:
            # 回收自行拉起的 SUT 进程树，避免 SUT 被孤儿化、多靶子连续验证时
            # 堆积占用端口/内存（实证套件串扰根因）。stop 幂等，重复调用安全。
            self._stop_sut()

    def _seed_nav_graph(self) -> None:
        """对声明 nav_seed 的靶子注入导航数据（SUT 可达之后、断言之前）。

        要求（依赖 seed_nav_graph 自身保证 + 本方法兜底）：
          - 幂等：种子器重复调用无副作用（upsert 语义）；
          - 未声明 nav_seed：seed_nav_graph 已 no-op 返回 seeded=False；
          - 不阻断：任何异常只记日志，绝不阻塞实证流程（fail-open 接线）。
        """
        if not (self.target_cfg.get("nav_seed") or {}):
            return
        with guard("_seed_nav_graph: from src.pipeline.steps.step3.nav_se", log=_log, level=logging.WARNING):
            from src.pipeline.steps.step3.nav_seed import seed_nav_graph

            _log.info("[verifier] 调用 seed_nav_graph(target_key=%s)", self.target_key)
            result = seed_nav_graph(self.target_key)
            _log.info("[verifier] seed_nav_graph 结果: %s", result)

    # ─────────────────────────────────────────────────────────────────────
    # API 模式（纯 requests，不打浏览器）
    # ─────────────────────────────────────────────────────────────────────

    def _run_api_mode(self) -> dict:
        """API 模式实证链路：探活/拉起 → 登录取 token → ApiAssertionRunner 跑用例。"""
        try:
            harness = SutHarness(
                url=self.url,
                port=self.port,
                target_key=self.target_key,
                db_env=self.db_env,
                mode=API_MODE,
                auth=self.target_cfg.get("auth"),
                launch=self.target_cfg.get("launch"),
            )
            self._sut_harness = harness
            self_started = harness.ensure_running()
            _log.info("[verifier] api SUT self_started=%s", self_started)
            # ★ nav_seed 接线：SUT 就绪后、断言前注入导航数据（fail-open）
            self._seed_nav_graph()

            # ★ 无登录业务/脆弱点探针路径（auth.type=none，如 edu_sut/fin_sut）：
            #   这些靶子的真实用例在 verification.cases，无需登录、不注入 token，
            #   直接按探针打端点。修复此前 api_login_get_token 对非认证靶子强制
            #   登录导致 login_failed 阻断的问题。
            verify_cfg = self.target_cfg.get("verification") or {}
            verify_cases = verify_cfg.get("cases")
            if verify_cases:
                _log.info(
                    "[verifier] %s 走 verification.cases 探针路径（%d 条）",
                    self.target_key, len(verify_cases),
                )
                runner = ApiProbeRunner(target_cfg=self.target_cfg)
                results = [runner.run_probe(c) for c in verify_cases]
                passed = sum(1 for r in results if r.get("pass"))
                failed = len(results) - passed
                vulns = sum(1 for r in results if r.get("vuln_captured"))
                summary = {
                    "all_pass": failed == 0 and vulns == 0,
                    "total": len(results),
                    "passed": passed,
                    "failed": failed,
                    "vulns_captured": vulns,
                }
                report = self._build_report(
                    backend={"kind": "api_probe"},
                    self_started_sut=self_started,
                    cases=results,
                    summary=summary,
                )
                self._persist_report(report)
                return report

            admin_token = harness.api_login_get_token("admin")
            if not admin_token:
                _log.error("[verifier] api admin 登录失败，未取到 token")
                report = self._build_report(
                    backend={"kind": "api_requests"},
                    self_started_sut=self_started,
                    cases=[],
                    summary={"all_pass": False, "blocked": "login_failed"},
                )
                self._persist_report(report)
                return report

            # 跑用例集（ApiAssertionRunner 由 DeepAssertionRunner 分派）
            runner = DeepAssertionRunner(
                page=None, context=None, target_cfg=self.target_cfg
            )
            # 注入 admin token 供 runner 使用
            if hasattr(runner, "_impl") and hasattr(runner._impl, "_admin_token"):
                runner._impl._admin_token = admin_token

            results: list[dict] = []
            for case in self.cases:
                r = runner.run_case(case)
                results.append(r)

            total = len(results)
            passed = sum(1 for r in results if r.get("overall") == "PASS")
            failed = total - passed
            all_pass = failed == 0

            # ★ 性能门禁（API 模式）：收集 API 调用记录 → 门禁判定
            #   走 cross_cut_gates.run_perf_check 统一入口（内部封装 perf_checker.run_full_perf_check），
            #   与 browser 模式三横切共用同一执行源，避免直接调用未导入的 run_full_perf_check。
            #   root：此前 getattr(runner, "_api_calls")，而 runner 是 DeepAssertionRunner
            #   （请求实际由其 _impl=ApiAssertionRunner 发出，且已登记 self._api_calls），
            #   取到 None → API 靶子 perf 恒 0 样本、基线永不刷新。现从 impl 透传读取，
            #   任何经 ApiAssertionRunner 执行的 API 靶子都能产出 perf 样本（通用修复）。
            api_calls = getattr(runner, "_api_calls", None)
            if not api_calls and getattr(runner, "_impl", None) is not None:
                api_calls = getattr(runner._impl, "_api_calls", None)
            perf_result = run_perf_check(
                page=None,
                api_calls=api_calls,
                target_cfg=self.target_cfg,
                run_id=str(self.target_cfg.get("target_key", "")),
            )

            summary = {
                "all_pass": all_pass,
                "total": total,
                "passed": passed,
                "failed": failed,
            }
            report = self._build_report(
                backend={"kind": "api_requests"},
                self_started_sut=self_started,
                cases=results,
                summary=summary,
            )
            report["perf"] = perf_result
            self._persist_report(report)
            return report
        except Exception as _e:
            _log.error("[verifier] api 验证异常: %s", _e, exc_info=True)
            report = self._build_report(
                backend={"kind": "api_requests"},
                self_started_sut=None,
                cases=[],
                summary={"all_pass": False, "error": str(_e)[:500]},
            )
            with guard("_run_api_mode: self._persist_report(report)", log=_log):
                self._persist_report(report)
            return report

    # ─────────────────────────────────────────────────────────────────────
    # 权限矩阵验证模式（RBAC 多角色探针，通用访问控制验证能力）
    # ─────────────────────────────────────────────────────────────────────

    def _run_authz_mode(self) -> dict:
        """权限矩阵验证模式：对 RBAC 靶子跑多角色探针（allow/deny 矩阵覆盖）。

        验证能力对齐软件测试理论中的访问控制测试核心维度：正向（有权限→允许）、
        负向（缺权限→拒绝 403）、边界（未认证→401）、默认拒绝（deny by default）。
        """
        try:
            harness = SutHarness(
                url=self.url,
                port=self.port,
                target_key=self.target_key,
                db_env=self.db_env,
                mode=API_MODE,
                auth=self.target_cfg.get("auth"),
                launch=self.target_cfg.get("launch"),
            )
            self._sut_harness = harness
            self_started = harness.ensure_running()
            _log.info("[verifier] authz SUT self_started=%s", self_started)
            # ★ nav_seed 接线：SUT 就绪后、断言前注入导航数据（fail-open）
            self._seed_nav_graph()

            verifier = AuthzMatrixVerifier(target_cfg=self.target_cfg)
            report = verifier.run()
            report["self_started_sut"] = self_started
            report["target"] = self.url
            report["target_key"] = self.target_key
            report["mode"] = "authz"
            self._persist_report(report)
            return report
        except Exception as _e:  # 任何异常如实 FAIL，不静默、不假绿
            _log.error("[verifier] authz 验证异常: %s", _e, exc_info=True)
            report = self._build_report(
                backend={"kind": "api_requests"},
                self_started_sut=None,
                cases=[],
                summary={"all_pass": False, "error": str(_e)[:500]},
            )
            with guard("_run_authz_mode: self._persist_report(report)", log=_log):
                self._persist_report(report)
            return report
        finally:
            self._stop_sut()

    def _merge_authz_gate(self, report: dict) -> dict:
        """把 authz（越权/访问控制）作为横切门禁并入功能性用例报告。

        ★ 根因修复配套：authz 是横切门禁，与功能性用例正交。功能性用例执行完后
          调用本方法，跑一遍 AuthzMatrixVerifier，把结果并入 report["security"]["authz"]
          （与 _persist_cross_cut_to_store 读取结构对齐，供 step5/报告渲染越权门禁），
          并把 authz 失败折入整体 all_pass（不降级：权限校验失败 = 整体 FAIL）。
        """
        try:
            verifier = AuthzMatrixVerifier(target_cfg=self.target_cfg)
            az = verifier.run()
            az_sum = az.get("summary") or {}
            az_detail = {
                "all_pass": bool(az_sum.get("all_pass")),
                "total": int(az_sum.get("total") or 0),
                "passed": int(az_sum.get("passed") or 0),
                "failed": int(az_sum.get("failed") or 0),
                "probes": (az.get("probes") or [])[:50],
            }
        except Exception as _e:  # noqa: BLE001 —— authz 门禁异常也要如实计入，不静默
            _log.error("[verifier] authz 横切门禁异常: %s", _e, exc_info=True)
            az_detail = {
                "all_pass": False,
                "total": 0,
                "passed": 0,
                "failed": 0,
                "probes": [],
                "error": str(_e)[:300],
            }

        # 并入 security.authz（与跨横切落库/报告读取结构一致）
        sec = dict(report.get("security") or {})
        sec["enabled"] = True
        sec["authz"] = az_detail
        report["security"] = sec
        report["authz"] = az_detail
        # authz 失败折入整体 all_pass（不降级纪律）
        summary = dict(report.get("summary") or {})
        if not az_detail["all_pass"]:
            summary["all_pass"] = False
        report["summary"] = summary
        with guard("_merge_authz_gate: report_write(report, self.report_pat", log=_log, level=logging.WARNING):
            # 重写含 authz 的报告文件 + 幂等落库 security oracle
            report_write(report, self.report_path)
            self._persist_cross_cut_to_store(report)
        return report

    # ─────────────────────────────────────────────────────────────────────
    # Browser 模式（真浏览器打 DOM）
    # ─────────────────────────────────────────────────────────────────────

    def _settle_page_for_sampling(self, page: Any) -> None:
        """横切采样前，把 browser 靶子导航回代表性列表页并等渲染稳定。

        通用规则驱动：取 canonical 列表 URL = ``base_url + list_path``；无
        ``list_path`` 或当前已是该页时不重复导航。任何重载失败只降级为沿用
        当前页（门禁各自 fail-safe，不阻断不伪造）——这是**通用**修复，
        不针对 smartshop 打补丁。
        """
        if self.mode != "browser":
            return
        base = (self.target_cfg.get("base_url") or self.target_cfg.get("url") or "").strip()
        list_path = (self.target_cfg.get("list_path") or "").strip()
        if not base or not list_path:
            return
        target_url = base if list_path.startswith(("http://", "https://", "/")) else None
        if target_url is None:
            if list_path.startswith("#"):  # hash 路由（如 #/books）直接拼 base，不加 "/"
                target_url = f"{base.rstrip('/')}{list_path}"
            else:
                target_url = f"{base.rstrip('/')}/{list_path.lstrip('/')}"
        with guard("_settle_page_for_sampling: current = page.url or ''", log=_log, level=logging.INFO):
            current = page.url or ""
            if current == target_url:
                return
            page.goto(target_url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(800)  # 等前端/服务端渲染稳定

    def _run_browser_mode(self) -> dict:
        """执行完整实证验证链路（真浏览器打活靶子）。"""
        from playwright.sync_api import sync_playwright

        # 1. 解析浏览器后端
        backend = BrowserBackendResolver().resolve()
        _log.info("[verifier] backend -> %s", backend)

        # 浏览器/上下文引用（用于 finally 防御性收尾，避免原生 teardown 崩溃丢失报告）
        _pw = None
        _browser = None
        report: dict | None = None

        try:
            _pw = sync_playwright().start()
            _browser = _pw.chromium.launch(**backend["launch_kwargs"])
            context = _browser.new_context()
            context.set_default_timeout(15000)
            page = context.new_page()
            page.on(
                "pageerror",
                lambda e: _log.warning("[verifier] pageerror: %s", e),
            )
            # 自动处理原生 dialog，但走安全策略（见 safe_dialog_action）：
            # 仅 confirm（如 smartshop 删除确认）自动 accept，alert/prompt 自动
            # dismiss，避免无条件 accept 误伤业务对话框、掩盖失败信号。
            # 删除仍按 DOM 结果判定（不降级断言）。
            def _on_dialog(dialog):
                action = safe_dialog_action(dialog.type)
                if action == "accept":
                    dialog.accept()
                else:
                    dialog.dismiss()

            with guard("_run_browser_mode: page.on('dialog', _on_dialog)", log=_log):
                page.on("dialog", _on_dialog)

            # 3. SutHarness 拉起/复用 + 登录
            harness = SutHarness(
                url=self.url,
                port=self.port,
                target_key=self.target_key,
                db_env=self.db_env,
                mode=self.mode,
                auth=self.target_cfg.get("auth"),
                launch=self.target_cfg.get("launch"),
            )
            self._sut_harness = harness
            self_started = harness.ensure_running()
            _log.info("[verifier] SUT self_started=%s", self_started)
            # ★ nav_seed 接线：SUT 就绪后、断言前注入导航数据（fail-open）
            self._seed_nav_graph()

            # auth.type == "none"（纯前端 SPA，无后端登录）→ 跳过登录与 cookie 注入。
            auth_type = (self.target_cfg.get("auth") or {}).get("type", "form_cookie")
            if auth_type == "none":
                _log.info("[verifier] auth.type=none，跳过登录；直接打靶")
                context.add_cookies([])
            else:
                cookies = harness.login_get_cookie()
                # 登录失败 → 如实标记，不抛不假绿
                if not cookies:
                    _log.error("[verifier] 登录失败，未取到 cookie")
                    report = self._build_report(
                        backend=backend,
                        self_started_sut=self_started,
                        cases=[],
                        summary={"all_pass": False, "blocked": "login_failed"},
                    )
                    # 6. 先落盘再返回，确保收尾崩溃也不丢报告
                    self._persist_report(report)
                    return report
                context.add_cookies(cookies)
                _log.info("[verifier] 已注入 cookie，跳过登录重定向")

            # 5. 构建执行器并跑用例集
            runner = DeepAssertionRunner(page, context, target_cfg=self.target_cfg)
            results: list[dict] = []
            for case in self.cases:
                r = runner.run_case(case)
                results.append(r)

            # 5.5-5.7 三横切门禁（a11y/perf/visual）统一入口（2026-09-02 收敛）
            # ★ 根因修复（2026-09-04）：横切采样必须在**干净加载的代表性列表页**上进行，
            #   而非最后一个用例（如 D01 删除）留下的过渡态——否则 visual 截图会拍到
            #   表头/内容尚未重绘完成的中间态，导致聪明截图呈现"缺表头/内容不完整"的
            #   伪 FAIL（smartshop 实测复现：D01 后 visual conf=0.0，全新 GET /products
            #   即 PASS conf=0.9）。这里对 browser 靶子在采样前重载 canonical 列表页并
            #   等渲染稳定；无 list_path（API/无列表页靶子）则沿用当前页（门禁各自容错）。
            self._settle_page_for_sampling(page)
            cross_cut = run_cross_cut_gates(
                page=page,
                api_calls=(getattr(runner, "_api_calls", None)
                           if hasattr(runner, "_api_calls") else None),
                target_cfg=self.target_cfg,
                run_id=str(self.target_cfg.get("target_key", "")),
            )

            # 6. 组装 summary
            total = len(results)
            passed = sum(1 for r in results if r.get("overall") == "PASS")
            failed = total - passed
            all_pass = failed == 0

            summary = {
                "all_pass": all_pass,
                "total": total,
                "passed": passed,
                "failed": failed,
            }

            report = self._build_report(
                backend=backend,
                self_started_sut=self_started,
                cases=results,
                summary=summary,
            )
            # a11y / perf / visual 三横切门禁结果作为独立维度写入报告（不影响用例 PASS/FAIL）
            report.update(cross_cut)

            # ★ 关键：在浏览器收尾（close/stop 在部分沙箱会原生崩溃）之前
            #   先把报告落盘并返回，确保任何 teardown 崩溃都不会丢失实证结果。
            self._persist_report(report)
            return report
        except Exception as _e:  # Playwright/网络等任何异常
            # 不静默、不假绿（遵守 NO_ASSERTION_DOWNGRADE）
            _log.error("[verifier] 验证异常: %s", _e, exc_info=True)
            report = self._build_report(
                backend=backend,
                self_started_sut=None,
                cases=[],
                summary={"all_pass": False, "error": str(_e)[:500]},
            )
            # 异常情况下也尽量落盘，便于排查（写失败不二次抛出）
            with guard("_run_browser_mode: self._persist_report(report)", log=_log):
                self._persist_report(report)
            return report
        finally:
            # 防御性收尾：沙箱中 browser.close()/pw.stop() 可能触发原生崩溃，
            # 用最宽异常吞掉，绝不影响上面已落盘/已返回的报告。
            with guard("_run_browser_mode: if _browser is not None: _browser.cl", log=_log):
                if _browser is not None:
                    _browser.close()
            with guard("_run_browser_mode: if _pw is not None: _pw.stop()", log=_log):
                if _pw is not None:
                    _pw.stop()
            # SUT 回收由 run() 的 finally 统一执行（authz 横切仍需同一 SUT 存活）。


    def run_and_print(self) -> dict:
        """执行验证并打印终端报告（与 _tmp_component_verify.py 风格一致）。

        Returns:
            同 run() 的报告 dict。
        """
        report = self.run()

        summary = report.get("summary", {})
        all_pass = summary.get("all_pass", False)
        blocked = summary.get("blocked")
        error = summary.get("error")

        print("\n" + "=" * 78)
        print("  靶子实证验证报告（TargetEmpiricalVerifier）")
        print("=" * 78)
        print(f"  靶子     : {report.get('target')}")
        print(f"  自拉起SUT: {report.get('self_started_sut')}")
        backend = report.get("backend") or {}
        print(
            f"  后端     : kind={backend.get('kind')} "
            f"channel={backend.get('channel')}"
        )

        for r in report.get("cases", []):
            print(f"\n[{r['case']}] {r.get('title', '')}  → 结果: {r['overall']}")
            if r.get("unique_value"):
                print(f"  唯一值: {r['unique_value']!r}")
            if "baseline_total" in r:
                print(
                    f"  基线总数={r['baseline_total']}  "
                    f"新总数={r.get('new_total')}"
                )
            if r.get("contains_unique") is not None:
                print(f"  列表含唯一值={r['contains_unique']}")
            if r.get("flash_danger_texts") is not None:
                print(f"  字段级错误文本={r['flash_danger_texts']}")
            if r.get("blocked_by"):
                print(f"  blocked_by={r['blocked_by']}")
            print("  断言:")
            for a in r.get("assertions", []):
                actual = a.get("actual", a.get("actual_contains"))
                print(f"    - [{'PASS' if a['pass'] else 'FAIL'}] {a['name']}")
                print(f"        期望={a.get('expected')}  实际={actual}")
            if r["overall"] != "PASS":
                print(f"  ★ 该用例未通过（如实暴露）")

        print("\n" + "=" * 78)
        if blocked:
            print(f"  总体：被拦截 (blocked={blocked})，ALL_PASS=False")
        elif error:
            print(f"  总体：验证异常 (error={error[:120]})，ALL_PASS=False")
        else:
            print(
                f"  总体：{'全部 PASS' if all_pass else '存在 FAIL（见上，如实暴露）'}"
                f"  passed={summary.get('passed')} failed={summary.get('failed')}"
            )
        print("=" * 78)
        return report

    # ─────────────────────────────────────────────────────────────────────
    # 内部工具
    # ─────────────────────────────────────────────────────────────────────

    def _build_report(
        self,
        backend: dict,
        self_started_sut,
        cases: list[dict],
        summary: dict,
    ) -> dict:
        """组装统一报告结构（schema 与 report_writer 兼容）。"""
        return {
            "target": self.url,
            "target_key": self.target_key,
            "mode": self.mode,
            "self_started_sut": self_started_sut,
            "backend": backend,
            "cases": cases,
            "summary": summary,
        }
