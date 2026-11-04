# -*- coding: utf-8 -*-
"""流水线配置 API — 用户级 CRUD。"""

import logging
from src.core.guarded import guard

from flask import jsonify, request, Blueprint

_log = logging.getLogger(__name__)

pipeline_cfg_bp = Blueprint("pipeline_cfg", __name__)

_table_ensured = False  # 模块级标志：表已确认存在后跳过重复 DDL


def _ensure_table(conn):
    global _table_ensured
    if _table_ensured:
        return
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pipeline_user_config (
            id           BIGINT AUTO_INCREMENT PRIMARY KEY,
            user_id      VARCHAR(128) NOT NULL,
            config_key   VARCHAR(128) NOT NULL,
            config_value TEXT NOT NULL,
            source       VARCHAR(16) NOT NULL DEFAULT 'manual',
            updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_user_key (user_id, config_key)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    # 旧表可能缺 source 列（幂等：已存在则忽略 Duplicate column 错误）
    with guard("_ensure_table: conn.execute( 'ALTER TABLE pipeline_", log=_log):
        conn.execute(
            "ALTER TABLE pipeline_user_config "
            "ADD COLUMN source VARCHAR(16) NOT NULL DEFAULT 'manual'"
        )
    _table_ensured = True


# ── 配置 schema：通用渲染引擎的数据源 ──
# 字段定义不硬编码在前端，后端统一管理。前端按分组动态生成表单。
# help 字段支持 \n 换行，由全局 kpi-tooltip (app-ui-core.js) 渲染。
CONFIG_SCHEMA = {
    "step2": {
        "title": "Step2 需求拆解配置",
        "help": "控制 AI 需求拆解阶段的分片大小、阈值和上下文注入量\n🧠 默认值由算法自动调优（基于文档大小 + 模型窗口 + 数据可用性）\n手动设置后将覆盖算法推荐值",
        "auto_tune": True,
        "items": {
            "step2_shard_max_chars": {
                "type": "int",
                "default": 15000,
                "min": 3000,
                "max": 50000,
                "label": "分片最大字符数",
                "help": "单次 AI 拆解的最大输入字符数\n范围: 3000~50000 | 默认: 15000\n作用: 控制每次 AI 调用的文本量\n影响: 太大容易稀释注意力，太小可能丢失上下文\n建议: 中文文档 10000~15000 均衡",
            },
            "step2_shard_overlap_chars": {
                "type": "int",
                "default": 200,
                "min": 50,
                "max": 2000,
                "label": "分片重叠字符数",
                "help": "相邻分片之间的重叠字符数\n范围: 50~2000 | 默认: 200\n作用: 防止分片边界处需求被截断\n建议: 长段落文档可适当增大",
            },
            "step2_shard_max_count": {
                "type": "int",
                "default": 8,
                "min": 2,
                "max": 20,
                "label": "最大分片数",
                "help": "单次拆解的最大分片数\n范围: 2~20 | 默认: 8\n作用: 控制 AI 调用总数上限\n影响: 超出的内容不会被拆解\n建议: 大文档可调 10~15",
            },
            "step2_shard_model_safety_factor": {
                "type": "float",
                "default": 0.6,
                "min": 0.3,
                "max": 0.9,
                "step": 0.05,
                "label": "模型上下文安全系数",
                "help": "分片大小 = 模型上下文 × 此系数\n范围: 0.3~0.9 | 默认: 0.6\n作用: 预留空间给 system_prompt 和输出\n建议: 输出量大的模型降低此值",
            },
            "step2_discover_max_tokens": {
                "type": "int",
                "default": 2048,
                "min": 256,
                "max": 16384,
                "label": "发现阶段最大 Token 数",
                "help": "Step2 发现阶段单次 AI 调用的最大输出 token 数\n范围: 256~16384 | 默认: 2048\n作用: 控制需求/模块发现阶段的生成长度\n推荐值由 auto_tune 按模型窗口推导（model_window × 0.12）",
            },
            "step2_large_doc_threshold": {
                "type": "int",
                "default": 30000,
                "min": 5000,
                "max": 100000,
                "label": "大文档阈值",
                "help": "超过此字符数启用分片拆解\n范围: 5000~100000 | 默认: 30000\n作用: 小文档全文拆解，大文档按模块/段落分片",
            },
            "step2_supplementary_min_table_hits": {
                "type": "int",
                "default": 2,
                "min": 1,
                "max": 10,
                "label": "辅助数据最小命中",
                "help": "DB 表名在模块文本中最少命中数\n范围: 1~10 | 默认: 2\n作用: 低于此值不注入 DB 知识（避免噪音）",
            },
            "step2_supplementary_figma_overlap": {
                "type": "float",
                "default": 0.3,
                "min": 0.0,
                "max": 1.0,
                "step": 0.05,
                "label": "Figma 页面最小重叠度",
                "help": "Figma 页面名在文档中的命中比例\n范围: 0~1 | 默认: 0.3\n作用: 低于此值精简 Figma 上下文\n影响: 全文拆解路径使用",
            },
            "step2_merge_short_module_chars": {
                "type": "int",
                "default": 200,
                "min": 50,
                "max": 2000,
                "label": "短模块合并阈值",
                "help": "字符数低于此值的模块被合并\n范围: 50~2000 | 默认: 200\n作用: 避免过度碎片化",
            },
            "step2_structural_max_modules": {
                "type": "int",
                "default": 15,
                "min": 2,
                "max": 50,
                "label": "结构大纲最大模块数",
                "help": "零AI结构提取的模块数量上限\n范围: 2~50 | 默认: 15",
            },
            "step2_dedup_jaccard_threshold": {
                "type": "float",
                "default": 0.6,
                "min": 0.3,
                "max": 0.95,
                "step": 0.05,
                "label": "去重 Jaccard 阈值",
                "help": "需求条目去重的相似度阈值\n范围: 0.3~0.95 | 默认: 0.6\n作用: 相似度超过此值的条目被合并",
            },
            "step2_ui_reduce_chars": {
                "type": "int",
                "default": 500,
                "min": 100,
                "max": 3000,
                "label": "UI 上下文精简量",
                "help": "Figma 重叠度不足时精简到的字符数\n范围: 100~3000 | 默认: 500",
            },
            "step2_ui_full_max_chars": {
                "type": "int",
                "default": 3000,
                "min": 500,
                "max": 8000,
                "label": "UI 上下文上限",
                "help": "全文拆解时 UI 注入最大字符数\n范围: 500~8000 | 默认: 3000",
            },
            "step2_ui_shard_max_chars": {
                "type": "int",
                "default": 2000,
                "min": 200,
                "max": 5000,
                "label": "UI 分片注入上限",
                "help": "按模块拆解时 UI 注入最大字符数\n范围: 200~5000 | 默认: 2000",
            },
            "step2_knowledge_max_chars": {
                "type": "int",
                "default": 3000,
                "min": 500,
                "max": 10000,
                "label": "系统知识上限",
                "help": "DB 知识注入最大字符数\n范围: 500~10000 | 默认: 3000",
            },
            "step2_quick_mode_max_chars": {
                "type": "int",
                "default": 3000,
                "min": 500,
                "max": 10000,
                "label": "快速模式输入上限",
                "help": "快速模式截取文档前N字符\n范围: 500~10000 | 默认: 3000",
            },
            "step2_per_module_timeout_seconds": {
                "type": "int",
                "default": 300,
                "min": 30,
                "max": 1800,
                "label": "单模块超时(秒)",
                "help": "单模块 AI 调用超时时间\n范围: 30~1800 | 默认: 300 (5分钟)",
            },
            "step2_max_modules": {
                "type": "int",
                "default": 20,
                "min": 2,
                "max": 50,
                "label": "最大模块数",
                "help": "单次拆解的模块数上限\n范围: 2~50 | 默认: 20\n作用: 超出截断，控制 AI 调用次数",
            },
        },
    },
    "step3": {
        "title": "Step3 用例建模配置",
        "help": "控制 AI 测试用例生成的上下文注入量、检索深度和建模策略\n🧠 默认值由算法自动调优（基于模型窗口 + 需求数量 + 数据可用性）\n手动设置后将覆盖算法推荐值",
        "auto_tune": True,
        "items": {
            "step3_rag_top_k": {
                "type": "int",
                "default": 5,
                "min": 0,
                "max": 20,
                "label": "RAG 检索条数",
                "help": "每个需求语义检索的相关信息条数\n范围: 0~20 | 默认: 5\n作用: 控制注入 prompt 的语义上下文量\n影响: 太大浪费 token，太小信息不足\n建议: 需求多时降低此值，trace 数据丰富时也可降低",
            },
            "step3_rag_retrieve_types": {
                "type": "str",
                "default": "figma_nodes,figma_components,similar_requirements",
                "label": "RAG 检索类型",
                "help": "RAG 检索的数据类型（逗号分隔）\n可选: api_endpoints,db_tables,figma_pages,figma_nodes,figma_components,similar_requirements\n注意: api_endpoints/db_tables/figma_pages 已由 trace_links 精确提供，默认跳过避免重复",
            },
            "step3_parallel_workers": {
                "type": "int",
                "default": 10,
                "min": 1,
                "max": 20,
                "label": "并行线程数",
                "help": "并行建模时的最大线程数\n范围: 1~20 | 默认: 10\n🌐 官方API(api.deepseek.com等)→推荐8~10\n🔀 代理网关(tokenapi等)→推荐1~2(防排队限流)\n过高可能触发 API 限流导致全部请求失败",
            },
            "step3_ctx_ui": {
                "type": "int",
                "default": 2200,
                "min": 200,
                "max": 8000,
                "label": "UI/语义上限(字符)",
                "help": "每需求注入的语义检索上下文最大字符数\n范围: 200~8000 | 默认: 2200\n影响: 控制 fuzzy 检查（测试方法/UI节点）的丰富度",
            },
            "step3_ctx_trace": {
                "type": "int",
                "default": 1400,
                "min": 200,
                "max": 6000,
                "label": "Trace 上限(字符)",
                "help": "每需求注入的 trace 链路最大字符数\n范围: 200~6000 | 默认: 1400\n影响: 控制 exact 检查（API路径/表名）的精准度",
            },
            "step3_ctx_schema": {
                "type": "int",
                "default": 1400,
                "min": 200,
                "max": 6000,
                "label": "Schema 上限(字符)",
                "help": "每需求注入的数据库 Schema 最大字符数\n范围: 200~6000 | 默认: 1400\n影响: 控制 SQL 检查点的列类型/约束准确度",
            },
            "step3_ctx_data_dict": {
                "type": "int",
                "default": 2200,
                "min": 200,
                "max": 6000,
                "label": "数据字典上限(字符)",
                "help": "每需求注入的数据字典最大字符数\n范围: 200~6000 | 默认: 2200",
            },
            "step3_ctx_project": {
                "type": "int",
                "default": 1000,
                "min": 100,
                "max": 4000,
                "label": "项目背景上限(字符)",
                "help": "每需求注入的项目背景最大字符数\n范围: 100~4000 | 默认: 1000",
            },
            "step3_ctx_rules": {
                "type": "int",
                "default": 900,
                "min": 100,
                "max": 3000,
                "label": "规则上限(字符)",
                "help": "每需求注入的测试规则/禁止项最大字符数\n范围: 100~3000 | 默认: 900",
            },
            "step3_ctx_few_shot": {
                "type": "int",
                "default": 900,
                "min": 100,
                "max": 3000,
                "label": "Few-shot上限(字符)",
                "help": "每需求注入的 few-shot 示例最大字符数\n范围: 100~3000 | 默认: 900",
            },
            "step3_ctx_feedback": {
                "type": "int",
                "default": 700,
                "min": 100,
                "max": 2000,
                "label": "反馈上限(字符)",
                "help": "每需求注入的历史失败反馈最大字符数\n范围: 100~2000 | 默认: 700",
            },
        },
    },
    "step4": {
        "title": "Step4 执行配置",
        "help": "控制 Playwright 浏览器执行测试用例时的行为参数\n调整后立即影响下一次 Step4 执行",
        "items": {
            # ★ 根因修复(2026-09-24): 采样策略对称化 —— P0~P4 每档均可独立「纳入 / 抽样」。
            #   根因: 原 schema 只暴露 P1 采样率 + P2 开关，P3/P4 既无配置项、
            #   step4_executor 也无对应分支 → 58% 用例（性能/UI=P3，兼容/安全/易用=P4）
            #   被静默丢弃且报告不可见。此处补齐全部档位，默认全量纳入（全量覆盖优先）。
            "step4_include_p0": {
                "type": "bool",
                "default": True,
                "label": "包含 P0（最高风险）",
                "help": "是否执行 P0 风险等级用例\n默认: 开启\n作用: P0 为核心功能用例，关闭会导致主干未验证\n建议: 保持开启",
            },
            "step4_p0_sample_rate": {
                "type": "float",
                "default": 1.0,
                "min": 0,
                "max": 1,
                "step": 0.1,
                "label": "P0 采样率",
                "help": "P0 风险等级用例的执行比例\n范围: 0~1 | 默认: 1.0（全量）\n作用: 控制主干覆盖广度\n建议: 保持 1.0",
            },
            "step4_include_p1": {
                "type": "bool",
                "default": True,
                "label": "包含 P1",
                "help": "是否执行 P1 风险等级用例\n默认: 开启\n作用: 覆盖高风险的异常/边界场景\n建议: 保持开启",
            },
            "step4_p1_sample_rate": {
                "type": "float",
                "default": 1.0,
                "min": 0,
                "max": 1,
                "step": 0.1,
                "label": "P1 采样率",
                "help": "P1 风险等级用例的执行比例\n范围: 0~1 | 默认: 1.0（全量）\n0 = 跳过全部 P1（仅执行 P0）\n1 = 全量执行 P1\n作用: 控制测试覆盖广度与执行成本的平衡\n建议: 项目初期用 0.5 试跑，稳定后调 1.0 全量",
            },
            "step4_include_p2": {
                "type": "bool",
                "default": True,
                "label": "包含 P2",
                "help": "是否执行 P2 风险等级用例\n默认: 开启 | 范围: 开/关\n作用: 覆盖中风险需求，避免报告口径与实际覆盖不符\n影响: 开启后 AI 调用量会显著增加",
            },
            "step4_p2_sample_rate": {
                "type": "float",
                "default": 1.0,
                "min": 0,
                "max": 1,
                "step": 0.1,
                "label": "P2 采样率",
                "help": "P2 风险等级用例的执行比例\n范围: 0~1 | 默认: 1.0（全量）\n作用: 在「包含 P2」开启后进一步按比例抽样\n建议: 保持 1.0",
            },
            "step4_include_p3": {
                "type": "bool",
                "default": True,
                "label": "包含 P3（性能/UI）",
                "help": "是否执行 P3 风险等级用例\n默认: 开启 | 范围: 开/关\n作用: 覆盖性能基线（LCP≤3s）与表单布局对齐等横切维度\n影响: 关闭会使性能/UI 维度在报告中完全不可见",
            },
            "step4_p3_sample_rate": {
                "type": "float",
                "default": 1.0,
                "min": 0,
                "max": 1,
                "step": 0.1,
                "label": "P3 采样率",
                "help": "P3 风险等级用例的执行比例\n范围: 0~1 | 默认: 1.0（全量）\n作用: 控制性能/UI 维度的覆盖密度\n建议: 保持 1.0",
            },
            "step4_include_p4": {
                "type": "bool",
                "default": True,
                "label": "包含 P4（兼容/安全/易用）",
                "help": "是否执行 P4 风险等级用例\n默认: 开启 | 范围: 开/关\n作用: 覆盖多分辨率兼容、XSS 基础安全门禁、点击区域可达性等维度\n影响: 关闭会使兼容/安全/易用维度在报告中完全不可见",
            },
            "step4_p4_sample_rate": {
                "type": "float",
                "default": 1.0,
                "min": 0,
                "max": 1,
                "step": 0.1,
                "label": "P4 采样率",
                "help": "P4 风险等级用例的执行比例\n范围: 0~1 | 默认: 1.0（全量）\n作用: 控制兼容/安全/易用维度的覆盖密度\n建议: 保持 1.0",
            },
            "step4_red_team_enabled": {
                "type": "bool",
                "default": False,
                "label": "红蓝对抗（全局）",
                "help": "全局红蓝对抗总开关，同时控制 Step4 执行验证 + RAG 多策略检索\n默认: 关闭 | 范围: 开/关\n┃ Step4: 对执行结果进行独立对抗审查，检测假阳性\n┃ RAG: semantic + keyword + hybrid 三策略并行检索 + 红队验证 + RRF 仲裁\n效果: 提高可靠性和检索准确度，但增加约 30% AI 调用\n意义: 关键项目的质量兜底防线\n建议: 关键项目上线前开启\n注: 环境变量 RAG_BLUE_RED_ENABLED=True 可单独覆盖 RAG 侧",
            },
            "browser_max_ai_rounds": {
                "type": "int",
                "default": 8,
                "min": 1,
                "max": 30,
                "label": "AI 最大轮次",
                "help": "浏览器 AI 决策循环的最大轮数\n范围: 1~30 | 默认: 8\n超过后中止当前意图并标记为超时\n作用: 控制单意图的 AI 成本上限\n建议: 页面复杂的系统可调到 12~15",
            },
            "browser_max_parallel": {
                "type": "int",
                "default": 1,
                "min": 1,
                "max": 8,
                "label": "浏览器并行数",
                "help": "同时运行的浏览器实例上限\n范围: 1~8 | 默认: 1\n作用: 并行数越大执行越快，但内存占用越高\n每实例约 200~500MB 内存\n建议: 8GB 内存机器设 2~3，16GB 可设 4~6",
            },
            "default_step4_timeout_ms": {
                "type": "int",
                "default": 30000,
                "min": 1000,
                "max": 120000,
                "step": 5000,
                "label": "Step4 超时(ms)",
                "help": "单个意图执行超时时间\n范围: 1000~120000 | 默认: 30000 (30秒)\n超时后当前意图标记为失败并跳过\n作用: 防止死循环或无响应页面卡住整个流水线\n建议: 被测系统响应慢可调到 45000~60000",
            },
        },
    },
    "confidence": {
        "title": "置信度阈值",
        "help": "控制报告中的置信度分档阈值\n用于 Step4 执行结果和汇总报告的置信度评级\n注意: 低线 < 中线 < 高线，三者联动",
        "items": {
            "confidence_low_threshold": {
                "type": "float",
                "default": 0.50,
                "min": 0,
                "max": 1,
                "step": 0.05,
                "label": "低置信度线",
                "help": "低于此值为低置信度（红色）\n范围: 0~1 | 默认: 0.50\n意义: 低置信度意图需要人工确认\n建议: 对质量要求高的项目可调到 0.60",
            },
            "confidence_medium_threshold": {
                "type": "float",
                "default": 0.70,
                "min": 0,
                "max": 1,
                "step": 0.05,
                "label": "中置信度线",
                "help": "低于此值但高于低线为中置信度（黄色）\n范围: 0~1 | 默认: 0.70\n意义: 中置信度意图建议人工复核\n必须 > 低线",
            },
            "confidence_high_threshold": {
                "type": "float",
                "default": 0.90,
                "min": 0,
                "max": 1,
                "step": 0.05,
                "label": "高置信度线",
                "help": "高于此值为高置信度（绿色）\n范围: 0~1 | 默认: 0.90\n意义: 高置信度意图结果可信，可自动通过\n作用: 决定哪些意图可以免人工复核\n必须 > 中线\n建议: 对质量要求极高的项目可调到 0.95",
            },
        },
    },
    "ai": {
        "title": "AI 调用全局参数",
        "help": "控制 AI 模型调用的全局行为和超时设置",
        "items": {
            "ai_local_timeout": {
                "type": "float",
                "default": 15,
                "min": 1,
                "max": 120,
                "label": "本地模型超时(s)",
                "help": "本地 Ollama 模型调用超时\n范围: 1~120 | 默认: 15\n作用: 防止本地模型无响应卡住流水线\n仅对 Ollama 本地部署生效\n建议: 模型较大（如 70B）可调到 30~60",
            },
        },
    },
    "step_timeout": {
        "title": "Step 级超时(秒)",
        "help": "各步骤流水线的最大执行时间\n超时后自动中止当前步骤并记录已有结果\n大型项目（>100 条需求）建议适当调宽 Step2/3/4\n单位: 秒 | 支持环境变量覆盖",
        "items": {
            "STEP1_TIMEOUT_SECONDS": {
                "type": "int",
                "default": 600,
                "min": 60,
                "max": 7200,
                "step": 60,
                "label": "Step1 数据拉取超时",
                "help": "Step1 数据拉取 + Figma/Confluence/YApi 接入\n范围: 60~7200 | 默认: 600 (10分钟)\n作用: 控制数据源接入阶段的最大等待时间\n意义: 超时后中止数据拉取，后续步骤使用已有数据继续\n影响: 接入数据源较多或网络慢时需调宽\n建议: 常规项目 600 够用，数据源 >5 个可调 1200",
            },
            "STEP2_TIMEOUT_SECONDS": {
                "type": "int",
                "default": 2400,
                "min": 60,
                "max": 7200,
                "step": 60,
                "label": "Step2 拆解超时",
                "help": "Step2 AI 需求拆解 + 测试用例生成\n范围: 60~7200 | 默认: 2400 (40分钟)\n作用: 控制 AI 拆解需求生成测试用例的最大时间\n意义: 超时后已生成的意图继续流转，未完成的被跳过\n影响: 需求量大或 AI 响应慢时需调宽\n建议: 50+ 需求的项目调 3600~4800",
            },
            "STEP3_TIMEOUT_SECONDS": {
                "type": "int",
                "default": 3600,
                "min": 60,
                "max": 10800,
                "step": 60,
                "label": "Step3 建模超时",
                "help": "Step3 意图建模 + 去重 + 质量评估\n范围: 60~10800 | 默认: 3600 (1小时)\n作用: 控制意图建模和去重阶段的最大时间\n意义: 超时后已建模的意图进入 Step4 执行\n影响: 测试用例数量多或并行线程受限时需调宽\n建议: 200+ 意图调 5400~7200",
            },
            "STEP4_TIMEOUT_SECONDS": {
                "type": "int",
                "default": 7200,
                "min": 60,
                "max": 14400,
                "step": 60,
                "label": "Step4 执行超时",
                "help": "Step4 Playwright 浏览器执行测试用例\n范围: 60~14400 | 默认: 7200 (2小时)\n作用: 控制整个浏览器执行阶段的最大时间\n意义: 超时后未执行的意图标记为跳过，已完成的正常记录\n影响: 意图数多或被测系统响应慢时需调宽\n建议: 100+ 意图 + 串行执行调 10800~14400",
            },
            "STEP5_TIMEOUT_SECONDS": {
                "type": "int",
                "default": 600,
                "min": 60,
                "max": 3600,
                "step": 60,
                "label": "Step5 报告超时",
                "help": "Step5 报告生成 + 摘要计算\n范围: 60~3600 | 默认: 600 (10分钟)\n作用: 控制报告写入和统计汇总的最大时间\n意义: 超时后使用已生成的部分报告\n影响: 通常不会超时，除非写入外部系统阻塞\n建议: 一般无需调整",
            },
        },
    },
    "evidence": {
        "title": "证据引擎权重",
        "help": "控制证据引擎置信度计算的四个因子权重\n四项权重之和 = 1.0（归一化计算，无需严格手动凑整）\n权重越大表示该因子对最终置信度的影响越大",
        "items": {
            "evidence_weight_evidence": {
                "type": "float",
                "default": 0.40,
                "min": 0,
                "max": 1,
                "step": 0.05,
                "label": "证据因子权重",
                "help": "执行证据（Step4 实际通过/失败）对置信度的贡献\n范围: 0~1 | 默认: 0.40\n意义: 最客观的因子——实际执行结果\n建议: 保持最高权重，一般不低于 0.30",
            },
            "evidence_weight_calibration": {
                "type": "float",
                "default": 0.35,
                "min": 0,
                "max": 1,
                "step": 0.05,
                "label": "校准因子权重",
                "help": "贝叶斯校准（AI 声称置信度 vs 实际通过率）的贡献\n范围: 0~1 | 默认: 0.35\n意义: 修正 AI 过度自信或过度保守的倾向\n建议: AI 输出质量不稳定时可适当调高",
            },
            "evidence_weight_extraction": {
                "type": "float",
                "default": 0.15,
                "min": 0,
                "max": 1,
                "step": 0.05,
                "label": "提取因子权重",
                "help": "关键数据提取完整度的贡献\n范围: 0~1 | 默认: 0.15\n意义: 验证步骤是否完整提取了预期数据\n建议: 对数据完整性要求高的场景可调到 0.20~0.25",
            },
            "evidence_weight_schema": {
                "type": "float",
                "default": 0.10,
                "min": 0,
                "max": 1,
                "step": 0.05,
                "label": "结构因子权重",
                "help": "Schema 结构完整度（字段填充率）的贡献\n范围: 0~1 | 默认: 0.10\n意义: 辅助因子，评估测试用例的结构化程度\n建议: 通常保持 0.10 即可",
            },
            "evidence_high_confidence": {
                "type": "float",
                "default": 0.85,
                "min": 0,
                "max": 1,
                "step": 0.05,
                "label": "证据高置信度线",
                "help": "证据引擎判定为高置信度的阈值\n范围: 0~1 | 默认: 0.85\n意义: 综合分 >= 此值则判定为高置信度\n建议: 一般无需调整",
            },
            "evidence_medium_confidence": {
                "type": "float",
                "default": 0.70,
                "min": 0,
                "max": 1,
                "step": 0.05,
                "label": "证据中置信度线",
                "help": "证据引擎判定为中置信度的阈值\n范围: 0~1 | 默认: 0.70\n意义: 综合分在 中线~高线 之间为中置信度\n作用: 中置信度结果建议人工复核\n必须 < 高线\n建议: 一般无需调整",
            },
        },
    },
    "verification": {
        "title": "三轨验证置信度档位",
        "help": "控制 UI/API/DB 三轨验证结果的置信度 base_score 档位值\n这些分数是置信度的起点，还会叠加 API/DB 证据 bonus\n四个档位必须满足: 全通过 > 多数通过 > 部分失败 > 全失败",
        "items": {
            "verification_confidence_all_pass": {
                "type": "float",
                "default": 0.95,
                "min": 0,
                "max": 1,
                "step": 0.05,
                "label": "全通过分数",
                "help": "所有活跃轨道全部通过时的 base_score\n范围: 0~1 | 默认: 0.95\n意义: 最高档——所有验证都通过\n建议: 保持 0.95，给 bonus 留出上升到 1.0 的空间",
            },
            "verification_confidence_has_pass": {
                "type": "float",
                "default": 0.65,
                "min": 0,
                "max": 1,
                "step": 0.05,
                "label": "多数通过分数",
                "help": "≥2/3 轨通过时的 base_score\n范围: 0~1 | 默认: 0.65\n意义: 多数验证通过，但有部分失败\n建议: 对容错性高的系统可调高到 0.70~0.75",
            },
            "verification_confidence_has_fail": {
                "type": "float",
                "default": 0.35,
                "min": 0,
                "max": 1,
                "step": 0.05,
                "label": "部分失败分数",
                "help": "有通过但不足 2/3 轨通过时的 base_score\n范围: 0~1 | 默认: 0.35\n意义: 大部分验证失败\n建议: 一般无需调整",
            },
            "verification_confidence_all_fail": {
                "type": "float",
                "default": 0.10,
                "min": 0,
                "max": 1,
                "step": 0.05,
                "label": "全失败分数",
                "help": "全部活跃轨道失败时的 base_score\n范围: 0~1 | 默认: 0.10\n意义: 最低档——所有验证都失败\n建议: 保持低值以明确标记失败，一般不高于 0.20",
            },
        },
    },
    "rag": {
        "title": "RAG 引擎参数",
        "help": "控制 RAG 语义检索引擎的核心参数\n影响 AI 生成测试用例时的上下文增强质量\n参数调整后需重新构建索引（上传文档时自动触发）",
        "items": {
            "rag_embed_dim": {
                "type": "int",
                "default": 128,
                "min": 64,
                "max": 1024,
                "step": 64,
                "label": "向量维度",
                "help": "embedding 向量的维度数\n范围: 64~1024 | 默认: 128\n意义: 决定向量检索的精度，越高越精确但内存开销越大\n注意: 必须与 embedding 模型的输出维度匹配\n建议: 本地小模型用 128/256，云端大模型按模型要求设置",
            },
            "rag_bm25_k1": {
                "type": "float",
                "default": 1.5,
                "min": 0.1,
                "max": 5.0,
                "step": 0.1,
                "label": "BM25 K1 参数",
                "help": "BM25 词频饱和参数\n范围: 0.1~5.0 | 默认: 1.5\n意义: 控制词频对相关性评分的饱和速度\nk1 越大 → 高频词收益越大\n建议: 中文短文本用 1.2~1.5，长文档用 1.5~2.0",
            },
            "rag_bm25_b": {
                "type": "float",
                "default": 0.75,
                "min": 0.1,
                "max": 1.0,
                "step": 0.05,
                "label": "BM25 B 参数",
                "help": "BM25 文档长度归一化参数\n范围: 0.1~1.0 | 默认: 0.75\n意义: 控制长文档的惩罚力度\nb 越大 → 长文档越容易被惩罚\n建议: 文档长度差异大时用 0.75，差异小可用 0.5",
            },
            "rag_context_snippet_len": {
                "type": "int",
                "default": 200,
                "min": 50,
                "max": 1000,
                "step": 50,
                "label": "上下文截断长度",
                "help": "API/DB/Page 上下文注入给 AI 时的最大字符数\n范围: 50~1000 | 默认: 200\n作用: 控制 RAG 注入的上下文量，影响 Token 消耗\n建议: 简单接口用 200，复杂页面可调到 400~600",
            },
            "rag_embed_text_max_len": {
                "type": "int",
                "default": 2000,
                "min": 500,
                "max": 8000,
                "step": 500,
                "label": "Embedding 最大长度",
                "help": "发送 embedding API 的文本最大字符数\n范围: 500~8000 | 默认: 2000\n作用: 超长文本会被截断后再向量化\n注意: 超出 embedding 模型上下文窗口会报错\n建议: 按实际 embedding 模型的 max_length 设置",
            },
            "rag_keyword_max_count": {
                "type": "int",
                "default": 50,
                "min": 10,
                "max": 200,
                "step": 10,
                "label": "关键词最大数",
                "help": "每个 chunk 最大关键词提取数量\n范围: 10~200 | 默认: 50\n作用: 关键词用于 BM25 混合检索的补充\n建议: 中文文档因分词粒度细，30~50 通常够用",
            },
            "rag_db_col_limit": {
                "type": "int",
                "default": 15,
                "min": 5,
                "max": 100,
                "step": 5,
                "label": "DB 表最大列数",
                "help": "DB 表结构作为 RAG 上下文时的最大列数\n范围: 5~100 | 默认: 15\n作用: 超出部分会被截断，节省 Token\n建议: 宽表可调到 30~50，窄表保持 15 即可",
            },
            "rag_page_element_limit": {
                "type": "int",
                "default": 5,
                "min": 1,
                "max": 50,
                "label": "页面元素上限",
                "help": "页面关键元素（按钮/输入框/链接等）最大提取数\n范围: 1~50 | 默认: 5\n作用: 控制 DOM 结构作为 RAG 上下文的详细程度\n建议: 页面元素多的复杂系统可调到 10~15",
            },
            # ★ 深度合并：以下 4 项 RAG 调参原在「系统配置 → RAG 分组」，现收敛为
            #   本处单一编辑源（引擎读键为 RAG_*，经 pipeline_config 大小写归一化生效）
            "rag_rrf_k": {
                "type": "int",
                "default": 60,
                "min": 1,
                "max": 200,
                "label": "RRF 融合常数",
                "help": "RRF 融合常数\n范围: 1~200 | 默认: 60\n越大低排名结果权重越高\n作用: 控制混合检索 RRF 融合时低排名结果的保留强度",
            },
            "rag_hybrid_bm25_weight": {
                "type": "float",
                "default": None,
                "min": 0,
                "max": 1,
                "step": 0.05,
                "label": "混合 BM25 权重",
                "help": "混合检索 BM25 权重\n默认: null（自动推断）\n越大越偏关键词匹配\n置空自动推断",
            },
            "rag_similarity_metric": {
                "type": "enum",
                "options": [
                    {"value": "auto", "label": "自动推断（默认）"},
                    {"value": "cosine", "label": "余弦相似度"},
                    {"value": "dot", "label": "点积"},
                    {"value": "bm25", "label": "纯 BM25"},
                    {"value": "hybrid", "label": "混合 RRF"},
                ],
                "default": "auto",
                "label": "相似度计算方法",
                "help": "相似度计算方法\n默认: auto\nauto/cosine/dot/bm25/hybrid\n作用: 决定检索排序的相似度口径",
            },
            "rag_l7_cross_encoder_threshold": {
                "type": "float",
                "default": 0.3,
                "min": 0,
                "max": 1,
                "step": 0.05,
                "label": "L7 重打分阈值",
                "help": "L7 Cross-Encoder 重打分阈值\n范围: 0~1 | 默认: 0.3\n低于此值的 chunk 被过滤\n调低→保留更多（召回率高）\n调高→过滤更严（精确率高）",
            },
        },
    },
    # ── ★ 执行阈值分组（单一事实源）──────────────────────────────────────
    #   需求：项目配置页「CI/CD 集成 → 执行阈值」原为只读镜像，且这些键在流水线
    #   配置页无编辑入口。此处将其正式并入流水线配置，成为唯一可编辑源，随后
    #   移除项目配置处的只读展示。全部键经 pipeline_config 大小写归一化读取生效。
    "exec_thresholds": {
        "title": "执行阈值",
        "help": "Step4 执行与结果判定所用的各类阈值（仅此处可编辑）\n引擎读取时大小写归一化，改后生效\n即原「项目配置→CI/CD→执行阈值」的只读镜像，已收敛到此唯一编辑源",
        "items": {
            "requirement_gate_decomposition_threshold": {
                "type": "float",
                "default": 0.3,
                "min": 0,
                "max": 1,
                "step": 0.05,
                "label": "拆解审核阈值",
                "help": "需求拆解结果是否达标的判定阈值\n范围: 0~1 | 默认: 0.3\n作用: Step2 审核拆解是否充分\n影响: 低于该值判为拆解不充分需重试\n效果: 调高对拆解质量要求更严",
            },
            "requirement_gate_intent_threshold": {
                "type": "float",
                "default": 0.3,
                "min": 0,
                "max": 1,
                "step": 0.05,
                "label": "意图审核阈值",
                "help": "需求是否具有可测意图的判定阈值\n范围: 0~1 | 默认: 0.3\n作用: 审核需求是否有可验证意图\n影响: 低于该值判为需求不可测\n效果: 调高会拦截更多模糊需求",
            },
            "requirement_gate_step_semantic_threshold": {
                "type": "float",
                "default": 0.55,
                "min": 0,
                "max": 1,
                "step": 0.05,
                "label": "步骤语义阈值",
                "help": "需求与步骤语义匹配判定阈值\n范围: 0~1 | 默认: 0.55\n作用: 审核需求与步骤的语义匹配度\n影响: 低于该值判为步骤不匹配\n效果: 调高减少误放行的步骤",
            },
            "requirement_gate_top_score_threshold": {
                "type": "float",
                "default": 0.55,
                "min": 0,
                "max": 1,
                "step": 0.05,
                "label": "匹配得分阈值",
                "help": "需求匹配命中得分门槛\n范围: 0~1 | 默认: 0.55\n作用: 高于该值视为命中\n影响: 决定匹配是否放行\n效果: 调高提升匹配精度、降低召回",
            },
            "rag_max_top_k": {
                "type": "int",
                "default": 50,
                "min": 1,
                "max": 200,
                "label": "RAG 最大 TopK",
                "help": "RAG 检索最大候选数\n范围: 1~200 | 默认: 50\n作用: 一次性召回的最大知识块数量\n影响: 候选越多上下文越全但越贵越慢\n效果: 调大提升召回、增加 Token 成本",
            },
            "rag_vector_search_threshold": {
                "type": "float",
                "default": 0.3,
                "min": 0,
                "max": 1,
                "step": 0.05,
                "label": "向量检索阈值",
                "help": "RAG 召回质量下限过滤阈值\n范围: 0~1 | 默认: 0.3\n作用: 过滤召回的相似度下限\n影响: 低于该相似度会被过滤\n效果: 调高减少噪声但可能漏召回",
            },
            "rag_semantic_dedup_threshold": {
                "type": "float",
                "default": 0.85,
                "min": 0,
                "max": 1,
                "step": 0.05,
                "label": "语义去重阈值",
                "help": "判定上下文是否重复的相似度阈值\n范围: 0~1 | 默认: 0.85\n作用: 识别并去重重复上下文\n影响: 高于该值判为重复并去重\n效果: 调低去重更激进、上下文更紧凑",
            },
            "ai_default_temperature": {
                "type": "float",
                "default": 0.0,
                "min": 0,
                "max": 2,
                "step": 0.05,
                "label": "AI 默认温度",
                "help": "AI 生成随机度\n范围: 0~2 | 默认: 0.0\n作用: 控制 AI 输出的随机性\n影响: 越高输出越发散、越低越保守\n效果: 测试场景建议低值保证稳定复现（同输入→同输出）",
            },
            "ai_default_max_tokens": {
                "type": "int",
                "default": 8192,
                "min": 256,
                "max": 65536,
                "step": 256,
                "label": "AI 默认最大 Token",
                "help": "AI 单次最大输出 Token\n范围: 256~65536 | 默认: 8192\n作用: 限制单次 AI 生成长度上限\n影响: 超出部分被截断\n效果: 调大支持更长输出、增加耗时成本",
            },
            "browser_parallel_auto_limit": {
                "type": "bool",
                "default": True,
                "label": "并发自动限制",
                "help": "是否按可用内存自动估算并发上限\n范围: 开/关 | 默认: 开\n作用: 开启时并发受可用内存约束\n影响: 自动推导浏览器并发数\n效果: 开启防内存溢出，关闭则手动设置",
            },
            "browser_max_parallel": {
                "type": "int",
                "default": 1,
                "min": 1,
                "max": 8,
                "label": "浏览器最大并发",
                "help": "同时运行的浏览器实例上限\n范围: 1~8 | 默认: 1\n作用: 并行执行浏览实例数\n影响: 决定 Step4 执行速度\n效果: 调高提速但需充足内存与稳定网络",
            },
            "browser_parallel_reserved_mb": {
                "type": "int",
                "default": 2048,
                "min": 512,
                "max": 32768,
                "step": 256,
                "label": "浏览器预留内存(MB)",
                "help": "为系统与开销预留的内存\n范围: 512~32768 | 默认: 2048\n作用: 估算可用并发时扣除该值\n影响: 决定可用并发数\n效果: 调大更保守、并发更低",
            },
            "browser_parallel_instance_mb": {
                "type": "int",
                "default": 800,
                "min": 256,
                "max": 4096,
                "step": 64,
                "label": "单实例占用(MB)",
                "help": "单浏览器实例平均内存占用\n范围: 256~4096 | 默认: 800\n作用: 估算每个实例的内存\n影响: 决定可用并发数\n效果: 按实际占用校准可更准确",
            },
            "risk_embedding_similarity_threshold": {
                "type": "float",
                "default": 0.5,
                "min": 0,
                "max": 1,
                "step": 0.05,
                "label": "风险嵌入相似度阈值",
                "help": "风险识别语义相似度判定门槛\n范围: 0~1 | 默认: 0.5\n作用: 判定是否命中同一风险\n影响: 高于该值视为命中\n效果: 调高减少误报风险",
            },
            "dedup_similarity_threshold": {
                "type": "float",
                "default": 0.85,
                "min": 0,
                "max": 1,
                "step": 0.05,
                "label": "用例去重阈值",
                "help": "判定两个用例是否重复的相似度阈值\n范围: 0~1 | 默认: 0.85\n作用: 识别并合并重复用例\n影响: 高于该值判为重复\n效果: 调低去重更强、减少冗余用例",
            },
            "step2_changed_page_threshold": {
                "type": "float",
                "default": 0.5,
                "min": 0,
                "max": 1,
                "step": 0.05,
                "label": "页面变更检测阈值",
                "help": "判定页面是否变更的相似度门槛\n范围: 0~1 | 默认: 0.5\n作用: 检测页面是否发生变更\n影响: 低于该值视为已变更\n效果: 调高更敏感地检出页面变化",
            },
            "baseline_health_degraded_threshold": {
                "type": "float",
                "default": 0.6,
                "min": 0,
                "max": 1,
                "step": 0.05,
                "label": "性能退化阈值",
                "help": "判定性能是否退化的健康度门槛\n范围: 0~1 | 默认: 0.6\n作用: 性能基线退化判定\n影响: 低于该值视为基线退化\n效果: 调高更早告警性能下滑",
            },
            "confidence_weight_assertion": {
                "type": "float",
                "default": 0.30,
                "min": 0,
                "max": 1,
                "step": 0.05,
                "label": "置信度权重·断言率",
                "help": "置信度计算中断言覆盖的权重\n范围: 0~1 | 默认: 0.30\n作用: 断言对可信度的贡献\n影响: 权重和保持≈1.0\n效果: 需与其他权重联动调整",
            },
            "confidence_weight_structured": {
                "type": "float",
                "default": 0.20,
                "min": 0,
                "max": 1,
                "step": 0.05,
                "label": "置信度权重·结构化率",
                "help": "置信度计算中结构化输出的权重\n范围: 0~1 | 默认: 0.20\n作用: 结构化一致性对可信度的贡献\n影响: 权重和保持≈1.0\n效果: 需与其他权重联动调整",
            },
            "confidence_weight_hard_verify": {
                "type": "float",
                "default": 0.18,
                "min": 0,
                "max": 1,
                "step": 0.05,
                "label": "置信度权重·硬校验率",
                "help": "置信度计算中硬校验结果的权重\n范围: 0~1 | 默认: 0.18\n作用: 硬校验对可信度的贡献\n影响: 权重和保持≈1.0\n效果: 需与其他权重联动调整",
            },
            "confidence_weight_first_try": {
                "type": "float",
                "default": 0.15,
                "min": 0,
                "max": 1,
                "step": 0.05,
                "label": "置信度权重·首试通过率",
                "help": "置信度计算中首试即过的权重\n范围: 0~1 | 默认: 0.15\n作用: 首次尝试成功对可信度的贡献\n影响: 权重和保持≈1.0\n效果: 需与其他权重联动调整",
            },
            "confidence_weight_semantic": {
                "type": "float",
                "default": 0.12,
                "min": 0,
                "max": 1,
                "step": 0.05,
                "label": "置信度权重·语义对齐度",
                "help": "置信度计算中语义匹配的权重\n范围: 0~1 | 默认: 0.12\n作用: 语义对齐对可信度的贡献\n影响: 权重和保持≈1.0\n效果: 需与其他权重联动调整",
            },
            "confidence_weight_cache": {
                "type": "float",
                "default": 0.05,
                "min": 0,
                "max": 1,
                "step": 0.05,
                "label": "置信度权重·缓存命中率",
                "help": "置信度计算中缓存命中的权重\n范围: 0~1 | 默认: 0.05\n作用: 缓存命中对可信度的贡献\n影响: 权重和保持≈1.0\n效果: 需与其他权重联动调整",
            },
        },
    },
}


# ── 深度合并：Step4 插件开关从 config.yaml step4_plugins 收敛到流水线配置 ──
#   单一编辑源：此处注入各插件 enabled 布尔开关（引擎在 browser_core._init_plugins
#   以本单一事实源覆盖插件 enabled 开关注入；各插件内部阈值仍留 config.yaml 默认，
#   不在此重复暴露，避免冗余）。键名保存为小写，经 pipeline_config 大小写归一化生效。
_STEP4_PLUGIN_KEYS = (
    "ground_truth",
    "visual_baseline",
    "self_healing",
    "long_term_memory",
    "performance_monitor",
    "self_healing_vision",
    "harness_governance",
    "scorecard_oracle",
    "token_budget",
    "flaky_guard",
    "multi_agent",
    "business_capabilities",
    "cdp_metrics",
    "perf_collector",
)
for _pk in _STEP4_PLUGIN_KEYS:
    CONFIG_SCHEMA["step4"]["items"][f"step4_plugins_{_pk}_enabled"] = {
        "type": "bool",
        "default": True,
        "label": f"插件 {_pk} 启用",
        "help": f"Step4 浏览器执行插件 {_pk} 的启用开关\n范围: 开/关 | 默认: 开\n作用: 决定该插件在浏览器执行阶段是否介入（执行增强、结果校验或数据采集）\n影响: 关闭后该插件能力停用，仅走基础执行链路，再次开启即时恢复",
    }


# ── 路由函数 ──


def _get_user_id():
    try:
        from src.core.config_loader import get_current_user

        uid = get_current_user()
        if not uid:
            from flask import abort

            abort(401, description="未登录")
        return uid
    except Exception:
        from flask import abort

        abort(401, description="未登录")


def _get_conn():
    from src.core.db_manager import get_connection

    return get_connection()


def _read_user_overrides(user_id: str):
    conn = _get_conn()
    try:
        _ensure_table(conn)
        rows = conn.execute(
            "SELECT config_key, config_value FROM pipeline_user_config WHERE user_id=%s",
            (user_id,),
        ).fetchall()
        return {r["config_key"]: r["config_value"] for r in rows}
    finally:
        try:
            conn.close()
        except Exception as _e:
            _log.debug("execute + fetchall + conn.close: %s", _e, exc_info=True)


@pipeline_cfg_bp.route("/api/user-pipeline-config", methods=["GET"])
def get_user_config():
    uid = _get_user_id()
    overrides = _read_user_overrides(uid)

    # 按前端期望的嵌套结构组织：{schema: {...}, config: {...}}
    schema_out = {}
    config_out = {}
    recommended_out = {}

    # 按项目检测一次信号（需求数/文档数/trace/figma 可用性），供 step2/step3 共用
    _proj_sig = None
    with guard("get_user_config: from src.core.config_loader import c", log=_log):
        from src.core.config_loader import config as _rcfg
        from src.core.pipeline_config import detect_project_signals

        _proj = getattr(_rcfg, "project_name", "") or ""
        _proj_sig = detect_project_signals(_proj)

    for group_key, group in CONFIG_SCHEMA.items():
        schema_out[group_key] = {
            "title": group.get("title", group_key),
            "help": group.get("help", ""),
            "items": {},
        }
        config_out[group_key] = {}
        items = group.get("items", {})
        for key, meta in items.items():
            # schema: 仅结构定义（type/default/label/help/min/max/step）
            schema_out[group_key]["items"][key] = {
                "type": meta.get("type", "str"),
                "default": meta.get("default"),
                "label": meta.get("label", key),
                "help": meta.get("help", ""),
                "min": meta.get("min"),
                "max": meta.get("max"),
                "step": meta.get("step"),
            }
            # config: 运行时值 + 覆盖状态
            val = overrides.get(key)
            type_ = meta.get("type", "str")
            is_overridden = key in overrides
            if val is not None:
                if type_ == "bool":
                    val = str(val).lower() in ("true", "1", "yes")
                elif type_ == "int":
                    try:
                        val = int(val)
                    except Exception:
                        val = meta["default"]
                elif type_ == "float":
                    try:
                        val = float(val)
                    except Exception:
                        val = meta["default"]
                else:
                    val = str(val)
            else:
                val = meta.get("default")
                is_overridden = False
            config_out[group_key][key] = {
                "value": val,
                "is_overridden": is_overridden,
            }

    # ★ 推荐值：统一算法为全部 9 组生成（step2/step3 复用 _auto_tune，其余分组启发式）
    #   只读提示，不影响引擎实际读取；任何异常降级为空 dict，绝不阻断配置加载。
    try:
        from src.core._config_recommender import recommend_all

        recommended_out = recommend_all(CONFIG_SCHEMA, _proj_sig)
    except Exception as e:
        _log.warning("[pipeline_config] 推荐值计算失败: %s", str(e)[:80])
        recommended_out = {}

    result = {"success": True, "schema": schema_out, "config": config_out}
    if recommended_out:
        result["recommended"] = recommended_out
    return jsonify(result)


@pipeline_cfg_bp.route("/api/pipeline-config/recommended", methods=["GET"])
def get_recommended_config():
    """返回全部 9 组算法自调优推荐值（零用户输入，一键填入）。"""
    try:
        from src.core._config_recommender import recommend_all
        from src.core.pipeline_config import detect_project_signals
        from src.core.config_loader import config as _acfg

        proj = getattr(_acfg, "project_name", "") or ""
        sig = detect_project_signals(proj)

        # ★ 统一算法：step2/step3 复用 _auto_tune，其余分组启发式；全部收敛后可落地
        recommended = recommend_all(CONFIG_SCHEMA, sig)

        return jsonify(
            {
                "success": True,
                "recommended": recommended,
                "_meta": {
                    "project": proj,
                    "model_window": sig.get("model_window"),
                    "num_reqs": sig.get("num_reqs"),
                    "num_docs": sig.get("num_docs"),
                    "doc_total_chars": sig.get("doc_total_chars"),
                    "has_rag": sig.get("has_rag"),
                    "has_trace": sig.get("has_trace"),
                    "has_db": sig.get("has_db"),
                    "has_figma": sig.get("has_figma"),
                },
            }
        )
    except Exception as e:
        _log.warning("[pipeline_config] 推荐值计算失败: %s", str(e)[:200])
        return jsonify({"success": False, "error": str(e)[:200]})


@pipeline_cfg_bp.route("/api/user-pipeline-config", methods=["PUT"])
def put_user_config():
    uid = _get_user_id()
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "请求体为空"}), 400

    valid_keys = set()
    for group in CONFIG_SCHEMA.values():
        valid_keys.update(group.get("items", {}).keys())
    # ★ 安全校验：仅允许 CONFIG_SCHEMA 白名单内的 key 写入，防止任意注入
    # 注意：故意不 update(body.keys())，只接受已注册的配置项

    conn = _get_conn()
    updated = 0
    try:
        _ensure_table(conn)
        for key, val in body.items():
            if key not in valid_keys:
                continue
            conn.execute(
                "INSERT INTO pipeline_user_config(user_id, config_key, config_value, source) "
                "VALUES(%s,%s,%s,'manual') "
                "ON DUPLICATE KEY UPDATE config_value=VALUES(config_value), source='manual'",
                (uid, key, str(val)),
            )
            updated += 1
        conn.commit()
        _log.info("用户 %s 更新了 %d 项流水线配置", uid, updated)
    except Exception as e:
        _log.error("保存流水线配置失败: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        try:
            conn.close()
        except Exception as _e:
            _log.debug("update + commit + update: %s", _e, exc_info=True)

    with guard("put_user_config: from src.core.pipeline_config import", log=_log):
        from src.core.pipeline_config import invalidate_user_config_cache

        invalidate_user_config_cache(uid)

    return jsonify({"success": True, "updated": updated}), 200


@pipeline_cfg_bp.route("/api/user-pipeline-config", methods=["DELETE"])
def delete_user_config():
    uid = _get_user_id()
    body = request.get_json(silent=True) or {}
    key = str(body.get("key", "")).strip()
    if not key:
        return jsonify({"ok": False, "error": "缺少 key 参数"}), 400

    conn = _get_conn()
    try:
        _ensure_table(conn)
        conn.execute(
            "DELETE FROM pipeline_user_config WHERE user_id=%s AND config_key=%s",
            (uid, key),
        )
        conn.commit()
        _log.info("用户 %s 恢复默认配置: %s", uid, key)
    except Exception as e:
        _log.error("删除用户配置失败: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        try:
            conn.close()
        except Exception as _e:
            _log.debug("commit + conn.close + close(): %s", _e, exc_info=True)

    try:
        from src.core.pipeline_config import invalidate_user_config_cache

        invalidate_user_config_cache(uid)
    except Exception as _e:
        _log.debug("conn.close + close() + cache: %s", _e, exc_info=True)

    return jsonify({"success": True, "restored": key}), 200


# ════════════════════════════════════════════════════════════════════════
# ★ 切项目自动套推荐值（用户偏好驱动的自动调参）
# ════════════════════════════════════════════════════════════════════════

@pipeline_cfg_bp.route("/api/pipeline-config/apply-recommended", methods=["POST"])
def post_apply_recommended():
    """手动触发：计算算法推荐值并写入 pipeline_user_config（按钮可选复用）。"""
    uid = _get_user_id()
    try:
        from src.backend.routes.pipeline_config._auto_apply import (
            apply_recommended_to_user,
        )

        applied = apply_recommended_to_user(uid, force=True)
        return jsonify({"success": True, "applied": applied})
    except Exception as e:
        _log.warning("手动套用推荐值失败: %s", e)
        return jsonify({"success": False, "error": str(e)[:200]}), 500


@pipeline_cfg_bp.route("/api/user-preference/<key>", methods=["GET"])
def get_user_preference(key):
    """读取用户偏好（值已 JSON 解析）。"""
    uid = _get_user_id()
    try:
        from src.backend.routes.pipeline_config._auto_apply import read_pref

        return jsonify({"ok": True, "value": read_pref(uid, key)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@pipeline_cfg_bp.route("/api/user-preference/<key>", methods=["PUT"])
def put_user_preference(key):
    """写入用户偏好（值以 JSON 存储，支持 bool/number/str）。"""
    uid = _get_user_id()
    body = request.get_json(silent=True) or {}
    if "value" not in body:
        return jsonify({"ok": False, "error": "缺少 value 字段"}), 400
    try:
        from src.backend.routes.pipeline_config._auto_apply import write_pref

        write_pref(uid, key, body["value"])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500
