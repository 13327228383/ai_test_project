# -*- coding: utf-8 -*-
"""反馈条选择器 —— 全引擎**单一事实源**（错误/校验/成功提示）。

★ 根因修复(2026-09-25) RC-AE: 消除「多套错误词表漂移」导致的系统性假 FAIL。
  根因（5 层 why）:
    L1 现象: Agent guided 层已用共享错误条选择器判 PASS（命中 `.flash-danger`
      / `[data-testid='client-error']`），checks 层同一期望却判
      `element_exists: 找到0个匹配元素` → 用例整体 FAIL（实测 accept_sut_final
      64 条 I02/I03/N01/B01 批量假 FAIL）。
    L2 直接: 生成端（Step3）的 element_exists 用的是**自己硬编码的 7 项词表**
      `[role="alert"], .error, .invalid, .danger, .ant-message-error, .field-error,
      .form-error` —— 不含 `.flash-danger` / `[data-testid*='error']` 等
      **框架无关**形态，扫不到真实渲染的错误条。
    L3 为何: 错误提示选择器在仓库里存在 **4 份互不相同的拷贝**（`_db_ui` 一份、
      `_rule_extractor_checks` 一份、`case_rules/base` 两份），任何一份的更新
      都无法惠及其它三处。
    L4 为何反复: 词表没有权威归属 —— 每加一个前端生态就在调用点就地补一份，
      于是"补了 Ant 漏 Flash、补了 Flash 漏 data-testid"。
    L5 根本: 缺少"反馈条识别"的单一事实源，验证器之间用不同证据对同一期望
      下相反结论（弱判定否决强证据）。
  修复: 反馈条选择器集中到本模块，Step3 生成端 / Step4 校验端 / Agent 探索端
    全部从此导入，杜绝再漂移。
  通用性: 跨系统/行业/页面 —— 反馈条形态与实现框架无关，零业务硬编码。

★ 为什么有两个错误词表（召回 vs 精确）:
  - `ERROR_SELECTORS`（召回优先）用于**正向**断言「错误提示应当出现」——
    漏判的代价是假 FAIL，故并集尽量宽（含 `.error` / `.invalid` / `.danger`
    这类广义类名）。
  - `NO_ERROR_SCAN_SELECTORS`（精确优先）用于**负向**断言「页面无致命错误」——
    误判的代价是假 FAIL（页面本无错误却被某个同名装饰性元素命中），
    故只保留语义明确的错误指示器，不收广义类名。
  两者同源（精确集是召回集的子集），语义差异在注释中固化，不再各自漂移。
"""

# ── 精确错误指示器（框架类 + 框架无关类 + data-testid 语义标识）──
_PRECISE_ERROR = [
    # W3C ARIA
    "[role='alert']",
    # Ant Design
    ".ant-message-error",
    ".ant-message-notice-error",
    ".ant-notification-error",
    ".ant-notification-notice-error",
    ".ant-alert-error",
    ".ant-form-item-explain-error",
    # ElementUI
    ".el-message--error",
    ".el-notification--error",
    ".el-alert--error",
    ".el-form-item__error",
    # Bootstrap / 通用表单校验
    ".invalid-feedback",
    ".is-invalid",
    ".field-error",
    # 通用语义类
    ".error-message",
    ".error-tip",
    ".error-text",
    ".form-error",
    ".message-error",
    ".toast-error",
    ".notification-error",
    ".validation-error",
    ".error-bar",
    # 框架无关错误条（服务端 Flash danger / 自定义 client 校验错误条）
    ".flash-danger",
    ".alert-danger",
    # data-testid 语义标识（与 SUCCESS 侧对称：`*success*` 有兜底，`*error*` 也必须有，
    #   否则按语义命名的错误条——如 `*-error`——会被漏检 → 断言"未出现反馈"恒不成立）
    "[data-testid='client-error']",
    "[data-testid*='error']",
    "[data-testid*='error-message']",
    "[data-testid*='error-bar']",
    "[data-testid*='form-error']",
    "[data-testid*='validation-error']",
]

# 广义类名：正向召回用，负向扫描不用（防装饰性同名元素误报"页面有错误"）
_BROAD_ERROR = [
    ".error",
    ".invalid",
    ".danger",
]

