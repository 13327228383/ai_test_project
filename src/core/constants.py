# -*- coding: utf-8 -*-
"""系统级常量集中点 (v6.1)

所有跨模块的固定字符串/数字/路径/字面量统一在此定义，
严禁再在业务代码里写硬编码字符串/URL/账号/弱密码清单。
新增常量请先想清楚：能不能走 config 走配置项？必须硬编码的（如 SSRF 白名单）
才放进这里。

分桶:
- DEMO_*  : 演示/示例数据（被 config.demo.* 覆盖）
- SSRF_*  : SSRF 防护白名单（必须硬编码，不能走配置避免被攻击者篡改）
- WEAK_*  : 密码黑名单（必须硬编码，避免被运维通过配置绕过）
- PROV_*  : Provider 标识符（不是 URL，是枚举）
- RISK_*  : 风险等级枚举
- TEST_*  : 测试相关枚举
- INTENT_* : 意图状态/类型枚举
"""

import os
import logging
from src.core.config_loader import config

_log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 演示/示例数据 (统一从 config.demo 读取，无 config 时走 env)
# ═══════════════════════════════════════════════════════════════
def _demo_value(key: str, default: str) -> str:
    """从 config.demo.* 读取演示值，兼容环境变量 DEMO_* 覆盖。"""
    try:
        demo = config._get("DEMO", {}) if hasattr(config, "_get") else {}
        if isinstance(demo, dict) and demo.get(key):
            return str(demo[key])
    except Exception as _e:
        _log.debug("_demo_value: %s", _e, exc_info=True)
    env_val = os.environ.get(f"DEMO_{key.upper()}")
    if env_val:
        return env_val
    return default


def _demo_credentials() -> dict:
    try:
        creds = (
            config._get("DEMO_SAMPLE_CREDENTIALS", {})
            if hasattr(config, "_get")
            else {}
        )
        if isinstance(creds, dict) and creds:
            return {
                "username": str(creds.get("username", "admin")),
                "password": str(creds.get("password", "123456")),
            }
    except Exception as _e:
        _log.debug("_demo_credentials: %s", _e, exc_info=True)
    return {
        "username": "admin",
        "password": os.environ.get("DEMO_DEFAULT_PASSWORD", "demo123456"),
    }  # DEMO ONLY — override via DEMO_SAMPLE_CREDENTIALS env var


# 演示项目名（用于单元测试/示例代码，运行时无影响）
DEMO_PROJECT: str = _demo_value("SAMPLE_PROJECT", "SmartShop")
# 演示账号
DEMO_USERNAME: str = _demo_credentials()["username"]
DEMO_PASSWORD: str = _demo_credentials()["password"]
# 演示邮箱后缀
DEMO_EMAIL_SUFFIX: str = _demo_value("SAMPLE_USER_EMAIL_SUFFIX", "@example.com")


# ═══════════════════════════════════════════════════════════════
# 本地服务默认地址 ★ 兜底常量
# 当 config 加载失败 / 字段缺失时使用的默认 base URL
# 集中在这里便于跨模块统一（属于「兜底默认值」类硬编码）
# ★ 模型名不入此清单：模型名必须由前端配置入库（user_credentials），
#   任何场景下都不允许硬编码默认模型名（避免静默使用错误模型消耗云端额度）
# ═══════════════════════════════════════════════════════════════
OLLAMA_DEFAULT_API_BASE: str = os.environ.get(
    "OLLAMA_API_BASE", "http://localhost:11434"
)
DATA_FACTORY_DEFAULT_API_BASE: str = os.environ.get(
    "TARGET_DATA_FACTORY_API_BASE", "http://127.0.0.1:8000"
)
DATA_FACTORY_DEFAULT_API_TIMEOUT: int = 30


# ═══════════════════════════════════════════════════════════════
# SSRF 防护白名单 ★ 必须硬编码
# 放在代码里防止被攻击者通过改配置文件绕过
# ═══════════════════════════════════════════════════════════════
SSRF_SAFE_HOSTS: tuple = (
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
)

# Token 代理业务方域名（生产环境部署时通过 AUTH_TOKEN_PROXY_HOSTS 环境变量追加）
# ★ 不再硬编码默认域名，必须通过环境变量显式配置才加入白名单
AUTH_TOKEN_PROXY_HOSTS: tuple = tuple(
    x
    for h in os.environ.get("AUTH_TOKEN_PROXY_EXTRA", "").split(",")
    if (x := h.strip())
)


