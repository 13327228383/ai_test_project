# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""[未复核-需 GLM/DeepSeek 把关] 深断言真实执行器（DeepAssertionRunner）。

★ 职责：将临时脚本 `scripts/e2e_engine/real_browser_verify.py` 中的
  `run_p01` / `run_negative` / `_count_total` 泛化为引擎常驻执行器。

★ 核心约束（design doc Part A §0 / 共享知识 §8）：
  - 浏览器交互一律用系统 Chrome（lifecycle 已通过 BrowserBackendResolver 保证）。
  - 不降级断言硬纪律：NO_ASSERTION_DOWNGRADE=True；任何找不到元素/按钮必须
    如实 FAIL 或 blocked，禁止退化为"页面加载"假 PASS。
  - 分页失真克服：列表总数必须 _count_total（逐页累加），不能用当前页行数。
  - selector 来自靶子 data-testid / target_cfg；通用靶子由 target_cfg 覆盖。

★ 多模式分派（docs/system_design_expend.md §1.1）：
  - DeepAssertionRunner 按 target_cfg["mode"] 分派到：
      * BrowserAssertionRunner（既有 hospital 逻辑抽象，支持 pagination.type=none /
        blocked_by=client_validation）；
      * ApiAssertionRunner（新增，纯 requests，见 api_assertion_runner.py）。
  - run_case(case) 委托给具体执行器。

★ 如实区分原生校验 vs 后端拒绝（design doc Part A §5 待明确事项 2）：
  - B01 空提交被浏览器原生 HTML5 required 拦截（仍在表单、无 DOM .flash-danger）
    → 返回加 blocked_by="browser_native_validation"，行数不变仍 PASS，
    但字段级错误维度 pass:false 如实记录（不掩盖）。
  - spa_demo 用 validate() 客户端拦截 → blocked_by="client_validation"。
  - N01 若后端无校验导致行数+1 → 如实 FAIL（这正是要暴露的）。