# ── 正向「错误提示存在」断言（召回优先）──
ERROR_SELECTORS = _PRECISE_ERROR + _BROAD_ERROR

# ── 负向「页面无致命错误」断言（精确优先）──
NO_ERROR_SCAN_SELECTORS = list(_PRECISE_ERROR)

# ── 字段级校验提示（含 aria-invalid / 表单错误态）──
VALIDATION_SELECTORS = (
    ["[aria-invalid='true']"]
    + ERROR_SELECTORS
    + [".ant-form-item-has-error", ".el-form-item.is-error"]
)

# ── 正向「成功/接受提示存在」断言 ──
SUCCESS_SELECTORS = [
    "[role='status']",
    # Ant Design
    ".ant-message-success",
    ".ant-message-notice-success",
    ".ant-notification-success",
    ".ant-notification-notice-success",
    ".ant-alert-success",
    # ElementUI
    ".el-message--success",
    ".el-notification--success",
    ".el-alert--success",
    # Bootstrap / 通用
    ".alert-success",
    ".valid-feedback",
    ".success",
    ".message",
    # 通用语义类
    ".success-message",
    ".success-feedback",
    ".message-success",
    ".toast-success",
    ".notification-success",
    ".form-success",
    ".feedback-success",
    # 框架无关成功条（服务端 Flash success）
    ".flash-success",
    # data-testid 语义标识
    "[data-testid*='success']",
    "[data-testid*='message-success']",
]


def as_csv(selectors) -> str:
    """把选择器列表拼成 element_exists 可用的单个 selector 字符串。"""
    return ", ".join(selectors)


def split_selector_list(sel: str) -> list[str]:
    """把 CSV 选择器串拆回**单个选择器**列表。

    ★ 根因修复(2026-09-25) RC-AF(续2): 形态判据必须**逐条**选择器生效。
      根因: `is_broad_error_selector` 只对"选择器整体恰等于 `.danger`"返回真。
        而 Step3 发射的 `_FIELD_ERROR_SELECTOR` 是**整串 CSV**（含 `.error, .invalid,
        .danger`）→ Step4 `_handle_field_error_visible` 拿整串去判形态 → 恒为假 →
        广义类名不受形态判据约束 → 靶子 `<a class="danger">删除</a>` 被当作
        "字段级错误提示可见" → 非法输入用例**假 PASS**（实测 TC-REQ-067-N01/B01）。
      修复: 消费端先把 CSV 拆成单条再逐条判形态，使 RC-AF 的判据真正落地。
      通用性: 纯字符串处理，与业务/框架无关；调用端只负责拆，判定仍由本模块提供。
    """
    return [s.strip() for s in str(sel or "").split(",") if s.strip()]


ERROR_SELECTOR_CSV = as_csv(ERROR_SELECTORS)
VALIDATION_SELECTOR_CSV = as_csv(VALIDATION_SELECTORS)
SUCCESS_SELECTOR_CSV = as_csv(SUCCESS_SELECTORS)

# ── 「确认/拦截」提示面选择器（析取式期望的另一分支的证据面）──
# 语义: 危险操作先弹二次确认（原生 confirm 或页内 modal）＝系统拦住了操作。
#   原生 confirm 由引擎 `_captured_dialogs` 承载（dialog_check），本表只覆盖
#   **页内**确认容器。与「模态框存在」的能力清单选择器语义不同：那里问"页面
#   有没有模态组件"，这里问"是否正在向用户确认某个决定"。
CONFIRM_SELECTORS = [
    "[role='dialog']",
    "[role='alertdialog']",
    "dialog[open]",
    ".modal.show",
    ".modal.in",
    ".ant-modal-confirm",
    ".ant-modal-wrap:not([style*='display: none'])",
    ".el-message-box",
    ".el-dialog__wrapper",
    ".n-dialog",
    "[data-testid*='confirm']",
    "[data-testid*='dialog']",
]
CONFIRM_SELECTOR_CSV = as_csv(CONFIRM_SELECTORS)

