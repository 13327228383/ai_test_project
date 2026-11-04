# -*- coding: utf-8 -*-
"""SystemDataResolver — 样本数据提取与目录文本构建。"""

import logging

_log = logging.getLogger(__name__)


class SamplingMixin:
    """测试账号 / 业务实体样本提取，以及 Prompt 目录文本格式化。"""

    def _extract_test_accounts(
        self, db_agent, db_type: str, cls: dict
    ) -> list[dict[str, str]]:
        user_t = self._safe_name(cls["user_table"])
        role_t = self._safe_name(cls["role_table"])
        name_col = self._safe_name(cls["user_name_col"])
        role_name_col = self._safe_name(cls.get("role_name_col") or "NAME")
        fk_col = self._safe_name(cls.get("user_role_fk_col") or "")
        role_id_col = self._safe_name(cls.get("role_id_col", "ID"))
        q = (
            '"'
            if db_type in ("yasdb", "postgresql", "oracle")
            else ("`" if db_type == "mysql" else "")
        )

        if fk_col and role_t:
            sql = (
                "SELECT u.%s AS UN, r.%s AS RN, u.%s AS RID "
                "FROM %s u LEFT JOIN %s r ON u.%s = r.%s"
            ) % (
                q + name_col + q,
                q + role_name_col + q,
                q + fk_col + q,
                q + user_t + q,
                q + role_t + q,
                q + fk_col + q,
                q + role_id_col + q,
            )
        else:
            sql = "SELECT %s AS UN FROM %s" % (q + name_col + q, q + user_t + q)

        try:
            result = db_agent.query(sql)
            if not (result.get("success") and result.get("data")):
                return []
            return [
                {
                    "username": str(r.get("UN", "")),
                    "role_name": str(r.get("RN", "未知")),
                    "role_id": str(r.get("RID", "")),
                }
                for r in result["data"]
            ]
        except Exception as e:
            _log.info("[SystemDataResolver] 账号提取失败: %s", e)
            return []

    def _extract_business_samples(
        self, db_agent, db_type: str, cls: dict
    ) -> dict[str, list[dict]]:
        entity_tables = cls.get("entity_tables", {})
        if not entity_tables:
            return {}
        if db_type in ("yasdb", "oracle"):
            q = '"'
        elif db_type == "mysql":
            q = "`"
        else:
            q = '"'

        samples = {}
        for table, info in list(entity_tables.items())[:50]:
            safe_table = self._safe_name(table)
            code_col = self._safe_name(info.get("code_col", ""))
            name_col = self._safe_name(info.get("name_col", ""))
            columns = info.get("columns", [])  # ★ 完整列名，用于识别关联表
            # ★ 确定排序列：code_col > name_col > 第一列
            order_col = code_col or name_col or ""
            if code_col:
                sel = [q + code_col + q]
                if name_col:
                    sel.append(q + name_col + q)
            else:
                sel = ["*"]

            try:
                sel_str = ", ".join(sel)
                ordered_results = []

                # ★ 关联表识别：无 code 列 + ≤4 列 → 很可能是多对多/一对多关联表
                # 用 GROUP BY + COUNT 展示基数分布，比原始行更能体现数据关系
                _is_junction = not code_col and len(columns) <= 4 and len(columns) >= 2

                if _is_junction:
                    # 取前两列做双向 GROUP BY：col1→col2 分布 + col2→col1 分布
                    c1, c2 = self._safe_name(columns[0]), self._safe_name(columns[1])
                    junction_rows = []
                    if db_type in ("yasdb", "oracle"):
                        # 正向：col1 下有多少个不同的 col2
                        sql_j1 = (
                            "SELECT %s AS grp_key, COUNT(*) AS cnt, "
                            "LISTAGG(%s, ',') WITHIN GROUP (ORDER BY %s) AS related "
                            "FROM %s%s%s GROUP BY %s ORDER BY cnt DESC FETCH FIRST 5 ROWS ONLY"
                        ) % (
                            q + c1 + q,
                            q + c2 + q,
                            q + c2 + q,
                            q,
                            safe_table,
                            q,
                            q + c1 + q,
                        )
                        # 反向：col2 下有多少个不同的 col1
                        sql_j2 = (
                            "SELECT %s AS grp_key, COUNT(*) AS cnt, "
                            "LISTAGG(%s, ',') WITHIN GROUP (ORDER BY %s) AS related "
                            "FROM %s%s%s GROUP BY %s ORDER BY cnt DESC FETCH FIRST 3 ROWS ONLY"
                        ) % (
                            q + c2 + q,
                            q + c1 + q,
                            q + c1 + q,
                            q,
                            safe_table,
                            q,
                            q + c2 + q,
                        )
                    else:
                        sql_j1 = (
                            "SELECT %s AS grp_key, COUNT(*) AS cnt, "
                            "GROUP_CONCAT(%s ORDER BY %s SEPARATOR ',') AS related "
                            "FROM %s%s%s GROUP BY %s ORDER BY cnt DESC LIMIT 5"
                        ) % (
                            q + c1 + q,
                            q + c2 + q,
                            q + c2 + q,
                            q,
                            safe_table,
                            q,
                            q + c1 + q,
                        )
                        sql_j2 = (
                            "SELECT %s AS grp_key, COUNT(*) AS cnt, "
                            "GROUP_CONCAT(%s ORDER BY %s SEPARATOR ',') AS related "
                            "FROM %s%s%s GROUP BY %s ORDER BY cnt DESC LIMIT 3"
                        ) % (
                            q + c2 + q,
                            q + c1 + q,
                            q + c1 + q,
                            q,
                            safe_table,
                            q,
                            q + c2 + q,
                        )

                    # ★ 先拿总行数，帮助 AI 理解基数规模
                    sql_total = "SELECT COUNT(*) AS total FROM %s%s%s" % (
                        q,
                        safe_table,
                        q,
                    )
                    r_total = db_agent.query(sql_total)
                    total_rows = 0
                    if r_total.get("success") and r_total.get("data"):
                        total_rows = (
                            list(r_total["data"][0].values())[0]
                            if r_total["data"]
                            else 0
                        )

                    r_j1 = db_agent.query(sql_j1)
                    if r_j1.get("success") and r_j1.get("data"):
                        junction_rows.extend(
                            [
                                {
                                    "_direction": "%s→%s" % (c1, c2),
                                    "grp_key": str(row.get("GRP_KEY", "")),
                                    "cnt": row.get("CNT", 0),
                                    "related": str(row.get("RELATED", "")),
                                }
                                for row in r_j1["data"]
                            ]
                        )

                    r_j2 = db_agent.query(sql_j2)
                    if r_j2.get("success") and r_j2.get("data"):
                        junction_rows.extend(
                            [
                                {
                                    "_direction": "%s→%s" % (c2, c1),
                                    "grp_key": str(row.get("GRP_KEY", "")),
                                    "cnt": row.get("CNT", 0),
                                    "related": str(row.get("RELATED", "")),
                                }
                                for row in r_j2["data"]
                            ]
                        )

                    if junction_rows:
                        # 标记为关联表，附上总行数
                        junction_rows.insert(
                            0,
                            {
                                "_direction": "_total",
                                "grp_key": table,
                                "cnt": total_rows,
                                "related": "%d 行, %s ↔ %s" % (total_rows, c1, c2),
                            },
                        )
                        samples[table] = junction_rows
                    continue  # 关联表走完，跳过后续普通采样
                # ─── 非关联表：排序采样 ───
                if order_col:
                    # ★ 排序后取样本：正序头部 3 条 + 倒序尾部 2 条，覆盖数据范围
                    if db_type in ("yasdb", "oracle"):
                        sql_asc = (
                            "SELECT %s FROM %s%s%s ORDER BY %s%s%s ASC FETCH FIRST 3 ROWS ONLY"
                            % (sel_str, q, safe_table, q, q, order_col, q)
                        )
                    elif db_type == "mysql":
                        sql_asc = (
                            "SELECT %s FROM %s%s%s ORDER BY %s%s%s ASC LIMIT 3"
                            % (sel_str, q, safe_table, q, q, order_col, q)
                        )
                    else:
                        sql_asc = (
                            "SELECT %s FROM %s%s%s ORDER BY %s%s%s ASC LIMIT 3"
                            % (sel_str, q, safe_table, q, q, order_col, q)
                        )

                    r_asc = db_agent.query(sql_asc)
                    if r_asc.get("success") and r_asc.get("data"):
                        ordered_results.extend(r_asc["data"])

                    # 倒序尾部（含最大值），去重合并
                    if db_type in ("yasdb", "oracle"):
                        sql_desc = (
                            "SELECT %s FROM %s%s%s ORDER BY %s%s%s DESC FETCH FIRST 2 ROWS ONLY"
                            % (sel_str, q, safe_table, q, q, order_col, q)
                        )
                    elif db_type == "mysql":
                        sql_desc = (
                            "SELECT %s FROM %s%s%s ORDER BY %s%s%s DESC LIMIT 2"
                            % (sel_str, q, safe_table, q, q, order_col, q)
                        )
                    else:
                        sql_desc = (
                            "SELECT %s FROM %s%s%s ORDER BY %s%s%s DESC LIMIT 2"
                            % (sel_str, q, safe_table, q, q, order_col, q)
                        )
                    r_desc = db_agent.query(sql_desc)
                    if r_desc.get("success") and r_desc.get("data"):
                        # 去重：避免单条数据在正序和倒序中都出现
                        seen = set()
                        for row in ordered_results:
                            seen.add(tuple(str(v) for v in row.values()))
                        for row in r_desc["data"]:
                            key = tuple(str(v) for v in row.values())
                            if key not in seen:
                                ordered_results.append(row)
                else:
                    # 无排序列，退回到原始逻辑但加 ROWID/主键排序
                    if db_type in ("yasdb", "oracle"):
                        sql = (
                            "SELECT %s FROM %s%s%s ORDER BY 1 ASC FETCH FIRST 5 ROWS ONLY"
                            % (sel_str, q, safe_table, q)
                        )
                    else:
                        sql = "SELECT %s FROM %s%s%s ORDER BY 1 LIMIT 5" % (
                            sel_str,
                            q,
                            safe_table,
                            q,
                        )
                    r = db_agent.query(sql)
                    if r.get("success") and r.get("data"):
                        ordered_results = r["data"]

                if ordered_results:
                    samples[table] = ordered_results
            except Exception as _e:
                _log.debug("未知: %s", _e, exc_info=True)
        return samples

    # ═══════════════════════════════════════════════════════════════
    # 目录文本构建（输出给 AI Prompt 的最终格式）
    # ═══════════════════════════════════════════════════════════════

    def _build_catalog_text(
        self, cls: dict, accounts: list[dict], biz_samples: dict[str, list[dict]]
    ) -> str:
        parts = [
            "## 系统真实数据目录（从数据库自动发现，生成测试用例请使用以下真实数据）",
            "",
        ]
        if accounts:
            parts += [
                "### 测试账号与角色",
                "测试用例步骤中涉及登录/权限验证时，必须从以下真实账号中选择：",
                "",
            ]
            try:
                from src.core.config_loader import config

                default_pwd = config.target_password
            except Exception:
                default_pwd = ""
            for a in accounts:
                parts.append(
                    "- `%s` | 角色: %s (ROLE_ID=%s)"
                    % (a["username"], a["role_name"], a["role_id"])
                )
            if default_pwd:
                parts += ["", "★ 所有测试账号的登录密码均为: `%s`" % default_pwd]
            parts += [
                "",
                "★ 严禁虚构不存在的用户名（如「技术中心员工」「财务部员工」）。\n"
                "★ 如需测试的角色没有对应账号，标注「角色无可用测试账号」并跳过该测试用例。",
                "",
            ]

        if biz_samples:
            # ★ 分离实体表和关联表
            entity_samples = {}
            junction_samples = {}
            for table, rows in biz_samples.items():
                if rows and isinstance(rows[0], dict) and "_direction" in rows[0]:
                    junction_samples[table] = rows
                else:
                    entity_samples[table] = rows

            if entity_samples:
                parts += [
                    "### 业务实体真实数据",
                    "测试用例操作步骤中涉及数据选择/搜索/编辑时，优先使用以下真实数据：",
                    "",
                ]
                for table, rows in entity_samples.items():
                    info = cls.get("entity_tables", {}).get(table, {})
                    code_col = info.get("code_col")
                    name_col = info.get("name_col")
                    parts.append("**%s**: " % table)
                    if code_col:
                        for row in rows:
                            cv, nv = row.get(code_col, ""), (
                                row.get(name_col, "") if name_col else ""
                            )
                            parts.append(
                                "  - `%s`%s" % (cv, (" (%s)" % nv) if nv else "")
                            )
                    else:
                        for row in rows[:2]:
                            flat = ", ".join(
                                "%s=%s" % (k, v) for k, v in list(row.items())[:5]
                            )
                            parts.append("  - {%s}" % flat)
                    parts.append("")
                parts.append("★ 新建数据的测试用例应在 db_checks 中验证落库结果。\n")

            if junction_samples:
                parts += [
                    "### 数据关联关系（多对多/一对多基数分布）",
                    "以下展示关联表中各实体的关联密度，帮助理解数据间的数量关系：",
                    "",
                ]
                for table, rows in junction_samples.items():
                    total_row = rows[0] if rows else {}
                    parts.append("**%s** — %s" % (table, total_row.get("related", "")))
                    for row in rows[1:]:
                        direction = row.get("_direction", "")
                        parts.append(
                            "  - [%s] `%s` → %d 条关联 ({%s})"
                            % (
                                direction,
                                row.get("grp_key", ""),
                                row.get("cnt", 0),
                                row.get("related", ""),
                            )
                        )
                    parts.append("")
                parts.append(
                    '★ 验证多对多关系时，注意上述基数分布，避免假设"一对一"。\n'
                )

        if not accounts and not biz_samples:
            parts.append("（数据库中未发现结构化测试数据，测试用例将使用通用描述。）")
        return "\n".join(parts)

    # ═══════════════════════════════════════════════════════════════
    # ★ P1-2: 数据字典格式化（注入 Step 3 Prompt）
    # ═══════════════════════════════════════════════════════════════

    def format_data_dict_context(self) -> str:
        """从缓存中读取系统数据目录，格式化为可注入 Prompt 的数据字典。"""
        catalog = self.resolve(force_refresh=False)
        if not catalog.get("success"):
            return ""
        parts = ["## 📖 系统数据字典（★ 测试用例中涉及的具体值必须从此字典选取）"]
        # 枚举字段
        parts += ["### 枚举字段", "| 表.字段 | 合法值 |", "|----------|--------|"]
        cls = catalog.get("classification", {})
        for table, info in cls.get("entity_tables", {}).items():
            code_col = info.get("code_col", "")
            if code_col:
                samples = catalog.get("biz_samples", {}).get(table, [])
                if samples:
                    name_col = info.get("name_col", "")
                    values = []
                    for row in samples[:5]:
                        val = row.get(code_col, "")
                        nv = row.get(name_col, "") if name_col else ""
                        values.append("%s(%s)" % (val, nv) if nv else str(val))
                    parts.append(
                        "| %s.%s | %s |" % (table, code_col, ", ".join(values[:5]))
                    )
        # 可配置的通用枚举
        enum_fields = self.DEFAULT_ENUM_FIELDS
        try:
            from src.core.config_loader import config as _sdr_cfg

            custom_enums = getattr(_sdr_cfg, "custom_enum_fields", None)
            if isinstance(custom_enums, dict):
                enum_fields = {**enum_fields, **custom_enums}
        except Exception as _e:
            _log.debug("import + load: %s", _e, exc_info=True)
        for field_name, values in enum_fields.items():
            parts.append("| %s | %s |" % (field_name, values))
        parts.append("")
        # 真实数据样本（★ 通用：按表名+code列展示，不硬编码业务领域）
        parts.append("### 真实数据样本")
        accounts = catalog.get("accounts", [])
        if accounts:
            parts.append(
                "- 测试账号: %s"
                % ", ".join(a.get("username", "") for a in accounts[:5])
            )
        biz_samples = catalog.get("biz_samples", {})
        entity_tables = cls.get("entity_tables", {})
        shown_entities = []
        for table, rows in biz_samples.items():
            # ★ 跳过关联表样本（有 _direction 字段），单独在下方展示
            if rows and isinstance(rows[0], dict) and "_direction" in rows[0]:
                continue
            info = entity_tables.get(table, {})
            code_col = info.get("code_col", "")
            if code_col and rows:
                vals = [
                    str(row.get(code_col, "")) for row in rows[:5] if row.get(code_col)
                ]
                if vals:
                    shown_entities.append(
                        "- %s.%s: %s" % (table, code_col, ", ".join(vals))
                    )
        if shown_entities:
            parts.extend(shown_entities[:10])  # 最多展示 10 张表的样本
        # ★ 关联表基数摘要
        junction_tables = {
            t: r
            for t, r in biz_samples.items()
            if r and isinstance(r[0], dict) and "_direction" in r[0]
        }
        if junction_tables:
            parts.append("### 数据关联基数")
            for table, rows in junction_tables.items():
                total_row = rows[0] if rows else {}
                parts.append("- **%s**: %s" % (table, total_row.get("related", "")))
                for row in rows[1:]:
                    parts.append(
                        "  - [%s] `%s` → %d 条"
                        % (
                            row.get("_direction", ""),
                            row.get("grp_key", ""),
                            row.get("cnt", 0),
                        )
                    )
        if not accounts and not shown_entities and not junction_tables:
            parts.append("- （数据库中未发现结构化测试数据）")
        parts.append("")
        return "\n".join(parts)

    # ═══════════════════════════════════════════════════════════════
    # ★ 两层数据注入：紧凑目录（全局）+ 按需详情（按需求相关性筛选）
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _score_table_relevance(
        table_name: str, requirement_text: str, module_name: str, entity_tables: dict
    ) -> int:
        """纯规则相关性评分，不依赖任何业务领域知识。

        评分维度（按权重递减）：
        1. 表名完整出现在需求文本中 → +10
        2. 表名完整出现在模块名中 → +8
        3. 表名去除常见后缀后匹配需求/模块 → +5
        4. 表的 code_col 值出现在需求文本中（如需求提到 "ORDER_NO"） → +6
        5. 关联表（≤4列无code_col）桥接了两张高分表 → +4（在外层处理）
        """
        score = 0
        tl = table_name.lower().strip()
        req_lower = (requirement_text or "").lower()
        mod_lower = (module_name or "").lower()

        if not req_lower and not mod_lower:
            return 0

        # 1. 完整表名命中
        if tl and tl in req_lower:
            score += 10
        if tl and mod_lower and tl in mod_lower:
            score += 8

        # 2. 下划线分段匹配（order_item → "order" 和 "item" 分别匹配）
        tl_parts = set(tl.split("_"))
        for part in tl_parts:
            if len(part) >= 3:  # 忽略太短的片段如 "t_" "a_"
                if part in req_lower:
                    score += 3
                if mod_lower and part in mod_lower:
                    score += 2

        # 3. 去除常见后缀后匹配（item → order_item, detail → order_detail）
        for suffix in (
            "_item",
            "_detail",
            "_line",
            "_info",
            "_rel",
            "_map",
            "_log",
            "_ext",
        ):
            if tl.endswith(suffix):
                base = tl[: -len(suffix)]
                if base and len(base) >= 3:
                    if base in req_lower:
                        score += 5
                    if mod_lower and base in mod_lower:
                        score += 3
                break  # 只去一个后缀

        # 4. code_col 值在需求中出现
        info = entity_tables.get(table_name, {})
        code_col = (info.get("code_col") or "").lower()
        if code_col and code_col in req_lower:
            score += 6

        return score

    def _build_compact_catalog(self) -> str:
        """构建紧凑全局数据目录，始终完整注入，确保 AI 知道所有表的存在。

        格式：逗号分隔多表，去除冗余 markdown，目标 ~600 字符覆盖 50 张表。
        """
        catalog = self.resolve()
        if not catalog.get("success"):
            return ""
        cls = catalog.get("classification", {})
        raw_entity = cls.get("entity_tables", {})
        biz_samples = catalog.get("biz_samples", {})
        accounts = catalog.get("accounts", [])

        # ★ 兼容旧缓存（list 格式）和新格式（dict）
        if isinstance(raw_entity, list):
            entity_tables = {t: {} for t in raw_entity}
        else:
            entity_tables = raw_entity

        if not entity_tables and not accounts:
            return ""

        parts = ["## 📖 数据目录", ""]

        # ── 表清单（逗号分隔，超紧凑） ──
        table_entries = []
        for table, info in entity_tables.items():
            code_col = info.get("code_col", "") if isinstance(info, dict) else ""
            if code_col:
                table_entries.append("%s(%s)" % (table, code_col))
            else:
                table_entries.append(table)
        if table_entries:
            parts.append("表: " + ", ".join(table_entries))

        # ── 关联表基数 ──
        junction_info = []
        for table, rows in biz_samples.items():
            if rows and isinstance(rows[0], dict) and "_direction" in rows[0]:
                total_row = rows[0] if rows else {}
                junction_info.append(total_row.get("related", table))
        if junction_info:
            parts.append("关联: " + " | ".join(junction_info))

        # ── 测试账号 ──
        if accounts:
            acct_strs = []
            for a in accounts[:10]:
                un = a.get("username", "?")
                rn = a.get("role_name", "")
                acct_strs.append("%s(%s)" % (un, rn) if rn else un)
            parts.append("账号: " + ", ".join(acct_strs))

        parts.append(
            "★ 以上为数据库全部可用数据，测试用例中涉及的表名/字段名/测试数据必须来源于此，不得虚构。"
        )
        return "\n".join(parts)

    def build_table_details(self, table_names, max_chars=800, top_n=5):
        """为指定表构建详细样本数据，用于 Layer 2 按需详情注入。

        table_names: 相关表名列表（按相关性降序，取前 top_n）
        max_chars:   详情段落最大字符数
        """
        if not table_names:
            return ""
        catalog = self.resolve()
        if not catalog.get("success"):
            return ""
        cls = catalog.get("classification", {})
        raw_entity = cls.get("entity_tables", {})
        biz_samples = catalog.get("biz_samples", {})

        # ★ 兼容旧缓存（list 格式）和新格式（dict）
        if isinstance(raw_entity, list):
            entity_tables = {t: {} for t in raw_entity}
        else:
            entity_tables = raw_entity

        parts = []
        shown = set()
        char_count = 0
        header = "### 相关表数据详情"
        # header 不计入限制，保证至少有一张表能展示

        for table in table_names[:top_n]:
            if table in shown:
                continue
            info = entity_tables.get(table, {})
            code_col = info.get("code_col", "")
            name_col = info.get("name_col", "")
            rows = biz_samples.get(table, [])

            if not rows:
                continue

            entry = ""
            if rows and isinstance(rows[0], dict) and "_direction" in rows[0]:
                # 关联表 → 基数信息
                total_row = rows[0]
                entry = "**%s** — %s" % (table, total_row.get("related", ""))
                for row in rows[1:3]:  # 最多展示 2 个方向的 top key
                    entry += "\n  - [%s] `%s` → %d 条" % (
                        row.get("_direction", ""),
                        row.get("grp_key", ""),
                        row.get("cnt", 0),
                    )
            else:
                # 普通实体表 → 样本值
                entry = "**%s** (%s): " % (table, code_col or "?")
                vals = []
                for row in rows[:5]:
                    cv = str(row.get(code_col, "")) if code_col else ""
                    if not cv:
                        continue
                    nv = str(row.get(name_col, "")) if name_col else ""
                    vals.append("`%s`%s" % (cv, ("(%s)" % nv) if nv else ""))
                entry += ", ".join(vals) if vals else "（无样本）"

            # 检查是否超过字符限制
            test_len = char_count + len(entry) + 2
            if char_count > 0 and test_len > max_chars:
                # 至少保证 1 张表的详情能展示
                if shown:
                    break

            parts.append(entry)
            shown.add(table)
            char_count = len("\n".join(parts))

        if not parts:
            return ""

        result = header + "\n" + "\n".join(parts)
        return result

    def format_data_dict_two_layer(
        self, requirement_text: str = "", module_name: str = "", top_n: int = 5
    ) -> str:
        """两层数据注入入口：紧凑目录（全局）+ 按需详情（按需求相关性筛选）。

        供 _contexts.py 调用，替代原有的 format_data_dict_context() 一刀切截断。
        """
        catalog = self.resolve()
        if not catalog.get("success"):
            return ""

        # Layer 1: 紧凑目录（始终完整）
        compact = self._build_compact_catalog()
        if not compact:
            return ""

        # Layer 2: 按需求相关性筛选表详情
        classification = catalog.get("classification", {})
        raw_entity = classification.get("entity_tables", {})
        biz_samples = catalog.get("biz_samples", {})

        # ★ 兼容旧缓存（list 格式）和新格式（dict）
        if isinstance(raw_entity, list):
            # 旧格式：仅表名列表，从 biz_samples 反推 code_col
            entity_tables = {}
            for t in raw_entity:
                rows = biz_samples.get(t, [])
                if rows and not (isinstance(rows[0], dict) and "_direction" in rows[0]):
                    # 从第一行样本推断 code_col（取第一个非空列名）
                    first_row = rows[0]
                    for k in first_row:
                        if first_row.get(k):
                            entity_tables[t] = {
                                "code_col": k,
                                "name_col": "",
                                "columns": list(first_row.keys()),
                            }
                            break
                    else:
                        entity_tables[t] = {
                            "code_col": "",
                            "name_col": "",
                            "columns": list(first_row.keys()) if first_row else [],
                        }
                else:
                    entity_tables[t] = {"code_col": "", "name_col": "", "columns": []}
        else:
            entity_tables = dict(raw_entity)  # 新格式：完整 dict

        # 评分所有表
        scored = [
            (
                self._score_table_relevance(
                    table, requirement_text, module_name, entity_tables
                ),
                table,
            )
            for table in entity_tables
        ]
        scored.sort(key=lambda x: -x[0])

        # 关联表加分：如果关联表桥接了两张高分表，提升其分数
        for i, (score, table) in enumerate(scored):
            rows = biz_samples.get(table, [])
            if rows and isinstance(rows[0], dict) and "_direction" in rows[0]:
                # 关联表：检查其 related 字段中包含的高分表名
                related_text = str(rows[0].get("related", ""))
                boost = 0
                for s2, t2 in scored:
                    if t2 != table and s2 >= 5 and t2.lower() in related_text.lower():
                        boost += 2
                if boost:
                    scored[i] = (score + boost, table)

        # 重新排序
        scored.sort(key=lambda x: -x[0])

        # 取 top_n 张有分数的表（分数>0），加上最多2张无分数但有数据的表
        relevant = [t for s, t in scored if s > 0][:top_n]
        # 如果相关表不足 top_n，补充一些有数据的表（至少让 AI 看到一些样本）
        if len(relevant) < min(3, top_n):
            for s, t in scored:
                if t not in relevant and biz_samples.get(t):
                    relevant.append(t)
                    if len(relevant) >= top_n:
                        break

        details = self.build_table_details(relevant, max_chars=800, top_n=top_n)
        if details:
            return compact + "\n" + details
        return compact