# ═══════════════════════════════════════════════════════════════
# 弱密码黑名单 ★ 必须硬编码
# 防止运维通过配置文件绕过
# ═══════════════════════════════════════════════════════════════
WEAK_PASSWORDS: frozenset = frozenset(
    {
        "123456",
        "password",
        "12345678",
        "qwerty",
        "abc123",
        "monkey",
        "1234567",
        "letmein",
        "trustno1",
        "dragon",
        "baseball",
        "iloveyou",
        "master",
        "sunshine",
        "ashley",
        "bailey",
        "shadow",
        "123456789",
        "654321",
        "superman",
        "qazwsx",
        "michael",
        "football",
        "password1",
        "password123",
        "admin",
        "root",
        "test",
        "guest",
        "111111",
        "000000",
        "1q2w3e4r",
        "qweasd",
        "asd123",
        "zxcvbn",
        "123qwe",
        "admin123",
        "root123",
        "test123",
        "guest123",
        "demo",
        "demo123",
    }
)


# ═══════════════════════════════════════════════════════════════
# AI Provider 标识符
# ═══════════════════════════════════════════════════════════════
PROVIDER_DEEPSEEK = "deepseek"
PROVIDER_OPENAI = "openai"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_NVIDIA = "nvidia"
PROVIDER_OLLAMA = "ollama"
PROVIDER_LOCAL = "local"
PROVIDER_ALL = (PROVIDER_DEEPSEEK, PROVIDER_OPENAI, PROVIDER_ANTHROPIC, PROVIDER_NVIDIA)


# ═══════════════════════════════════════════════════════════════
# 风险等级
# ═══════════════════════════════════════════════════════════════
RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"
RISK_CRITICAL = "critical"
RISK_ALL = (RISK_LOW, RISK_MEDIUM, RISK_HIGH, RISK_CRITICAL)


# ═══════════════════════════════════════════════════════════════
# 意图状态/类型
# ═══════════════════════════════════════════════════════════════
INTENT_STATUS_PENDING = "pending"
INTENT_STATUS_REVIEW = "review"
INTENT_STATUS_APPROVED = "approved"
INTENT_STATUS_REJECTED = "rejected"
INTENT_STATUS_EXECUTING = "executing"
INTENT_STATUS_PASSED = "passed"
INTENT_STATUS_FAILED = "failed"
INTENT_STATUS_SKIPPED = "skipped"


INTENT_TYPE_POSITIVE = "正向"
INTENT_TYPE_NEGATIVE = "负向"
INTENT_TYPE_EXCEPTION = "异常"
INTENT_TYPE_BOUNDARY = "边界值"
INTENT_TYPE_UI = "UI"
INTENT_TYPE_SECURITY = "安全"
INTENT_TYPE_PERFORMANCE = "性能"
INTENT_TYPE_INTEGRATION = "集成"


# ═══════════════════════════════════════════════════════════════
# 执行控制常量
# ═══════════════════════════════════════════════════════════════
DEFAULT_BROWSER_TIMEOUT_MS = 30000
DEFAULT_STEP_TIMEOUT_SECONDS = 600
QUICK_MODE_SAMPLE_SIZE = 3
MAX_AI_RETRY_TIMES = 3
MAX_CIRCUIT_FAILURE_THRESHOLD = 5
CIRCUIT_RECOVERY_TIMEOUT_SEC = 60

# LRU 容量 (硬编码兜底，应优先走 config.op_cache.max_capacity)
DEFAULT_OP_CACHE_CAPACITY = 5000
DEFAULT_RAG_LOAD_CHUNKS = 5000


# ═══════════════════════════════════════════════════════════════
# 文档存储路径协议前缀
# MySQL 化后所有"路径"都是 mysql:// URI 协议，统一前缀便于后续替换
# ═══════════════════════════════════════════════════════════════
# 原始文档 / 用户上传文档（Step1 拉取/用户上传）
MYSQL_DOC_URI_PREFIX = "mysql://source_documents/"
MYSQL_DOC_URI_REQUIREMENT = MYSQL_DOC_URI_PREFIX + "requirement"
MYSQL_DOC_URI_DEPLOYMENT = MYSQL_DOC_URI_PREFIX + "deployment"
MYSQL_DOC_URI_UPLOAD = MYSQL_DOC_URI_PREFIX + "upload"

# 流水线生成报告（Step3/Step5 产出，与 source_documents 分离）
MYSQL_DOC_URI_GENERATED_REPORT_PREFIX = "mysql://generated_reports/"
MYSQL_DOC_URI_REPORT = MYSQL_DOC_URI_GENERATED_REPORT_PREFIX + "report"
MYSQL_DOC_URI_HTML_REPORT = MYSQL_DOC_URI_GENERATED_REPORT_PREFIX + "html_report"
MYSQL_DOC_URI_COVERAGE_REPORT = (
    MYSQL_DOC_URI_GENERATED_REPORT_PREFIX + "coverage_report"
)


# ═══════════════════════════════════════════════════════════════
# 业务登录占位符 (跨模块协议字面量)
# ═══════════════════════════════════════════════════════════════
LOGIN_PLACEHOLDER_USERNAME = "{USERNAME}"
LOGIN_PLACEHOLDER_PASSWORD = "{PASSWORD}"
LOGIN_PLACEHOLDERS = (LOGIN_PLACEHOLDER_USERNAME, LOGIN_PLACEHOLDER_PASSWORD)