# ── CSRF 令牌字段名（单一事实源， 根因修复）──
# 根因: base.py / security_scanner / step3_inverse 各维护一份字段名词表
#   （Django csrfmiddlewaretoken / Flask _csrf / Rails authenticity_token），
#   补一份漏一份 → element_exists"CSRF令牌应存在"恒 0 匹配 → 假 FAIL。
#   通用性: 字段名是**技术栈事实**（非业务词），覆盖主流 Web 框架即跨系统/行业。
# 修复: 词表唯一归属此处，生成端/扫描端/反向校验端全部 import，只进不出。
CSRF_TOKEN_SELECTORS: list = [
    "input[name='csrfmiddlewaretoken']",   # Django
    "input[name='_csrf']",                 # Flask/CSRFProtect
    "input[name='_token']",                # Laravel/通用
    "input[name='csrf_token']",            # 通用命名
    "input[name='authenticity_token']",    # Rails
    "meta[name='csrf-token']",             # 前端 meta 注入
]
CSRF_TOKEN_SELECTOR_CSV: str = as_csv(CSRF_TOKEN_SELECTORS)

# CSRF 字段名（无选择器外壳；安全扫描/探测载荷拼装用）。同源于上述选择器词表，
# 消费端按自身形态（正则/载荷名）取用，保证"字段名"全仓唯一一份。
CSRF_TOKEN_FIELD_NAMES: list = sorted(
    {
        s.split("name='")[1].rstrip("']")
        for s in CSRF_TOKEN_SELECTORS
        if "name='" in s
    }.union({"csrfToken", "__RequestVerificationToken"})
)


# ═══════════════════════════════════════════════════════════════════════════════
# ★ 根因修复(2026-09-25) RC-AF: 「广义类名命中交互控件」必须视为装饰性，不得当作错误提示
# ═══════════════════════════════════════════════════════════════════════════════
# 根因（5 层 why）:
#   L1 现象: 靶子列表页的「删除」链接写作 `<a class="danger">删除</a>`，
#     任何**正向**错误提示断言（`field_error_visible_check` / `error_hint` /
#     引导层"拒绝语义命中错误条"）都命中它 → 报告
#     `字段级错误提示可见: '删除'` → 非法输入用例**假 PASS**
#     （实测 accept_sut_final TC-REQ-024-I01 / 067-N01 / 067-B01：
#      登录失败、非法搜索输入等被测路径根本没触发校验，却因 `.danger` 装饰链接判过）。
#   L2 直接: 广义类名 `.error` / `.invalid` / `.danger` 是**类名启发式**，
#     它们既用于错误条，也用于"危险操作按钮"这类**装饰性**语义 → 命中不等于有错误。
#   L3 为何: RC-AE 只把**负向**扫描（no_error）收窄到精确集，
#     但**正向**扫描（错误提示应出现）仍在用含广义类名的召回集，且没有形态约束。
#   L4 为何反复: "错误提示"这个概念只有**词表**没有**形态定义** ——
#     缺少"什么算错误提示"的判定规则，于是每处调用点各自解释。
#   L5 根本: 缺少"错误提示 = 内容节点 ≠ 交互控件"这一**通用形态判据**。
# 修复: 在本单一事实源内定义形态判据 —— 广义类名命中时，若命中元素自身或
#   3 层内祖先为交互控件（a/button/input/select/textarea/option 或
#   role∈{button,link,menuitem,tab,checkbox,radio,switch}），判为装饰性、丢弃。
#   精确错误指示器（.flash-danger / [role=alert] / .ant-message-error …）不受影响
#   —— 它们本身就是"消息容器"语义，命中即真。
# 通用性: 跨系统/行业/框架 —— 与实现无关（任何 UI 里"按钮上的 danger 类"
#   都不是错误消息），零业务硬编码。
BROAD_ERROR_SELECTORS = list(_BROAD_ERROR)
_BROAD_ERROR_SET = frozenset(s.strip().lower() for s in _BROAD_ERROR)

_INTERACTIVE_TAGS = ("A", "BUTTON", "INPUT", "SELECT", "TEXTAREA", "OPTION")
_INTERACTIVE_ROLES = (
    "button", "link", "menuitem", "menuitemcheckbox", "menuitemradio",
    "tab", "checkbox", "radio", "switch",
)