"""

import logging
import random
import re
import string
import time

from src.pipeline.steps.step4.empirical.constants import (
    API_MODE,
    BLOCKED_BY_BROWSER_NATIVE,
    NO_ASSERTION_DOWNGRADE,
)

_log = logging.getLogger(__name__)

# ── 分页文本解析（克服分页失真 + 防止静默退化假 PASS）────────────────────
# ★ 通用契约（NO_ASSERTION_DOWNGRADE）：列表总数必须由"第X/Y页"解析出的总页数
#   逐页累加得到，禁止"解析不出就退化为当前页行数"的静默降级（会制造假 PASS）。
PAGER_RE = re.compile(r"第\s*\d+\s*/\s*(\d+)\s*页")


class PaginationUnavailable(RuntimeError):
    """分页信息不可用（靶子契约偏离），用于阻止静默退化假 PASS。"""


def parse_pager_total_pages(text: str) -> int | None:
    """从分页控件文本解析总页数（"第X/Y页" 格式，允许中文/英文空格）。

    Args:
        text: 分页控件 inner_text（如 "第 1 / 3 页" / "第2/5页"）。

    Returns:
        总页数 int（Y）；无法识别返回 None——调用方**必须如实处理**，
        不得退化为当前页行数制造假 PASS。
    """
    if not text:
        return None
    m = PAGER_RE.search(text)
    return int(m.group(1)) if m else None


def classify_negation_blocked_by(
    *,
    is_boundary: bool,
    still_on_form: bool,
    rows_unchanged: bool,
    case: dict,
    default_blocked_by: str | None = None,
) -> str | None:
    """B01 边界场景的 blocked_by 归类（纯函数，便于单元测试与配置兜底）。

    ★ 去靶子化：判定不再依据 target_key 分支，而完全由 per-target 配置驱动——
      - 优先级：case 显式 blocked_by > target_cfg["default_blocked_by"]。
      - 两者皆缺省时，采用通用浏览器兜底 browser_native_validation
        （HTML5 required 是浏览器表单的基线行为，非任何特定靶子的专属约定）。
      各浏览器靶子应在 target_cfg 显式声明 default_blocked_by（spa=client_validation、
      smartshop=server_validation、hospital=browser_native），避免落入通用兜底。
    """
    if not (is_boundary and still_on_form and rows_unchanged):
        return None
    cfg_blocked = case.get("blocked_by") or default_blocked_by
    if cfg_blocked:
        return cfg_blocked
    return BLOCKED_BY_BROWSER_NATIVE


# 通用 selector 默认映射（空）：引擎不内置任何靶子专属 selector，由 target_cfg 注入。
# 键约定（跨靶子统一，见 docs §3.2）：list_items / btn_new / input_title /
# input_price / input_stock / btn_save / form / field_error / pagination_current。
# 浏览器靶子若未在 target_cfg["selectors"] 提供必需键，将如实 KeyError/配置缺失报错，
# 而非静默复用某个特定系统（历史上曾默认 hospital）的 selector 造成误判。
_DEFAULT_SELECTORS: dict[str, str] = {}

# 正向新增的通用字段填充默认（browser 靶子；兜底，非任何特定靶子绑定）。
# title=<unique> 唯一值 / price=9.9 / stock=5 —— 价格·库存是通用 CRUD 字段语义，
# 各靶子可用 target_cfg["fill"]["positive"] 覆盖（如 hospital 把 price 覆盖为 <mrn>）。
_DEFAULT_POSITIVE_FILL: dict[str, str] = {
    "title": "<unique>",
    "price": "9.9",
    "stock": "5",
}

# 负向（N01）的通用字段填充默认（兜底，非任何特定靶子绑定）。
# 与历史 `else`（hospital 等兜底）分支行为等价：填非法 title（暴露后端字段级错误），
# price/stock 留空（不填）——缺省等价“未声明 fill.negative 的 browser 靶子”的兜底，
# 让服务端/原生校验拦截。各靶子用 target_cfg["fill"]["negative"] 覆盖
# （spa=title 留空+price/stock 合法；smartshop=price<=0 隔离到价格维度）。
_DEFAULT_NEGATIVE_FILL: dict[str, str] = {
    "title": "!@#$%",
    "price": None,
    "stock": None,
}


class DeepAssertionRunner:
    """按 target_cfg["mode"] 分派到 BrowserAssertionRunner / ApiAssertionRunner。"""

    def __init__(
        self,
        page=None,
        context=None,
        target_cfg: dict | None = None,
    ) -> None:
        """初始化。

        Args:
            page: Playwright Page 对象（browser 模式使用；api 模式可传 None）。
            context: Playwright BrowserContext（保留以便后续扩展）。
            target_cfg: 靶子配置，含 "mode" / "selectors" / "base_url" 等。
        """
        self.target_cfg = target_cfg or {}
        mode = self.target_cfg.get("mode", "browser")

        if mode == API_MODE:
            # API 模式：探针套件随靶子走 —— 按 target_cfg["probes"] 声明用
            # importlib 按文件路径动态加载（nav_seed 同款去靶子化模式）。
            # fail-closed：未声明 probes 的 api 靶子如实 BLOCKED，不静默回落
            # 到任何靶子的业务探针（引擎树内禁止靶子端点/错误码）。
            import importlib.util
            from pathlib import Path as _Path

            from src.pipeline.steps.step4.empirical.constants import BLOCKED_BY_MISSING_PROBES

            probes_cfg = self.target_cfg.get("probes") or {}
            probe_file = probes_cfg.get("file")
            probe_cls = probes_cfg.get("cls")
            if not probe_file or not probe_cls:
                _log.error(
                    "[DeepAssertion] api 靶子未声明 probes.file/cls，如实 BLOCKED（不回落）: %s",
                    self.target_cfg.get("target_key"),
                )
                self._impl = _BlockedApiRunner(
                    reason=f"target_cfg 未声明 probes（file/cls），无法执行 API 探针: {self.target_cfg.get('target_key')}",
                    code=BLOCKED_BY_MISSING_PROBES,
                )
            else:
                probe_path = _Path(probe_file)
                if not probe_path.is_absolute():
                    probe_path = _Path(__file__).resolve().parents[5] / probe_file
                spec = importlib.util.spec_from_file_location(
                    f"_target_probes_{self.target_cfg.get('target_key', 'x')}", probe_path
                )
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                _probe_cls = getattr(mod, probe_cls)
                self._impl = _probe_cls(
                    target_cfg=self.target_cfg,
                    token=self.target_cfg.get("_token"),
                )
        else:
            self._impl = BrowserAssertionRunner(
                page=page, context=context, target_cfg=self.target_cfg
            )

    # ─────────────────────────────────────────────────────────────────────
    # 公开 API（委托给具体执行器）
    # ─────────────────────────────────────────────────────────────────────

    def run_case(self, case: dict) -> dict:
        """执行单条深断言用例（委托）。"""
        return self._impl.run_case(case)


class BrowserAssertionRunner:
    """对真实页面执行深断言（P01 正向 / N01 负向 / B01 边界）。

    支持通用靶子：
      - pagination.type=="none"：直接数当前 DOM，不逐页累加（spa 前端全量渲染）。
      - blocked_by=="client_validation"：spa validate() 客户端拦截场景。
    """

    def __init__(
        self,
        page,
        context=None,
        target_cfg: dict | None = None,
    ) -> None:
        """初始化。

        Args:
            page: Playwright Page 对象（已注入登录 cookie）。
            context: Playwright BrowserContext（保留以便后续扩展）。
            target_cfg: 靶子配置，可含 "selectors" 覆盖默认 selector 映射、
                "base_url" 覆盖列表基地址、"pagination" 分页策略、
                "list_path" 列表相对路径（各靶子在 target_cfg 自声明，无通用默认）。
        """
        self.page = page
        self.context = context
        self.target_cfg = target_cfg or {}
        # selector 优先级：target_cfg.selectors > 通用默认（空，无系统级兜底）
        cfg_selectors = self.target_cfg.get("selectors") or {}
        self.sel = {**_DEFAULT_SELECTORS, **cfg_selectors}
        self.base_url = (
            self.target_cfg.get("base_url")
            or self.target_cfg.get("url")
            or ""
        ).rstrip("/")
        if not self.base_url:
            # ★ 不降级：base_url 缺失会导致 page.goto 收到相对 URL 而失败，
            #   必须明确报错，而非静默产生 "Cannot navigate to invalid URL"。
            raise ValueError(
                "BrowserAssertionRunner 需要 target_cfg['base_url']（靶子绝对基地址，"
                "如 http://127.0.0.1:5081），否则无法导航；禁止相对 URL 静默失败。"
            )
        # 列表相对路径（跨靶子可配，各靶子在 target_cfg 自声明；缺省为空 = 直抵 base_url）。
        self.list_path = self.target_cfg.get("list_path", "")
        # 列表文本容器：优先 target_cfg["list_text_sel"]，其次 selectors["list_text"]，
        # 最后回退 selectors["list_items"]（通用键，不再硬编码特定系统的容器 selector）。
        self.list_text_sel = (
            self.target_cfg.get("list_text_sel")
            or self.sel.get("list_text")
            or self.sel.get("list_items")
        )
        # 分页策略：none=直接数 DOM；server_page_param=逐页累加。
        self.pagination = self.target_cfg.get("pagination") or {"type": "none"}
        self.pagination_type = self.pagination.get("type", "none")

    # ─────────────────────────────────────────────────────────────────────
    # 公开 API
    # ─────────────────────────────────────────────────────────────────────

    def run_case(self, case: dict) -> dict:
        """执行单条深断言用例。

        Args:
            case: 含 case_id（P01/N01/B01）、可选 fill_valid / fill_invalid 等。

        Returns:
            {case, title, trajectory, assertions:[{name,expected,actual,pass}],
             overall, ...}
        """
        case_id = case.get("case_id", "UNKNOWN")
        if case_id == "P01":
            return self._run_p01(case)
        if case_id in ("N01", "B01"):
            fill_invalid = case.get("fill_invalid", case_id == "N01")
            return self._run_negative(case, case_id, fill_invalid)
        if case_id == "D01":
            return self._run_delete(case)
        _log.error("[BrowserAssertionRunner] 未知 case_id=%s", case_id)
        return {
            "case": case_id,
            "title": "未知用例",
            "trajectory": [f"未知 case_id={case_id}"],
            "assertions": [],
            "overall": "FAIL",
        }

    # ─────────────────────────────────────────────────────────────────────
    # 字段填充策略（per-target 下沉 · 去靶子化）
    # ─────────────────────────────────────────────────────────────────────

    def _resolve_fill_value(self, token):
        """解析 fill 策略中的字段值 token。

        token 语义（与 target_cfgs.py 各靶子 fill 配置对应）：
          - None         → 跳过该字段（不填）。
          - "<unique>"   → 唯一值（"验证记录"+随机6位），用于需唯一的标题/名称。
          - "<mrn>"      → 唯一 MRN（"P"+随机6位），用于 hospital 的 MRN 字段。
          - "<empty>"    → 空串（填空触发 required/校验）。
          - 其他字符串   → 字面量直接填入。
        """
        if token is None:
            return None
        if token == "<unique>":
            return "验证记录" + self._rand_suffix()
        if token == "<mrn>":
            return "P" + self._rand_suffix()
        if token == "<empty>":
            return ""
        return token

    def _apply_fill_spec(self, spec, overrides=None) -> dict:
        """按 fill 策略填表单，返回 {field_key: 实际填入值}。

        Args:
            spec: 字段填充策略 dict（键 title/price/stock，值语义见 _resolve_fill_value；
                  可选 confirm 键 = 勾选的选择器 key 列表，如 ["input_confirm"]）。
            overrides: 覆盖值（如 case["fill_valid"]），字段优先于 spec。

        通用去重：input_stock 与 input_price 指向同一 DOM 时 stock 跳填
        （hospital 二者同映射 MRN，避免二次填充覆盖 MRN 语义）。
        """
        overrides = overrides or {}
        filled: dict[str, str] = {}
        for field_key in ("title", "price", "stock"):
            token = overrides.get(field_key, spec.get(field_key))
            sel = self.sel.get("input_" + field_key)
            val = self._resolve_fill_value(token)
            if val is None or not sel:
                continue
            if field_key == "stock" and sel == self.sel.get("input_price"):
                continue
            self.page.fill(sel, val)
            filled[field_key] = val
        # 勾选型字段（如全能力靶子的「信息确认」checkbox）：确认掩码状态前
        # 先勾选，避免客户端校验拦截导致提交永远停在表单页（P01 假 FAIL 根因）。
        for confirm_sel_key in spec.get("confirm") or overrides.get("confirm") or []:
            sel = self.sel.get(confirm_sel_key)
            if not sel:
                continue
            with guard("_apply_fill_spec: self.page.check(sel)", log=_log, level=logging.WARNING):
                self.page.check(sel)
                filled["confirm_" + confirm_sel_key] = True
        return filled

    # ─────────────────────────────────────────────────────────────────────
    # P01 正向
    # ─────────────────────────────────────────────────────────────────────

    def _run_p01(self, case: dict) -> dict:
        """P01 正向：新增 → 列表总数+1 且含唯一值。"""
        result: dict = {
            "case": "P01",
            "title": "正向流程：新增记录",
            "trajectory": [],
            "assertions": [],
            "overall": "FAIL",
        }
        unique_name = "验证记录" + self._rand_suffix()
        result["unique_value"] = unique_name

        try:
            # 1. 进列表，基线总数
            self._goto_list()
            baseline = self._count_total()
            result["baseline_total"] = baseline
            result["trajectory"].append(f"进入列表，基线总数={baseline}")

            # 2. 点新增
            self.page.click(self.sel["btn_new"])
            self.page.wait_for_selector(self.sel["form"], timeout=8000)
            result["trajectory"].append("点击『新增』→ 打开新增表单")

            # 3. 填值：完全由 target_cfg["fill"]["positive"] 派发（去靶子化）。
            #   hospital 在配置中把 price 覆盖为 <mrn>（MRN 语义）；spa/smartshop
            #   等未声明 fill.positive 的靶子走 _DEFAULT_POSITIVE_FILL 兜底。
            #   case["fill_valid"] 可作字段级覆盖（显式 title/price/stock）。
            fill_spec = (self.target_cfg.get("fill") or {}).get("positive") or _DEFAULT_POSITIVE_FILL
            filled = self._apply_fill_spec(fill_spec, overrides=case.get("fill_valid") or {})
            unique_name = filled.get("title") or unique_name
            result["unique_value"] = unique_name
            result["trajectory"].append(
                f"fill 标题={filled.get('title')!r}, price={filled.get('price')!r}, "
                f"stock={filled.get('stock')!r}（按 fill.positive 配置派发）"
            )

            # 4. 保存 → 回到列表 → 等待 DB 提交 → 计数（克服分页失真）。
            #    不用 expect_navigation 包裹：避免导航竞态导致计数读到旧状态。
            self.page.click(self.sel["btn_save"])
            # 等待列表 DOM 就绪（兼容 hash 路由 spa / 服务端重定向）。
            if "#" in self.list_path:
                self.page.wait_for_function(
                    "document.querySelector('#view') && "
                    "document.querySelector('#view').children.length > 0",
                    timeout=8000,
                )
            else:
                self.page.wait_for_selector(self.sel["list_items"], timeout=8000)
            time.sleep(2.0)
            # 显式回到列表首页（确保落点在已知页，且 DB 已提交）。
            self._goto_list()
            time.sleep(0.5)
            result["trajectory"].append("点击『保存』→ 重定向回列表")

            # 5. 断言：总数+1 且含唯一值
            new_total = self._count_total()
            # 唯一值断言跨页搜索：分页靶子新增记录按 id 升序排最后（如在末页），
            # 只读当前页必然漏判（P01 假 FAIL）。搜索后恢复第 1 页落点。
            contains = self._search_list_text(unique_name)
            result["new_total"] = new_total
            result["contains_unique"] = contains

            a1 = {
                "name": "列表总数 == 基线+1",
                "expected": baseline + 1,
                "actual": new_total,
                "pass": new_total == baseline + 1,
            }
            a2 = {
                "name": "列表含唯一值文本",
                "expected": unique_name,
                "actual_contains": contains,
                "pass": contains,
            }
            result["assertions"] = [a1, a2]
            result["overall"] = "PASS" if (a1["pass"] and a2["pass"]) else "FAIL"
        except Exception as _e:
            # 不降级：如实 FAIL 并记录异常原因
            _log.error("[P01] 执行异常: %s", _e, exc_info=True)
            result["trajectory"].append(f"异常: {str(_e)[:200]}")
            result["error"] = str(_e)[:200]
            result["overall"] = "FAIL"
        return result

    # ─────────────────────────────────────────────────────────────────────
    # N01 / B01 负向
    # ─────────────────────────────────────────────────────────────────────

    def _run_negative(self, case: dict, case_id: str, fill_invalid: bool) -> dict:
        # [未复核-需 GLM/DeepSeek 把关] blocked_by 判定核心分支（配置值/spa/client 推导）。
        """N01/B01 负向：非法/边界提交 → 字段级错误可见 + 列表行数不变。

        B01 空提交：
          - hospital：被浏览器原生 HTML5 required 拦截 →
            blocked_by="browser_native_validation"。
          - spa_demo：被 validate() 客户端拦截 → blocked_by="client_validation"。
        行数不变仍 PASS，但字段级错误维度如实 pass:false（不掩盖）。
        N01 若后端无校验导致行数+1 → 如实 FAIL。
        """
        result: dict = {
            "case": case_id,
            "title": "负向/边界校验",
            "trajectory": [],
            "assertions": [],
            "overall": "FAIL",
        }
        try:
            self._goto_list()
            baseline = self._count_total()
            result["baseline_total"] = baseline
            result["trajectory"].append(f"进入列表，基线总数={baseline}")

            self.page.click(self.sel["btn_new"])
            self.page.wait_for_selector(self.sel["form"], timeout=8000)
            result["trajectory"].append("点击『新增』→ 打开新增表单")

            if fill_invalid:
                # N01 负向：填表数据完全由 target_cfg["fill"]["negative"] 派发
                # （去靶子化，无靶子名分支）。spa_demo / smartshop / hospital 已各自
                # 声明；未声明走 _DEFAULT_NEGATIVE_FILL（title=!@#$% 非法、price/stock
                # 留空），行为等价历史 else 兜底，让原生/客户端/服务端校验拦截。
                fill_spec = (self.target_cfg.get("fill") or {}).get("negative") or _DEFAULT_NEGATIVE_FILL
                self._apply_fill_spec(fill_spec, overrides=case.get("fill_valid") or {})
                result["trajectory"].append(
                    "N01 按 fill.negative 配置填非法值（去靶子化派发）"
                )
            else:
                # B01：边界——清空 required 字段（input_title），触发校验拦截。
                self.page.fill(self.sel["input_title"], "")
                result["trajectory"].append("清空 标题（边界：空 required）")

            # 尝试保存（包住导航，确保 POST 完成后再断言）
            with guard("_run_negative: with self.page.expect_navigation(tim", log=_log, level=logging.WARNING):
                with self.page.expect_navigation(timeout=10000):
                    self.page.click(self.sel["btn_save"])
            # 等待表单稳定（服务端校验后重定向回表单并渲染 flash）。
            with guard("_run_negative: self.page.wait_for_selector(self.sel", log=_log):
                self.page.wait_for_selector(self.sel["form"], timeout=8000)
            time.sleep(1.5)

            # 仍在表单？判断是否被拦截未提交
            still_on_form = self.page.query_selector(self.sel["form"]) is not None
            result["still_on_form"] = still_on_form
            result["current_url"] = self.page.url
            result["trajectory"].append(
                f"点击『保存』→ 仍在表单={still_on_form}, url={self.page.url}"
            )

            # 字段级错误可见性（DOM 真实选择器）
            # 服务端校验重定向回表单后渲染 flash；多等一会确保 DOM 稳定。
            time.sleep(1.5)
            danger = self.page.query_selector_all(self.sel["field_error"])
            danger_texts = [d.inner_text().strip() for d in danger if d.inner_text()]
            # 兜底：检查 flash 容器（如 hospital 的 [data-testid='msg-flash']）。
            if not danger_texts:
                msg = self.page.query_selector("[data-testid='msg-flash']")
                if msg and msg.inner_text().strip():
                    danger_texts = [
                        t.strip()
                        for t in msg.inner_text().split("\n")
                        if t.strip()
                    ]
            field_error_visible = len(danger_texts) > 0
            result["flash_danger_texts"] = danger_texts

            # 列表总数（被拦截仍在表单 → 回列表再数）
            if still_on_form:
                self._goto_list()
            new_total = self._count_total()
            result["new_total"] = new_total
            rows_unchanged = new_total == baseline

            a1 = {
                "name": "字段级错误可见",
                "expected": "可见",
                "actual": danger_texts if field_error_visible else "未见（可能为校验拦截）",
                "pass": field_error_visible,
            }
            a2 = {
                "name": "列表总数不变（共 N 条）",
                "expected": baseline,
                "actual": new_total,
                "pass": rows_unchanged,
            }
            result["assertions"] = [a1, a2]

            # 如实区分拦截类型（统一走纯函数 classify_negation_blocked_by）：
            #  - case 显式 blocked_by 优先（如 spa_demo B01 client_validation）。
            #  - 否则按 target_cfg.default_blocked_by 兜底（修复配置漏填：smartshop
            #    无 HTML5 required，漏填时不应误判 browser_native_validation）。
            #  - 再按靶子特征推导：spa_demo → client_validation；其余 → browser_native。
            if (not fill_invalid) and still_on_form and rows_unchanged:
                result["blocked_by"] = classify_negation_blocked_by(
                    is_boundary=True,
                    still_on_form=still_on_form,
                    rows_unchanged=rows_unchanged,
                    case=case,
                    default_blocked_by=self.target_cfg.get("default_blocked_by"),
                )
            # 核心不变式：行数不变即视为该路径被正确拒绝
            result["overall"] = "PASS" if rows_unchanged else "FAIL"
        except Exception as _e:
            _log.error("[%s] 执行异常: %s", case_id, _e, exc_info=True)
            result["trajectory"].append(f"异常: {str(_e)[:200]}")
            result["error"] = str(_e)[:200]
            result["overall"] = "FAIL"
        return result

    # ─────────────────────────────────────────────────────────────────────
    # D01 删除路径
    # ─────────────────────────────────────────────────────────────────────

    def _run_delete(self, case: dict) -> dict:
        """D01 删除：删掉一条**可删除**记录 → 列表行数 -1 且被删标题消失。

        兼容两类靶子：
          - spa_demo（pagination=none，localStorage 全量渲染）：数 #view li；
            删除按钮 = button.btn.danger（每条 li 内）。
          - smartshop（pagination=browser，服务端分页）：数当前页行；删除按钮 =
            delete_btn 选择器（每条 tr 内删除表单的 submit）。

        ★ 不假设首行可删（去"首行删除"隐性耦合）：业务规则可能保护部分行
          （如 fullcov_sut 的 maintenance 状态资产禁止删除，SUT 会 flash 拒绝并
          重定向但行数不变）。引擎按行序尝试，遇到被拒的行（总数不变）换下一行，
          直到某行成功删除（总数 -1）为止；全部被拒 → 如实 FAIL 并说明原因。
        不降级：行数未 -1 / 标题仍出现 / 无可删除行 → 如实 FAIL。
        """
        result: dict = {
            "case": "D01",
            "title": "删除商品后列表-1且该项消失",
            "trajectory": [],
            "assertions": [],
            "overall": "FAIL",
        }
        try:
            # 1. 进列表，基线行数（spa 用当前 DOM；smartshop 用分页累加总数）
            self._goto_list()
            baseline = self._count_total()
            result["baseline_total"] = baseline
            result["trajectory"].append(f"进入列表，基线行数={baseline}")

            # 2. 逐行尝试删除，直到找到一条可删除行（总数 -1）
            max_try = 5
            deleted_title = ""
            new_total = baseline
            for _i in range(max_try):
                self._goto_list()  # 回到第 1 页，保证行索引稳定
                rows = self.page.query_selector_all(self.sel["list_items"])
                if _i >= len(rows):
                    break
                row = rows[_i]
                title = self._row_title(row)
                btn = row.query_selector(self.sel["delete_btn"])
                if btn is None:
                    continue
                btn.click()
                # 等待重渲染（spa 异步重渲染 / smartshop 服务端重定向回列表）
                self.page.wait_for_selector(self.sel["list_items"], timeout=8000)
                time.sleep(1.0)
                self._goto_list()
                time.sleep(0.3)
                new_total = self._count_total()
                if new_total == baseline - 1:
                    deleted_title = title
                    result["trajectory"].append(
                        f"删除第 {_i + 1} 行 {title!r} → 总数 {baseline}→{new_total}"
                    )
                    break
                # 未被删除（业务规则拒绝/无删除副作用）→ 换下一行
                result["trajectory"].append(
                    f"第 {_i + 1} 行 {title!r} 未被删除（总数仍 {new_total}），换下一行"
                )

            if not deleted_title:
                result["new_total"] = new_total
                result["trajectory"].append(
                    f"尝试 {max_try} 行均未被删除，D01 FAIL（可能均受业务规则保护）"
                )
                result["error"] = "无可删除行：前 %d 行删除后列表总数均未减少" % max_try
                result["assertions"] = [{
                    "name": "列表行数 == 基线-1",
                    "expected": baseline - 1,
                    "actual": new_total,
                    "pass": False,
                }]
                result["overall"] = "FAIL"
                return result

            result["deleted_title"] = deleted_title
            result["new_total"] = new_total

            # 3. 标题消失断言（列表文本不再包含被删标题）
            list_text = self._list_text()
            disappeared = deleted_title not in list_text
            result["title_disappeared"] = disappeared
            result["trajectory"].append(
                f"重渲染后行数={new_total}，被删标题消失={disappeared}"
            )

            a1 = {
                "name": "列表行数 == 基线-1",
                "expected": baseline - 1,
                "actual": new_total,
                "pass": new_total == baseline - 1,
            }
            a2 = {
                "name": "被删标题从列表消失",
                "expected": "消失",
                "actual_disappeared": disappeared,
                "pass": disappeared,
            }
            result["assertions"] = [a1, a2]
            result["overall"] = (
                "PASS" if (a1["pass"] and a2["pass"]) else "FAIL"
            )
        except Exception as _e:
            _log.error("[D01] 执行异常: %s", _e, exc_info=True)
            result["trajectory"].append(f"异常: {str(_e)[:200]}")
            result["error"] = str(_e)[:200]
            result["overall"] = "FAIL"
        return result

    def _row_title(self, row) -> str:
        """取单个列表行的标题文本（D01 消失断言的可比对值）。

        优先级（去靶子化）：
          1) target_cfg selectors.row_title_sel —— 靶子自声明标题单元格；
          2) 通用标题约定：.product-name（smartshop）/ <strong>（spa_demo）；
          3) 第一个 <td> 文本。
        绝不回退到整行 inner_text（会产出跨列拼接串，如
        "3\\t名称\\t分类\\t…" ，既非真标题也无法作为稳定比对值）。
        """
        sel = self.sel.get("row_title_sel")
        if sel:
            node = row.query_selector(sel)
            if node is not None:
                return (node.inner_text() or "").strip()
        for conv in (".product-name", "strong"):
            node = row.query_selector(conv)
            if node is not None:
                return (node.inner_text() or "").strip()
        cells = row.query_selector_all("td")
        if cells:
            return (cells[0].inner_text() or "").strip()
        return ""

    def _first_row_title(self) -> str:
        """取列表第一条记录的标题文本（用于 D01 消失断言）。取不到返回空串。"""
        rows = self.page.query_selector_all(self.sel["list_items"])
        if not rows:
            return ""
        return self._row_title(rows[0])

    # ─────────────────────────────────────────────────────────────────────
    # 列表计数工具（克服分页失真）
    # ─────────────────────────────────────────────────────────────────────

    def _count_rows_current_page(self) -> int:
        """统计【当前页】可见行数（真实 selector）。"""
        rows = self.page.query_selector_all(self.sel["list_items"])
        return len(rows)

    def _count_total(self) -> int:
        # [未复核-需 GLM/DeepSeek 把关] 分页累加边界（正则脆弱性 + 导航竞态，见 §14）。
        """真实统计列表总数。

        ★ 鲁棒性分层（克服「分页正则退化」：靶子文案一变正则即失效）：
          1) 结构化总数优先：若配置了 pagination.total_field_sel（DOM 属性，
             如 [data-testid='total-count'] 的 data-total），直接读数字，
             不依赖任何文案格式——最稳。
          2) 文本分页解析：解析"第X/Y页"得总页数 Y，逐页累加每页行数
             （克服分页失真）。browser 类型解析不出即 raise（不假绿）；
             server_page_param 沿用 lenient 兜底（当前页行数）保持历史行为。

        - pagination.type=="none"：直接数当前 DOM（spa 前端全量渲染）。
        """
        if self.pagination_type == "none":
            return self._count_rows_current_page()

        # ① 结构化总数优先（最稳，不依赖 UI 文案）
        total_field_sel = self.pagination.get("total_field_sel")
        if total_field_sel:
            total = self._read_structured_total(total_field_sel)
            if total is not None:
                return total

        # ② 文本分页解析（"第X/Y页"）。
        #    ★ 优先级修正（2026-09-28）：pagination.current_sel 是各靶子 pagination
        #      契约里声明的权威选择器（fullcov_sut=list-summary 含"共N条/第X/Y页"）；
        #      selectors.pagination_current 是遗留散落键（纯页码数字，文本无可解析）。
        #      原写法前者被后者遮蔽 → fullcov_sut 总数恒退化为当前页行数 → P01/D01
        #      计数断言假 FAIL。现改为契约键优先，其余靶子两键一致不受影响。
        sel_pager = self.pagination.get("current_sel", "") or self.sel.get(
            "pagination_current", ""
        )
        if not sel_pager:
            return self._count_rows_current_page()
        try:
            self.page.wait_for_selector(sel_pager, timeout=5000)
        except Exception:
            return self._count_rows_current_page()
        el = self.page.query_selector(sel_pager)
        if not el:
            return self._count_rows_current_page()
        parsed = parse_pager_total_pages(el.inner_text() or "")
        if parsed is None:
            # 分页文本无法解析：
            #  - browser 类型（smartshop 合同明确要求"第X/Y页"）：解析不出即属靶子
            #    契约偏离，必须如实暴露（raise），禁止静默退化成"当前页行数"假 PASS。
            #  - server_page_param（hospital 等）：沿用 lenient 兜底（当前页行数），
            #    保持历史行为不回归。
            if self.pagination_type == "browser":
                raise PaginationUnavailable(
                    "分页控件文本不符合'第X/Y页'契约，无法可靠统计总数；"
                    "禁止退化为当前页行数（违反 NO_ASSERTION_DOWNGRADE）"
                )
            return self._count_rows_current_page()
        total_pages = parsed

        # 回到第 1 页再逐页累加
        self._goto_list(page=1)
        self.page.wait_for_selector(self.sel["list_items"], timeout=8000)
        total = 0
        for _pg in range(1, total_pages + 1):
            if _pg > 1:
                self._goto_list(page=_pg)
                self.page.wait_for_selector(self.sel["list_items"], timeout=8000)
            total += self._count_rows_current_page()
        # ★ 页落点不变量：累加结束必须恢复第 1 页。否则后续基于"第一条记录"
        #   的操作（如 D01 取待删行）会落在累加停驻的末页 → 删错对象（D01 假 FAIL）。
        self._goto_list(page=1)
        return total

    def _read_structured_total(self, sel: str) -> int | None:
        """从结构化 DOM 属性读取真实总数（如 data-total）。

        ★ 通用契约（克服分页正则退化）：优先用结构化数字，避免依赖
          "第X/Y页"等易变文案。属性名可配（total_attr，默认 data-total）。
        """
        try:
            self.page.wait_for_selector(sel, timeout=5000)
        except Exception:
            return None
        el = self.page.query_selector(sel)
        if not el:
            return None
        attr = self.pagination.get("total_attr", "data-total")
        raw = el.get_attribute(attr) or el.inner_text() or ""
        raw = raw.strip().replace(",", "")
        if raw.isdigit():
            return int(raw)
        return None

    def _goto_list(self, page: int | None = None) -> None:
        """导航到列表（list_path 来自 target_cfg，缺省为空 = 直抵 base_url）。

        - server_page_param 型靶子：可选 ?page=N 分页。
        - hash 路由靶子：list_path 含 "#"（如 "#/books"），无需 page 参数。
        """
        if self.base_url:
            url = self.base_url + self.list_path
        else:
            url = self.list_path
        if page and page > 1 and "?" not in url:
            url += "?page=%d" % page
        self.page.goto(url, wait_until="domcontentloaded")
        self.page.wait_for_selector(self.sel["list_items"], timeout=8000)

        # spa 等 hash 路由靶子：等待视图渲染（DOM 异步构建）。
        if "#" in self.list_path:
            with guard("_goto_list: self.page.wait_for_function( 'docume", log=_log):
                self.page.wait_for_function(
                    "document.querySelector('#view') && "
                    "document.querySelector('#view').children.length > 0",
                    timeout=8000,
                )

    def _list_text(self) -> str:
        """取列表区域可见文本（用于唯一值可见性断言）。

        容器由 list_text_sel 指定（来自 target_cfg["list_text_sel"] /
        selectors["list_text"]，通用键，各靶子自声明）。
        """
        container = self.page.query_selector(self.list_text_sel)
        if not container:
            return ""
        return (container.inner_text() or "").strip()

    def _search_list_text(self, needle: str) -> bool:
        """跨分页搜索唯一值：新增记录按 id 升序排末页时，
        只读当前页必然漏判（P01 假 FAIL）。搜索后恢复第 1 页落点。

        分页信息不可用/无分页时，退化为仅查当前页（不假绿也不误报）。
        """
        if needle and needle in self._list_text():
            return True
        total_pages = self._total_pages()
        if not total_pages or total_pages <= 1:
            return False
        with guard("_search_list_text: for _pg in range(2, total_pages + 1)", log=_log):
            for _pg in range(2, total_pages + 1):
                self._goto_list(page=_pg)
                if self.page.wait_for_selector(
                    self.sel["list_items"], timeout=8000
                ):
                    if needle and needle in self._list_text():
                        self._goto_list(page=1)
                        return True
        self._goto_list(page=1)
        return False

    def _total_pages(self) -> int | None:
        """解析分页总页数（单源：与 _count_total 共用 PAGER_RE 契约）。"""
        sel_pager = self.pagination.get("current_sel", "") or self.sel.get(
            "pagination_current", ""
        )
        if not sel_pager:
            return None
        try:
            if not self.page.wait_for_selector(sel_pager, timeout=3000):
                return None
        except Exception:
            return None
        el = self.page.query_selector(sel_pager)
        if not el:
            return None
        return parse_pager_total_pages(el.inner_text() or "")

    @staticmethod
    def _rand_suffix() -> str:
        return "".join(random.choices(string.digits, k=6))


class _BlockedApiRunner:
    """api 靶子未声明探针时的 fail-closed 占位：每个用例如实 BLOCKED，不假绿。"""

    def __init__(self, reason: str, code: str) -> None:
        self._reason = reason
        self._code = code

    def run_case(self, case: dict) -> dict:
        return {
            "case": case.get("case_id") or case.get("case") or "",
            "overall": "BLOCKED",
            "pass": False,
            "assertions": [],
            "error": self._reason,
            "blocked_by": self._code,
        }