# ═══════════════════════════════════════════════════════════════
# 登录/表单相关 CSS 选择器兜底 (被 _synonyms_common.py 中的扩展覆盖)
# 之所以保留在 constants：零依赖启动时的兜底
# ═══════════════════════════════════════════════════════════════
LOGIN_USERNAME_SELECTORS_FALLBACK: tuple = (
    'input[type="text"][name*="user" i]',
    'input[type="email"][name*="user" i]',
    'input[name="username" i]',
    'input[name="user" i]',
    'input[name="account" i]',
    'input[name="login" i]',
    'input[placeholder*="用户名" i]',
    'input[placeholder*="账号" i]',
    'input[placeholder*="user" i]',
)

LOGIN_PASSWORD_SELECTORS_FALLBACK: tuple = (
    'input[type="password"]',
    'input[name="password" i]',
    'input[name="pwd" i]',
    'input[placeholder*="密码" i]',
    'input[placeholder*="password" i]',
)


# ═══════════════════════════════════════════════════════════════
# 健康度评分 (硬编码兜底值)
# ═══════════════════════════════════════════════════════════════
DEFAULT_HEALTH_WEIGHTS: dict = {
    "coverage": 1.0,
    "hallucination_inverse": 2.0,  # 100 - rate
    "guard_block_inverse": 1.5,
    "three_track_inconsistency_inverse": 1.0,
    "flaky_inverse": 1.0,
    "cache_hit": 0.5,
    "intent_driven": 1.2,
}


# ═══════════════════════════════════════════════════════════════
# 浏览器相关硬编码兜底 (走 config.playwright.* 优先)
# ═══════════════════════════════════════════════════════════════
DEFAULT_BROWSER_VIEWPORT_WIDTH = 1920
DEFAULT_BROWSER_VIEWPORT_HEIGHT = 1080


# ═══════════════════════════════════════════════════════════════
# 项目角色/系统角色
# ═══════════════════════════════════════════════════════════════
ROLE_SYSTEM_ADMIN = "admin"
ROLE_SYSTEM_USER = "user"
ROLE_SYSTEM_VIEWER = "viewer"
ROLE_PROJECT_ADMIN = "owner"
ROLE_PROJECT_MEMBER = "editor"
ROLE_PROJECT_VIEWER = "viewer"
SYSTEM_ROLES = (ROLE_SYSTEM_ADMIN, ROLE_SYSTEM_USER, ROLE_SYSTEM_VIEWER)
PROJECT_ROLES = (ROLE_PROJECT_ADMIN, ROLE_PROJECT_MEMBER, ROLE_PROJECT_VIEWER)


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════
def is_ssrf_safe_host(host: str) -> bool:
    """检查 host 是否在 SSRF 白名单 (localhost/127.0.0.1/0.0.0.0/::1/业务方 token 代理)。"""
    if not host:
        return False
    h = host.strip().lower()
    if h in SSRF_SAFE_HOSTS:
        return True
    if h in AUTH_TOKEN_PROXY_HOSTS:
        return True
    return False


# ═══════════════════════════════════════════════════════════════
# SSRF 内部地址黑名单 (禁止 base_url 指向这些地址 — 安全基线)
# 必须硬编码：黑名单走配置等于给攻击者开后门
# ═══════════════════════════════════════════════════════════════
INTERNAL_HOSTS: frozenset = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "[::1]",
        "::1",
        "metadata.google.internal",
        "169.254.169.254",  # AWS / Azure metadata
    }
)


def is_internal_host(host: str) -> bool:
    """检查 host 是否内网/回环/云元数据地址（用于禁止外部输入指向这些地址）。"""
    if not host:
        return False
    h = host.strip().lower().strip("[]")
    if h in INTERNAL_HOSTS:
        return True
    # IP 形式判断
    import ipaddress

    try:
        ip = ipaddress.ip_address(h)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return True
    except ValueError as e:
        _log.debug("非IP格式，跳过内网检查: %s", e)
    return False


# ═══════════════════════════════════════════════════════════════
# 断言文本合法性防御（跨 builder + executor 共享，单点权威）
# ═══════════════════════════════════════════════════════════════
def is_illegal_assertion_text(text: str) -> bool:
    """判断字符串是否为"伪断言"（无业务语义，不可作为测试断言语义）。

    判定: 空串 / 仅空白 / 纯数字（非3位HTTP状态码） / 单字符标点/数字。
    典型场景: Step3 AI 偶发把 expected_feedback 生成为 "1"/"0"/"ok" 等，
    原执行器兜底 body_contains("1") → HTML body 总有 "1" → 404 也假 PASS。
    此函数在 verification_plan_builder 层就过滤掉，不进入执行链。
    """
    if not text or not str(text).strip():
        return True
    s = str(text).strip()
    # 纯数字（非 3 位 HTTP 状态码）
    if s.isdigit() and len(s) != 3:
        return True
    # 单字符（标点/字母/数字）
    if len(s) <= 1:
        return True
    return False