# 单次 DOM 求值：自身或 ≤3 层祖先是否为交互控件
_IS_INTERACTIVE_JS = """(el) => {
    const tags = %s;
    const roles = %s;
    let n = el;
    for (let d = 0; d < 3 && n; d++) {
        if (tags.indexOf(n.tagName) >= 0) return true;
        const r = ((n.getAttribute && n.getAttribute('role')) || '').toLowerCase();
        if (roles.indexOf(r) >= 0) return true;
        n = n.parentElement;
    }
    return false;
}""" % (list(_INTERACTIVE_TAGS), list(_INTERACTIVE_ROLES))


def is_broad_error_selector(sel: str) -> bool:
    """该选择器是否属于「广义类名启发式」（需叠加形态判据）。"""
    return str(sel or "").strip().lower() in _BROAD_ERROR_SET


async def is_decorative_broad_match(el) -> bool:
    """广义类名命中元素是否为**装饰性交互控件**（按钮/链接/输入）。

    是 → 该命中不代表"页面出现错误提示"，调用方应丢弃。
    异常一律返回 False（fail-open：不因探测失败而丢真命中，避免假 FAIL）。
    """
    try:
        return bool(await el.evaluate(_IS_INTERACTIVE_JS))
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# ★ 根因修复(2026-09-25) RC-AF(续): 「活动 DOM 反馈条探测」JS 也归入单一事实源
# ═══════════════════════════════════════════════════════════════════════════════
# 根因（5 层 why）:
#   L1 现象: Agent 引导执行层（`_action._detect_page_error_elements`）自带一份
#     page.evaluate JS，用**召回集**（含 `.danger`）扫错误条且**无形态判据**
#     → 靶子列表页 `<a class="danger">删除</a>` 被当成"页面出现错误提示"
#     → 「拒绝语义命中活动 DOM 错误条」判 PASS → 非法输入用例**假 PASS**。
#   L2 直接: RC-AF 的形态判据只落在 Step4 校验端（`_db_ui`），Agent 端是
#     **漏掉的消费端**（同一判据的第二份实现缺失）。
#   L3 为何: 探测逻辑（选择器 + 可见性 + 形态判据）分散在各调用点，
#     "什么算错误条"的判定无法一处生效。
#   L4 为何反复: 反馈条识别缺"单一事实源"这一层的**行为**（不只数据）——
#     选择器集中了，但**判定算法**仍各自复制。
#   L5 根本: 反馈条探测应是单一事实源提供的**能力**（build_*_detect_js），
#     调用端只负责执行，不再复写判定逻辑。
# 修复: 由本模块生成自包含的 page.evaluate JS（选择器 + 广义类名集合 +
#   交互控件形态判据全部内联），Agent 端与 Step4 端共用同一判定。
# 通用性: 跨系统/行业/框架 —— 探测算法与业务无关，零硬编码。
def build_detect_js(selectors, broad) -> str:
    """生成自包含的「活动 DOM 反馈条探测」JS（可直接 page.evaluate）。

    - 可见性: offsetParent + getComputedStyle 双重判定，过滤隐藏残留节点；
    - 形态判据: `broad` 中的广义类名命中时，若元素自身或 ≤3 层祖先为交互控件
      （a/button/input/select/textarea/option 或 role∈交互角色）则丢弃；
    - 返回: 命中列表（`selector: text`，最多 6 条）。
    """
    import json as _json

    return """() => {
    const sels = %s;
    const broad = %s;
    const tags = %s;
    const roles = %s;
    function isDecorative(el) {
        let n = el;
        for (let d = 0; d < 3 && n; d++) {
            if (tags.indexOf(n.tagName) >= 0) return true;
            const r = ((n.getAttribute && n.getAttribute('role')) || '').toLowerCase();
            if (roles.indexOf(r) >= 0) return true;
            n = n.parentElement;
        }
        return false;
    }
    const hits = [];
    for (const sel of sels) {
        let els = [];
        try { els = document.querySelectorAll(sel); } catch (e) { continue; }
        const needShape = broad.indexOf(sel) >= 0;
        for (const el of els) {
            if (el.offsetParent === null) continue;
            const cs = window.getComputedStyle(el);
            if (cs.display === 'none' || cs.visibility === 'hidden') continue;
            if (needShape && isDecorative(el)) continue;
            const t = (el.innerText || el.textContent || '').trim();
            if (t) hits.push(sel + ': ' + t.substring(0, 60));
            if (hits.length >= 6) return hits;
        }
    }
    return hits;
}""" % (
        _json.dumps([str(s) for s in selectors]),
        _json.dumps([str(s) for s in broad]),
        _json.dumps(list(_INTERACTIVE_TAGS)),
        _json.dumps(list(_INTERACTIVE_ROLES)),
    )


