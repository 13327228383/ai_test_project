# -*- coding: utf-8 -*-
"""单元测试：项目单 owner 约束（自动降级）与会话迁移。

镜像 src/users/project_auth.py。
- assign_project(level='owner') 若项目已有其他 owner → 自动降级为 member
- transfer_owner           系统管理员调换管理员 → 旧 owner 自动降级
- schema.sql               project_member 具备 execution.step3/4/5 权限
全部通过 mock _conn 实现，不依赖真实 DB。
"""

from pathlib import Path

from src.users import project_auth as pa


class _FakeCursor:
    """模拟 pymysql 游标：fetchone 返回预置行 / None。"""

    def __init__(self, row=None, rowcount=0):
        self._row = row
        self.rowcount = rowcount
        self.lastrowid = 1

    def fetchone(self):
        return self._row


class _FakeConn:
    """模拟连接：按 SQL 关键词返回预置旧 owner，记录 update/insert 调用。

    execute 返回带 fetchone() 的游标（与 pymysql 行为一致）。
    """

    def __init__(self, cur_owner):
        self.cur_owner = cur_owner  # 当前项目 owner（None 表示无）
        self.sqls = []  # 已执行的所有 (sql, params)
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def _row_for(self, sql, params):
        up = sql.upper()
        if "ROLE='OWNER'" in up or "role='owner'" in up:
            params_l = [str(p) for p in (params or [])]
            # 参数形如 (project, user_id, ...) 或 (user_id, project)
            target = None
            for p in (params or []):
                if str(p) not in (self.cur_owner or "",):
                    target = p
            return {"user_id": self.cur_owner} if (self.cur_owner and target) else None
        return None

    def execute(self, sql, params=None):
        self.sqls.append((sql, params))
        up = sql.strip().upper()
        if up.startswith("SELECT"):
            row = self._row_for(sql, params)
            return _FakeCursor(row=row)
        if "UPDATE" in up or "INSERT" in up:
            return _FakeCursor(rowcount=1)
        return _FakeCursor()

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True

    def ping(self, reconnect=False):
        return True


def _patched(monkeypatch, cur_owner):
    conn = _FakeConn(cur_owner)
    monkeypatch.setattr(pa, "_conn", lambda: conn)
    return conn


# ── 1. schema.project_member 具备 step3/4/5 ──

def test_schema_project_member_has_step345():
    schema = (
        Path(pa.__file__).resolve().parent.parent
        / "core" / "schema.sql"
    )
    raw = schema.read_text(encoding="utf-8")
    # 切片到 project_member 权限域，避免与 admin/project_admin 混淆
    idx = raw.index("('project_member', 'project.create'),")
    blob = raw[idx:]
    for perm in ("execution.step3", "execution.step4", "execution.step5"):
        assert f"('project_member', '{perm}')," in blob, f"缺 {perm}"


# ── 2. assign_project 设新 owner 自动降级旧 owner ──

def test_assign_project_demotes_old_owner(monkeypatch):
    conn = _patched(monkeypatch, cur_owner="u_old")
    assert pa.assign_project("proj", "u_new", "owner") is True

    demote = [s for s, _ in conn.sqls if "SET role='member'" in s or "SET role='member'" in s]
    assert demote, "应执行旧 owner 降级 UPDATE"
    setowner = None
    for s, p in conn.sqls:
        if "ROLE='OWNER'" in s.upper().replace("`", ""):
            setowner = p
    # 新 owner 被写入 owner（含 ON DUPLICATE 设 owner）
    assert any(
        "ROLE='OWNER'" in s.upper() for s, _ in conn.sqls
    ), "应设置新 owner"


def test_assign_project_owner_keeps_when_no_previous(monkeypatch):
    conn = _patched(monkeypatch, cur_owner=None)
    assert pa.assign_project("proj2", "u_boss", "owner") is True
    # 无旧 owner → 不应出现降级 UPDATE
    demote = [s for s, _ in conn.sqls if "role='member'" in s.lower()]
    assert not demote, "无旧 owner 时不应降级任何用户"


def test_assign_project_member_no_demote(monkeypatch):
    conn = _patched(monkeypatch, cur_owner="u_old")
    assert pa.assign_project("proj3", "u_m", "member") is True
    demote = [s for s, _ in conn.sqls if "role='member'" in s.lower()]
    assert not demote, "设 member 不应触发 owner 降级"


# ── 3. transfer_owner 调换管理员自动降级 ──

def test_transfer_owner_demotes_old_and_sets_new(monkeypatch):
    conn = _patched(monkeypatch, cur_owner="u_old")
    assert pa.transfer_owner("proj", "u_new", by="u_admin") is True

    up = " ".join(s.upper() for s, _ in conn.sqls)
    assert "SET ROLE='MEMBER'" in up.replace("`", ""), "旧 owner 应降级为 member"
    assert "ROLE='OWNER'" in up, "新 owner 应被设置"

    # 确保授予新 owner project_admin 系统角色（SQL 字面量）
    assert any(
        "PROJECT_ADMIN" in s.upper() for s, _ in conn.sqls
    ), "应授予新 owner project_admin 角色"


def test_transfer_owner_no_previous_just_assign(monkeypatch):
    conn = _patched(monkeypatch, cur_owner=None)
    assert pa.transfer_owner("proj2", "u_first", by="u_admin") is True
    demote = [s for s, _ in conn.sqls if "role='member'" in s.lower()]
    assert not demote, "无旧 owner 时不应降级"


def test_transfer_owner_same_owner_noop(monkeypatch):
    conn = _patched(monkeypatch, cur_owner="u_x")
    # 目标 owner 即当前 owner（探测结果与 new 相同）→ 无降级逻辑执行
    row_conn = _FakeConn(cur_owner=None)
    monkeypatch.setattr(pa, "_conn", lambda: row_conn)
    assert pa.transfer_owner("proj", "u_x", old_owner="u_x", by="u_admin") is True
    demote = [s for s, _ in row_conn.sqls if "role='member'" in s.lower()]
    assert not demote, "新旧 owner 相同不应降级"