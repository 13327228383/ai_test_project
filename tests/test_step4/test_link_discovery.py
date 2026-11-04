# -*- coding: utf-8 -*-
"""单元测试：Step4 预检「同源链接发现」（AGENTS.md §4.17-A）。

背景：预检只遍历侧边栏菜单项，平铺路由系统（无菜单）菜单项恒为 0 → 原实现直接
`return 0` 放弃 → nav_graph_nodes 永空 → Step3 无导航事实 → 用例首步落到站点根。

本文件只钉纯函数 `_same_origin_link_candidates`（筛选规则可确定性验证）：
同源过滤、协议过滤、登出/破坏性排除、入口页排除、按 path 去重、上限。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.pipeline.steps.step4.browser_core.single_mixins._exploratory import (  # noqa: E402
    _page_identity,
    _path_identity,
    _same_origin_link_candidates,
)


def _urls(cands: list) -> list[str]:
    return [c[0] for c in cands]


def test_same_origin_relative_links_are_resolved_and_deduplicated():
    anchors = [
        ["/products", "商品列表"],
        ["/products?page=2", "商品列表第2页"],  # 同 path 不同 query → 去重
        ["/products/new", "新增商品"],
        ["/products/1/edit", "编辑商品"],
        ["/products/1/delete", "删除商品"],  # 破坏性动作 → 排除
        ["http://127.0.0.1:8020/products/new", "重复的同源绝对地址"],  # 已见 path
        ["http://evil.example.com/x", "跨域"],  # 跨域 → 排除
        ["javascript:void(0)", "脚本"],  # 非导航协议 → 排除
        ["#top", "锚点"],  # 纯锚点 → 排除
        ["mailto:a@b.c", "邮件"],  # 非导航协议 → 排除
        ["/logout", "退出登录"],  # 登出 → 排除
    ]
    got = _same_origin_link_candidates("http://127.0.0.1:8020/", anchors, max_pages=10)
    assert _urls(got) == [
        "http://127.0.0.1:8020/products",
        "http://127.0.0.1:8020/products/new",
        "http://127.0.0.1:8020/products/1/edit",
    ]
    assert got[0][1] == "商品列表"  # 链接文本保留（页面身份的候选来源）


def test_entry_page_itself_is_never_requeued():
    anchors = [["/", "首页"], ["/dashboard", "看板"]]
    got = _same_origin_link_candidates(
        "http://127.0.0.1:8020/dashboard", anchors, max_pages=10
    )
    assert _urls(got) == ["http://127.0.0.1:8020/"]  # 入口页自身已排除


def test_max_pages_cap_is_applied():
    anchors = [[f"/p{i}", f"页面{i}"] for i in range(20)]
    got = _same_origin_link_candidates("http://h:1/", anchors, max_pages=3)
    assert len(got) == 3


def test_malformed_input_never_raises():
    assert _same_origin_link_candidates("", [["/a", "A"]]) == []
    assert _same_origin_link_candidates("http://h:1/", []) == []
    assert _same_origin_link_candidates("http://h:1/", [None, "x", ("/a",)]) == []


# ── 路由模板去重（实体 id 不是页面身份）──
def test_path_identity_normalizes_entity_id_segments():
    """实体 id 段（数字/UUID/长十六进制/长字母数字串）归一化为 ``{id}``。"""
    # 数字 id
    assert _path_identity("/products/940/edit") == "/products/{id}/edit"
    # 同一路由模板的多个实体实例必须归一为同一身份
    assert _path_identity("/products/940/edit") == _path_identity("/products/941/edit")
    # UUID
    _u = "/orders/3f2b8c1e-9d4a-4b7e-8c2f-1a2b3c4d5e6f/detail"
    assert _path_identity(_u) == "/orders/{id}/detail"
    # 长十六进制 / 长字母数字串（含数字）
    assert _path_identity("/blob/deadbeefdeadbeef") == "/blob/{id}"
    assert _path_identity("/share/a1b2c3d4e5") == "/share/{id}"
    # 语义路径段不是实体 id（保持原样）
    assert _path_identity("/products/new") == "/products/new"
    assert _path_identity("/") == "/"
    assert _path_identity("") == "/"


def test_entity_pages_do_not_eat_page_quota():
    """列表页上的多个同类实体链接只占 1 个页面上限（真实导航页面不会被挤掉）。

    根因实测(2026-10-28, smartshop)：`/products/940..944/edit` 5 个实体页把
    ``max_pages`` 名额占满 → `/products/new` 等真实页面未能发现。
    """
    anchors = [
        ["/products/940/edit", "编辑商品"],
        ["/products/941/edit", "编辑商品"],
        ["/products/942/edit", "编辑商品"],
        ["/products/new", "新增商品"],
        ["/products", "商品列表"],
    ]
    got = _urls(_same_origin_link_candidates("http://127.0.0.1:8020/", anchors, 10))
    assert got == [
        "http://127.0.0.1:8020/products/940/edit",  # 首见实体页保留（作为该模板的代表）
        "http://127.0.0.1:8020/products/new",
        "http://127.0.0.1:8020/products",
    ]
    # 同样的名额在"实体页未归一时"会被消耗殆尽
    assert len(got) == 3


def test_flat_route_system_discovers_all_page_types():
    """平铺路由（无侧边栏）系统：链接发现应覆盖全部页面类型。

    认证页（``/login``）与入口页自身都不是业务导航节点（与落库判据共用
    ``_is_auth_page_url``）。
    """
    anchors = [
        ["/", "商品管理"],
        ["/products", "商品列表"],
        ["/products/new", "新增商品"],
        ["/products/940/edit", "编辑"],
        ["/products/941/edit", "编辑"],
        ["/login", "登录"],
        ["/logout", "退出"],
    ]
    got = _urls(_same_origin_link_candidates("http://127.0.0.1:8020/products", anchors, 10))
    assert got == [
        "http://127.0.0.1:8020/",
        "http://127.0.0.1:8020/products/new",
        "http://127.0.0.1:8020/products/940/edit",
    ]


# ── 页面身份（写库 page_title，Step3 导航解析的匹配依据）──
def test_page_identity_prefers_semantic_text_over_doc_title():
    assert _page_identity("商品列表", "SmartShop", "/products") == "商品列表"
    # 泛化导航文案（翻页/查看/编辑/返回…）不是页面身份 → 回落文档标题
    assert _page_identity("下一页", "SmartShop 商品管理", "/products") == "SmartShop 商品管理"
    assert _page_identity("Edit", "Product Detail", "/p/1/edit") == "Product Detail"
    assert _page_identity("»", "订单中心", "/orders?p=2") == "订单中心"
    # 文档标题也缺失 → 路径兜底（绝不落空，RC-D）
    assert _page_identity("", "", "/products") == "/products"
    assert _page_identity("下一页", "", "/products") == "/products"
