# -*- coding: utf-8 -*-
"""
XMind 测试设计生成器
=====================
将 AI 分析后的测试设计结构化为 XMind 思维导图文件 (.xmind)。

XMind 经典格式 = ZIP 压缩包，内含：
  - content.xml    思维导图结构
  - META-INF/manifest.xml  清单文件

使用方式：
    from src.pipeline.steps.step3.xmind_generator import build_xmind
    result = build_xmind(design_data, "项目名")
    # result["path"] = 临时 .xmind 文件路径
"""

import tempfile
import uuid
import zipfile
from pathlib import Path
from datetime import datetime
from typing import Any
from defusedxml.ElementTree import Element, SubElement, tostring
import logging
from src.core.constants import DEMO_PROJECT  # noqa: F401

_log = logging.getLogger(__name__)


# ── 方法缩写 → 全称 ──
METHOD_NAMES = {
    "EP": "等价类划分",
    "BV": "边界值分析",
    "EG": "错误推测法",
    "ST": "状态迁移",
    "DT": "决策表",
    "OT": "正交实验",
    "CE": "因果图",
    "FD": "场景法",
    "CT": "组合测试",
}


def _escape_xml(text: str) -> str:
    """转义 XML 特殊字符。"""
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _build_content_xml(design: dict[str, Any]) -> str:
    """
    将测试设计数据构建为 XMind content.xml。

    design 结构:
    {
        "project": "项目名",
        "version": "v4.0",
        "types": [
            {
                "name": "功能测试",
                "modules": [
                    {
                        "name": "登录模块",
                        "points": [
                            {"desc": "用户名输入", "method": "EP", "technique": "等价类：有效/无效/空"},
                            ...
                        ]
                    }
                ]
            }
        ]
    }
    """
    NS = "urn:xmind:xmap:xmlns:content:2.0"
    root = Element(
        "xmap-content",
        {
            "xmlns": NS,
            "xmlns:fo": "http://www.w3.org/1999/XSL/Format",
            "xmlns:svg": "http://www.w3.org/2000/svg",
            "xmlns:xhtml": "http://www.w3.org/1999/xhtml",
            "xmlns:xlink": "http://www.w3.org/1999/xlink",
            "version": "2.0",
        },
    )

    sheet = SubElement(
        root,
        "sheet",
        {"id": _gen_id(), "timestamp": str(int(datetime.now().timestamp() * 1000))},
    )

    # 根主题：版本名优先（如"v2.1 测试设计"），否则用项目名
    proj = design.get("project", "测试设计")
    ver = design.get("version", "")
    if ver and not ver.startswith("v") and len(ver) > 3:
        # 有意义的版本名（如"v2.1"），直接作为主标题
        root_title = "%s 测试设计" % ver
    elif ver:
        root_title = "%s %s 测试设计" % (proj, ver)
    else:
        root_title = "%s 测试设计" % proj
    root_topic = _make_topic(root_title, _gen_id())
    sheet.append(root_topic)

    children_elem = SubElement(root_topic, "children")
    topics_elem = SubElement(children_elem, "topics", {"type": "attached"})

    for test_type in design.get("types", []):
        type_name = test_type.get("name", "未分类")
        type_topic = _make_topic("📁 " + type_name, _gen_id())
        type_children = SubElement(type_topic, "children")
        type_topics = SubElement(type_children, "topics", {"type": "attached"})

        for module in test_type.get("modules", []):
            mod_name = module.get("name", "未命名模块")
            mod_topic = _make_topic("📦 " + mod_name, _gen_id())
            mod_children = SubElement(mod_topic, "children")
            mod_topics = SubElement(mod_children, "topics", {"type": "attached"})

            for point in module.get("points", []):
                desc = point.get("desc", "")
                method = point.get("method", "")
                technique = point.get("technique", "")
                method_full = METHOD_NAMES.get(method, method)
                label = "[%s] %s" % (method, desc) if method else desc
                pt_topic = _make_topic(label, _gen_id())

                # 技术细节作为子节点
                pt_children_elem = SubElement(pt_topic, "children")
                pt_sub_topics = SubElement(
                    pt_children_elem, "topics", {"type": "attached"}
                )
                if technique:
                    tech_topic = _make_topic(
                        "🔧 %s：%s" % (method_full, technique), _gen_id()
                    )
                    pt_sub_topics.append(tech_topic)

                # suggested_intent 作为测试用例骨架子节点
                sc = point.get("suggested_intent")
                if sc and isinstance(sc, dict):
                    sc_title = sc.get("title", "")
                    sc_type = sc.get("type", "")
                    sc_pri = sc.get("risk_level", "P2")
                    sc_steps = sc.get("steps", "")
                    sc_label = (
                        "📝 [%s|%s] %s" % (sc_pri, sc_type, sc_title)
                        if sc_title
                        else ""
                    )
                    if sc_label:
                        intent_topic = _make_topic(sc_label, _gen_id())
                        if sc_steps:
                            case_children = SubElement(intent_topic, "children")
                            case_topics = SubElement(
                                case_children, "topics", {"type": "attached"}
                            )
                            for si, step in enumerate(sc_steps.split(",")[:5]):
                                step_text = step.strip()
                                if step_text:
                                    case_topics.append(
                                        _make_topic(
                                            "%d. %s" % (si + 1, step_text), _gen_id()
                                        )
                                    )
                        pt_sub_topics.append(intent_topic)

                mod_topics.append(pt_topic)

            type_topics.append(mod_topic)

        topics_elem.append(type_topic)

    # 美化 XML 输出
    raw = tostring(root, encoding="unicode")
    # 添加 XML 声明
    return '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n' + raw


