# -*- coding: utf-8 -*-
"""规则加载（从 MySQL test_rules 表）+ 自动发现规则保存 — 从 test_rules_kb.py 拆出，零业务逻辑变更。"""

import json
from src.core.guarded import guard
import hashlib
import logging

from .default_rules import DEFAULT_RULES

_log = logging.getLogger(__name__)


MAX_AUTO_DISCOVERED_RULES_PER_PROJECT = 5000


class _LoaderMixin:
    """rules_path / _ensure_loaded / save_patterns 三段式规则 IO。"""

    # rules_path 已删除（测试规则已迁 MySQL test_rules 表，无磁盘路径）

    def _ensure_loaded(self):
        if self._rules is not None:
            return
        # 加载默认规则
        self._rules = dict(DEFAULT_RULES)
        # ★ MySQL 化：先加载全局规则，再加载项目级覆盖
        conn = None
        try:
            from src.core.config_loader import config
            from src.core.db_manager import get_connection

            conn = get_connection()
            proj = ""
            with guard("_ensure_loaded: from src.backend.state import _curre", log=_log):
                from src.backend.state import _current_project
                proj = _current_project()
            if not proj:
                proj = getattr(config, "project_name", "") or ""
            if not proj:
                _log.warning("[RulesKB] 无法获取项目名，仅加载全局规则")
                proj = ""  # 空字符串 → 只匹配 global 规则
            rows = conn.execute(
                "SELECT scope_type, rules_json FROM test_rules "
                "WHERE (scope_type='global' AND scope_id='') OR (scope_type='project' AND scope_id=%s) "
                "ORDER BY CASE WHEN scope_type='global' THEN 0 ELSE 1 END",
                (proj,),
            ).fetchall()
            project_rules = {}
            for row in rows or []:
                rules = (
                    json.loads(row["rules_json"])
                    if isinstance(row.get("rules_json"), str)
                    else row.get("rules_json")
                )
                if not isinstance(rules, dict):
                    continue
                # 合并：全局数据库规则和项目规则都追加到内置默认规则后面，项目最后覆盖上下文
                extra_patterns = rules.get("patterns", [])
                if extra_patterns:
                    self._rules["patterns"] = (
                        list(self._rules.get("patterns", [])) + extra_patterns
                    )
                extra_constraints = rules.get("global_constraints", [])
                if extra_constraints:
                    self._rules["global_constraints"] = (
                        list(self._rules.get("global_constraints", []))
                        + extra_constraints
                    )
                if row.get("scope_type") == "project":
                    project_rules = rules
            self._project_rules = project_rules or None
        except Exception as _pass_e:
            _log.warning("非关键异常: %s", _pass_e, exc_info=True)
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _pass_e:
                    _log.warning("非关键异常: %s", _pass_e, exc_info=True)

    def save_patterns(self, new_patterns: list[dict]) -> bool:
        """★ 将自动发现的新规则写入项目覆盖表。

        更新策略：
        - 自动规则上限：每个项目最多 5000 条；
        - 去重键：module + suggested_rule；
        - 命中已有规则时更新并移动到末尾，不重复追加；
        - 超过上限时丢弃最旧的自动发现规则，保留人工规则。
        """
        conn = None
        try:
            from src.core.config_loader import config
            from src.core.db_manager import get_connection

            conn = get_connection()
            proj = ""
            with guard("save_patterns: from src.backend.state import _curre", log=_log):
                from src.backend.state import _current_project
                proj = _current_project()
            if not proj:
                proj = getattr(config, "project_name", "") or ""
            if not proj:
                _log.warning("[RulesKB] 无法获取项目名，仅加载全局规则")
                proj = ""  # 空字符串 → 只匹配 global 规则
            # 读取项目级覆盖规则；自动发现规则是项目上下文产物，不污染全局默认规则。
            row = conn.execute(
                "SELECT rules_json FROM test_rules WHERE scope_type='project' AND scope_id=%s",
                (proj,),
            ).fetchone()
            existing = row.get("rules_json") if row and row.get("rules_json") else {}
            if isinstance(existing, str):
                existing = json.loads(existing)
            if not isinstance(existing, dict):
                existing = {}
            patterns = existing.get("patterns", [])
            for rule in new_patterns:
                module = str(rule.get("module", "") or "").strip()
                suggested_rule = str(rule.get("suggested_rule", "") or "").strip()
                if not module or not suggested_rule:
                    continue
                dedupe_key = "%s::%s" % (module, suggested_rule)
                digest = hashlib.md5(dedupe_key.encode("utf-8")).hexdigest()[:8]
                pattern_id = "H%s" % digest.upper()
                patterns = [
                    p
                    for p in patterns
                    if not (
                        isinstance(p, dict)
                        and p.get("source") == "hallucination_logger"
                        and (
                            p.get("dedupe_key") == dedupe_key
                            or p.get("id") == pattern_id
                        )
                    )
                ]
                patterns.append(
                    {
                        "id": pattern_id,
                        "name": "自动发现: %s 模块幻觉" % module,
                        "match_keywords": [module],
                        "must_cover": [suggested_rule],
                        "common_bugs": ["AI 生成的验证意图中虚构了不存在的元素/API"],
                        "min_intents": 2,
                        "source": "hallucination_logger",
                        "dedupe_key": dedupe_key,
                    }
                )
            manual_patterns = [
                p
                for p in patterns
                if not (
                    isinstance(p, dict) and p.get("source") == "hallucination_logger"
                )
            ]
            auto_patterns = [
                p
                for p in patterns
                if isinstance(p, dict) and p.get("source") == "hallucination_logger"
            ]
            if len(auto_patterns) > MAX_AUTO_DISCOVERED_RULES_PER_PROJECT:
                auto_patterns = auto_patterns[-MAX_AUTO_DISCOVERED_RULES_PER_PROJECT:]
            patterns = manual_patterns + auto_patterns
            existing["patterns"] = patterns
            conn.execute(
                "INSERT INTO test_rules(scope_type, scope_id, rules_json) VALUES('project', %s, %s) "
                "ON DUPLICATE KEY UPDATE rules_json=%s",
                (
                    proj,
                    json.dumps(existing, ensure_ascii=False),
                    json.dumps(existing, ensure_ascii=False),
                ),
            )
            conn.commit()
            # 重新加载内存缓存
            self._rules = None
            self._project_rules = None
            return True
        except Exception as e:
            logging.getLogger("test_rules_kb").info(
                "  [TestRulesKB] MySQL 保存失败: %s", str(e)[:60]
            )
            return False
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _pass_e:
                    _log.warning("非关键异常: %s", _pass_e, exc_info=True)
