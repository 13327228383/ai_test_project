# -*- coding: utf-8 -*-
"""
CI/CD 集成路由
==============
提供 API Token 管理、Webhook 回调触发、CI 配置模板生成。

路由：
    POST   /api/ci/token           → 创建 API Token
    GET    /api/ci/tokens          → 列出所有 Token（脱敏）
    DELETE /api/ci/token/<id>      → 撤销 Token
    GET    /api/ci/template/jenkins    → 生成 Jenkins Pipeline 配置
    GET    /api/ci/template/github     → 生成 GitHub Actions workflow
    GET    /api/ci/webhook-config      → 获取 webhook 配置信息

设计原则：
- Token 只显示一次（创建时返回明文），后续只展示脱敏值
- CI 模板动态生成，包含当前项目的 API 端点
"""

import hashlib
from src.core.guarded import guard
import os
import secrets
import json
import logging
from flask import Blueprint, jsonify, request
from src.models.permission import check_admin_or_permission

_log = logging.getLogger(__name__)
ci_bp = Blueprint("ci_integration", __name__)


def _get_project() -> str:
    from src.backend.state import _current_project

    return _current_project() or ""


def _check_admin():
    ok, err = check_admin_or_permission("system.admin")
    if not ok:
        return jsonify({"ok": False, "error": err}), 403
    return None


def _mask_token(token_hash: str) -> str:
    """脱敏显示 token hash。"""
    if len(token_hash) > 16:
        return f"{token_hash[:8]}...{token_hash[-8:]}"
    return "****"


def _safe_json_loads(raw_scopes):
    """安全解析 scopes JSON，失败返回空列表。"""
    try:
        return json.loads(raw_scopes or "[]")
    except (json.JSONDecodeError, TypeError):
        return []


# ═══════════════════════════════════════════════════════════════════
# API Token 管理
# ═══════════════════════════════════════════════════════════════════


@ci_bp.route("/api/ci/token", methods=["POST"])
def api_create_token():
    """创建 API Token（仅管理员）。返回明文 token，仅此一次。"""
    _err = _check_admin()
    if _err:
        return _err

    data = request.get_json(silent=True) or {}
    label = (data.get("label") or "CI Token").strip()[:255]
    project = (data.get("project") or _get_project()).strip()[:128]
    scopes = data.get("scopes", ["run"])

    if not isinstance(scopes, list):
        scopes = ["run"]

    # 生成随机 token
    raw_token = "e2e_" + secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    # 获取当前用户ID
    from src.core.config_loader import get_current_user

    created_by = get_current_user() or "admin"

    try:
        from src.core.db_manager import get_connection, release_connection

        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO api_tokens (token_hash, project, label, scopes, created_by) "
                "VALUES (%s, %s, %s, %s, %s)",
                (
                    token_hash,
                    project or "*",
                    label,
                    json.dumps(scopes, ensure_ascii=False),
                    created_by,
                ),
            )
            conn.commit()
        finally:
            release_connection(conn)
    except Exception as e:
        _log.error("[CI] Token 创建失败: %s", str(e)[:120])
        return jsonify({"ok": False, "error": f"创建失败: {str(e)[:80]}"}), 500

    _log.info(
        "[CI] ✅ API Token 已创建: label=%s project=%s scopes=%s",
        label,
        project,
        scopes,
    )
    return jsonify(
        {
            "ok": True,
            "token": raw_token,
            "label": label,
            "project": project,
            "scopes": scopes,
            "message": "⚠️ Token 仅显示一次，请立即复制保存！",
        }
    )


@ci_bp.route("/api/ci/tokens", methods=["GET"])
def api_list_tokens():
    """列出所有 API Token（脱敏）。"""
    _err = _check_admin()
    if _err:
        return _err

    try:
        from src.core.db_manager import get_connection, release_connection

        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT id, token_hash, project, label, scopes, created_by, "
                "last_used_at, use_count, is_active, created_at "
                "FROM api_tokens ORDER BY id DESC"
            ).fetchall()
        finally:
            release_connection(conn)

        tokens = []
        for r in rows or []:
            tokens.append(
                {
                    "id": r["id"],
                    "token_hash": _mask_token(r["token_hash"]),
                    "project": r["project"],
                    "label": r["label"],
                    "scopes": (_safe_json_loads(r["scopes"])),
                    "created_by": r["created_by"],
                    "last_used_at": (
                        str(r["last_used_at"]) if r["last_used_at"] else None
                    ),
                    "use_count": r["use_count"],
                    "is_active": bool(r["is_active"]),
                    "created_at": str(r["created_at"]) if r["created_at"] else None,
                }
            )
        return jsonify({"ok": True, "tokens": tokens})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:120]}), 500


@ci_bp.route("/api/ci/token/<int:token_id>", methods=["DELETE"])
def api_revoke_token(token_id):
    """撤销（禁用）API Token。"""
    _err = _check_admin()
    if _err:
        return _err

    try:
        from src.core.db_manager import get_connection, release_connection

        conn = get_connection()
        try:
            conn.execute("UPDATE api_tokens SET is_active=0 WHERE id=%s", (token_id,))
            conn.commit()
        finally:
            release_connection(conn)
        return jsonify({"ok": True, "message": f"Token #{token_id} 已撤销"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:120]}), 500


