# -*- coding: utf-8 -*-
"""
用例生成规则 — 查询路由
"""

from flask import jsonify
import logging

_log = logging.getLogger(__name__)

from src.backend.routes.case_rules import case_rules_bp
from src.pipeline.step3.case_rules import get_rule_meta_list
from src.pipeline.steps.step1._rule_meta import (
    get_rule_meta_list as get_step1_rule_meta_list,
)
from src.pipeline.steps.step2.breakdown_rules import (
    get_rule_meta_list as get_step2_rule_meta_list,
)
from src.pipeline.steps.step4.verification._rule_meta import (
    get_rule_meta_list as get_step4_rule_meta_list,
)
from src.pipeline.steps.step5._rule_meta import (
    get_rule_meta_list as get_step5_rule_meta_list,
)


@case_rules_bp.route("/api/case-generation-rules")
def api_get_case_rules():
    """获取 Step3 用例生成规则清单（只读）。"""
    try:
        rules = get_rule_meta_list()
        return jsonify(
            {
                "ok": True,
                "step": "step3",
                "total": len(rules),
                "rules": rules,
            }
        )
    except Exception as e:
        _log.error("[api_get_case_rules] 异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "服务器内部错误"}), 500


@case_rules_bp.route("/api/step1-rag-rules")
def api_get_step1_rules():
    """获取 Step1 文档采集 + RAG chunks 构建/检索规则清单（只读）。"""
    try:
        rules = get_step1_rule_meta_list()
        return jsonify(
            {
                "ok": True,
                "step": "step1",
                "total": len(rules),
                "rules": rules,
            }
        )
    except Exception as e:
        _log.error("[api_get_step1_rules] 异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "服务器内部错误"}), 500


@case_rules_bp.route("/api/step2-breakdown-rules")
def api_get_step2_rules():
    """获取 Step2 需求拆解规则清单（只读）。"""
    try:
        rules = get_step2_rule_meta_list()
        return jsonify(
            {
                "ok": True,
                "step": "step2",
                "total": len(rules),
                "rules": rules,
            }
        )
    except Exception as e:
        _log.error("[api_get_step2_rules] 异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "服务器内部错误"}), 500


@case_rules_bp.route("/api/step4-verification-rules")
def api_get_step4_rules():
    """获取 Step4 检查执行规则清单（只读）。"""
    try:
        rules = get_step4_rule_meta_list()
        return jsonify(
            {
                "ok": True,
                "step": "step4",
                "total": len(rules),
                "rules": rules,
            }
        )
    except Exception as e:
        _log.error("[api_get_step4_rules] 异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "服务器内部错误"}), 500


@case_rules_bp.route("/api/step5-report-rules")
def api_get_step5_rules():
    """获取 Step5 报告生成规则清单（只读）。"""
    try:
        rules = get_step5_rule_meta_list()
        return jsonify(
            {
                "ok": True,
                "step": "step5",
                "total": len(rules),
                "rules": rules,
            }
        )
    except Exception as e:
        _log.error("[api_get_step5_rules] 异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "服务器内部错误"}), 500
