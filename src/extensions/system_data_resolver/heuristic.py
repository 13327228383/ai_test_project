# -*- coding: utf-8 -*-
from __future__ import annotations

"""SystemDataResolver — 启发式表分类。"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


class HeuristicMixin:
    """基于列名模式的启发式用户表 / 角色表 / 业务实体表识别。"""

    def _classify_tables(self, all_tables: dict[str, list[str]]) -> dict[str, Any]:
        cls = {
            "user_table": None,
            "user_name_col": None,
            "user_pwd_col": None,
            "role_table": None,
            "role_name_col": None,
            "role_id_col": "ID",
            "user_role_fk_col": None,
            "entity_tables": {},
        }
        user_candidates, role_candidates = [], []

        for table, cols in all_tables.items():
            cols_u = [c.upper() for c in cols]
            cols_set = set(cols_u)

            # ─ 用户表候选 ─
            name_col = self._match_col(cols_u, self.USER_NAME_PATS)
            pwd_col = self._match_col(cols_u, self.USER_PWD_PATS)
            if name_col and pwd_col:
                score = 1
                tl = table.lower()
                if any(p in tl for p in ["user", "account", "member", "admin"]):
                    score += 3
                if tl in ("sys_dict", "sys_config", "sys_param"):
                    score -= 2
                user_candidates.append((table, score, name_col, pwd_col))

            # ─ 角色表候选 ─
            rn_col = self._match_col(cols_u, self.ROLE_NAME_PATS)
            if rn_col and "ID" in cols_set:
                score = 1
                tl = table.lower()
                if any(p in tl for p in self.ROLE_TABLE_PATS):
                    score += 3
                role_candidates.append((table, score, rn_col))

            # ─ 业务实体表 ─
            # ★ 修复：不再要求必须有 *_CODE 列才计入。
            # 所有非系统表都算业务实体表，code_col/name_col 仅作附加元数据（有则更优）。
            # 同时保存完整列名，供采样层识别关联表做 GROUP BY 基数采样。
            if not self._is_system_table(table):
                code_col = self._match_col(cols_u, self.ENTITY_CODE_PATS)
                name_col_e = self._match_col(cols_u, self.ENTITY_NAME_PATS)
                cls["entity_tables"][table] = {
                    "code_col": code_col,
                    "name_col": name_col_e,
                    "columns": cols,  # ★ 完整列名，采样层用于判断关联表/基数采样
                }

        # 选最优用户表
        if user_candidates:
            user_candidates.sort(key=lambda x: -x[1])
            best = user_candidates[0]
            cls["user_table"] = best[0]
            cls["user_name_col"] = best[2]
            cls["user_pwd_col"] = best[3]

            # 在用户表中找 FK → 角色表
            user_cols = all_tables.get(best[0], [])
            fk_col = self._match_col(user_cols, self.ROLE_FK_PATS)
            if fk_col:
                cls["user_role_fk_col"] = fk_col
                # 匹配最佳角色表
                if role_candidates:
                    role_candidates.sort(key=lambda x: -x[1])
                    cls["role_table"] = role_candidates[0][0]
                    cls["role_name_col"] = role_candidates[0][2]

        # 兜底：独立选最佳角色表
        if cls["role_table"] is None and role_candidates:
            role_candidates.sort(key=lambda x: -x[1])
            cls["role_table"] = role_candidates[0][0]
            cls["role_name_col"] = role_candidates[0][2]

        # 如果还没找到 FK，反查用户表
        if cls["user_table"] and cls["role_table"] and not cls["user_role_fk_col"]:
            user_cols = all_tables.get(cls["user_table"], [])
            fk = self._match_col(user_cols, self.ROLE_FK_PATS)
            if fk:
                cls["user_role_fk_col"] = fk

        # 最后手段：通过真实外键约束发现 FK
        if cls["user_table"] and cls["role_table"] and not cls["user_role_fk_col"]:
            cls["user_role_fk_col"] = self._discover_fk_from_constraints(
                cls["user_table"], cls["role_table"]
            )

        return cls

    def _match_col(self, cols: list[str], patterns: list[str]) -> str | None:
        """在列名列表中匹配模式，返回第一个匹配的列名。"""
        cols_u = [c.upper() for c in cols]
        for pat in patterns:
            pu = pat.upper()
            # 精确匹配
            if pu in cols_u:
                return cols[cols_u.index(pu)]
            # 后缀通配 *_CODE → 任意_CODE
            if pu.startswith("*_"):
                suffix = pu[1:]
                for i, c in enumerate(cols_u):
                    if c.endswith(suffix):
                        return cols[i]
            # 包含匹配
            for i, c in enumerate(cols_u):
                if pu in c:
                    return cols[i]
        return None

    def _is_system_table(self, table: str) -> bool:
        tl = table.lower()
        return any(tl.startswith(p) for p in self.SYS_TABLE_PREFIXES)

    def _discover_fk_from_constraints(
        self, user_table: str, role_table: str
    ) -> str | None:
        """从数据库真实外键约束中查找 user_table → role_table 的 FK 列。"""
        try:
            from src.extensions.db.db_agent import DatabaseAgent

            agent = DatabaseAgent()
            agent.connect()
            try:
                from src.core.config_loader import config

                db_type = config.db_type.lower()
                if db_type in ("yasdb", "oracle"):
                    # ★ P1: 使用参数化查询
                    r = agent.query(
                        "SELECT a.COLUMN_NAME FROM USER_CONS_COLUMNS a "
                        "JOIN USER_CONSTRAINTS c ON a.CONSTRAINT_NAME = c.CONSTRAINT_NAME "
                        "WHERE c.CONSTRAINT_TYPE = 'R' AND a.TABLE_NAME = %s",
                        (user_table,),
                    )
                    if r.get("success") and r.get("data"):
                        for row in r["data"]:
                            col = (
                                list(row.values())[0]
                                if isinstance(row, dict)
                                else row[0]
                            )
                            col_upper = str(col).upper()
                            if "ROLE" in col_upper or role_table.upper() in col_upper:
                                return str(col)
            finally:
                agent.close()
        except Exception as _e:
            _log.debug("close(): %s", _e, exc_info=True)
        return None