# ═══════════════════════════════════════════════════════════════════
# CI 配置模板
# ═══════════════════════════════════════════════════════════════════


def _get_base_url() -> str:
    """推断服务的基础 URL。"""
    # 尝试从请求获取
    with guard("_get_base_url: host = request.host_url.rstrip('/')", log=_log):
        host = request.host_url.rstrip("/")
        if host:
            return host
    # 走配置兜底（与 config.yaml 中 web.bind_port 对齐）
    try:
        from src.core.config_loader import config

        return str(config._get("web.ci_default_base_url", "http://localhost:5001"))
    except Exception as _e:
        _log.debug("[CI] 读取 web.ci_default_base_url 配置失败，使用默认值: %s", _e)
        return os.environ.get("CI_BASE_URL", "http://localhost:5001")


@ci_bp.route("/api/ci/template/jenkins", methods=["GET"])
def api_ci_template_jenkins():
    """生成 Jenkins Pipeline 配置（声明式）。"""
    base_url = _get_base_url()
    project = request.args.get("project", _get_project())
    token_hint = request.args.get("token", "<YOUR_API_TOKEN>")

    pipeline_script = """// Jenkins 声明式 Pipeline — E2E 智能测试引擎
// 使用方法：
// 1. 在 Jenkins 中创建 Pipeline 任务
// 2. 将以下内容粘贴到 Pipeline script 区域
// 3. 替换 <YOUR_API_TOKEN> 为实际 Token（在引擎管理后台 → CI 集成 创建）

pipeline {{
    agent any

    environment {{
        E2E_BASE_URL = '{base_url}'
        E2E_API_TOKEN = '{token_hint}'
        E2E_PROJECT = '{project}'
    }}

    stages {{
        stage('E2E - 数据拉取') {{
            steps {{
                script {{
                    def response = httpRequest(
                        url: "${{E2E_BASE_URL}}/api/step/1",
                        httpMode: 'POST',
                        customHeaders: [[name: 'X-API-Key', value: E2E_API_TOKEN]],
                        timeout: 600,
                        validResponseCodes: '200'
                    )
                    echo "Step 1 完成: ${{response.content}}"
                }}
            }}
        }}

        stage('E2E - 需求拆解') {{
            steps {{
                script {{
                    def response = httpRequest(
                        url: "${{E2E_BASE_URL}}/api/step/2",
                        httpMode: 'POST',
                        customHeaders: [[name: 'X-API-Key', value: E2E_API_TOKEN]],
                        timeout: 600,
                        validResponseCodes: '200'
                    )
                    echo "Step 2 完成: ${{response.content}}"
                }}
            }}
        }}

        stage('E2E - 意图生成') {{
            steps {{
                script {{
                    def response = httpRequest(
                        url: "${{E2E_BASE_URL}}/api/step/3",
                        httpMode: 'POST',
                        customHeaders: [[name: 'X-API-Key', value: E2E_API_TOKEN]],
                        timeout: 600,
                        validResponseCodes: '200'
                    )
                    echo "Step 3 完成: ${{response.content}}"
                }}
            }}
        }}

        stage('E2E - 执行验证') {{
            steps {{
                script {{
                    def response = httpRequest(
                        url: "${{E2E_BASE_URL}}/api/step/4",
                        httpMode: 'POST',
                        customHeaders: [[name: 'X-API-Key', value: E2E_API_TOKEN]],
                        timeout: 1800,
                        validResponseCodes: '200'
                    )
                    echo "Step 4 完成: ${{response.content}}"
                }}
            }}
        }}

        stage('E2E - 报告生成') {{
            steps {{
                script {{
                    def response = httpRequest(
                        url: "${{E2E_BASE_URL}}/api/step/5",
                        httpMode: 'POST',
                        customHeaders: [[name: 'X-API-Key', value: E2E_API_TOKEN]],
                        timeout: 300,
                        validResponseCodes: '200'
                    )
                    echo "Step 5 完成: ${{response.content}}"
                }}
            }}
        }}
    }}

    post {{
        always {{
            // 查询最终状态
            script {{
                def statusResponse = httpRequest(
                    url: "${{E2E_BASE_URL}}/api/status",
                    httpMode: 'GET',
                    customHeaders: [[name: 'X-API-Key', value: E2E_API_TOKEN]],
                    validResponseCodes: '200'
                )
                echo "E2E 状态: ${{statusResponse.content}}"
            }}
        }}
    }}
}}""".format(
        base_url=base_url, token_hint=token_hint, project=project
    )
    return jsonify(
        {
            "ok": True,
            "type": "jenkins",
            "content": pipeline_script,
            "base_url": base_url,
            "project": project,
        }
    )


