# -*- coding: utf-8 -*-
"""
模块级常量（消除魔法数字）与 Prompt 模板
"""

import sys
import json
from pathlib import Path
from typing import Any
import logging

_log = logging.getLogger(__name__)

# 确保 src/ 在 sys.path 中
_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.core.config_loader import config
from src.core.pipeline_config import pipeline_config
from src.core.prompt_templates import prompt_templates
from src.ai.ai_client import ai_brain as _ai_brain

# ══════════════════════════════════════════════════
# 模块级常量（消除魔法数字）
# ══════════════════════════════════════════════════

# AI 调用参数
BREAKDOWN_TEMPERATURE = pipeline_config.ai_default_temperature  # 需求拆解温度
BREAKDOWN_MAX_TOKENS = pipeline_config.ai_default_max_tokens  # 需求拆解最大 token
DISCOVER_TEMPERATURE = 0.2  # 模块发现温度
DISCOVER_MAX_TOKENS = (
    pipeline_config.step2_discover_max_tokens
)  # 模块发现最大 token ★ v6: pipeline_config
DISCOVER_RAW_TEXT_LIMIT = (
    pipeline_config.step2_discover_raw_text_limit
)  # 模块发现原始文本截断长度 ★ v6: pipeline_config
CROSS_DEP_TEMPERATURE = 0.2  # 跨模块依赖分析温度
CROSS_DEP_MAX_TOKENS = 1024  # 跨模块依赖分析最大 token
_EXTRACT_TEMPERATURE = pipeline_config.ai_default_temperature  # 三元组提取温度
_EXTRACT_MAX_TOKENS = pipeline_config.ai_default_max_tokens  # 三元组提取最大 token
_STABILITY_SEED = pipeline_config.ai_stability_seed  # 确定性采样种子（None=不传seed）
CROSS_DEP_REQ_SUMMARY_LIMIT = 80  # 跨模块分析需求摘要条数上限
CROSS_DEP_DESC_SNIPPET_LEN = 80  # 需求描述摘要截断长度

# 去重参数
DEDUP_DEFAULT_SIMILARITY_THRESHOLD = (
    pipeline_config.dedup_similarity_threshold
)  # 默认相似度去重阈值

# 一致性检查参数
CONSISTENCY_FIELD_PATTERN = r"([\u4e00-\u9fff]{2,6})[：:]"  # 字段名提取正则
CONSISTENCY_TITLE_KEY_LEN = 10  # 标题取前N字作为分组 key

# 依赖验证参数
DEP_SHARED_PATH_MIN_LEN = 5  # 共享 API 路径最小公共前缀长度

# Schema 校验参数
SCHEMA_ERROR_LOG_THRESHOLD = 2  # 超过此数量的错误才打印汇总
SCHEMA_ERROR_DETAIL_LIMIT = 3  # 最多打印的错误详情条数

# ── 需求拆解系统提示词模板（breakdown_system）──
BREAKDOWN_SYSTEM_TEMPLATE = (
    "你是一名资深软件需求分析师，擅长从原始会议纪要、需求文档中提取和拆解功能需求。\n"
    "你的任务是将用户提供的原始需求文本，拆解为独立的需求条目，并识别每条需求所属的"
    "功能模块和可能的依赖关系。\n\n"
    "{focus_line}"
    "## 输出格式\n"
    "请严格输出一个 JSON 数组，每个元素包含以下字段：\n"
    "- req_id: 需求编号，格式为 REQ-XXX（XXX 为三位数字，从 {id_prefix} 开始递增）\n"
    "- module: 功能模块名称，例如【模块A】、【模块B】、【模块C】等\n"
    "- description: 需求描述，用简洁的语句概括需求要点（50-200 字），"
    "保留原始文档中的关键条件、数值和逻辑\n"
    '- dependency_req_id: 依赖的前置需求编号，如果没有依赖则为空字符串 ""\n'
    '- source: 该需求在原文中的出处，格式为段落标题或首句关键词（10-30字），如 "§2.1 权限控制"\n\n'
    "## 拆解规则\n"
    "1. 每条需求应是一个独立、可测试的功能点\n"
    "2. 不要遗漏任何需求点，即使只有一句话也要作为独立条目\n"
    "3. module 字段应尽可能准确分类，同一模块的需求集中在一起\n"
    "4. 如果需求 B 的实现在逻辑上依赖需求 A 先完成（例如 B 修改了 A 的 UI），"
    "则 B 的 dependency_req_id 填写 A 的编号\n"
    "5. 忽略纯后端需求和未定（待定）需求\n"
    "6. source 字段标注该需求在文档中的出处（段落标题/首句），方便溯源\n"
    "7. 不要输出任何 JSON 之外的解释文字\n"
    "{ui_section}"
    "## 输出示例\n"
    "[\n"
    "  {{\n"
    '    "req_id": "REQ-001",\n'
    '    "module": "模块A",\n'
    '    "description": "用户执行操作A，系统返回处理结果",\n'
    '    "dependency_req_id": "",\n'
    '    "source": "§2.1 模块A功能"\n'
    "  }},\n"
    "  {{\n"
    '    "req_id": "REQ-002",\n'
    '    "module": "模块A",\n'
    '    "description": "用户配置模块A参数，系统保存并生效",\n'
    '    "dependency_req_id": "REQ-001",\n'
    '    "source": "§2.1 模块A功能 段落2"\n'
    "  }}\n"
    "]\n"
)
prompt_templates.register("breakdown_system", BREAKDOWN_SYSTEM_TEMPLATE)

# ── 去重：基于代码相似度（非 AI 提示词，注册占位）──
prompt_templates.register("dedup_system", "")
