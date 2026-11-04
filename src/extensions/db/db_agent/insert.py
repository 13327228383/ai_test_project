from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.extensions.db.db_agent.connection import DatabaseAgent
# DatabaseAgentInsertMixin — 外键感知插入

import datetime
import logging
from collections.abc import Callable
from typing import Any

_log = logging.getLogger(__name__)


class DatabaseAgentInsertMixin:
    """外键约束感知的智能插入方法。"""

    # ---- 外键依赖图（内置核心业务表的外键关系） ----

    # 外键依赖定义：(子表, 父表, 子表外键列, 父表参照列, 默认值生成函数)
    _FK_DEPS = {
        "FORMULATION_FORMULA_DETAIL": [
            (
                "FORMULATION_BASIC_INFO",
                "ID",
                "FORMULATION_ID",
                lambda: None,
            ),  # FORMULATION_ID 必须由调用方提供
        ],
        "FILE_CHUNK": [
            ("FILE", "ID", "FILE_ID", lambda: None),
        ],
        "SENSOR_EVALUATION": [
            ("TASK_SCHEME", "ID", "SCHEME_ID", lambda: None),
        ],
        "SMOKE_EVALUATION": [
            ("TASK_SCHEME", "ID", "SCHEME_ID", lambda: None),
        ],
        "TASK_SCHEME": [
            ("PRODUCT_TASK", "ID", "TASK_ID", lambda: None),
        ],
        "SYS_USER": [
            (
                "SYS_ROLE",
                "ID",
                "ROLE_ID",
                lambda: DatabaseAgent.ROLE_VISITOR,
            ),  # 默认访客角色
        ],
        "TAG_RELATION": [
            ("SYS_TAG", "ID", "TAG_ID", lambda: None),
        ],
    }

    # ---- 外键约束感知的智能插入 ----

    def insert(self, table: str, data: dict[str, Any]) -> dict[str, Any]:
        """
        向指定表插入一行数据。自动处理外键依赖、时间戳字段、缺失 NOT NULL 字段。
        返回 {"success": True, "lastrowid": N, "table": "..."}
        """
        self._ensure_connected()
        table_upper = table.upper()

        # 外键依赖处理
        fk_deps = self._get_foreign_key_deps(table_upper)
        for parent_table, parent_col, child_col, default_fn in fk_deps:
            if child_col not in data or data[child_col] is None:
                # 尝试从 parent 获取默认值
                parent_val = self._resolve_parent_value(
                    parent_table, parent_col, default_fn
                )
                data[child_col] = parent_val

        # 时间戳字段自动填充
        # ★ 策略：当 _TABLE_COLUMNS 已加载时，仅写入表中实际存在的列；
        #          当未加载时，保持原有行为（始终写入，向下兼容）。
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        table_cols = self._TABLE_COLUMNS.get(table_upper)  # 未加载 schema
        table_cols_upper = (
            {c.upper() for c in table_cols} if table_cols is not None else None
        )
        for ts_col in ["CREATE_TIME", "UPDATE_TIME"]:
            # 仅当 schema 已加载 且 确定表中不存在该列时才跳过
            if table_cols_upper is not None and ts_col not in table_cols_upper:
                continue
            if ts_col in data:
                if data[ts_col] is None:
                    data[ts_col] = now
            else:
                data[ts_col] = now

        # 构造 INSERT 语句
        # ★ 安全修复：对column名做sanitize，防止SQL注入
        columns = [self._sanitize_ident(c) for c in data.keys()]
        placeholders = ["%s"] * len(columns)
        values = tuple(data.values())

        sql = (
            f"INSERT INTO {self._sanitize_ident(table_upper)} ({', '.join(columns)}) "
            f"VALUES ({', '.join(placeholders)})"
        )

        result = self.execute(sql, values)
        result["table"] = table_upper
        result["data_inserted"] = data
        return result

    def _get_foreign_key_deps(self, table: str) -> list[tuple[str, str, str, Callable]]:
        """获取指定表的外键依赖列表 [(父表名, 父表参照列, 子表外键列, 默认值函数), ...]"""
        return self._FK_DEPS.get(table.upper(), [])

    def _resolve_parent_value(
        self,
        parent_table: str,
        parent_col: str,
        default_fn: Callable,
    ) -> Any:
        """从父表获取有效的参照值。"""
        qt = self._quote_ident(parent_table)
        qc = self._quote_ident(parent_col)
        lim = self._limit_clause(1)
        sql = f"SELECT {qc} FROM {qt} {lim}"
        result = self.query(sql)
        if result["success"] and result["data"]:
            return result["data"][0][parent_col]
        elif default_fn:
            return default_fn()
        return None
