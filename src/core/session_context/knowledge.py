# -*- coding: utf-8 -*-
"""
SessionKnowledgeMixin - 流水线知识注入
==================================
负责：按流水线阶段分级提供系统知识（Step2/Step3/Step4）、
      TraceabilityEngine 结构化 schema、测试账号获取、连接关闭。
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


class SessionKnowledgeMixin:
    """流水线知识注入 Mixin。"""

    def for_step2(self) -> str:
        lines = []
        lines.append("\n## 系统数据实体概览（辅助需求拆解时理解功能间数据依赖）\n")
        tables = self._get_table_list()
        if tables:
            lines.append("### 数据库核心表（表名体现业务实体）")
            for t in tables[: self.TABLE_LIST_MAX]:
                lines.append("- %s" % t)
            lines.append("")
        # ★ v3: 注入智能表关系分析
        rel = self._discover_relationships_by_sampling()
        if rel:
            lines.append(rel)
            lines.append("")
        lines.append(
            "★ 拆解建议：如果多个需求操作同一张表，或一张表的字段被多个模块引用，"
            "可能存在逻辑依赖关系，请在 dependency_req_id 中标注。"
        )
        return "\n".join(lines)

    def for_step3(self) -> str:
        lines = []
        lines.append("\n## 数据库字段类型与约束（辅助生成合法测试数据）\n")
        schema = self._get_table_schema_summary()
        if schema:
            lines.append(schema)
            lines.append("")
        # 注入表间关系（辅助生成跨表校验的 db_checks）
        rel = self._discover_relationships_by_sampling()
        if rel:
            lines.append(rel)
            lines.append("")
        lines.append(
            "★ 数据构造提示：生成测试用例步骤中的输入值时，请参考上述字段类型和约束，"
            "确保数值在合理范围内（如 INT 字段不要填字符串，VARCHAR(N) 不要超过 N 字符）。"
            "涉及多表操作的测试用例，需在 db_checks 中验证关联表的数据一致性。"
        )
        return "\n".join(lines)

    def for_step4(self) -> dict[str, Any]:
        return self.get_state(force_refresh=True)

    def for_traceability(self) -> str:
        """为 TraceabilityEngine 提供结构化 DB schema 文本。"""
        lines = []
        tables = self._get_table_list()
        if not tables:
            return ""

        lines.append("## 数据库表结构")
        sample_tables = tables[: self.TABLE_LIST_MAX]
        columns_by_table = self._batch_fetch_columns(sample_tables, include_extra=False)
        for t in sample_tables:
            cols = columns_by_table.get(t, [])
            if not cols:
                continue
            col_strs = [
                "%s(%s)" % (col[0], col[1]) for col in cols[: self.COLS_PER_TABLE_MAX]
            ]
            if col_strs:
                lines.append("- %s: %s" % (t, ", ".join(col_strs)))

        # 追加表间关系，供 traceability 与测试数据输入使用
        rel = self._discover_relationships_by_sampling()
        if rel:
            lines.append("\n## 数据库表间关系（1:N / N:1 / N:M）")
            lines.append(rel)

        return "\n".join(lines) if len(lines) > 1 else ""

    def get_test_accounts(self) -> str:
        """获取系统真实数据目录（自动发现 + 缓存 + AI 兜底）。
        委托给 SystemDataResolver，自动识别用户表/角色表/业务实体表。"""
        try:
            from src.extensions.system_data_resolver import system_data

            catalog = system_data.resolve()
            return catalog.get("catalog_text", "")
        except Exception as e:
            _log.info("[SessionContext] 系统数据目录获取失败: %s", e)
            return ""

    def close(self) -> None:
        if self._db_connected:
            try:
                from src.extensions.db.db_agent import db_agent

                db_agent.close()
            except Exception as e:
                _log.warning("close 关闭数据库连接异常: %s", e)
            finally:
                self._db_connected = False
