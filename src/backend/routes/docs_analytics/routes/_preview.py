# -*- coding: utf-8 -*-
"""
routes/_preview.py — 文件预览路由
=================================
- api_docs_preview — 文件预览 API（含 XMind 解析，最大端点）

★ 全 MySQL：path 支持 mysql:// URI + 虚拟相对路径双协议，经
  fetchers.mysql_doc.resolve_doc 统一解析，无磁盘依赖。
"""

import json
import io
import re
import zipfile
from flask import jsonify, request

from src.backend.state import _current_project
from src.models.permission import check_admin_or_permission
from src.backend.routes.docs_analytics.fetchers.mysql_doc import resolve_doc


def register(bp):
    """注册文件预览路由到 Blueprint。"""

    @bp.route("/api/docs/preview")
    def api_docs_preview():
        """按文件扩展名返回预览数据。★ 全 MySQL：resolve_doc 双协议解析。"""
        ok, err = check_admin_or_permission("docs.view", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        if not _current_project():
            return jsonify({"ok": False, "error": "未选择项目"}), 400
        proj = _current_project()
        path_arg = request.args.get("path", "")
        path_arg = re.sub(r"(?<!:)[\\/]+", "/", path_arg).strip("/").replace("..", "")
        if not path_arg:
            return jsonify({"ok": False, "error": "缺少 path 参数"}), 400

        doc = resolve_doc(path_arg, proj)
        if not doc:
            return jsonify({"ok": False, "error": "文档不存在: %s" % path_arg}), 404

        kind = doc["kind"]
        ext = doc["ext"]

        try:
            if kind == "text":
                content = doc["content_text"] or ""
                if ext == ".json":
                    try:
                        data = json.loads(content)
                    except json.JSONDecodeError:
                        data = content
                    return jsonify({"type": "json", "data": data, "size": len(content)})
                if ext == ".html":
                    return jsonify(
                        {"type": "html", "content": content, "size": len(content)}
                    )
                # .md / .log 均按 markdown 渲染
                return jsonify(
                    {"type": "markdown", "content": content, "size": len(content)}
                )

            if kind == "log":
                content = doc["content_text"] or ""
                return jsonify(
                    {"type": "markdown", "content": content, "size": len(content)}
                )

            if kind in ("binary", "xlsx"):
                raw = doc["content_bytes"] or b""
                if ext == ".xlsx":
                    return _preview_xlsx(raw)
                if ext == ".xmind":
                    return _preview_xmind(raw, path_arg)
                # 其他二进制：返回大小信息
                return jsonify({"type": "binary", "ext": ext, "size": len(raw)})

            if kind == "png":
                raw = doc["content_bytes"] or b""
                # 截图无法在 JSON 预览里内联渲染，返回 base64 供前端 <img>
                import base64

                return jsonify(
                    {
                        "type": "image",
                        "ext": ".png",
                        "size": len(raw),
                        "data_url": "data:image/png;base64,%s"
                        % base64.b64encode(raw).decode("ascii"),
                    }
                )

            return jsonify({"ok": False, "error": "不支持预览此类型 (%s)" % kind}), 400
        except Exception as e:
            return jsonify({"ok": False, "error": "预览失败: %s" % str(e)}), 500


def _preview_xlsx(raw: bytes):
    """从 xlsx 二进制解析表格预览。"""
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    sheets = []
    for sn in wb.sheetnames:
        ws = wb[sn]
        rows = []
        for row in ws.iter_rows(values_only=True):
            rows.append([str(v) if v is not None else "" for v in row])
        sheets.append({"name": sn, "rows": rows[:200]})
    wb.close()
    return jsonify({"type": "excel", "sheets": sheets, "size": len(raw)})


def _preview_xmind(raw: bytes, path_arg: str):
    """从 xmind 二进制（zip）解析树形结构。"""
    with zipfile.ZipFile(io.BytesIO(raw), "r") as zf:
        if "content.xml" in zf.namelist():
            xm_raw = zf.read("content.xml").decode("utf-8")
            import defusedxml.ElementTree as ET

            root = ET.fromstring(xm_raw)
            _ns = "urn:xmind:xmap:xmlns:content:2.0"

            def _find(el, tag):
                r = el.find("{%s}%s" % (_ns, tag))
                if r is None:
                    r = el.find(tag)
                return r

            def _findall(el, tag):
                r = el.findall("{%s}%s" % (_ns, tag))
                return r if r else el.findall(tag)

            def _parse_topic(topic_el):
                title_el = _find(topic_el, "title")
                title = (
                    title_el.text.strip()
                    if title_el is not None and title_el.text
                    else ""
                )
                item = {"title": title}
                children_el = _find(topic_el, "children")
                if children_el is not None:
                    topics_el = _find(children_el, "topics")
                    if topics_el is not None:
                        sub_topics = _findall(topics_el, "topic")
                        if sub_topics:
                            item["children"] = [_parse_topic(st) for st in sub_topics]
                return item

            sheet = _find(root, "sheet")
            if sheet is None:
                sheet = root
            root_topic = _find(sheet, "topic")
            tree = (
                _parse_topic(root_topic)
                if root_topic is not None
                else {"title": path_arg.rsplit("/", 1)[-1]}
            )
            return jsonify({"type": "xmind", "tree": tree, "size": len(raw)})

        if "content.json" in zf.namelist():
            data = json.loads(zf.read("content.json").decode("utf-8"))

            def _extract_tree(node):
                if isinstance(node, list):
                    return [_extract_tree(n) for n in node]
                if not isinstance(node, dict):
                    return str(node)
                item = {"title": node.get("title", "")}
                children = node.get("children", {})
                attached = (
                    children.get("attached", []) if isinstance(children, dict) else []
                )
                if attached:
                    item["children"] = _extract_tree(attached)
                return item

            tree = _extract_tree(data)
            return jsonify({"type": "xmind", "tree": tree, "size": len(raw)})

        return jsonify(
            {"type": "xmind", "zip_contents": zf.namelist(), "size": len(raw)}
        )
