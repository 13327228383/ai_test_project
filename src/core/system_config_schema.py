# -*- coding: utf-8 -*-
"""系统配置归一化分类 —— 前后端对齐的单一事实源。

设计原则（对齐 pipeline-config 的 CONFIG_SCHEMA 范式）：
- 分组(group) 由 config.yaml 顶层章节派生 → 新增章节自动成组，零漂移。
- 字段类型由 Python 值推断：bool→bool, int→int, float→float, list→list, 其余→str。
- 元数据(标签/帮助/枚举/敏感)由 SYSTEM_CONFIG_META 富集，复用 system-config.html 原定义。
- 后端 /api/system-config 同时返回 schema 与当前值；前端 schema 驱动渲染，
  不再维护硬编码 GROUPS 白名单（彻底消除「后端有、前端不显示」的漂移隐患）。

★ 与旧版差异：旧 system-config.html 在前端 JS 里写死 3 个分组 ~16 个键，
  新增 config.yaml 键不会出现在界面。本模块把「分类 + 元数据」收到后端单一事实源，
  前端只负责渲染，config.yaml 加键即前端可见。
"""
from __future__ import annotations
from src.core.guarded import guard

import logging
from pathlib import Path
from typing import Any, Optional

_log = logging.getLogger(__name__)


# ── 分组定义：config.yaml 顶层章节 → 归一化分组（标题/顺序/说明）──
# 未在表中的章节会自动建组（标题取章节名大写），保证零漂移。
GROUP_DEFS: dict[str, dict] = {
    "STORAGE": {"title": "元数据库", "order": 10, "desc": "测试引擎自身使用的元数据库（config.yaml），存储需求/用例/执行记录"},
    "AI": {"title": "AI 模型与路由", "order": 20, "desc": "主 AI 模型、路由策略与调用参数"},
    "OLLAMA": {"title": "本地 Ollama 模型", "order": 25, "desc": "本地部署的 Ollama 模型配置"},
    "ORACLE": {"title": "视觉/预言机(VLM)", "order": 30, "desc": "视觉语言模型等预言机能力配置"},
    "PLAYWRIGHT": {"title": "浏览器执行引擎", "order": 40, "desc": "Playwright 浏览器驱动与执行参数"},
    "SESSION": {"title": "智能会话上下文", "order": 50, "desc": "引擎级会话与上下文注入策略"},
    "PROVIDERS": {"title": "AI Provider 配置", "order": 60, "desc": "第三方 AI Provider 接入参数"},
    "FIGMA": {"title": "Figma 设计源接入", "order": 70, "desc": "Figma 设计稿接入与解析参数"},
    "BOOTSTRAP": {"title": "初始化引导", "order": 80, "desc": "系统首次启动的初始化引导配置"},
    "WEB": {"title": "Web 控制面板", "order": 90, "desc": "Web 服务与静态资源参数"},
    "EXPORT": {"title": "报告导出", "order": 100, "desc": "测试报告导出格式与路径"},
    "RAG": {"title": "RAG 检索引擎", "order": 110, "desc": "检索算法参数，控制 Step2/3 知识检索的召回与排序策略"},
    "STEP4_PLUGINS": {"title": "Step4 执行插件", "order": 120, "desc": "Step4 浏览器执行各插件的默认开关与阈值"},
    # ★ 深度合并：RAG 调参 + Step4 插件/执行调参已收敛到流水线配置（唯一编辑源）。
    #   该系统配置分组仅作只读归档（redirected=1），不在此编辑，杜绝双源冗余。
    "CONVERGED": {
        "title": "RAG / Step4 调参（已合并）",
        "order": 115,
        "desc": "RAG 检索与 Step4 执行/插件的算法调参已统一收敛到「质量治理 → 流水线配置」的 rag / step4 分组，作为唯一编辑源。此处仅只读归档默认值，不再在系统配置重复编辑。",
    },
    # 顶层散装标量（功能开关/全局项）统一归集，避免每个键自成一组
    "GLOBAL": {"title": "全局开关与系统", "order": 5, "desc": "跨项目共享的功能开关与系统级全局参数"},
}

# 顶层散装标量归到 GLOBAL 组（其余顶层章节进入自身分组）
_GLOBAL_KEY_PREFIXES = (
    "PERF_GATE_ENABLED",
    "A11Y_REPORT_ENABLED",
    "A11Y_GATE_ENABLED",
    "SECURITY_REPORT_ENABLED",
    "CHANGE_IMPACT_KB",
    "IDENTITY_POOL_ENABLED",
    "PRIVILEGE_MATRIX_ENABLED",
    "CONTRACT",
    "PROJECT_SOURCE_DIR",
)

# ★ 已合并到流水线配置的章节：RAG 调参 + Step4 插件/执行。映射到只读 CONVERGED 组。
_CONVERGED_SECTIONS = ("RAG", "STEP4_PLUGINS")


