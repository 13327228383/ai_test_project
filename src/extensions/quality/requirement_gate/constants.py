# -*- coding: utf-8 -*-
"""
门禁常量 Mixin + GateResult 数据类
包含：阈值参数、同义词映射、否定词列表、MySQL 同义词加载
"""

import logging
from src.core.guarded import guard
import json
from typing import Set
from dataclasses import dataclass, field

_log = logging.getLogger(__name__)

from src.core.pipeline_config import pipeline_config


class _GateConstantsMixin:
    """门禁常量 Mixin —— 所有阈值、同义词、否定词均定义在此。"""

    # ── 匹配阈值与算法参数（从 PipelineConfig 读取，可通过环境变量覆盖）──────────────────
    _DECOMPOSITION_BLOCK_THRESHOLD = (
        pipeline_config.requirement_gate_decomposition_threshold
    )  # 拆解功能点匹配度低于此值 → 阻断
    _BLOCK_THRESHOLD = pipeline_config.requirement_gate_decomposition_threshold  # 别名
    _DECOMPOSITION_WARN_THRESHOLD = (
        pipeline_config.requirement_gate_decomposition_warn_threshold
    )  # 拆解功能点匹配度低于此值 → 警告
    _INTENT_BLOCK_THRESHOLD = (
        pipeline_config.requirement_gate_intent_threshold
    )  # 测试用例整体匹配度低于此值 → 阻断
    _STEP_BLOCK_THRESHOLD = (
        pipeline_config.requirement_gate_step_block_threshold
    )  # 测试用例步骤匹配度低于此值 → 阻断
    _MAX_KEYWORDS = 20  # 提取关键词的最大数量
    _NEGATION_WINDOW = 10  # 否定词检测窗口（字符数）

    # ★ 同义词映射表（中文常见同义词）
    _SYNONYM_MAP = {
        "创建": {"创建", "新增", "添加", "建立", "生成"},
        "删除": {"删除", "移除", "清除", "销毁"},
        "修改": {"修改", "编辑", "更新", "变更", "调整"},
        "查询": {"查询", "查找", "搜索", "检索", "查看"},
        "登录": {"登录", "登入", "登陆", "signin"},
        "退出": {"退出", "登出", "注销", "logout"},
        "导入": {"导入", "上传", "引入"},
        "导出": {"导出", "下载", "输出"},
        "审核": {"审核", "审批", "核准", "审查"},
        "提交": {"提交", "报送", "上报"},
        "取消": {"取消", "撤销", "撤回", "作废"},
        "启用": {"启用", "激活", "开启"},
        "禁用": {"禁用", "停用", "关闭", "冻结"},
        "分配": {"分配", "派发", "分派", "指定"},
        "回收": {"回收", "收回", "撤销"},
        "合并": {"合并", "整合", "融合"},
        "拆分": {"拆分", "分割", "分解"},
        "复制": {"复制", "拷贝", "克隆"},
        "移动": {"移动", "转移", "迁移"},
        "排序": {"排序", "排列", "整理"},
        "筛选": {"筛选", "过滤", "条件查询"},
        "统计": {"统计", "汇总", "合计", "计算"},
        "对比": {"对比", "比较", "对照"},
        "验证": {"验证", "校验", "检查", "核对"},
        "授权": {"授权", "许可", "权限分配"},
        "配置": {"配置", "设置", "设定", "参数调整"},
        "同步": {"同步", "更新", "一致化"},
        "备份": {"备份", "存档", "复制保存"},
        "恢复": {"恢复", "还原", "回滚"},
        "归档": {"归档", "存档", "封存"},
        "提醒": {"提醒", "通知", "告警", "提示"},
        "订阅": {"订阅", "关注", "跟踪"},
        "分享": {"分享", "共享", "转发"},
        "收藏": {"收藏", "关注", "标记"},
        "评价": {"评价", "评分", "打分", "反馈"},
        "推荐": {"推荐", "建议", "优选"},
        "关联": {"关联", "绑定", "链接", "对应"},
        "解绑": {"解绑", "解除", "取消关联"},
        "替换": {"替换", "更换", "更新"},
        "覆盖": {"覆盖", "重写", "替换"},
        "清空": {"清空", "清除", "重置", "归零"},
        "初始化": {"初始化", "重置", "恢复默认"},
        "批量": {"批量", "大量", "多条", "批量处理"},
        "逐条": {"逐条", "单个", "逐一", "逐个"},
        "全选": {"全选", "全部选择", "批量选择"},
        "反选": {"反选", "反向选择"},
        "展开": {"展开", "打开", "显示"},
        "收起": {"收起", "折叠", "隐藏"},
        "放大": {"放大", "扩大", "放大显示"},
        "缩小": {"缩小", "缩小显示", "压缩"},
        "刷新": {"刷新", "更新", "重新加载"},
        "预览": {"预览", "查看", "预浏览"},
        "打印": {"打印", "输出", "打印输出"},
        "导出": {"导出", "下载", "输出"},
        "导入": {"导入", "上传", "引入"},
        "模板": {"模板", "样板", "范本", "模型"},
        "版本": {"版本", "修订", "迭代"},
        "历史": {"历史", "记录", "日志", "轨迹"},
        "轨迹": {"轨迹", "路径", "流程"},
        "流程": {"流程", "步骤", "工序", "环节"},
        "节点": {"节点", "步骤", "环节", "关卡"},
        "状态": {"状态", "情况", "阶段", "模式"},
        "角色": {"角色", "身份", "岗位", "职责"},
        "部门": {"部门", "单位", "组织", "团队"},
        "岗位": {"岗位", "职位", "职务", "角色"},
        "用户": {"用户", "账号", "操作员", "人员"},
        "管理员": {"管理员", "超级用户", "系统管理员", "admin"},
        "普通用户": {"普通用户", "一般用户", "普通人员", "user"},
        "访客": {"访客", "游客", "临时用户", "guest"},
        "权限": {"权限", "权利", "授权", "许可"},
        "菜单": {"菜单", "导航", "栏目", "功能列表"},
        "按钮": {"按钮", "控件", "操作项", "功能键"},
        "表单": {"表单", "表格", "单据", "输入界面"},
        "列表": {"列表", "清单", "目录", "明细"},
        "详情": {"详情", "明细", "详细信息", "具体内容"},
        "摘要": {"摘要", "概要", "简介", "概述"},
        "备注": {"备注", "注释", "说明", "附注"},
        "附件": {"附件", "文件", "文档", "资料"},
        "标签": {"标签", "标记", "标识", "分类"},
        "分类": {"分类", "类别", "类型", "分组"},
        "分组": {"分组", "归类", "分群", "分簇"},
        "排序": {"排序", "排列", "整理", "顺序"},
        "编号": {"编号", "序号", "代码", "标识码"},
        "编码": {"编码", "代码", "编号", "标识"},
        "名称": {"名称", "名字", "标题", "称谓"},
        "标题": {"标题", "题目", "名称", "主题"},
        "描述": {"描述", "说明", "介绍", "阐述"},
        "内容": {"内容", "信息", "数据", "资料"},
        "信息": {"信息", "数据", "内容", "资料"},
        "数据": {"数据", "信息", "资料", "记录"},
        "记录": {"记录", "日志", "条目", "数据"},
        "字段": {"字段", "列", "属性", "数据项"},
        "属性": {"属性", "特性", "特征", "参数"},
        "参数": {"参数", "变量", "配置项", "设置"},
        "值": {"值", "数值", "数据", "内容"},
        "类型": {"类型", "种类", "类别", "分类"},
        "格式": {"格式", "样式", "形式", "规范"},
        "规则": {"规则", "规范", "标准", "准则"},
        "策略": {"策略", "方案", "方法", "措施"},
        "算法": {"算法", "方法", "公式", "计算规则"},
        "模型": {"模型", "模板", "样板", "范式"},
        "方案": {"方案", "计划", "策略", "方法"},
        "计划": {"计划", "方案", "规划", "安排"},
        "任务": {"任务", "工作", "作业", "事项"},
        "项目": {"项目", "工程", "课题", "任务"},
        "产品": {"产品", "商品", "货物", "物品"},
        "服务": {"服务", "业务", "功能", "应用"},
        "应用": {"应用", "软件", "系统", "程序"},
        "系统": {"系统", "平台", "软件", "应用"},
        "平台": {"平台", "系统", "基础", "底座"},
        "模块": {"模块", "组件", "单元", "部件"},
        "组件": {"组件", "模块", "控件", "元件"},
        "接口": {"接口", "API", "服务接口", "调用接口"},
        "服务": {"服务", "接口", "功能", "应用"},
        "功能": {"功能", "特性", "能力", "用途"},
        "特性": {"特性", "特点", "特征", "属性"},
        "性能": {"性能", "效率", "速度", "响应"},
        "效率": {"效率", "性能", "速度", "效能"},
        "速度": {"速度", "速率", "效率", "性能"},
        "响应": {"响应", "反馈", "答复", "回执"},
        "延迟": {"延迟", "滞后", "延时", "等待"},
        "并发": {"并发", "并行", "同时", "多线程"},
        "容量": {"容量", "负载", "承载", "能力"},
        "负载": {"负载", "压力", "负荷", "工作量"},
        "压力": {"压力", "负载", "负荷", "强度"},
        "稳定性": {"稳定性", "可靠性", "健壮性", "鲁棒性"},
        "可靠性": {"可靠性", "稳定性", "可用性", "可信度"},
        "可用性": {"可用性", "可靠性", "稳定性", "易用性"},
        "安全性": {"安全性", "保密性", "防护性", "安全等级"},
        "保密性": {"保密性", "安全性", "机密性", "隐私性"},
        "完整性": {"完整性", "一致性", "准确性", "正确性"},
        "一致性": {"一致性", "完整性", "统一性", "协调性"},
        "准确性": {"准确性", "正确性", "精确性", "无误性"},
        "正确性": {"正确性", "准确性", "合理性", "合法性"},
        "合法性": {"合法性", "合规性", "正当性", "合理性"},
        "合规性": {"合规性", "合法性", "规范性", "标准性"},
        "规范性": {"规范性", "标准性", "合规性", "统一性"},
        "标准性": {"标准性", "规范性", "统一性", "一致性"},
        "兼容性": {"兼容性", "适配性", "互操作性", "可移植性"},
        "可移植性": {"可移植性", "兼容性", "迁移性", "跨平台性"},
        "可扩展性": {"可扩展性", "扩展性", "伸缩性", "弹性"},
        "可维护性": {"可维护性", "维护性", "易维护性", "可修复性"},
        "可读性": {"可读性", "易读性", "清晰性", "易懂性"},
        "可测试性": {"可测试性", "测试性", "可验证性", "可检验性"},
        "可追溯性": {"可追溯性", "追踪性", "可回溯性", "可审计性"},
        "可审计性": {"可审计性", "可追溯性", "可审查性", "可核查性"},
        "可配置性": {"可配置性", "配置性", "可定制性", "可调整性"},
        "可定制性": {"可定制性", "可配置性", "可个性化", "可调整性"},
        "可重用性": {"可重用性", "复用性", "可复用性", "可重复使用性"},
        "复用性": {"复用性", "可重用性", "可复用性", "重复使用性"},
    }

    # 构建反向映射：每个词 -> 它的同义词集合
    _SYNONYM_EXPAND: dict[str, Set[str]] = {}

    # ★ 否定词列表（用于检测否定语境）
    _NEGATION_WORDS = {
        "不",
        "无",
        "非",
        "未",
        "否",
        "没有",
        "禁止",
        "限制",
        "除外",
        "排除",
        "避免",
    }

    # ── ★ KEEP-11: MySQL 同义词库加载 ──
    @classmethod
    def _load_synonyms_from_db(cls):
        """从 MySQL 加载同义词表，与硬编码 _SYNONYM_MAP 合并。"""
        with guard("_load_synonyms_from_db: from src.core.db_manager import get_", log=_log):
            from src.core.db_manager import get_connection

            conn = get_connection()
            try:
                # ★ 2026-06-22：建表已统一由 src/core/schema.sql 负责，本处不再 CREATE TABLE
                rows = conn.execute(
                    "SELECT core_word, synonym_group FROM synonym_library"
                ).fetchall()
                try:
                    from src.core.config_loader import config

                    project = str(getattr(config, "project_name", "") or "")
                except Exception:
                    project = ""
                if project:
                    rows = list(rows or []) + list(
                        conn.execute(
                            "SELECT core_word, synonym_group FROM project_synonym_overrides WHERE project=%s",
                            (project,),
                        ).fetchall()
                        or []
                    )
                for row in rows or []:
                    core = row["core_word"]
                    group = (
                        json.loads(row["synonym_group"])
                        if isinstance(row["synonym_group"], str)
                        else row["synonym_group"]
                    )
                    if isinstance(group, list):
                        cls._SYNONYM_MAP.setdefault(core, set()).update(group)
                if rows:
                    _log.info(
                        "[RequirementGate] 从 MySQL 加载 %d 条同义词组", len(rows)
                    )
                    # 重建反向映射
                    cls._SYNONYM_EXPAND.clear()
                    for core_word, synonyms in cls._SYNONYM_MAP.items():
                        for word in synonyms:
                            cls._SYNONYM_EXPAND.setdefault(word, set()).update(synonyms)
            finally:
                conn.close()

    @classmethod
    def reload_synonyms_from_db(cls):
        """项目同义词覆盖写入后热刷新：从硬编码基线 + DB 重建同义词映射。

        ★ _load_synonyms_from_db 用 setdefault().update() 累加，直接重复调用会累积重复词。
          必须先从 _SYNONYM_MAP_BUILTIN 快照恢复硬编码基线，再清反向映射，最后重新加载 DB。
        """
        import copy as _copy

        with guard("reload_synonyms_from_db: cls._SYNONYM_MAP.clear()", log=_log, level=logging.WARNING):
            cls._SYNONYM_MAP.clear()
            if getattr(cls, "_SYNONYM_MAP_BUILTIN", None):
                cls._SYNONYM_MAP.update(_copy.deepcopy(cls._SYNONYM_MAP_BUILTIN))
            cls._SYNONYM_EXPAND.clear()
            cls._load_synonyms_from_db()


# 构建反向映射：每个词 -> 它的同义词集合
for core_word, synonyms in _GateConstantsMixin._SYNONYM_MAP.items():
    for word in synonyms:
        if word not in _GateConstantsMixin._SYNONYM_EXPAND:
            _GateConstantsMixin._SYNONYM_EXPAND[word] = set()
        _GateConstantsMixin._SYNONYM_EXPAND[word].update(synonyms)

# ★ 保存硬编码同义词基线快照（DB 加载前），供 reload_synonyms_from_db 热刷新时恢复
import copy as _builtin_copy

_GateConstantsMixin._SYNONYM_MAP_BUILTIN = _builtin_copy.deepcopy(
    _GateConstantsMixin._SYNONYM_MAP
)

# 模块导入时自动加载
_GateConstantsMixin._load_synonyms_from_db()


@dataclass
class GateResult:
    """门禁校验结果。"""

    passed: bool = True
    blockers: list[dict] = field(default_factory=list)  # 阻断项（必须修复）
    warnings: list[dict] = field(default_factory=list)  # 警告项（建议修复）
    summary: str = ""