@ci_bp.route("/api/ci/template/github", methods=["GET"])
def api_ci_template_github():
    """生成 GitHub Actions workflow 配置。"""
    base_url = _get_base_url()
    project = request.args.get("project", _get_project())
    token_hint = request.args.get("token", "${{ secrets.E2E_API_TOKEN }}")

    workflow_yaml = """# GitHub Actions Workflow — E2E 智能测试引擎
# 文件位置: .github/workflows/e2e-test.yml
#
# 使用方法：
# 1. 在 GitHub 仓库 Settings → Secrets → Actions 添加 E2E_API_TOKEN
# 2. 将本文件放入 .github/workflows/ 目录
# 3. push 到 main 分支后自动触发

name: E2E 智能测试

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]
  workflow_dispatch:
    inputs:
      from_step:
        description: '起始步骤 (1-5)'
        required: false
        default: '1'
      project:
        description: '项目名称'
        required: false
        default: '{project}'

jobs:
  e2e-test:
    runs-on: ubuntu-latest
    timeout-minutes: 120

    steps:
      - name: 检查 Python
        run: python3 --version

      - name: Step 1 — 数据拉取
        if: github.event.inputs.from_step <= '1' || github.event.inputs.from_step == ''
        run: |
          curl -s -X POST \\
            -H "X-API-Key: ${{{{ secrets.E2E_API_TOKEN }}}}" \\
            "${{{{ env.E2E_BASE_URL }}}}/api/step/1" | tee /tmp/e2e_step1.json
        env:
          E2E_BASE_URL: {base_url}

      - name: Step 2 — 需求拆解
        if: github.event.inputs.from_step <= '2' || github.event.inputs.from_step == ''
        run: |
          curl -s -X POST \\
            -H "X-API-Key: ${{{{ secrets.E2E_API_TOKEN }}}}" \\
            "${{{{ env.E2E_BASE_URL }}}}/api/step/2" | tee /tmp/e2e_step2.json
        env:
          E2E_BASE_URL: {base_url}

      - name: Step 3 — 意图生成
        if: github.event.inputs.from_step <= '3' || github.event.inputs.from_step == ''
        run: |
          curl -s -X POST \\
            -H "X-API-Key: ${{{{ secrets.E2E_API_TOKEN }}}}" \\
            "${{{{ env.E2E_BASE_URL }}}}/api/step/3" | tee /tmp/e2e_step3.json
        env:
          E2E_BASE_URL: {base_url}

      - name: Step 4 — 执行验证
        if: github.event.inputs.from_step <= '4' || github.event.inputs.from_step == ''
        run: |
          curl -s -X POST \\
            -H "X-API-Key: ${{{{ secrets.E2E_API_TOKEN }}}}" \\
            "${{{{ env.E2E_BASE_URL }}}}/api/step/4" | tee /tmp/e2e_step4.json
        env:
          E2E_BASE_URL: {base_url}

      - name: Step 5 — 报告生成
        if: github.event.inputs.from_step <= '5' || github.event.inputs.from_step == ''
        run: |
          curl -s -X POST \\
            -H "X-API-Key: ${{{{ secrets.E2E_API_TOKEN }}}}" \\
            "${{{{ env.E2E_BASE_URL }}}}/api/step/5" | tee /tmp/e2e_step5.json
        env:
          E2E_BASE_URL: {base_url}

      - name: 下载测试报告
        if: always()
        run: |
          curl -s -H "X-API-Key: ${{{{ secrets.E2E_API_TOKEN }}}}" \\
            "${{{{ env.E2E_BASE_URL }}}}/api/export/report" \\
            -o e2e_test_report.md
          cat e2e_test_report.md >> $GITHUB_STEP_SUMMARY
        env:
          E2E_BASE_URL: {base_url}
""".format(
        base_url=base_url, project=project
    )
    return jsonify(
        {
            "ok": True,
            "type": "github_actions",
            "content": workflow_yaml,
            "base_url": base_url,
            "project": project,
        }
    )


@ci_bp.route("/api/ci/webhook-config", methods=["GET"])
def api_ci_webhook_config():
    """获取 webhook 集成配置信息。"""
    base_url = _get_base_url()
    project = request.args.get("project", _get_project())

    return jsonify(
        {
            "ok": True,
            "project": project,
            "base_url": base_url,
            "endpoints": {
                "trigger_single_step": {
                    "method": "POST",
                    "url": f"{base_url}/api/step/1",
                    "headers": {"X-API-Key": "<YOUR_TOKEN>"},
                    "description": "触发单个步骤 (1-5)",
                },
                "trigger_all": {
                    "method": "POST",
                    "url": f"{base_url}/api/run-all?project={project}",
                    "headers": {"X-API-Key": "<YOUR_TOKEN>"},
                    "description": "触发完整流水线",
                },
                "query_status": {
                    "method": "GET",
                    "url": f"{base_url}/api/status",
                    "headers": {"X-API-Key": "<YOUR_TOKEN>"},
                    "description": "查询运行状态",
                },
                "download_report": {
                    "method": "GET",
                    "url": f"{base_url}/api/export/report?project={project}",
                    "headers": {"X-API-Key": "<YOUR_TOKEN>"},
                    "description": "下载测试报告",
                },
            },
        }
    )
