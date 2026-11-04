/**
 * docs-workbench.js — 文档中心 5 Tab 工作台（T03 / P1-2）
 * ======================================================
 *
 * 设计约束（AGENTS.md）：
 *   1. 复用 app-common.js 的 `A.*` 公共库，**不重复造轮子**；
 *      `window._A` 在本文件执行时已由 _layout_base.html 先行加载，
 *      但仍统一走 `lib()` 惰性取值，避免加载顺序变化后静默失效。
 *   2. 所有拼进 innerHTML 的动态数据**必须**经 `esc()`（= A.escHtml）转义 —— XSS 红线。
 *   3. 禁止 CDN（CSP default-src 'self'），图形一律 CSS/内联 SVG 手写。
 *   4. 空数据必须显示占位态，不允许留白。
 *   5. Tab 懒加载：面板首次激活才发请求，避免一次打开 20 个接口。
 *
 * 覆盖的后端接口（20 个，全部 docs_bp，返回体为「裸 payload / {ok:false,error}」约定）：
 *   Tab2 追溯   : /api/docs/traceability/matrix, /api/docs/traceability,
 *                 /api/docs/traceability/coverage, /api/trace/req/<id>
 *   Tab3 覆盖   : /api/docs/coverage, /api/trace/case/<id>, /api/docs/executors
 *   Tab4 冒烟   : /api/docs/generate-smoke/status, /api/docs/smoke-intents?check=1,
 *                 /api/docs/smoke-intent-files, /api/docs/intent-asset-files,
 *                 /api/docs/breakdown?check=1, /api/docs/generate-smoke (POST)
 *   Tab5 版本   : /api/docs/versions, /api/docs/version/<id>, /api/docs/diff/report,
 *                 /api/docs/diff/impact, /api/docs/rag/stats,
 *                 /api/docs/tool-execution-log, /api/docs/figma-stats
 */