# ── 字段元数据富集层：复用 system-config.html 原有定义（上移至后端单一事实源）──
# key → {label?, help?, type?, options?:[{value,label}], secret?, min?, max?, step?}
SYSTEM_CONFIG_META: dict[str, dict] = {
    # ── RAG ──
    "RAG_RRF_K": {"type": "int", "help": "RRF 融合常数\n默认值：60\n越大低排名结果权重越高"},
    "RAG_HYBRID_BM25_WEIGHT": {"type": "float", "help": "混合检索 BM25 权重\n默认值：null（自动推断）\n越大偏关键词匹配"},
    "RAG_SIMILARITY_METRIC": {
        "type": "enum",
        "options": [
            {"value": "auto", "label": "自动推断（默认）"},
            {"value": "cosine", "label": "余弦相似度"},
            {"value": "dot", "label": "点积"},
            {"value": "bm25", "label": "纯 BM25"},
            {"value": "hybrid", "label": "混合 RRF"},
        ],
        "help": "相似度计算方法\n默认值：auto\nauto/cosine/dot/bm25/hybrid",
    },
    "RAG_L7_CROSS_ENCODER_THRESHOLD": {
        "type": "float",
        "help": "L7 Cross-Encoder 重打分阈值\n默认值：0.3\n分数低于此值的 chunk 被过滤\n调低→保留更多（召回率高）\n调高→过滤更严（精确率高）\n范围：0.0-1.0",
    },
    # ── STORAGE ──
    "STORAGE_ENGINE": {"type": "enum", "options": [{"value": "mysql", "label": "MySQL"}], "help": "存储引擎类型\n默认值：mysql\nconfig.yaml 定义，系统启动依赖"},
    "STORAGE_MYSQL_HOST": {"help": "元数据库主机\n默认值：127.0.0.1\nconfig.yaml 定义，系统启动依赖"},
    "STORAGE_MYSQL_PORT": {"type": "int", "help": "元数据库端口\n默认值：3306\nconfig.yaml 定义，系统启动依赖"},
    "STORAGE_MYSQL_USER": {"help": "元数据库用户名（敏感字段）\nconfig.yaml 定义，系统启动依赖", "secret": True},
    "STORAGE_MYSQL_PASSWORD": {"help": "元数据库密码（敏感字段）\nconfig.yaml 定义，系统启动依赖", "secret": True},
    "STORAGE_MYSQL_DATABASE": {"help": "元数据库名\n默认值：ai_test_engine\nconfig.yaml 定义，系统启动依赖"},
    "STORAGE_MYSQL_CHARSET": {"help": "字符集\n默认值：utf8mb4\nconfig.yaml 定义，系统启动依赖"},
    # ── SESSION ──
    "SESSION_SMART_STEP_STRIP_LOGIN": {"type": "bool", "help": "智能去除登录步骤\n默认值：true"},
    "SESSION_SMART_STEP_EXPAND_IMPORT": {"type": "bool", "help": "智能展开导入步骤\n默认值：true"},
    "SESSION_SYSTEM_KNOWLEDGE_INJECT_PROMPT": {"type": "bool", "help": "系统知识注入 Prompt\n默认值：true"},
    "SESSION_STATE_CACHE_SECONDS": {"type": "int", "help": "会话状态缓存秒数\n默认值：120"},
    # ── STEP4_PLUGINS ──
    "STEP4_PLUGINS_FLAKY_GUARD_ENABLED": {"type": "bool", "help": "Flaky 检测\n默认值：true"},
}


def _yaml_path() -> Path:
    return Path(__file__).resolve().parent / "config.yaml"


def _infer_type(v: Any) -> str:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, list):
        return "list"
    if isinstance(v, dict):
        return "dict"
    return "str"


def _is_secret(key: str) -> bool:
    k = key.upper()
    return any(s in k for s in ("PASSWORD", "API_KEY", "SECRET", "TOKEN"))


# ── 常用/高级 分层：降低首屏认知负荷 ──
# 仅「安装/首次使用期真实需设」的少量项标 common（Bootstrap 管理员密码 + 默认 AI Provider），
# 其余 100+ 引擎内部/调优项标 advanced，前端默认折叠（点「高级配置」展开）。
# 依据：config项审计——用户真实修改极少；靶子连接走项目配置、AI Key 走独立密钥 UI，
# 本全局 schema 仅 Bootstrap 密码与默认 Provider 属首屏必见项。
COMMON_KEY_HINTS = ("BOOTSTRAP_ADMIN_PASSWORD", "DEFAULT_PROVIDER")


def _tier_of(key: str) -> str:
    k = key.upper()
    if any(h in k for h in COMMON_KEY_HINTS):
        return "common"
    return "advanced"


