# -*- coding: utf-8 -*-
"""用例审核页（/intent-review）置信度分析回归（2026-10-28 锁定）。

锁定两条已被实测确认的契约：
  1. 「置信度分析」5 个维度条必须真实渲染（填充条 rect 宽高 > 0）。
     缺陷史：renderConfidence 用 `<span class="md-dim-bar-fill">`，CSS 未给 display，
     span 默认 inline → width/height 失效 → 实测 0×0，5 条进度条全部不可见。
  2. 勾选单条用例必须联动右栏详情（置信度分析按该用例渲染）且勾选状态保持。
     缺陷史：勾选框 click 冒泡到行 → selectCase → renderList 在激活行为前移除 input，
     浏览器跳过激活 → change 不触发、checked 回滚、已选计数停在 0（"勾选无反应"）。
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
    """确保后端可达；若不在运行则临时拉起，随会话结束恢复。"""
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


def _login(pg):
    pg.goto(f"http://127.0.0.1:{PORT}/login", wait_until="domcontentloaded")
    pg.wait_for_timeout(300)
    pg.select_option("select.form-select", "admin")
    pg.fill("#loginPassword", "admin123")
    pg.keyboard.press("Enter")
    pg.wait_for_load_state("domcontentloaded")
    pg.wait_for_timeout(800)


def _open_review(pg):
    _login(pg)
    pg.goto(f"http://127.0.0.1:{PORT}/intent-review", wait_until="domcontentloaded")
    pg.wait_for_timeout(2500)
    pg.wait_for_selector(".md-list-item", timeout=20000)


def test_confidence_dimension_bars_are_rendered(page):
    """5 个维度条的填充必须可见（rect 宽高 > 0），不能是 inline 的 0×0。"""
    _open_review(page)
    dims = page.evaluate(
        """()=>Array.from(document.querySelectorAll('.md-dim-item')).map(function(i){
             const f=i.querySelector('.md-dim-bar-fill');
             if(!f) return {label:null};
             const r=f.getBoundingClientRect();
             return {label:(i.querySelector('.md-dim-name')||{}).textContent||'',
                     w:r.width, h:r.height, display:getComputedStyle(f).display};
           })"""
    )
    assert len(dims) == 5, f"维度条应为 5 个，实际 {len(dims)}: {dims}"
    broken = [d for d in dims if not d.get("w") or not d.get("h")]
    assert not broken, f"存在不可见的维度进度条: {broken}"


def test_single_case_checkbox_drives_detail_and_keeps_state(page):
    """勾选单条用例 → 详情切换到该用例、置信度刷新、已选计数=1、勾选保持。"""
    _open_review(page)
    rows = page.evaluate(
        """()=>Array.from(document.querySelectorAll('.md-list-item')).map(r=>r.dataset.caseId)"""
    )
    if len(rows) < 2:
        pytest.skip("用例不足 2 条，无法验证单条勾选")
    target = rows[1]

    page.locator(".md-list-item").nth(1).locator("input.ir-row-cb").click()
    page.wait_for_timeout(500)

    state = page.evaluate(
        """(cid)=>{
             const row=document.querySelector(`.md-list-item[data-case-id="${cid}"]`);
             const cb=row?row.querySelector('input.ir-row-cb'):null;
             return {detail:(document.querySelector('#detailCaseId')||{}).textContent,
                     checked: cb ? cb.checked : null,
                     sel: (document.querySelector('#selectedCount')||{}).textContent,
                     score: (document.querySelector('#confidenceScore')||{}).textContent};
           }""",
        target,
    )
    assert state["detail"] == target, f"勾选未联动详情：detail={state['detail']} 期望 {target}"
    assert state["checked"] is True, "勾选状态被回滚（activate 前节点被移除）"
    assert state["sel"] == "1", f"已选计数异常: {state['sel']}"
    assert state["score"] and state["score"].endswith("%"), f"置信度分数未渲染: {state['score']}"