(function () {
  'use strict';

  // ══════════════════════════════════════════════════════════
  // 0. A.* 公共库绑定
  // ══════════════════════════════════════════════════════════
  // _layout_base.html 在 {% block scripts %} **之前**加载 app-common.js，
  // 因此 window._A 到这里必定就绪。沿用本项目既有约定（见 ai-health.html）
  // 做一次显式守卫后直接调用，不再为每个 API 写降级分支 —— 那些分支永远
  // 走不到，只会掩盖「公共库没加载」这种必须暴露的部署事故。
  if (!window._A) { console.error('[docs-workbench] _A 未初始化，工作台不启动'); return; }

  /** 取公共库命名空间（惰性取值，容忍后续加载顺序调整）。 */
  function lib() { return window._A; }

  function $(id) { return lib().$(id); }
  /** HTML 文本转义 —— 所有动态内容进 innerHTML 前必过此函数（XSS 红线）。 */
  function esc(s) { return lib().escHtml(s); }
  /** 属性值转义。 */
  function escA(s) { return lib().escAttr(s); }
  /** GET 走 dedupedApi 去重，写操作走 api。 */
  function api(method, url, body) {
    var L = lib();
    return (String(method).toUpperCase() === 'GET' ? L.dedupedApi : L.api)(method, url, body);
  }
  function toast(msg, type) { lib().toast(msg, type); }
  /** 骨架屏（注意：A.skeleton 是「写 innerHTML」而非「返回串」）。 */
  function skel(el, preset) { lib().skeleton(el, preset || 'list'); }
  function hasPerm(p) { return lib().hasPerm(p); }
  /** 动态节点插入后需重扫 [data-perm] 才能生效显隐。 */
  function reapplyPerms() { lib().applyPermissions(); }

  // ══════════════════════════════════════════════════════════
  // 1. 通用小工具
  // ══════════════════════════════════════════════════════════

  /** 后端 docs_bp 错误约定：成功=裸 payload，失败={ok:false,error}。 */
  function isErr(d) { return !d || d.ok === false; }

  /** 取错误文案。 */
  function errMsg(d, fallback) {
    return (d && d.error) ? String(d.error) : (fallback || '加载失败');
  }

  /** 统一占位态。 */
  function emptyHtml(msg, sub) {
    var h = '<div class="drilldown-empty">' + esc(msg || '暂无数据');
    if (sub) h += '<div style="margin-top:6px;font-size:var(--fs-xs);opacity:0.75;">' + esc(sub) + '</div>';
    return h + '</div>';
  }

  /** 写入占位态。 */
  function setEmpty(el, msg, sub) {
    if (el) el.innerHTML = emptyHtml(msg, sub);
  }

  /** 百分比（0 除法安全）。 */
  function pct(part, total) {
    var t = Number(total) || 0;
    if (t <= 0) return 0;
    return Math.round((Number(part) || 0) / t * 1000) / 10;
  }

  /** 覆盖率条 HTML（数值已转义；class 由数值推导，非用户输入）。 */
  function coverageBar(value) {
    var v = Math.max(0, Math.min(100, Number(value) || 0));
    var cls = v >= 80 ? '' : (v >= 50 ? ' is-warn' : ' is-bad');
    return '<div class="coverage-bar">' +
      '<div class="coverage-bar-track"><div class="coverage-bar-fill' + cls +
      '" style="width:' + v + '%;"></div></div>' +
      '<span class="coverage-bar-value">' + esc(v.toFixed(1)) + '%</span></div>';
  }

  /** 字节数人类可读。 */
  function fmtBytes(n) {
    var b = Number(n) || 0;
    if (b <= 0) return '0 B';
    var units = ['B', 'KB', 'MB', 'GB'];
    var i = 0;
    while (b >= 1024 && i < units.length - 1) { b /= 1024; i += 1; }
    return (i === 0 ? String(Math.round(b)) : b.toFixed(1)) + ' ' + units[i];
  }

  /** 计数单元格：0 → 标红（未覆盖信号）。 */
  function countCell(n) {
    var v = Number(n) || 0;
    var cls = v > 0 ? 'trace-cell-covered' : 'trace-cell-uncovered';
    return '<td class="' + cls + '">' + esc(String(v)) + '</td>';
  }

  /**
   * 渲染统计卡片行。cards = [[label, value, colorCssVar?], ...]。
   * color 为常量（非用户输入），故不经 esc。
   */
  function statCards(cards) {
    var h = '<div class="docs-stats-row">';
    (cards || []).forEach(function (c) {
      h += '<div class="docs-stat-card"><div class="docs-stat-label">' + esc(c[0]) +
        '</div><div class="docs-stat-value" style="color:' +
        (c[2] || 'var(--color-primary)') + ';">' + esc(String(c[1])) + '</div></div>';
    });
    return h + '</div>';
  }

  // ══════════════════════════════════════════════════════════
  // 2. 下钻抽屉（与 diagnostics 复用同一套 .drilldown-* 样式）
  // ══════════════════════════════════════════════════════════

  /** 关闭抽屉。 */
  function closeDrawer() {
    var d = $('docsDrawer');
    var b = $('docsDrawerBackdrop');
    if (d) d.classList.remove('open');
    if (b) b.classList.remove('open');
  }

  /**
   * 打开抽屉并异步填充。
   * @param {string} title 抽屉标题（会被转义）
   * @param {function(HTMLElement):void} filler 拿到 body 元素后自行渲染
   */
  function openDrawer(title, filler) {
    var d = $('docsDrawer');
    var b = $('docsDrawerBackdrop');
    var t = $('docsDrawerTitle');
    var body = $('docsDrawerBody');
    if (!d || !body) return;
    if (t) t.textContent = String(title == null ? '' : title);
    skel(body, 'list');
    d.classList.add('open');
    if (b) b.classList.add('open');
    if (typeof filler === 'function') filler(body);
  }

  // ══════════════════════════════════════════════════════════
  // 3. Tab 调度（懒加载）
  // ══════════════════════════════════════════════════════════

  var LOADERS = {};          // tabName -> loader fn
  var loadedTabs = { files: true };  // files 面板由 docs.html 自身的 initDocs() 负责

  /**
   * 激活指定 Tab，首次激活时触发对应 loader。
   * @param {string} name Tab 标识
   */
  function activateTab(name) {
    var bar = $('docsTabBar');
    if (!bar) return;
    var items = bar.querySelectorAll('.tab-bar-item');
    var i;
    for (i = 0; i < items.length; i += 1) {
      items[i].classList.toggle('active', items[i].getAttribute('data-tab') === name);
      items[i].setAttribute('aria-selected', items[i].getAttribute('data-tab') === name ? 'true' : 'false');
    }
    var panels = document.querySelectorAll('.tab-panel[data-tab-panel]');
    for (i = 0; i < panels.length; i += 1) {
      panels[i].classList.toggle('active', panels[i].getAttribute('data-tab-panel') === name);
    }
    if (!loadedTabs[name] && typeof LOADERS[name] === 'function') {
      loadedTabs[name] = true;   // 先置位，避免重复点击并发打请求
      LOADERS[name]();
    }
  }

  /** 强制重载当前 Tab（刷新按钮用）。 */
  function reloadTab(name) {
    if (typeof LOADERS[name] === 'function') {
      loadedTabs[name] = true;
      LOADERS[name]();
    }
  }

  // ══════════════════════════════════════════════════════════
  // 4. Tab2 — 需求追溯矩阵（未覆盖标红 + 横向滚动 + 首列冻结）
  // ══════════════════════════════════════════════════════════

  var _matrixRows = [];       // 合并后的矩阵行缓存，供「仅看未覆盖」本地过滤
  var CONF_LABEL = { high: '高', medium: '中', low: '低' };

  /**
   * 合并 traceability/matrix（API/DB/页面数）与 coverage（用例数）。
   * @param {Array} matrix
   * @param {Array} coverage
   * @returns {Array} 行数组
   */
  function mergeMatrix(matrix, coverage) {
    var byReq = {};
    var i, m, c, rid;
    for (i = 0; i < (matrix || []).length; i += 1) {
      m = matrix[i] || {};
      rid = String(m.req_id || '');
      if (!rid) continue;
      byReq[rid] = {
        req_id: rid,
        module: String(m.module || ''),
        api_count: Number(m.api_count) || 0,
        db_count: Number(m.db_count) || 0,
        page_count: Number(m.page_count) || 0,
        confidence: String(m.confidence || ''),
        intent_count: 0,
        desc: ''
      };
    }
    for (i = 0; i < (coverage || []).length; i += 1) {
      c = coverage[i] || {};
      rid = String(c.req_id || '');
      if (!rid) continue;
      if (!byReq[rid]) {
        byReq[rid] = {
          req_id: rid, module: String(c.module || ''), api_count: 0, db_count: 0,
          page_count: 0, confidence: '', intent_count: 0, desc: ''
        };
      }
      byReq[rid].intent_count = Number(c.intent_count) || 0;
      byReq[rid].desc = String(c.desc || '');
      if (!byReq[rid].module) byReq[rid].module = String(c.module || '');
    }
    var rows = [];
    Object.keys(byReq).sort().forEach(function (k) { rows.push(byReq[k]); });
    return rows;
  }

  /** 一行是否存在未覆盖维度。 */
  function rowUncovered(r) {
    return !r.api_count || !r.db_count || !r.page_count || !r.intent_count;
  }

  /** 渲染追溯矩阵表格。 */
  function renderMatrix() {
    var host = $('traceMatrixHost');
    if (!host) return;
    var onlyGap = !!($('traceOnlyGap') && $('traceOnlyGap').checked);
    var rows = onlyGap ? _matrixRows.filter(rowUncovered) : _matrixRows;

    if (!rows.length) {
      setEmpty(host, onlyGap ? '没有未覆盖的需求' : '暂无追溯数据',
        onlyGap ? '所有需求的 API / DB / 页面 / 用例四个维度均已覆盖' : '请先运行 Step2 生成需求追溯链路');
      return;
    }

    var h = '<div class="trace-matrix-scroll"><table class="trace-matrix"><thead><tr>' +
      '<th class="trace-req">需求 ID</th>' +
      '<th>模块</th><th>API 接口</th><th>DB 表</th><th>设计页面</th><th>测试用例</th>' +
      '<th>置信度</th><th>四维覆盖</th><th>需求摘要</th><th>操作</th>' +
      '</tr></thead><tbody>';

    rows.forEach(function (r) {
      var dims = [r.api_count, r.db_count, r.page_count, r.intent_count];
      var hit = dims.filter(function (n) { return Number(n) > 0; }).length;
      var gap = rowUncovered(r);
      h += '<tr class="' + (gap ? 'trace-row-uncovered' : '') + '">';
      h += '<th class="trace-req" title="' + escA(r.req_id) + '">' + esc(r.req_id) + '</th>';
      h += '<td>' + esc(r.module || '—') + '</td>';
      h += countCell(r.api_count);
      h += countCell(r.db_count);
      h += countCell(r.page_count);
      h += countCell(r.intent_count);
      h += '<td>' + esc(CONF_LABEL[r.confidence] || r.confidence || '—') + '</td>';
      h += '<td style="min-width:150px;">' + coverageBar(hit / 4 * 100) + '</td>';
      h += '<td style="max-width:320px;overflow:hidden;text-overflow:ellipsis;">' +
        esc(r.desc || '—') + '</td>';
      h += '<td><button type="button" class="btn btn-sm trace-drill-btn" data-req="' +
        escA(r.req_id) + '">链路</button></td>';
      h += '</tr>';
    });
    h += '</tbody></table></div>';
    host.innerHTML = h;
  }

  /** 渲染追溯汇总（数据源维度覆盖率）。 */
  function renderTraceSummary(cov, linkCount) {
    var host = $('traceSummaryHost');
    if (!host) return;
    var s = (cov && cov.summary) ? cov.summary : null;
    if (!s && !linkCount) {
      setEmpty(host, '暂无覆盖汇总', '需先运行 Step2 建立追溯链路');
      return;
    }
    host.innerHTML = statCards([
      ['追溯链路数', String(linkCount || 0)],
      ['API 覆盖', s ? esc(String(s.api_coverage_rate != null ? s.api_coverage_rate : '—')) : '—'],
      ['DB 覆盖', s ? esc(String(s.db_coverage_rate != null ? s.db_coverage_rate : '—')) : '—'],
      ['页面覆盖', s ? esc(String(s.page_coverage_rate != null ? s.page_coverage_rate : '—')) : '—']
    ]);
  }

  /** Tab2 加载器。 */
  function loadTrace() {
    var host = $('traceMatrixHost');
    var sum = $('traceSummaryHost');
    skel(host, 'list');
    skel(sum, 'kpi');
    Promise.all([
      api('GET', '/api/docs/traceability/matrix'),
      api('GET', '/api/docs/coverage'),
      api('GET', '/api/docs/traceability/coverage'),
      api('GET', '/api/docs/traceability')
    ]).then(function (res) {
      var mx = res[0], cv = res[1], tc = res[2], tr = res[3];
      var matrix = (!isErr(mx) && mx.matrix) ? mx.matrix : [];
      var coverage = (!isErr(cv) && cv.coverage) ? cv.coverage : [];
      _matrixRows = mergeMatrix(matrix, coverage);
      renderMatrix();
      renderTraceSummary(isErr(tc) ? null : tc, (!isErr(tr) && tr.count) ? tr.count : 0);
      if (isErr(mx) && isErr(cv)) {
        setEmpty(host, '追溯数据加载失败', errMsg(mx, errMsg(cv, '')));
      }
    }).catch(function () {
      setEmpty(host, '追溯数据加载失败', '请稍后重试');
      setEmpty(sum, '暂无数据', '');
    });
  }
  LOADERS.trace = loadTrace;

  /** 需求链路下钻。 */
  function drillReq(reqId) {
    openDrawer('需求链路 · ' + reqId, function (body) {
      api('GET', '/api/trace/req/' + encodeURIComponent(reqId)).then(function (d) {
        if (isErr(d)) { setEmpty(body, '链路加载失败', errMsg(d)); return; }
        body.innerHTML = renderTraceChain(d);
      });
    });
  }

  /** 用例链路下钻。 */
  function drillCase(caseId) {
    openDrawer('用例链路 · ' + caseId, function (body) {
      api('GET', '/api/trace/case/' + encodeURIComponent(caseId)).then(function (d) {
        if (isErr(d)) { setEmpty(body, '链路加载失败', errMsg(d)); return; }
        body.innerHTML = renderTraceChain(d);
      });
    });
  }

  /** 链路详情统一渲染（req / case 两种响应共用字段）。 */
  function renderTraceChain(d) {
    var sections = [
      ['需求', d.requirement],
      ['测试用例', d.test_case],
      ['需求切片', d.slice],
      ['追溯链路', d.trace_link]
    ];
    var lists = [
      ['关联测试用例', d.test_cases],
      ['Figma 页面', d.figma_pages],
      ['API 端点', d.api_endpoints],
      ['数据库表', d.db_tables],
      ['DOM 快照', d.dom_snapshots]
    ];
    var h = '';
    sections.forEach(function (s) {
      if (!s[1] || typeof s[1] !== 'object') return;
      h += '<div style="margin-bottom:var(--space-4);"><div class="drilldown-title" style="margin-bottom:6px;">' +
        esc(s[0]) + '</div><table class="drilldown-table"><tbody>';
      Object.keys(s[1]).slice(0, 24).forEach(function (k) {
        var v = s[1][k];
        if (v && typeof v === 'object') v = JSON.stringify(v);
        h += '<tr><th style="width:130px;">' + esc(k) + '</th><td class="dd-reason">' +
          esc(v == null ? '—' : String(v)) + '</td></tr>';
      });
      h += '</tbody></table></div>';
    });
    lists.forEach(function (s) {
      var arr = s[1];
      if (!Array.isArray(arr) || !arr.length) return;
      h += '<div style="margin-bottom:var(--space-4);"><div class="drilldown-title" style="margin-bottom:6px;">' +
        esc(s[0]) + ' (' + esc(String(arr.length)) + ')</div><table class="drilldown-table"><tbody>';
      arr.slice(0, 40).forEach(function (it) {
        var label;
        if (it && typeof it === 'object') {
          label = it.name || it.title || it.case_id || it.endpoint || it.table_name ||
            it.page_name || it.id || JSON.stringify(it);
        } else {
          label = it;
        }
        h += '<tr><td class="dd-reason">' + esc(String(label == null ? '—' : label)) + '</td></tr>';
      });
      h += '</tbody></table></div>';
    });
    return h || emptyHtml('该对象暂无关联链路', '可能尚未运行 Step2 追溯建链');
  }

  // ══════════════════════════════════════════════════════════
  // 5. Tab3 — 覆盖缺口
  // ══════════════════════════════════════════════════════════

  var COV_STATUS = { ok: '充分', low: '偏低', gap: '缺口' };

  /** Tab3 加载器。 */
  function loadCoverage() {
    var host = $('covTableHost');
    var sum = $('covSummaryHost');
    skel(host, 'list');
    skel(sum, 'kpi');
    Promise.all([
      api('GET', '/api/docs/coverage'),
      api('GET', '/api/docs/executors')
    ]).then(function (res) {
      var cv = res[0], ex = res[1];
      if (isErr(cv)) {
        setEmpty(sum, '覆盖数据不可用', errMsg(cv));
        setEmpty(host, '暂无覆盖数据', '请先执行 Step2 需求拆解');
        return;
      }
      renderCovSummary(cv, (ex && ex.executors) ? ex.executors : []);
      renderCovTable(cv.coverage || []);
    }).catch(function () {
      setEmpty(sum, '覆盖数据不可用', '请稍后重试');
      setEmpty(host, '暂无覆盖数据', '数据加载失败');
    });
  }
  LOADERS.coverage = loadCoverage;

  /** 覆盖汇总卡片。 */
  function renderCovSummary(cv, executors) {
    var host = $('covSummaryHost');
    if (!host) return;
    var total = Number(cv.total_reqs) || 0;
    var covered = Number(cv.covered) || 0;
    var cards = [
      ['需求总数', String(total), 'var(--color-primary)'],
      ['已覆盖', String(covered), 'var(--state-success)'],
      ['未覆盖', String(Number(cv.uncovered) || 0), 'var(--state-error)'],
      ['覆盖偏低', String(Number(cv.low_count) || 0), 'var(--state-warning)'],
      ['可用执行人', String((executors || []).length), 'var(--step5-color)']
    ];
    host.innerHTML = statCards(cards) +
      '<div style="margin-top:var(--space-3);max-width:420px;">' +
      coverageBar(pct(covered, total)) + '</div>';
  }

  /** 覆盖明细表（缺口标红）。 */
  function renderCovTable(list) {
    var host = $('covTableHost');
    if (!host) return;
    var onlyGap = !!($('covOnlyGap') && $('covOnlyGap').checked);
    var rows = onlyGap
      ? (list || []).filter(function (r) { return r && r.status !== 'ok'; })
      : (list || []);
    if (!rows.length) {
      setEmpty(host, onlyGap ? '没有覆盖缺口' : '暂无需求数据',
        onlyGap ? '全部需求的用例数均 ≥ 3' : '请先执行 Step2 需求拆解');
      return;
    }
    var h = '<div class="trace-matrix-scroll"><table class="trace-matrix"><thead><tr>' +
      '<th class="trace-req">需求 ID</th><th>模块</th><th>用例数</th><th>状态</th>' +
      '<th>风险</th><th>AI 建议</th><th>需求摘要</th>' +
      '</tr></thead><tbody>';
    rows.forEach(function (r) {
      var gap = r.status !== 'ok';
      h += '<tr class="' + (gap ? 'trace-row-uncovered' : '') + '">';
      h += '<th class="trace-req">' + esc(r.req_id) + '</th>';
      h += '<td>' + esc(r.module || '—') + '</td>';
      h += countCell(r.intent_count);
      h += '<td class="' + (gap ? 'trace-cell-uncovered' : 'trace-cell-covered') + '">' +
        esc(COV_STATUS[r.status] || r.status || '—') + '</td>';
      h += '<td>' + esc(r.risk || '—') + '</td>';
      h += '<td style="max-width:220px;white-space:normal;">' + esc(r.advice || '—') + '</td>';
      h += '<td style="max-width:340px;overflow:hidden;text-overflow:ellipsis;">' +
        esc(r.desc || '—') + '</td>';
      h += '</tr>';
    });
    h += '</tbody></table></div>';
    host.innerHTML = h;
  }

  // ══════════════════════════════════════════════════════════
  // 6. Tab4 — 冒烟与资产
  // ══════════════════════════════════════════════════════════

  /** Tab4 加载器。 */
  function loadSmoke() {
    var host = $('smokeHost');
    skel(host, 'card');
    Promise.all([
      api('GET', '/api/docs/generate-smoke/status'),
      api('GET', '/api/docs/smoke-intents?check=1'),
      api('GET', '/api/docs/smoke-intent-files'),
      api('GET', '/api/docs/intent-asset-files'),
      api('GET', '/api/docs/breakdown?check=1')
    ]).then(function (res) {
      renderSmoke({
        status: isErr(res[0]) ? {} : res[0],
        check: isErr(res[1]) ? {} : res[1],
        smokeFiles: (res[2] && res[2].files) ? res[2].files : [],
        assetFiles: (res[3] && res[3].files) ? res[3].files : [],
        breakdown: isErr(res[4]) ? {} : res[4]
      });
    }).catch(function () {
      setEmpty(host, '冒烟数据分析失败', '请稍后重试');
    });
  }
  LOADERS.smoke = loadSmoke;

  /** 文件列表片段。 */
  function fileListHtml(title, files, emptyTip) {
    var h = '<div class="docs-panel-header" style="margin-top:var(--space-3);"><span>' +
      esc(title) + '</span><span class="docs-panel-hint">' +
      esc(String((files || []).length) + ' 个') + '</span></div>';
    if (!files || !files.length) return h + emptyHtml('暂无文件', emptyTip);
    h += '<table class="drilldown-table"><thead><tr><th>文件名</th><th>大小</th><th>更新时间</th></tr></thead><tbody>';
    files.slice(0, 50).forEach(function (f) {
      h += '<tr><td>' + esc(f.name || '—') + '</td><td>' + esc(fmtBytes(f.size)) +
        '</td><td>' + esc(f.mtime || '—') + '</td></tr>';
    });
    return h + '</tbody></table>';
  }

  /** 渲染冒烟面板。 */
  function renderSmoke(d) {
    var host = $('smokeHost');
    if (!host) return;
    var st = d.status || {};
    var ck = d.check || {};
    var cards = [
      ['冒烟用例数', String(Number(st.count) || 0), 'var(--color-primary)'],
      ['生成状态', st.done ? '已完成' : (st.error ? '失败' : '待生成'),
        st.done ? 'var(--state-success)' : (st.error ? 'var(--state-error)' : 'var(--text-tertiary)')],
      ['缓存 Excel', ck.exists ? '已就绪' : '未生成',
        ck.exists ? 'var(--state-success)' : 'var(--text-tertiary)'],
      ['需求拆解表', d.breakdown && d.breakdown.exists
        ? (String(Number(d.breakdown.size) || 0) + ' 条') : '未生成', 'var(--step2-color)'],
      ['版本标签', String(st.version_label || '—'), 'var(--step5-color)']
    ];
    var h = statCards(cards);

    if (st.error) {
      h += '<div class="drilldown-empty" style="color:var(--state-error-text);">生成错误：' +
        esc(String(st.error)) + '</div>';
    }

    h += '<div style="margin-top:var(--space-4);display:flex;gap:var(--space-2);flex-wrap:wrap;">' +
      '<button type="button" class="btn btn-sm" id="btnGenSmoke" data-perm="execution.run">重新生成冒烟用例</button>' +
      '<a class="btn btn-sm" href="/api/docs/smoke-intents" download data-perm="docs.view">下载冒烟 Excel</a>' +
      '<a class="btn btn-sm" href="/api/docs/breakdown" download data-perm="docs.view">下载需求拆解表</a>' +
      '<a class="btn btn-sm" href="/api/docs/feature-list" download data-perm="docs.view">下载功能清单</a>' +
      '<a class="btn btn-sm" href="/api/docs/execution-stats?format=xlsx" download data-perm="docs.export">导出执行统计</a>' +
      '</div>';

    h += fileListHtml('冒烟用例文件', d.smokeFiles, '生成冒烟用例后此处会出现文件');
    h += fileListHtml('测试用例资产', d.assetFiles, '执行 Step3 生成测试用例后此处会出现文件');
    host.innerHTML = h;
    reapplyPerms();   // 动态插入的 [data-perm] 需重扫才生效
  }

  /** 触发冒烟生成。 */
  function triggerGenSmoke(btn) {
    if (!hasPerm('execution.run')) { toast('无执行权限，无法生成冒烟用例', 'error'); return; }
    if (!window.confirm('将在后台重新生成冒烟用例 Excel，可能覆盖现有缓存。确定继续？')) return;
    var run = function () {
      return api('POST', '/api/docs/generate-smoke', {}).then(function (d) {
        if (isErr(d)) { toast(errMsg(d, '生成失败'), 'error'); return; }
        toast(d.hint || '冒烟用例已在后台生成', 'success');
        window.setTimeout(function () { reloadTab('smoke'); }, 1500);
      });
    };
    var wrap = lib().withBtnLoading;
    if (wrap && btn) { wrap(btn, '生成中...', run); } else { run(); }
  }

  // ══════════════════════════════════════════════════════════
  // 7. Tab5 — 版本与变更
  // ══════════════════════════════════════════════════════════

  /** Tab5 加载器。 */
  function loadVersions() {
    var host = $('verHost');
    skel(host, 'card');
    var docType = ($('verDocType') && $('verDocType').value) || 'requirement';
    Promise.all([
      api('GET', '/api/docs/versions?type=' + encodeURIComponent(docType)),
      api('GET', '/api/docs/diff/report'),
      api('GET', '/api/docs/diff/impact'),
      api('GET', '/api/docs/rag/stats'),
      api('GET', '/api/docs/figma-stats'),
      api('GET', '/api/docs/tool-execution-log?limit=30')
    ]).then(function (res) {
      renderVersions({
        versions: (!isErr(res[0]) && res[0].versions) ? res[0].versions : [],
        diff: isErr(res[1]) ? {} : res[1],
        impact: isErr(res[2]) ? {} : res[2],
        rag: isErr(res[3]) ? {} : res[3],
        figma: isErr(res[4]) ? {} : res[4],
        toolLog: (res[5] && res[5].items) ? res[5].items : []
      });
    }).catch(function () {
      setEmpty(host, '版本数据加载失败', '请稍后重试');
    });
  }
  LOADERS.versions = loadVersions;

  /** 渲染版本面板。 */
  function renderVersions(d) {
    var host = $('verHost');
    if (!host) return;
    var rag = d.rag || {};
    var fig = d.figma || {};
    var cards = [
      ['历史版本', String((d.versions || []).length), 'var(--color-primary)'],
      ['RAG 分片', String(Number(rag.chunk_count) || 0), 'var(--step2-color)'],
      ['向量数', String(Number(rag.vector_count) || 0), 'var(--step3-color)'],
      ['Figma 页面', String(Number(fig.page_count) || 0), 'var(--step1-color)'],
      ['指令日志', String((d.toolLog || []).length), 'var(--step4-color)']
    ];
    var h = statCards(cards);

    // ── 版本列表 ──
    h += '<div class="docs-panel-header" style="margin-top:var(--space-4);"><span>文档版本</span>' +
      '<span class="docs-panel-hint">点击查看版本内容</span></div>';
    if (!(d.versions || []).length) {
      h += emptyHtml('暂无历史版本', '文档保存后会自动生成版本快照');
    } else {
      h += '<table class="drilldown-table"><thead><tr><th>版本</th><th>创建时间</th>' +
        '<th>操作人</th><th>备注</th><th>操作</th></tr></thead><tbody>';
      d.versions.slice(0, 40).forEach(function (v) {
        var vid = v.version_id || v.id || '';
        h += '<tr><td>' + esc(v.version_label || v.version || vid || '—') + '</td>' +
          '<td>' + esc(v.created_at || v.mtime || '—') + '</td>' +
          '<td>' + esc(v.operator || v.created_by || '—') + '</td>' +
          '<td class="dd-reason">' + esc(v.note || v.remark || '—') + '</td>' +
          '<td>' + (vid
            ? '<button type="button" class="btn btn-sm ver-view-btn" data-vid="' + escA(vid) +
              '">查看</button>'
            : '—') + '</td></tr>';
      });
      h += '</tbody></table>';
    }

    // ── 变更对比 ──
    h += '<div class="docs-panel-header" style="margin-top:var(--space-4);"><span>变更对比</span>' +
      '<span class="docs-panel-hint">' + esc(d.diff && d.diff.timestamp ? String(d.diff.timestamp) : '') +
      '</span></div>';
    if (d.diff && d.diff.message) {
      h += emptyHtml(String(d.diff.message), '');
    } else if (d.diff && Array.isArray(d.diff.entries) && d.diff.entries.length) {
      h += '<table class="drilldown-table"><thead><tr><th>实体类型</th><th>实体 ID</th>' +
        '<th>变更类型</th><th>详情</th></tr></thead><tbody>';
      d.diff.entries.slice(0, 50).forEach(function (e) {
        var det = e.details;
        if (det && typeof det === 'object') det = JSON.stringify(det);
        h += '<tr><td>' + esc(e.entity_type || '—') + '</td><td>' + esc(e.entity_id || '—') +
          '</td><td>' + esc(e.change_type || '—') + '</td><td class="dd-reason">' +
          esc(det == null ? '—' : String(det)) + '</td></tr>';
      });
      h += '</tbody></table>';
    } else {
      h += emptyHtml('无变更记录', '需至少两个版本快照才能对比');
    }

    // ── 影响分析 ──
    var imp = d.impact || {};
    h += '<div class="docs-panel-header" style="margin-top:var(--space-4);"><span>影响分析</span>' +
      '<span class="docs-panel-hint">' + esc(imp.severity ? ('严重度 ' + imp.severity) : '') +
      '</span></div>';
    if (imp.message || (!imp.severity && !(imp.affected_requirements || []).length)) {
      h += emptyHtml(String(imp.message || '暂无影响分析'), '需存在历史快照与追溯链路');
    } else {
      h += '<table class="drilldown-table"><tbody>' +
        '<tr><th style="width:140px;">受影响需求</th><td class="dd-reason">' +
        esc((imp.affected_requirements || []).join('、') || '—') + '</td></tr>' +
        '<tr><th>受影响用例</th><td class="dd-reason">' +
        esc((imp.affected_test_cases || []).join('、') || '—') + '</td></tr>' +
        '<tr><th>处置建议</th><td class="dd-reason">' +
        esc((imp.suggestions || []).join('；') || '—') + '</td></tr>' +
        '</tbody></table>';
    }

    // ── 指令执行日志 ──
    h += '<div class="docs-panel-header" style="margin-top:var(--space-4);"><span>指令执行日志</span>' +
      '<span class="docs-panel-hint">最近 30 条</span></div>';
    if (!(d.toolLog || []).length) {
      h += emptyHtml('暂无指令日志', '执行测试后会记录 tool_call 明细');
    } else {
      h += '<div class="trace-matrix-scroll" style="max-height:320px;">' +
        '<table class="drilldown-table"><thead><tr><th>时间</th><th>用例</th><th>工具</th>' +
        '<th>结果</th><th>耗时</th><th>模型</th></tr></thead><tbody>';
      d.toolLog.forEach(function (it) {
        var okFlag = Number(it.result_ok) === 1 || it.result_ok === true;
        h += '<tr><td>' + esc(it.created_at || '—') + '</td><td>' + esc(it.case_id || '—') +
          '</td><td>' + esc(it.tool_name || '—') + '</td>' +
          '<td class="' + (okFlag ? 'trace-cell-covered' : 'trace-cell-uncovered') + '">' +
          esc(okFlag ? '成功' : '失败') + '</td>' +
          '<td>' + esc(String(Number(it.elapsed_ms) || 0) + ' ms') + '</td>' +
          '<td>' + esc(it.model || '—') + '</td></tr>';
      });
      h += '</tbody></table></div>';
    }
    host.innerHTML = h;
  }

  /** 版本内容下钻。 */
  function drillVersion(vid) {
    var docType = ($('verDocType') && $('verDocType').value) || 'requirement';
    openDrawer('版本内容 · ' + vid, function (body) {
      api('GET', '/api/docs/version/' + encodeURIComponent(vid) +
        '?type=' + encodeURIComponent(docType)).then(function (d) {
        if (isErr(d)) { setEmpty(body, '版本内容加载失败', errMsg(d)); return; }
        var content = d.content == null ? '' : String(d.content);
        if (!content) { setEmpty(body, '该版本内容为空', ''); return; }
        body.innerHTML = '<div style="font-size:var(--fs-xs);color:var(--text-tertiary);' +
          'margin-bottom:8px;">大小 ' + esc(fmtBytes(d.size)) + '</div>' +
          '<pre style="white-space:pre-wrap;word-break:break-word;font-family:var(--font-mono);' +
          'font-size:var(--fs-xs);line-height:1.6;margin:0;">' + esc(content) + '</pre>';
      });
    });
  }

  // ══════════════════════════════════════════════════════════
  // 8. 事件绑定与初始化
  // ══════════════════════════════════════════════════════════

  /** 绑定一次性的事件委托。 */
  function bindEvents() {
    var bar = $('docsTabBar');
    if (bar) {
      bar.addEventListener('click', function (ev) {
        var btn = ev.target.closest ? ev.target.closest('.tab-bar-item') : null;
        if (!btn || !bar.contains(btn)) return;
        var name = btn.getAttribute('data-tab');
        if (name) activateTab(name);
      });
    }

    // 表格内按钮 / 下钻（事件委托到 document，兼容动态渲染）
    document.addEventListener('click', function (ev) {
      var t = ev.target;
      if (!t || !t.closest) return;
      var drill = t.closest('.trace-drill-btn');
      if (drill) { drillReq(drill.getAttribute('data-req') || ''); return; }
      var caseBtn = t.closest('.case-drill-btn');
      if (caseBtn) { drillCase(caseBtn.getAttribute('data-case') || ''); return; }
      var verBtn = t.closest('.ver-view-btn');
      if (verBtn) { drillVersion(verBtn.getAttribute('data-vid') || ''); return; }
      var gen = t.closest('#btnGenSmoke');
      if (gen) { triggerGenSmoke(gen); return; }
      if (t.closest('#docsDrawerClose') || t.closest('#docsDrawerBackdrop')) {
        closeDrawer();
      }
    });

    // 「仅看未覆盖」本地过滤（不重新请求）
    var tGap = $('traceOnlyGap');
    if (tGap) tGap.addEventListener('change', renderMatrix);
    var cGap = $('covOnlyGap');
    if (cGap) cGap.addEventListener('change', function () { reloadTab('coverage'); });

    // 版本类型切换 → 重载 Tab5
    var vt = $('verDocType');
    if (vt) vt.addEventListener('change', function () { reloadTab('versions'); });

    // 各面板刷新按钮
    ['trace', 'coverage', 'smoke', 'versions'].forEach(function (name) {
      var b = $('btnReload_' + name);
      if (b) b.addEventListener('click', function () { reloadTab(name); });
    });

    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape') closeDrawer();
    });
  }

  /** 初始化工作台（幂等）。 */
  var _inited = false;
  function init() {
    if (_inited) return;
    if (!$('docsTabBar')) return;   // 非文档中心页面，直接跳过
    _inited = true;
    bindEvents();
    activateTab('files');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // 暴露最小接口供页面内联脚本调用（例如切项目后强制刷新）
  window.DocsWorkbench = {
    init: init,
    activate: activateTab,
    reload: reloadTab,
    closeDrawer: closeDrawer
  };
})();