def _group_of(section: str, field_key: str) -> str:
    """把扁平键映射到归一化分组（section 为顶层章节名）。"""
    s = section.upper()
    if s in _CONVERGED_SECTIONS:
        return "CONVERGED"
    if any(field_key.startswith(p) for p in _GLOBAL_KEY_PREFIXES):
        return "GLOBAL"
    return section.upper()


def _iter_leaves(raw: dict) -> list[dict]:
    """产出 [(group, field_key, value)]，顶层章节作分组，顶层散装标量归 GLOBAL。

    嵌套结构保留完整前缀（如 STEP4_PLUGINS_GROUND_TRUTH_ENABLED），
    分组取顶层章节名（STEP4_PLUGINS），零漂移。
    """
    out: list[dict] = []

    def walk(node: Any, section: str, prefix: str) -> None:
        if not isinstance(node, dict):
            return
        for k, v in node.items():
            nk = f"{prefix}_{k}" if prefix else k
            if isinstance(v, dict) and not isinstance(v, bool):
                walk(v, section, nk)
            else:
                # 列表/标量统一作叶子（list/dict 类型由 _infer_type 判定）
                out.append({"group": _group_of(section, nk.upper()), "key": nk.upper(), "value": v})

    for section, value in raw.items():
        if section == "projects":
            continue
        if isinstance(value, dict):
            # ★ 初始 prefix = section，保证嵌套键带全前缀
            walk(value, section.upper(), section.upper())
        else:
            # 顶层散装标量
            out.append({"group": _group_of(section.upper(), section.upper()), "key": section.upper(), "value": value})
    return out


def load_config_yaml_values() -> dict:
    """加载 config.yaml 系统级配置（排除 projects 节），平铺为 UPPERCASE 键名。"""
    p = _yaml_path()
    if not p.is_file():
        return {}
    try:
        import yaml as _yaml

        with open(str(p), "r", encoding="utf-8") as f:
            raw = _yaml.safe_load(f) or {}
        result: dict = {}
        for leaf in _iter_leaves(raw):
            result[leaf["key"]] = leaf["value"]
        return result
    except Exception as e:  # noqa: BLE001
        _log.warning("[system-config-schema] config.yaml 加载失败: %s", e)
        return {}


def build_system_config_schema(overrides: Optional[dict] = None) -> dict:
    """构造归一化 schema（分组 + 字段元数据 + 当前值）。

    Args:
        overrides: system_config 表覆盖值（dict key→value），用于标注 is_overridden。
    Returns:
        {"groups": [ {key,title,desc,order,items:[...]} ]}
    """
    raw = {}
    with guard("build_system_config_schema: import yaml as _yaml", log=_log, level=logging.WARNING):
        import yaml as _yaml

        with open(str(_yaml_path()), "r", encoding="utf-8") as f:
            raw = _yaml.safe_load(f) or {}

    overrides = overrides or {}
    leaves = _iter_leaves(raw)

    groups: dict[str, dict] = {}
    for leaf in leaves:
        gk = leaf["group"]
        gd = GROUP_DEFS.get(gk)
        if gd is None:
            # 未知章节自动建组，保证零漂移
            gd = {"title": gk, "order": 200, "desc": ""}
        g = groups.setdefault(
            gk,
            {"key": gk, "title": gd["title"], "desc": gd.get("desc", ""), "order": gd.get("order", 200), "items": []},
        )
        fk = leaf["key"]
        val = leaf["value"]
        meta = SYSTEM_CONFIG_META.get(fk, {})
        ftype = meta.get("type") or _infer_type(val)
        item = {
            "key": fk,
            "type": ftype,
            "value": val,
            "default": val,
            "label": meta.get("label") or fk,
            "help": meta.get("help") or "",
            "secret": bool(meta.get("secret", False)) or _is_secret(fk),
            "options": meta.get("options"),
            "tier": _tier_of(fk),
        }
        if "min" in meta:
            item["min"] = meta["min"]
        if "max" in meta:
            item["max"] = meta["max"]
        if "step" in meta:
            item["step"] = meta["step"]
        if fk in overrides:
            item["is_overridden"] = True
            item["value"] = overrides[fk]
        # ★ 深度合并：已收敛到流水线配置的字段 → 只读归档，前端不渲染输入控件
        if gk == "CONVERGED":
            item["redirected"] = True
        g["items"].append(item)

    # 组按 order 排序；组内按 config.yaml 出现顺序（保持自然顺序）
    ordered = sorted(groups.values(), key=lambda x: x["order"])
    return {"groups": ordered}


if __name__ == "__main__":
    s = build_system_config_schema()
    total = sum(len(g["items"]) for g in s["groups"])
    print(f"groups={len(s['groups'])} items={total}")
    for g in s["groups"]:
        print(f"  [{g['key']}] {g['title']} ({len(g['items'])})")