# 正向错误条探测（召回集 + 广义类名形态判据）
ERROR_DETECT_JS = build_detect_js(ERROR_SELECTORS, _BROAD_ERROR)
# 正反馈条探测（成功集，无广义类名，无需形态判据）
SUCCESS_DETECT_JS = build_detect_js(SUCCESS_SELECTORS, [])


# ═══════════════════════════════════════════════════════════════════════════════
# ★ 根因修复(2026-09-25) FIX4/AJ: 「访问控制拒绝」的判定证据也归入单一事实源
# ═══════════════════════════════════════════════════════════════════════════════
# 根因（5 层 why）:
#   L1 现象: 访问控制需求（"viewer 不允许新增/编辑/删除，返回 403"）的用例被生成
#     `text_contains` / `semantic_check`，断言的是需求里的**设计陈述**
#     （"不同角色获得对应的数据访问与操作权限"）—— 规格文字在页面上根本不存在
#     → 用例恒 FAIL（实测 accept_sut_final TC-REQ-068-E01）。
#   L2 直接: 生成端没有"访问控制型期望"的断言形态，只能退回通用文本匹配。
#   L3 为何: "拒绝"的可观察形态（拒绝页 / 无权限文案 / 重定向登录）没有权威定义，
#     于是每个生成器各自解释，或干脆不解释（退化为文本匹配）。
#   L4 为何反复: 与 RC-AE 同源 —— 识别"某类反馈"的知识散落在调用点，
#     没有单一事实源，新场景必然再次退化。
#   L5 根本: 「反馈形态识别」的单一事实源必须覆盖**全部**反馈语义
#     （错误条 / 成功条 / **拒绝**），否则缺口处必然长出第二套实现。
# 修复: 拒绝语义的**可观察形态**（拒绝页选择器 + 拒绝文案词 + 登录墙路径线索）
#   集中到本模块，Step3 生成端与 Step4 校验端共用。
# 通用性: 跨系统/行业/框架 —— 拒绝形态（403/无权限/重定向登录）与实现无关，
#   零业务硬编码。

# 拒绝页/无权限容器（语义精确的容器选择器，非广义类名启发式）
DENIAL_SELECTORS = [
    "[data-testid='error-title']",
    "[data-testid*='error-page']",
    "[data-testid*='forbidden']",
    "[data-testid*='access-denied']",
    "[data-testid*='permission']",
    ".error-page",
    ".forbidden-page",
    ".access-denied",
    ".permission-denied",
]

# 拒绝文案（大小写不敏感子串；覆盖 HTTP 状态码 / 中英文授权语义）
DENIAL_TEXT_KEYWORDS = (
    "403", "401", "forbidden", "unauthorized", "access denied", "permission denied",
    "not allowed", "权限不足", "无权限", "没有权限", "无权访问", "无权", "禁止访问",
    "拒绝访问", "访问被拒", "访问被拒绝", "未授权", "越权", "需要管理员",
    "requires admin", "admin required", "login required", "请先登录", "登录后",
)

# 登录墙路径线索（重定向到登录页 = 未认证被拒）
LOGIN_PATH_HINTS = ("login", "signin", "sign-in", "signon", "auth/login", "log-in")


def denial_signals_in_text(text: str) -> list[str]:
    """文本中命中的拒绝关键词（返回命中的词，便于审计取证）。"""
    low = str(text or "").lower()
    return [kw for kw in DENIAL_TEXT_KEYWORDS if kw in low]


def is_login_path(url: str) -> bool:
    """URL 是否为登录页（未认证访问被重定向到登录墙的判据）。"""
    try:
        from urllib.parse import urlsplit

        path = urlsplit(str(url or "")).path.lower()
    except Exception:  # noqa: BLE001
        path = str(url or "").lower()
    return any(h in path for h in LOGIN_PATH_HINTS)