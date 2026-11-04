# -*- coding: utf-8 -*-
"""算法规则清单（/skill-rules）卡片自适应铺满回归（2026-10-28 锁定）。

锁定契约：卡片数少（当前 5 张）时，每一行都必须铺满网格宽度，不得留空格子。
缺陷史：`columns: 300px` 多列布局在 1440px 下排成 3 列（每列 375px），
5 张卡按 2/2/1 分布，右下角空出一整块（填充率 0.809）；
修复为 flex 行内等分（`flex: 1 1 300px`），末行卡片拉伸补满（填充率 0.974）。
"""
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

PORT = 8700
ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "src" / "backend" / "app.py"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _is_up(port=PORT):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


@pytest.fixture(scope="module")
def service():
    proc = None
    if not _is_up():
        proc = subprocess.Popen([sys.executable, "-u", str(BACKEND)],
                                cwd=str(ROOT), creationflags=CREATE_NO_WINDOW)
        for _ in range(60):
            time.sleep(0.5)
            if _is_up():
                break
        else:
            proc.terminate()
            pytest.skip("后端未能启动，跳过 UI 回归")
    yield
    if proc is not None:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture
def page(service):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page(viewport={"width": 1440, "height": 1000})
        yield pg
        b.close()


def _open_skill_rules(pg):
    pg.goto(f"http://127.0.0.1:{PORT}/login", wait_until="domcontentloaded")
    pg.wait_for_timeout(300)
    pg.select_option("select.form-select", "admin")
    pg.fill("#loginPassword", "admin123")
    pg.keyboard.press("Enter")
    pg.wait_for_load_state("domcontentloaded")
    pg.wait_for_timeout(800)
    pg.goto(f"http://127.0.0.1:{PORT}/skill-rules", wait_until="domcontentloaded")
    pg.wait_for_selector(".pc-rules-card", timeout=20000)
    pg.wait_for_timeout(900)


def test_five_rule_cards_fill_every_row(page):
    """1440px 下 5 张卡：每行宽度之和 + 间距 == 网格宽度（无空格子）。"""
    _open_skill_rules(page)
    d = page.evaluate(
        """()=>{
          const g=document.querySelector('.pc-rules-grid');
          const gr=g.getBoundingClientRect();
          const gap=parseFloat(getComputedStyle(g).columnGap)||0;
          const cards=Array.from(g.querySelectorAll('.pc-rules-card')).map(c=>{
            const r=c.getBoundingClientRect();
            return {w:r.width, h:r.height, top:Math.round(r.top-gr.top),
                    right:Math.round(r.right-gr.right)};
          });
          const rows={};
          cards.forEach(c=>{ (rows[c.top]=rows[c.top]||[]).push(c.w); });
          return {n:cards.length, gridW:gr.width, gap:gap,
                  rowWidths:Object.keys(rows).map(k=>({sum:rows[k].reduce((a,b)=>a+b,0),
                                                     n:rows[k].length})),
                  rightOverflow: cards.filter(c=>c.right>1).length};
        }"""
    )
    assert d["n"] == 5, f"规则卡应为 5 张，实际 {d['n']}"
    assert not d["rightOverflow"], "存在超出网格右缘的卡片"
    for row in d["rowWidths"]:
        expected = d["gridW"] - d["gap"] * (row["n"] - 1)
        assert abs(row["sum"] - expected) <= 2, (
            f"该行未铺满：卡片宽度和 {row['sum']:.1f} vs 期望 {expected:.1f}"
            f"（{row['n']} 张）"
        )


def test_cards_are_not_cramped(page):
    """卡片宽度不得小于 300px（避免被多列布局压到逼仄）。"""
    _open_skill_rules(page)
    widths = page.evaluate(
        """()=>Array.from(document.querySelectorAll('.pc-rules-card'))
             .map(c=>c.getBoundingClientRect().width)"""
    )
    assert min(widths) >= 300, f"存在过窄卡片: {[round(w) for w in widths]}"


def test_cards_fill_vertical_space(page):
    """卡片纵向铺满内容区：底部无成片留白，规则列表可见高度 > 360px。

    缺陷史：`.pc-rule-list` 固定 `max-height:360px`，5 张卡总高仅约 400px，
    1440×1000 下内容区底部空出约 480px；Step4 有 55 条规则（内容高 4575px）
    却只露出约 8 条。修复后行高等分视口剩余空间，列表在卡内滚动。
    """
    _open_skill_rules(page)
    page.wait_for_function(
        "()=>document.querySelectorAll('.pc-rule-list').length>=5", timeout=20000)
    d = page.evaluate(
        """()=>{
          const g=document.querySelector('.pc-rules-grid');
          const gr=g.getBoundingClientRect();
          const cards=Array.from(g.querySelectorAll('.pc-rules-card')).map(c=>{
            const r=c.getBoundingClientRect();
            return {top:r.top-gr.top, bottom:r.bottom-gr.top, h:r.height};
          });
          const lists=Array.from(document.querySelectorAll('.pc-rule-list'))
            .map(l=>l.getBoundingClientRect().height);
          const bodyRect=document.querySelector('.content-body').getBoundingClientRect();
          return {gridH:gr.height, bodyH:bodyRect.height,
                  cardBottom:Math.max(...cards.map(c=>c.bottom)),
                  bottomGap:gr.height-Math.max(...cards.map(c=>c.bottom)),
                  maxCardH:Math.max(...cards.map(c=>c.h)),
                  listMin:Math.min(...lists),
                  bodyScrolls: document.querySelector('.content-body').scrollHeight
                               > Math.ceil(bodyRect.height)};
        }"""
    )
    assert d["bottomGap"] <= 2, f"卡片下方仍有 {d['bottomGap']:.0f}px 空白"
    assert d["maxCardH"] <= d["gridH"] + 2, (
        f"卡片高度 {d['maxCardH']:.0f} 超出网格 {d['gridH']:.0f}（内容撑破行高）")
    assert d["listMin"] > 360, f"规则列表可见高仅 {d['listMin']:.0f}px（应大于原 360px 固定窗）"
    assert not d["bodyScrolls"], "内容区出现纵向滚动（应正好铺满而不溢出）"
