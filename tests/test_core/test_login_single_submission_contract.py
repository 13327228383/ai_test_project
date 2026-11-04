# -*- coding: utf-8 -*-
"""D38 契约：登录场景的凭证提交**只能执行一次**。

根因（实测 TC-REQ-040-E01 假 PRODUCT_BUG）：
    `verifications` 里含一批"填用户名/填密码/点登录"的步骤。
    `_execute_guided` 的登录专用路径会执行它们一遍；随后 Post 序列
    （`for _step in _post_verifications`）**又执行一遍**。
    第二次点击让页面重载、清空表单 → Post 4/4 的 verify 读到的仍是登录页
    → "错误凭证被拒绝"恒 FAIL。而靶子实际返回了「用户名或密码错误」。

判据（单一事实源）：登录专用路径提交完成后必须置位 `_submission_done`，
    使 Post 序列复用既有"重复创建防护"机制——只跑 verify、跳过重复 fill/click。
    与"重复创建防护"同源原理：同一业务提交不得执行两次。
"""
from __future__ import annotations

import inspect
import re

from src.agent.explorer_mixins import _action

MOD = inspect.getsource(_action)

# 登录路径收尾锚点：清空 inputs + 清空 _op_btn_text
_ANCHOR = re.compile(
    r'跳过常规"开表单→填→提交"流程\(凭证已填、登录已点\)'
)


def _login_tail(n_lines: int = 14) -> str:
    """返回登录路径收尾锚点之后的若干行。"""
    m = _ANCHOR.search(MOD)
    assert m, "未找到登录专用路径收尾注释"
    lines = MOD[m.end():].splitlines()
    return "\n".join(lines[:n_lines])


def test_login_path_marks_submission_done():
    """登录专用路径收尾必须置 _submission_done=True。"""
    tail = _login_tail()
    assert "_submission_done = True" in tail, (
        "登录路径收尾未置 _submission_done —— Post 序列会重复执行凭证提交，"
        "导致页面重载清空表单（假 FAIL 根因）。"
        "实际收尾段:\n" + tail
    )


def test_login_path_opens_replay_window():
    """同时置 _in_replay=True，令重复的 fill/click 段被跳过。"""
    tail = _login_tail()
    assert "_in_replay = True" in tail, (
        "登录路径未开启重放窗口 —— Post 的 fill/click 不会被跳过。"
        "实际收尾段:\n" + tail
    )


def test_replay_guard_still_lets_verify_run():
    """回归护栏：重放窗口内 verify 必须仍被执行（否则永远不判）。"""
    m = re.search(r"if _submission_done:(?P<body>(?:\s*\n\s*.+)+?)\n            #", MOD)
    assert m, "未找到重复创建防护段"
    body = m.group("body")
    assert 'if _action == "verify":' in body and "_in_replay = False" in body, (
        "重放窗口内必须放行 verify（并关闭重放窗口），否则断言永不执行"
    )


def test_submission_done_declared_nonlocal():
    """_submission_done 必须在 _exec_one_step 内被声明为 nonlocal（可写）。"""
    m = re.search(r"async def _exec_one_step\(.*?\):\s*\n\s*nonlocal (?P<nl>[^\n]+)", MOD)
    assert m, "未找到 _exec_one_step 的 nonlocal 声明"
    assert "_submission_done" in m.group("nl"), (
        "_submission_done 未声明 nonlocal —— 登录路径的赋值不会生效"
    )