def _make_topic(title: str, topic_id: str) -> Element:
    """创建一个 topic 元素。"""
    topic = Element(
        "topic",
        {"id": topic_id, "timestamp": str(int(datetime.now().timestamp() * 1000))},
    )
    title_elem = SubElement(topic, "title")
    title_elem.text = _escape_xml(title)
    return topic


def _gen_id() -> str:
    """生成 XMind 风格的 ID（短 UUID 取前 8 位）。"""
    return uuid.uuid4().hex[:8]


def _build_manifest_xml() -> str:
    """构建 META-INF/manifest.xml。"""
    return """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<manifest xmlns="urn:xmind:xmap:xmlns:manifest:1.0">
  <file-entry full-path="content.xml" media-type="text/xml"/>
</manifest>"""


def build_xmind(design_data: dict[str, Any], project_name: str = "") -> dict[str, Any]:
    """
    根据测试设计数据构建 .xmind 文件。

    参数:
        design_data: AI 返回的测试设计 JSON
        project_name: 项目名称（备用）

    返回:
        {"success": True, "path": "/tmp/xxx.xmind", "size": 12345}
    """
    try:
        if not design_data.get("project"):
            design_data["project"] = project_name

        content_xml = _build_content_xml(design_data)
        manifest_xml = _build_manifest_xml()

        # 打包为 .xmind (ZIP)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xmind")
        try:
            with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("content.xml", content_xml.encode("utf-8"))
                zf.writestr("META-INF/manifest.xml", manifest_xml.encode("utf-8"))

            file_size = Path(tmp.name).stat().st_size
            return {"success": True, "path": tmp.name, "size": file_size}
        finally:
            tmp.close()
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── CLI 自测 ──
if __name__ == "__main__":
    # ★ 某SPA系统业务名已统一到 config.demo.sample_project
    sample = {
        "project": DEMO_PROJECT,
        "version": "v4.0",
        "types": [
            {
                "name": "功能测试",
                "modules": [
                    {
                        "name": "登录模块",
                        "points": [
                            {
                                "desc": "用户名输入校验",
                                "method": "EP",
                                "technique": "等价类：有效用户名 / 无效用户名 / 空值",
                                "suggested_intent": {
                                    "title": "验证用户名输入合法性",
                                    "type": "正向",
                                    "risk_level": "P1",
                                    "steps": "打开登录页,输入有效用户名,点击登录,验证跳转",
                                },
                            },
                            {
                                "desc": "密码输入校验",
                                "method": "BV",
                                "technique": "边界值：空密码(-1) / 最短6位(0) / 最长20位(+1)",
                                "suggested_intent": {
                                    "title": "密码边界值校验",
                                    "type": "边界",
                                    "risk_level": "P1",
                                    "steps": "输入空密码,点击登录,验证错误提示,输入最短6位密码,验证通过",
                                },
                            },
                            {
                                "desc": "登录按钮响应",
                                "method": "EG",
                                "technique": "错误推测：重复点击 / 网络超时 / 验证码错误",
                                "suggested_intent": {
                                    "title": "登录按钮重复点击",
                                    "type": "异常",
                                    "risk_level": "P2",
                                    "steps": "进入登录页,快速连续点击登录,验证防重复提交",
                                },
                            },
                        ],
                    },
                    {
                        "name": "数据导入",
                        "points": [
                            {
                                "desc": "文件上传格式",
                                "method": "EP",
                                "technique": "等价类：xlsx/csv/无效格式",
                            },
                            {
                                "desc": "数据量边界",
                                "method": "BV",
                                "technique": "边界值：0行(-1) / 1行(0) / 10000行(+1)",
                            },
                        ],
                    },
                ],
            },
            {
                "name": "性能测试",
                "modules": [
                    {
                        "name": "列表加载",
                        "points": [
                            {
                                "desc": "大数据量渲染",
                                "method": "BV",
                                "technique": "边界值：100条(0) / 1000条(0) / 10000条(+1)",
                            },
                        ],
                    },
                ],
            },
            {
                "name": "接口测试",
                "modules": [
                    {
                        "name": "登录API",
                        "points": [
                            {
                                "desc": "Token 过期处理",
                                "method": "ST",
                                "technique": "状态迁移：有效→过期→刷新→拒绝",
                            },
                        ],
                    },
                ],
            },
        ],
    }
    result = build_xmind(sample, DEMO_PROJECT)
    if result["success"]:
        _log.info("XMind 已生成: %s (%d bytes)", result["path"], result["size"])
    else:
        _log.error("失败: %s", result["error"])
