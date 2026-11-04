/* 需求澄清问答页（T3 流水线强化专项）
 * 从 /api/step2/clarify/sessions 拉取歧义卡片 + 证据 + 澄清问题，
 * 用户逐条作答 → POST /api/step2/clarify/answers → 回填 requirements，
 * 再 POST /api/step2/clarify/release 解除闸门。
 * 权限：读接口登录即可；作答/解除闸门按钮带 data-perm="execution.run"。
 */
(function () {
'use strict';

const A = window._A;
if (!A) { console.error('[clarify] _A 未初始化'); return; }
const $ = A.$, escHtml = A.escHtml, escAttr = A.escAttr, api = A.api, appendLog = A.appendLog;

const state = {
  sessions: [],
  gateOpen: false,
  project: '',
  runId: '',
};

const els = {
  gateStatus: $('gateStatus'),
  blockingTotal: $('blockingTotal'),
  releasedTotal: $('releasedTotal'),
  banner: $('clarifyBanner'),
  list: $('sessionList'),
  empty: $('sessionEmpty'),
};

function currentProject() {
  if (A._currentProject) return A._currentProject;
  try {
    if (window._currentProjectName) return window._currentProjectName;
  } catch (e) { /* ignore */ }
  return '';
}

async function loadGate() {
  try {
    const d = await api('GET', '/api/step2/clarify/gate?project=' + encodeURIComponent(state.project));
    if (d && d.ok) {
      state.gateOpen = !!d.gate_open;
      els.blockingTotal.textContent = d.blocking_total || 0;
      els.releasedTotal.textContent = d.released || 0;
      els.gateStatus.textContent = state.gateOpen ? '⛔ 已暂停（待澄清）' : '✅ 已放行';
      // 走设计 token（app-design.css），换主题/个性化配置时自动自适应
      els.gateStatus.style.color = state.gateOpen ? 'var(--state-warning)' : 'var(--state-success)';
      els.banner.style.display = state.gateOpen ? 'block' : 'none';
    }
  } catch (e) { /* ignore */ }
}

function tagFor(s) {
  const sev = s.severity === 'high' ? '<span class="clr-tag clr-tag-high">HIGH</span>' : '';
  const st = s.status;
  let stTag = '<span class="clr-tag clr-tag-open">OPEN</span>';
  if (st === 'answered') stTag = '<span class="clr-tag clr-tag-answered">ANSWERED</span>';
  else if (st === 'released') stTag = '<span class="clr-tag clr-tag-released">RELEASED</span>';
  return sev + stTag;
}

function renderSession(s) {
  const card = document.createElement('div');
  card.className = 'clr-session';
  card.dataset.sessionId = s.session_id;

  const head = document.createElement('div');
  head.className = 'clr-session-head';
  head.innerHTML = '<span class="clr-session-title">' + escHtml(s.session_id || '') + '</span>' +
    tagFor(s) +
    '<span class="clr-session-title" style="margin-left:auto;">' + escHtml(s.ambiguity_type || '') + '</span>';
  card.appendChild(head);

  const body = document.createElement('div');
  body.className = 'clr-body';

  const point = document.createElement('div');
  point.className = 'clr-point';
  point.textContent = '需求 ' + (s.req_id || '-') + '：' + (s.ambiguity_type || '');
  body.appendChild(point);

  // 证据（source_quote）
  const ev = document.createElement('div');
  ev.className = 'clr-evidence';
  ev.textContent = '证据：' + (s.backfill && s.backfill.source_quote ? s.backfill.source_quote : '（见歧义报告原文）');
  body.appendChild(ev);

  // 已答展示
  const answers = s.answers || [];
  const answeredMap = {};
  answers.forEach(function (a) { answeredMap[a.question_index] = a.answer; });

  // 澄清问题列表
  const questions = s.clarify_questions || [];
  const answeredAll = answers.length >= questions.length && questions.length > 0;
  const qInputs = [];
  questions.forEach(function (q, idx) {
    const wrap = document.createElement('div');
    wrap.className = 'clr-question';
    const qt = document.createElement('div');
    qt.className = 'clr-question-text';
    qt.textContent = 'Q' + (idx + 1) + '  ' + q;
    wrap.appendChild(qt);
    const input = document.createElement('input');
    input.className = 'clr-question-input';
    input.dataset.qidx = String(idx);
    input.dataset.qtext = q;
    input.placeholder = '请输入回答…';
    input.value = answeredMap[idx] || '';
    if (s.status === 'released') input.disabled = true;
    wrap.appendChild(input);
    body.appendChild(wrap);
    qInputs.push(input);
  });

  // 已回填信息（answered 后展示）
  if (s.status === 'answered' || s.status === 'released') {
    const bf = s.backfill;
    if (bf) {
      const ok = document.createElement('div');
      ok.className = 'clr-answer-ok';
      ok.innerHTML = '<span>✅ 已回填：</span>' +
        (bf.operation ? '<span class="clr-backfill">operation=' + escHtml(bf.operation) + '</span>' : '') +
        (bf.condition ? '<span class="clr-backfill">condition=' + escHtml(bf.condition) + '</span>' : '') +
        (bf.expected_feedback ? '<span class="clr-backfill">expected_feedback=' + escHtml(bf.expected_feedback) + '</span>' : '');
      body.appendChild(ok);
    }
  }

  // 操作区
  const actions = document.createElement('div');
  actions.className = 'clr-actions';
  if (s.status !== 'released') {
    const btnSubmit = document.createElement('button');
    btnSubmit.className = 'cu-btn-primary';
    btnSubmit.type = 'button';
    btnSubmit.setAttribute('data-perm', 'execution.run');
    btnSubmit.textContent = s.status === 'answered' ? '重新提交' : '提交回答';
    btnSubmit.addEventListener('click', function () {
      const answers = qInputs.map(function (inp) {
        return { question_index: parseInt(inp.dataset.qidx, 10), question: inp.dataset.qtext, answer: inp.value };
      }).filter(function (a) { return a.answer && a.answer.trim(); });
      if (answers.length === 0) { A.toast('请至少回答一个问题', 'error'); return; }
      A.withBtnLoading(btnSubmit, '提交中…', function () {
        return api('POST', '/api/step2/clarify/answers', {
          session_id: s.session_id,
          answers: answers,
        }).then(function (r) {
          if (r && r.ok) {
            appendLog('✅ 会话 ' + s.session_id + ' 已作答，状态=' + r.session_status);
            A.toast('回答已提交并回填', 'success');
            return loadAll();
          }
          A.toast((r && r.error) || '提交失败', 'error');
          return null;
        });
      });
    });
    actions.appendChild(btnSubmit);

    const btnRelease = document.createElement('button');
    btnRelease.className = 'cu-btn-ghost';
    btnRelease.type = 'button';
    btnRelease.setAttribute('data-perm', 'execution.run');
    btnRelease.textContent = '解除闸门';
    btnRelease.addEventListener('click', function () {
      A.withBtnLoading(btnRelease, '解除中…', function () {
        return api('POST', '/api/step2/clarify/release', { session_id: s.session_id }).then(function (r) {
          if (r && r.ok) {
            appendLog('✅ 会话 ' + s.session_id + ' 闸门已解除 can_generate=' + r.can_generate);
            A.toast('闸门已解除', 'success');
            return loadAll();
          }
          A.toast((r && r.error) || '解除失败', 'error');
          return null;
        });
      });
    });
    actions.appendChild(btnRelease);
  }
  body.appendChild(actions);
  card.appendChild(body);
  return card;
}

function renderSessions() {
  els.empty.style.display = state.sessions.length === 0 ? 'block' : 'none';
  // 保留闸门信息栏外的其余内容重建
  const list = els.list;
  list.querySelectorAll('.clr-session').forEach(function (n) { n.remove(); });
  state.sessions.forEach(function (s) {
    list.appendChild(renderSession(s));
  });
}

async function loadAll() {
  state.project = currentProject();
  await loadGate();
  try {
    const d = await api('GET', '/api/step2/clarify/sessions?project=' + encodeURIComponent(state.project));
    if (d && d.ok) {
      state.sessions = d.items || [];
      renderSessions();
    }
  } catch (e) {
    appendLog('⚠️ 澄清会话加载失败: ' + String(e));
  }
}

function init() {
  const btn = $('btnRefreshClarify');
  if (btn) {
    btn.addEventListener('click', function () {
      A.withBtnLoading(btn, '刷新中', loadAll);
    });
  }
  loadAll();
}

A._onAuthOk = function () {
  if (A._renderSidebarUser) A._renderSidebarUser();
  if (A.applyPermissions) A.applyPermissions();
  loadAll();
};

if (A._currentUser) {
  init();
}

})();
