# -*- coding: utf-8 -*-
"""
门禁检查 Mixin (Facade)
通过多继承组合拆解、意图、持久化三个子 mixin，并保留共享的匹配算法。
"""

import logging
import re

_log = logging.getLogger(__name__)

from .constants import _GateConstantsMixin
from .check_mixins import _GateDecompositionMixin, _GateIntentsMixin, _GatePersistMixin


class _GateCheckMixin(_GateDecompositionMixin, _GateIntentsMixin, _GatePersistMixin):
    """门禁检查 Mixin —— 核心校验逻辑（Facade）。

    通过多继承组合三个子 mixin：
    - _GateDecompositionMixin: check_decomposition
    - _GateIntentsMixin: check_intents
    - _GatePersistMixin: persist_scores

    同时保留共享的匹配算法方法。
    """

    # ══════════════════════════════════════════════════════════
    # 匹配算法（共享工具方法）
    # ══════════════════════════════════════════════════════════

    def _extract_keywords(self, text: str) -> list[str]:
        """提取关键词（中文2-4字词 + 英文2+字符单词），过滤停用词后返回。"""
        if not text:
            return []

        # 中文关键词（2-4字）
        zh_keywords = re.findall(r"[\u4e00-\u9fff]{2,4}", text)
        # 英文单词（2+字符）
        en_keywords = re.findall(r"[a-zA-Z]{2,}", text.lower())

        # 去重并过滤常见停用词
        # ★ 增强：更完整的停用词列表
        stopwords = {
            "用户",
            "系统",
            "页面",
            "功能",
            "操作",
            "点击",
            "输入",
            "选择",
            "按钮",
            "菜单",
            "表格",
            "列表",
            "表单",
            "弹窗",
            "对话框",
            "的",
            "了",
            "是",
            "一个",
            "进行",
            "可以",
            "需要",
            "应该",
            "必须",
            "通过",
            "使用",
            "根据",
            "按照",
            "对于",
            "关于",
            "以及",
            "或者",
            "并且",
            "但是",
            "因为",
            "所以",
            "如果",
            "然后",
            "之后",
            "之前",
            "期间",
            "时候",
            "方式",
            "方法",
            "过程",
            "结果",
            "内容",
            "信息",
            "数据",
            "记录",
            "文件",
            "文档",
            "图片",
            "视频",
            "音频",
            "链接",
            "地址",
            "路径",
            "名称",
            "标题",
            "描述",
            "备注",
            "注释",
            "说明",
            "简介",
            "概述",
            "详情",
            "明细",
            "摘要",
            "总结",
            "报告",
            "通知",
            "消息",
            "邮件",
            "短信",
            "电话",
            "联系人",
            "客户",
            "供应商",
            "部门",
            "岗位",
            "角色",
            "权限",
            "账号",
            "密码",
            "登录",
            "退出",
            "注册",
            "激活",
            "验证",
            "确认",
            "提交",
            "保存",
            "取消",
            "关闭",
            "打开",
            "开始",
            "结束",
            "完成",
            "继续",
            "返回",
            "前进",
            "后退",
            "刷新",
            "重置",
            "清空",
            "删除",
            "新增",
            "添加",
            "创建",
            "编辑",
            "修改",
            "更新",
            "查询",
            "查看",
            "搜索",
            "筛选",
            "排序",
            "导出",
            "导入",
            "下载",
            "上传",
            "打印",
            "预览",
            "分享",
            "收藏",
            "关注",
            "订阅",
            "推荐",
            "评价",
            "评分",
            "反馈",
            "投诉",
            "建议",
            "意见",
            "申请",
            "审批",
            "审核",
            "核准",
            "备案",
            "归档",
            "备份",
            "恢复",
            "还原",
            "同步",
            "合并",
            "拆分",
            "复制",
            "粘贴",
            "剪切",
            "移动",
            "拖拽",
            "拖动",
            "滑动",
            "滚动",
            "缩放",
            "放大",
            "缩小",
            "全屏",
            "退出",
            "最小化",
            "最大化",
        }
        keywords = []
        for kw in zh_keywords + en_keywords:
            if kw not in stopwords and kw not in keywords:
                keywords.append(kw)

        return keywords[: self._MAX_KEYWORDS]

    def _check_in_requirement(self, keywords: list[str]) -> float:
        """
        检查关键词在需求文档中的匹配度。
        ★ 增强：支持同义词扩展 + 否定词检测
        返回 0.0-1.0 的匹配分数。
        """
        if not keywords or not self._requirement_lower:
            return 0.0

        matched = 0
        for kw in keywords:
            kw_lower = kw.lower()
            # 1. 直接匹配
            if kw_lower in self._requirement_lower:
                # 检查否定词语境
                if not self._has_negation_context(kw_lower, self._requirement_lower):
                    matched += 1
                continue
            # 2. 同义词扩展匹配
            synonyms = self._SYNONYM_EXPAND.get(kw_lower, set())
            for syn in synonyms:
                if syn.lower() in self._requirement_lower:
                    if not self._has_negation_context(
                        syn.lower(), self._requirement_lower
                    ):
                        matched += 1
                        break

        # 匹配分数 = 匹配关键词数 / 总关键词数
        return matched / len(keywords) if keywords else 0.0

    def _has_negation_context(
        self,
        keyword: str,
        text: str,
        window: int = _GateConstantsMixin._NEGATION_WINDOW,
    ) -> bool:
        """
        检测关键词附近是否有否定词。
        例如："不支持删除"中的"删除"应该被认为是否定语境。
        """
        idx = text.find(keyword)
        if idx < 0:
            return False
        # 检查关键词前的window个字符
        start = max(0, idx - window)
        context = text[start:idx]
        return any(neg in context for neg in _GateConstantsMixin._NEGATION_WORDS)
