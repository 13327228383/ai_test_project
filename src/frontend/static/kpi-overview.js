/* 统一 KPI 渲染模块（原独立 /kpi-overview 页并入「深度指标中心」的统一 KPI tab 后保留的可复用逻辑）
 * 从 GET /api/kpi/overview 拉取统一指标字典（≥13 项，每项带 source 标注），
 * 渲染：指标卡片 + 状态分布 + 趋势折线（近 10 次）+ 台账不符明细表。
 * 图表用内联 SVG（无新 CDN 依赖）。
 *
 * 对外暴露（挂 window，跨 metrics 页复用，不自动执行、不覆盖 _onAuthOk）：
 *   window._renderUnifiedKpi(hostEl, data)  —— 纯渲染：把 data 渲染进 hostEl 内的 4 个区块
 *   window._loadUnifiedKpi(hostEl, force)    —— 首次拉取 GET /api/kpi/overview（已缓存则只重渲，
 *                                                重复激活不重复请求；force=true 强制刷新）
 */
(function () {
'use strict';

const A = window._A;
if (!A) { console.error('[kpi-overview] _A 未初始化'); return; }

// hostEl 作用域内取子元素（避免跨页面 ID 冲突，保证函数可在任意宿主重用）
function el(host, id) {
  return host.querySelector('#' + id);
}

function currentProject() {
  if (A._currentProject) return A._currentProject;
  try { if (window._currentProjectName) return window._currentProjectName; } catch (e) {}
  return '';
}

// ── 指标卡片 ──
// ★ 语义色按「值」判定而非仅按 key：ledger_mismatch / low_confidence_count 为计数型，
//   值=0 是「好结果」（无异常）→ green；值>0 才是「bad」→ red。
function metricTone(key, value) {
  if (key === 'pass_rate' || key === 'coverage_rate' || key === 'closure_rate') return 'good';
  if (key === 'fake_pass_rate') return 'bad';
  if (key === 'ledger_mismatch' || key === 'low_confidence_count') return (value > 0) ? 'bad' : 'good';
  return '';
}

function fmtValue(key, m) {
  if (m == null || m.value == null) return '暂无数据';
  // ★ 仅比率类指标（0~1）加百分号；计数字段（如 low_confidence_count）不得加 %。
  if (key.endsWith('_rate') || key === 'avg_confidence') {
    return (Math.round(m.value * 10000) / 100) + '%';
  }
  return String(m.value);
}

// ★ 每个 KPI 的数据来源是其架构上「固定」的读取表（单一事实源），与是否有值无关。
//   故 source 在卡内必须是固定值，绝不允许显示「无数据源」；只有「值」才可能暂无数据。
const CARD_ORDER = [
  ['total', '用例总数', 'executions'],
  ['passed', '通过数', 'executions'],
  ['failed', '失败数', 'executions'],
  ['flaky', 'Flaky', 'executions'],
  ['skipped', '跳过', 'executions'],
  ['blocked', '阻断', 'executions'],
  ['pass_rate', '达成率', 'executions'],
  ['coverage_rate', '覆盖率', 'coverage_summaries'],
  ['fake_pass_rate', '假绿率', 'fake_pass_audit'],
  ['ledger_mismatch', '不符台账', 'ledger'],
  ['closure_rate', '补做闭环率', 'ledger'],
  ['avg_confidence', '平均置信度', 'executions'],
  ['low_confidence_count', '低置信度用例', 'executions'],
];

function renderCards(host, data) {
  const grid = el(host, 'kpiCards');
  if (!grid) return;
  grid.innerHTML = '';
  const metrics = (data && data.metrics) || {};
  const sources = new Set();
  CARD_ORDER.forEach(function (pair) {
    const key = pair[0], label = pair[1], fixedSource = pair[2];
    const m = metrics[key];
    const card = document.createElement('div');
    card.className = 'kpi-card';
    // ★ 无数据时不给语义色（good 绿 / bad 红），统一中性灰
    const noData = (m == null || m.value == null);
    const tone = noData ? '' : metricTone(key, m.value);
    const valueCls = noData ? 'kpi-card-value no-data' : ('kpi-card-value ' + tone).trim();
    // ★ source 呈「固定值」——取 CARD_ORDER 中架构定义的读取来源表，恒可展示。
    const sourceTag = fixedSource;
    card.innerHTML =
      '<div class="' + valueCls + '">' + A.escHtml(fmtValue(key, m)) + '</div>' +
      '<div class="kpi-card-label">' + A.escHtml(label) + '</div>' +
      '<div class="kpi-card-source">source: ' + A.escHtml(sourceTag) + '</div>';
    grid.appendChild(card);
    sources.add(fixedSource);
  });
  const srcEl = el(host, 'kpiSources');
  if (srcEl) srcEl.textContent = Array.from(sources).join(', ') || '—';
}

// ── 状态分布（横向条）──
function renderDistribution(host, data) {
  const block = el(host, 'kpiDistribution');
  if (!block) return;
  const dist = (data && data.distribution) || {};
  const keys = ['PASS', 'FAIL', 'FLAKY', 'SKIP', 'BLOCKED', 'PRE_GATE_BLOCKED', 'ERROR'];
  const total = keys.reduce(function (s, k) { return s + (dist[k] || 0); }, 0);
  if (total === 0) {
    block.innerHTML = '<div class="kpi-empty">暂无执行分布数据</div>';
    return;
  }
  // ★ 统一走 design token（含 hex 兜底，避免 token 缺失时条形不可见）
  const colors = {
    PASS: 'var(--state-success, #22c55e)',
    FAIL: 'var(--state-error, #ef4444)',
    FLAKY: 'var(--state-warning, #f59e0b)',
    SKIP: 'var(--text-secondary, #94a3b8)',
    BLOCKED: 'var(--state-info, #38bdf8)',
    PRE_GATE_BLOCKED: 'var(--step2-color, #a78bfa)',
    ERROR: 'var(--text-quaternary, #64748b)',
  };
  let html = '<div style="display:flex;flex-direction:column;gap:8px;">';
  keys.forEach(function (k) {
    const v = dist[k] || 0;
    if (v === 0) return;
    const pct = Math.round(v * 100 / total);
    html += '<div style="display:flex;align-items:center;gap:10px;">' +
      '<span style="width:110px;font-size:12px;color:var(--text-secondary);font-family:var(--font-mono);">' + k + '</span>' +
      '<div style="flex:1;height:14px;background:var(--bg-elevated);border-radius:7px;overflow:hidden;">' +
      '<div style="height:100%;width:' + pct + '%;background:' + (colors[k] || 'var(--text-secondary, #94a3b8)') + ';"></div></div>' +
      '<span style="width:52px;text-align:right;font-size:12px;font-family:var(--font-mono);color:var(--text-secondary);">' + v + ' (' + pct + '%)</span>' +
      '</div>';
  });
  html += '</div>';
  block.innerHTML = html;
}

// ── 趋势折线（SVG）──
function renderTrend(host, data) {
  const block = el(host, 'kpiTrend');
  if (!block) return;
  const trend = (data && data.trend) || [];
  if (trend.length === 0) {
    block.innerHTML = '<div class="kpi-empty">暂无趋势数据（至少需要 1 次运行记录）</div>';
    return;
  }
  const W = 560, H = 180, PAD = 30;
  // ★ 折线配色同样走 design token（SVG stroke/fill 支持 var()，附 hex 兜底）
  const series = [
    ['pass_rate', 'var(--state-success, #22c55e)', '通过率'],
    ['coverage_rate', 'var(--state-info, #38bdf8)', '覆盖率'],
    ['fake_pass_rate', 'var(--state-error, #ef4444)', '假绿率'],
  ];
  // ★ 无数据 vs 真 0 语义：缺数据的序列跳过连线，并在图例标注「暂无数据」。
  const points = trend.map(function (t, idx) {
    const x = PAD + idx * (W - PAD * 2) / Math.max(trend.length - 1, 1);
    return { x: x, t: t };
  });
  function hasValue(v) {
    return v != null && v !== '' && !isNaN(Number(v));
  }
  function seriesHasData(s) {
    return points.some(function (p) { return hasValue(p.t[s[0]]); });
  }
  let svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" style="width:100%;height:auto;max-height:220px;">';
  for (let g = 0; g <= 4; g++) {
    const y = PAD + g * (H - PAD * 2) / 4;
    svg += '<line x1="' + PAD + '" y1="' + y + '" x2="' + (W - PAD) + '" y2="' + y + '" stroke="var(--border-faint,#eee)" stroke-width="1"/>';
    svg += '<text x="' + (PAD - 4) + '" y="' + (y + 4) + '" text-anchor="end" font-size="10" fill="var(--text-secondary, #94a3b8)">' + (100 - g * 25) + '%</text>';
  }
  series.forEach(function (s) {
    const key = s[0], color = s[1];
    if (!seriesHasData(s)) return; // 无数据序列不画线
    let path = '', penDown = false;
    points.forEach(function (p) {
      if (!hasValue(p.t[key])) { penDown = false; return; } // 数据缺口断开连线
      const val = Number(p.t[key]);
      const y = H - PAD - val * (H - PAD * 2);
      path += (penDown ? 'L' : 'M') + p.x.toFixed(1) + ' ' + y.toFixed(1) + ' ';
      penDown = true;
    });
    if (path) {
      svg += '<path d="' + path + '" fill="none" stroke="' + color + '" stroke-width="2" stroke-linecap="round"/>';
    }
  });
  // 图例（无数据序列追加「暂无数据」标注）
  svg += '<g transform="translate(' + PAD + ',' + (H - 8) + ')">';
  let lx = 0;
  series.forEach(function (s) {
    const noData = !seriesHasData(s);
    const label = s[2] + (noData ? '（暂无数据）' : '');
    svg += '<rect x="' + lx + '" y="-8" width="10" height="10" fill="' + s[1] + '" rx="2"/>';
    svg += '<text x="' + (lx + 14) + '" y="0" font-size="10" fill="var(--text-secondary,#666)">' + label + '</text>';
    lx += 14 + label.length * 10 + 16;
  });
  svg += '</g></svg>';
  block.innerHTML = svg;
}

// ── 台账不符明细表 ──
function renderLedgerMismatches(host, data) {
  const block = el(host, 'kpiLedgerMismatches');
  if (!block) return;
  const mm = (data && data.ledger_mismatches) || [];
  if (mm.length === 0) {
    block.innerHTML = '<div class="kpi-empty">台账无不一致记录（真值缺失不计入不符）</div>';
    return;
  }
  let html = '<table class="kpi-mm-table"><thead><tr><th>case_id</th><th>target</th><th>状态</th><th>更新</th><th>处置</th><th>真值说明</th></tr></thead><tbody>';
  mm.forEach(function (r) {
    const st = (r.status || 'open');
    const tone = ['open', 'fixing', 'recheck', 'closed'].indexOf(st) >= 0 ? st : 'open';
    html += '<tr>' +
      '<td>' + A.escHtml(r.case_id || '') + '</td>' +
      '<td>' + A.escHtml(r.target || '') + '</td>' +
      '<td><span class="kpi-mm-tag ' + tone + '">' + A.escHtml(st.toUpperCase()) + '</span></td>' +
      '<td>' + A.escHtml((r.updated_at || '').replace('T', ' ').substring(0, 19)) + '</td>' +
      '<td>' + A.escHtml(r.disposition || '') + '</td>' +
      '<td>' + A.escHtml((r.truth_detail || '').substring(0, 60)) + '</td>' +
      '</tr>';
  });
  html += '</tbody></table>';
  block.innerHTML = html;
}

// ── 统一渲染入口：把 overview 数据渲染进 hostEl 内的 4 个区块 ──
window._renderUnifiedKpi = function (host, data) {
  if (!host || !data) return;
  // ★ 项目卡片优先显示云端 display_name，与顶栏/控制台一致；无 display_name 时回退代号。
  const projEl = el(host, 'kpiProject');
  if (projEl) projEl.textContent = A._currentProjectName || A._currentProject || data.project || '-';
  const runEl = el(host, 'kpiRunId');
  if (runEl) runEl.textContent = data.run_id || '-';
  renderCards(host, data);
  renderDistribution(host, data);
  renderTrend(host, data);
  renderLedgerMismatches(host, data);
};

// ── 惰性加载：首次激活拉取一次并缓存；重复激活只重渲不强刷；force=true 强制刷新 ──
let _data = null;
let _loading = false;

window._loadUnifiedKpi = function (host, force) {
  if (!host) return;
  if (_data && !force) {
    window._renderUnifiedKpi(host, _data);
    return;
  }
  if (_loading) return;
  _loading = true;
  A.api('GET', '/api/kpi/overview?project=' + encodeURIComponent(currentProject()))
    .then(function (d) {
      _loading = false;
      if (!d || d.ok !== true) {
        if (A.toast) A.toast((d && d.error) || 'KPI 加载失败', 'error');
        return;
      }
      _data = d;
      window._renderUnifiedKpi(host, d);
    })
    ['catch'](function (e) {
      _loading = false;
      if (A.toast) A.toast('KPI 加载异常: ' + String(e), 'error');
    });
};

})();