# DatabaseAgentTestDataMixin — 测试数据准备

import datetime
import logging
import uuid
from typing import Any

_log = logging.getLogger(__name__)


class DatabaseAgentTestDataMixin:
    """场景驱动的测试数据准备方法。"""

    TEST_PREFIX: str = "TEST_"

    # ---- 场景驱动的测试数据准备 ----

    def prepare_test_data(
        self,
        scenario: str,
        **overrides,
    ) -> dict[str, Any]:
        """
        根据场景描述准备测试数据。支持命名场景和自由模式（传入 table+data 或 spec 字典）。
        返回 {"success": True, "scenario": "...", "inserted": [...], "tag": "TEST_xxx"}
        """
        self._ensure_connected()

        # 生成唯一的测试标记
        tag = overrides.get("tag") or self._make_tag(scenario)

        # 查找场景模板
        template_fn = getattr(self, f"_scenario_{scenario}", None)

        if template_fn:
            # 命名场景
            specs = template_fn(tag=tag, **overrides)
        else:
            # 自由模式：期望 overrides 中包含 spec
            specs = overrides.get("spec", [])
            if not specs:
                return {
                    "success": False,
                    "error": (
                        f"未知场景 '{scenario}'，"
                        f"且未提供 spec。支持的场景: {self.list_scenarios()}"
                    ),
                }

        # 逐个表插入
        results = []
        for spec in specs:
            table = spec["table"]
            data = dict(spec.get("data", {}))

            # 应用 overrides
            for k, v in overrides.items():
                if k in data or k.upper() in [c.upper() for c in data]:
                    # 找到匹配的字段名
                    for field in data:
                        if field.upper() == k.upper():
                            data[field] = v
                            break

            result = self.insert(table, data)
            results.append(result)

            if not result["success"]:
                return {
                    "success": False,
                    "error": f"插入 {table} 失败: {result.get('error')}",
                    "partial": results,
                }

            # 如果该表是后续表的外键父表，更新后续 spec
            last_id = result.get("lastrowid", 0)
            for next_spec in specs:
                next_table = next_spec["table"].upper()
                for dep in self._get_foreign_key_deps(next_table):
                    parent_t, parent_c, child_c, _ = dep
                    if parent_t == table.upper() and parent_c == "ID":
                        if child_c not in next_spec.get("data", {}):
                            next_spec.setdefault("data", {})[child_c] = last_id

        return {
            "success": True,
            "scenario": scenario,
            "tag": tag,
            "inserted": results,
        }

    def _make_tag(self, scenario: str) -> str:
        """生成唯一的测试数据标记。"""
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        uid = str(uuid.uuid4())[: self._TAG_UID_LENGTH]
        return f"{self.TEST_PREFIX}{scenario}_{ts}_{uid}"

    def list_scenarios(self) -> list[str]:
        """列出所有可用的命名场景。"""
        return [
            name[len("_scenario_") :]
            for name in dir(self)
            if name.startswith("_scenario_") and callable(getattr(self, name))
        ]

    # 场景模板 mixin 已移除：原 db_scenarios_mixins 写死业务规则，违反通用性原则。
    # prepare_test_data 保留自由模式（传入 spec）供调用方使用。

    # ---- 辅助方法 ----

    def _next_id(self, table: str) -> int:
        """获取表的下一可用 ID（当前最大 ID + 1）。查询失败时回退到时间戳伪 ID。"""
        self._ensure_connected()
        safe_table = self._quote_ident(table.upper())
        try:
            sql = f"SELECT COALESCE(MAX(ID), 0) AS max_id FROM {safe_table}"
            self._cursor.execute(sql)
            row = self._cursor.fetchone()
            return int(row[0]) + 1
        except Exception as e:
            _log.warning(
                "[DatabaseAgent] _next_id 查询 %s 失败: %s，回退到时间戳伪 ID", table, e
            )
            return int(datetime.datetime.now().timestamp() * 1000)

    # table_exists(), flashback_table() 由 DbAdapterMixin 提供
