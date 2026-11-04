/* ════════════════════════════════════════════════════════════════
   T04 / P1-4 + P1-5 — W3 质量治理面板
   ────────────────────────────────────────────────────────────────
   数据源（★ 全部 GET，只读）：
     GET /api/quality/w3-summary?caps=...   D1 九类能力只读摘要
     GET /api/config/flags                  D2 开关明细（含 source/locked/danger）
   唯一写操作：
     POST /api/config/toggle                D2 开关切换（用户显式确认后才发）

   红线（AGENTS.md / 设计 §8.9）：
     · 本页绝不触发任何执行行为，不调用任何 run_* / enforce_* 端点；
     · 所有动态文本一律 textContent 落地，全文件零 innerHTML（防线 3）；
     · 图表全部手写 SVG（createElementNS），无任何外部库（CSP default-src 'self'）；
     · 配色只用 CSS 变量，不硬编码色值（§8.4）。
   ════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  var A = window._A;
  if (!A) return;

  var SVG_NS = 'http://www.w3.org/2000/svg';

  // 只拉本面板实际要展示的 7 个能力（privilege_matrix / identity_pool 不在 5 类卡片内，
  // 不请求可省掉两次 DB 查询）。顺序与后端 CAP_ORDER 一致。
  var WANTED_CAPS = [
    'fake_pass', 'engine_health', 'perf_baseline', 'adi',
    'contract', 'network_fault', 'idempotency',
  ];

  // 页面状态。flags 为 /api/config/flags 的 flags 明细快照，是开关 UI 的唯一事实源。
  var state = {
    flags: {},
    summary: {},
    busy: {},
  };

  // ══════════════════════════════════════════════════════
  // 基础 DOM 构件（全部 textContent，杜绝注入）
  // ══════════════════════════════════════════════════════

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = String(text);
    return node;
  }

  function clear(node) {
    while (node && node.firstChild) node.removeChild(node.firstChild);
  }

  /** 一组「标签 + 数值」指标块。values: [[label, value], ...] */
  function metricsRow(values) {
    var wrap = el('div', 'quality-metrics');
    for (var i = 0; i < values.length; i++) {
      var cell = el('div');
      cell.appendChild(el('div', 'quality-metric-label', values[i][0]));
      cell.appendChild(el('div', 'quality-metric-value', values[i][1]));
      wrap.appendChild(cell);
    }
    return wrap;
  }

  function badge(text, tone) {
    return el('span', 'quality-card-badge' + (tone ? ' ' + tone : ''), text);
  }

  function pct(v) {
    var n = Number(v);
    if (!isFinite(n)) return '—';
    return (n * 100).toFixed(2) + '%';
  }

  // ══════════════════════════════════════════════════════
  // 手写 SVG 图元（无外部库）
  // ══════════════════════════════════════════════════════

  function svgEl(name, attrs) {
    var node = document.createElementNS(SVG_NS, name);
    if (attrs) {
      for (var k in attrs) {
        if (Object.prototype.hasOwnProperty.call(attrs, k)) {
          node.setAttribute(k, String(attrs[k]));
        }
      }
    }
    return node;
  }

  function svgRoot(w, h) {
    var svg = svgEl('svg', {
      viewBox: '0 0 ' + w + ' ' + h,
      width: '100%',
      height: h,
      preserveAspectRatio: 'none',
      role: 'img',
    });
    return svg;
  }

  /**
   * 手写柱状图。series: [{label, value, color}]，color 传 CSS 变量名。
   * 纵轴按最大值归一；全零时画基线而非空白（但调用方应先判空走引导态）。
   */
  function svgBar(series, opts) {
    opts = opts || {};
    var W = 300, H = opts.height || 96;
    var padB = 18, padT = 6;
    var svg = svgRoot(W, H);
    if (!series || series.length === 0) return svg;

    var max = 0;
    for (var i = 0; i < series.length; i++) {
      var v = Number(series[i].value) || 0;
      if (v > max) max = v;
    }
    var slot = W / series.length;
    var barW = Math.max(6, Math.min(38, slot * 0.5));
    var plotH = H - padB - padT;

    for (var j = 0; j < series.length; j++) {
      var val = Number(series[j].value) || 0;
      var h = max > 0 ? Math.max(1, (val / max) * plotH) : 1;
      var cx = slot * j + slot / 2;
      svg.appendChild(svgEl('rect', {
        x: cx - barW / 2, y: padT + (plotH - h), width: barW, height: h,
        rx: 2, fill: series[j].color || 'var(--color-primary)',
      }));
      var cap = svgEl('text', {
        x: cx, y: H - 6, 'text-anchor': 'middle',
        'font-size': 9, fill: 'var(--text-tertiary)',
      });
      cap.textContent = String(series[j].label);
      svg.appendChild(cap);
      var num = svgEl('text', {
        x: cx, y: padT + (plotH - h) - 2, 'text-anchor': 'middle',
        'font-size': 9, fill: 'var(--text-secondary)',
      });
      num.textContent = String(series[j].display != null ? series[j].display : val);
      svg.appendChild(num);
    }
    return svg;
  }

  /**
   * 手写折线图。points: [{label, value}]，用于性能基线趋势对比。
   * 单点时退化为一个圆点（不画线），避免出现"看起来是趋势其实只有一个样本"的误导。
   */
  function svgLine(points, opts) {
    opts = opts || {};
    var W = 300, H = opts.height || 96;
    var padL = 4, padR = 4, padT = 8, padB = 18;
    var svg = svgRoot(W, H);
    if (!points || points.length === 0) return svg;

    var max = 0, min = Infinity;
    for (var i = 0; i < points.length; i++) {
      var v = Number(points[i].value) || 0;
      if (v > max) max = v;
      if (v < min) min = v;
    }
    if (!isFinite(min)) min = 0;
    var span = (max - min) || 1;
    var plotW = W - padL - padR;
    var plotH = H - padT - padB;
    var stepX = points.length > 1 ? plotW / (points.length - 1) : 0;

    function xy(idx) {
      var v = Number(points[idx].value) || 0;
      return {
        x: padL + (points.length > 1 ? stepX * idx : plotW / 2),
        y: padT + plotH - ((v - min) / span) * plotH,
      };
    }

    if (points.length > 1) {
      var d = '';
      for (var k = 0; k < points.length; k++) {
        var p = xy(k);
        d += (k === 0 ? 'M' : 'L') + p.x.toFixed(1) + ' ' + p.y.toFixed(1) + ' ';
      }
      svg.appendChild(svgEl('path', {
        d: d.trim(), fill: 'none',
        stroke: opts.color || 'var(--color-primary)', 'stroke-width': 1.6,
      }));
    }
    for (var m = 0; m < points.length; m++) {
      var q = xy(m);
      svg.appendChild(svgEl('circle', {
        cx: q.x, cy: q.y, r: 2.6,
        fill: points[m].color || opts.color || 'var(--color-primary)',
      }));
      if (points[m].label) {
        var t = svgEl('text', {
          x: q.x, y: H - 6, 'text-anchor': 'middle',
          'font-size': 9, fill: 'var(--text-tertiary)',
        });
        t.textContent = String(points[m].label);
        svg.appendChild(t);
      }
    }
    return svg;
  }

  // ══════════════════════════════════════════════════════
  // 引导态（验收 #6 / #7）
  // ══════════════════════════════════════════════════════

  /**
   * 渲染「该能力未启用，点此开启」引导态。
   * ★ 验收 #6：绝不显示 0、绝不报错 —— 未启用时后端只回 {enabled,flag_key,reason}，
   *   这里也只呈现文字与开启入口，不渲染任何数值。
   * opts.flags: [{key,label}]，每个入口各自携带自己的 flag_key 调 toggle（验收 #7）。
   */
  function renderEmptyState(host, opts) {
    opts = opts || {};
    var box = el('div', 'quality-empty');
    box.appendChild(el('div', null, opts.message || '该能力未启用'));

    var flags = opts.flags || [];
    for (var i = 0; i < flags.length; i++) {
      (function (f) {
        var meta = state.flags[f.key] || {};
        var btn = el('button', 'quality-empty-action');
        btn.type = 'button';
        btn.setAttribute('data-flag-key', f.key);
        if (meta.locked) {
          btn.textContent = (f.label ? f.label + '：' : '') + '被环境变量锁定，无法在此开启';
          btn.disabled = true;
          A.tooltip(btn, '该开关由环境变量提供，页面上的修改不会生效');
        } else {
          btn.textContent = flags.length > 1
            ? '点此开启「' + f.label + '」'
            : '点此开启';
          btn.addEventListener('click', function () {
            onToggle(f.key, true, btn);
          });
        }
        box.appendChild(btn);
      }(flags[i]));
    }

    if (opts.hint) box.appendChild(el('div', 'quality-note', opts.hint));
    host.appendChild(box);
  }

  /**
   * 已启用卡片的「关闭此能力」入口（补足"开启后如何关闭"的引导缺口）。
   * 与顶部开关区走同一 onToggle → 回读校验 → renderToggles/loadSummary 流程，
   * 关闭后卡片与顶部开关会同步刷新（同一 state.flags 单一事实源）。
   */
  function renderCardOffAction(host, cap) {
    var b = state.summary[cap] || {};
    var flagKey = CAP_FLAGS[cap] || b.flag_key;
    if (!flagKey) return;
    var meta = state.flags[flagKey] || {};
    var btn = el('button', 'quality-off-action');
    btn.type = 'button';
    btn.setAttribute('data-flag-key', flagKey);
    if (meta.locked) {
      btn.textContent = '该开关被环境变量锁定，无法关闭';
      btn.disabled = true;
      A.tooltip(btn, '该开关由环境变量提供，页面上的修改不会生效');
    } else {
      btn.textContent = '关闭' + (CAP_LABELS[cap] ? '「' + CAP_LABELS[cap] + '」' : '');
      btn.addEventListener('click', function () {
        onToggle(flagKey, false, btn);
      });
    }
    host.appendChild(btn);
  }

  // ══════════════════════════════════════════════════════
  // 5 类卡片定义
  // ══════════════════════════════════════════════════════

  var CARD_DEFS = [
    {
      id: 'fake_pass', title: '假通过审计', caps: ['fake_pass'],
      body: function (host, b) {
        var alert = !!b.alert;
        host.appendChild(metricsRow([
          ['PASS 总数', b.total_passes],
          ['疑似假 PASS', b.fake_passes],
          ['占比', pct(b.rate)],
          ['阈值', pct(b.threshold)],
        ]));
        var real = Math.max(0, (Number(b.total_passes) || 0) - (Number(b.fake_passes) || 0));
        var chart = el('div', 'quality-chart');
        chart.appendChild(svgBar([
          { label: '真实 PASS', value: real, color: 'var(--state-success)' },
          { label: '疑似假 PASS', value: Number(b.fake_passes) || 0,
            color: alert ? 'var(--state-error)' : 'var(--state-warning)' },
        ]));
        host.appendChild(chart);
        return { text: alert ? '超阈值' : '正常', tone: alert ? 'bad' : 'ok' };
      },
    },
    {
      id: 'engine_health', title: '引擎健康', caps: ['engine_health'],
      body: function (host, b) {
        var verdict = String(b.verdict || 'GREEN');
        host.appendChild(metricsRow([
          ['失败总数', b.total],
          ['框架错误', b.harness_error],
          ['占比', pct(b.harness_rate)],
          ['阈值', pct(b.threshold)],
        ]));
        var chart = el('div', 'quality-chart');
        var patterns = b.patterns || [];
        if (patterns.length > 0) {
          var series = [];
          for (var i = 0; i < Math.min(5, patterns.length); i++) {
            series.push({
              label: String(patterns[i].likely_module || 'other').slice(0, 8),
              value: Number(patterns[i].count) || 0,
              color: 'var(--state-critical)',
            });
          }
          chart.appendChild(svgBar(series));
        } else {
          var other = Math.max(0, (Number(b.total) || 0) - (Number(b.harness_error) || 0));
          chart.appendChild(svgBar([
            { label: '业务失败', value: other, color: 'var(--state-warning)' },
            { label: '框架错误', value: Number(b.harness_error) || 0, color: 'var(--state-error)' },
          ]));
        }
        host.appendChild(chart);
        var tone = verdict === 'GREEN' ? 'ok' : (verdict === 'RED' ? 'bad' : 'warn');
        return { text: verdict, tone: tone };
      },
    },
    {
      id: 'perf_baseline', title: '性能基线', caps: ['perf_baseline'],
      body: function (host, b) {
        var items = b.items || [];
        var degraded = Number(b.degraded_count) || 0;
        host.appendChild(metricsRow([
          ['对比窗口', b.window],
          ['监测接口', items.length],
          ['退化接口', degraded],
        ]));
        var pts = [];
        for (var i = 0; i < Math.min(8, items.length); i++) {
          var it = items[i];
          pts.push({
            label: String(i + 1),
            value: Number(it.current_mean) || 0,
            color: String(it.verdict) === 'degraded'
              ? 'var(--state-error)' : 'var(--color-primary)',
          });
        }
        var chart = el('div', 'quality-chart');
        chart.appendChild(svgLine(pts));
        host.appendChild(chart);
        host.appendChild(el('div', 'quality-note', '折线为各接口当前均值（ms），红点表示较基线退化 >20%'));
        return degraded > 0
          ? { text: '退化 ' + degraded, tone: 'bad' }
          : { text: '无退化', tone: 'ok' };
      },
    },
    {
      id: 'adi', title: 'ADI 断言深度分布', caps: ['adi'],
      body: function (host, b) {
        var dist = b.depth_distribution || [0, 0, 0, 0];
        host.appendChild(metricsRow([
          ['参与用例', b.total_cases],
          ['平均 ADI', b.mean],
          ['解析失败', b.unresolved],
        ]));
        var chart = el('div', 'quality-chart');
        chart.appendChild(svgBar([
          { label: 'ADI 0', value: Number(dist[0]) || 0, color: 'var(--state-error)' },
          { label: 'ADI 1', value: Number(dist[1]) || 0, color: 'var(--state-warning)' },
          { label: 'ADI 2', value: Number(dist[2]) || 0, color: 'var(--state-info)' },
          { label: 'ADI 3', value: Number(dist[3]) || 0, color: 'var(--state-success)' },
        ]));
        host.appendChild(chart);
        if (b.truncated) {
          host.appendChild(el('div', 'quality-note',
            '已达单次扫描上限 ' + b.scan_limit + ' 条，分布为抽样结果'));
        }
        var mean = Number(b.mean) || 0;
        return { text: '均值 ' + b.mean, tone: mean >= 2 ? 'ok' : (mean >= 1 ? 'warn' : 'bad') };
      },
    },
    // 契约 / 幂等 / 网络故障 —— 三张独立卡，各自独一开关（不再合并成一张组合卡）
    {
      id: 'contract', title: '契约断言', caps: ['contract'],
      body: function (host) {
        host.appendChild(el('div', 'quality-note',
          '开关态校验能力：覆盖面/命中明细见执行记录与报告，此处仅展示启用状态。'));
      },
    },
    {
      id: 'idempotency', title: '幂等比对', caps: ['idempotency'],
      body: function (host) {
        host.appendChild(el('div', 'quality-note',
          '开关态校验能力：重复执行比对结果见 Step5 报告幂等区块，此处仅展示启用状态。'));
      },
    },
    {
      id: 'network_fault', title: '网络故障注入', caps: ['network_fault'],
      body: function (host) {
        host.appendChild(el('div', 'quality-note',
          '开关态校验能力：故障注入/恢复见 Step4 执行记录，此处仅展示启用状态。'));
      },
    },
  ];

  var CAP_LABELS = {
    fake_pass: '假 PASS 审计',
    engine_health: '引擎健康治理',
    perf_baseline: '性能基线',
    adi: 'ADI 断言深度',
    contract: '契约断言',
    idempotency: '幂等比对',
    network_fault: '网络故障注入',
  };

  // 能力卡 → 开关 key（与后端 TOGGLEABLE_FLAGS / CAP_FLAGS 保持一致的映射）。
  // ★ 单一事实源修复(2026-09-01)：卡片「启用 / 未启用」状态必须与顶部开关区
  //   共用 _state.flags 这一份数据（开关 UI 的唯一事实源），绝不再用 w3-summary
  //   里独立返回的 enabled 判定 —— 否则两份数据不同请求（各自欠 30s GET 缓存、
  //   各自不同缓存 key）可能错位，导致「能力卡显示开启、开关区显示关闭（反之亦然）」。
  var CAP_FLAGS = {
    fake_pass: 'FAKE_PASS_AUDIT_ENABLED',
    engine_health: 'ENGINE_HEALTH_ENABLED',
    perf_baseline: 'PERF_BASELINE_ENABLED',
    adi: 'ADI_GATE_ENABLED',
    contract: 'CONTRACT_ENABLED',
    idempotency: 'IDEMPOTENCY_ENABLED',
    network_fault: 'NETWORK_FAULT_ENABLED',
  };

  // ══════════════════════════════════════════════════════
  // 卡片渲染（验收 #5 / #6）
  // ══════════════════════════════════════════════════════

  /** 渲染单张卡片。返回值仅用于内部调试，实际 DOM 直接挂到 grid 上。 */
  function renderCard(def) {
    var card = el('div', 'quality-card');
    card.setAttribute('data-card', def.id);
    var head = el('div', 'quality-card-head');
    head.appendChild(el('div', 'quality-card-title', def.title));
    card.appendChild(head);

    var body = el('div');
    body.style.display = 'contents';
    card.appendChild(body);

    var status = renderSingleCard(body, def);
    if (status && status.text) head.appendChild(badge(status.text, status.tone));
    return card;
  }

  /** 单能力卡片：依次判「未启用 → 采集异常 → 无数据 → 正常」四态。
   *  ★ 单一事实源：启用态一律以 _state.flags（与开关区同一份数据）判定，
   *    w3-summary 只负责「已启用时」的统计数据与采集异常，不负责启用/未启用判定。 */
  function renderSingleCard(host, def) {
    var cap = def.caps[0];
    var b = state.summary[cap];
    // 从开关单一事实源推断启用态（与顶部开关区完全一致，杜绝跨接口错位）
    var flagKey = CAP_FLAGS[cap] || (b && b.flag_key);
    var flagMeta = flagKey ? state.flags[flagKey] : null;
    var enabled = !!(flagMeta && flagMeta.value);

    if (!b) {
      if (flagKey && !enabled) {
        renderEmptyState(host, {
          message: '该能力未启用，点此开启',
          flags: [{ key: flagKey, label: CAP_LABELS[cap] || cap }],
        });
        return { text: '未启用', tone: '' };
      }
      renderEmptyState(host, { message: '尚未取到该能力数据' });
      return { text: '—', tone: '' };
    }
    if (!enabled) {
      renderEmptyState(host, {
        message: '该能力未启用，点此开启',
        flags: flagKey ? [{ key: flagKey, label: CAP_LABELS[cap] || cap }] : [],
        hint: b.reason || '',
      });
      return { text: '未启用', tone: '' };
    }
    if (b.error) {
      renderEmptyState(host, { message: '数据采集异常', hint: b.error });
      renderCardOffAction(host, cap);
      return { text: '采集异常', tone: 'bad' };
    }
    if (!b.has_data) {
      renderEmptyState(host, {
        message: '能力已启用，但当前项目/运行暂无可统计数据',
        hint: '执行一次流水线后回到本页即可看到统计结果',
      });
      renderCardOffAction(host, cap);
      return { text: '暂无数据', tone: '' };
    }
    var st = def.body(host, b);
    renderCardOffAction(host, cap);
    return st;
  }

  function renderCards() {
    var grid = A.$('qualityGrid');
    if (!grid) return;
    clear(grid);
    for (var i = 0; i < CARD_DEFS.length; i++) {
      grid.appendChild(renderCard(CARD_DEFS[i]));
    }
  }

  // ══════════════════════════════════════════════════════
  // 开关区（验收 #7 ~ #11）
  // ══════════════════════════════════════════════════════

  function makeSwitch(key, meta) {
    var sw = el('button', 'toggle-switch' + (meta.value ? ' on' : ''));
    sw.type = 'button';
    sw.setAttribute('data-flag-key', key);
    sw.setAttribute('role', 'switch');
    sw.setAttribute('aria-checked', meta.value ? 'true' : 'false');
    sw.setAttribute('aria-label', (meta.label || key) + ' 开关');
    if (meta.locked) {
      // 验收 #9：env 锁定 → 置灰 + tooltip 说明
      sw.classList.add('locked');
      sw.disabled = true;
      A.tooltip(sw, '被环境变量锁定：该开关由环境变量提供，页面修改不会生效');
    } else {
      sw.addEventListener('click', function () {
        onToggle(key, !meta.value, sw);
      });
    }
    return sw;
  }

  function makeToggleRow(key, meta) {
    var row = el('div', 'toggle-row');
    row.setAttribute('data-flag-row', key);
    var left = el('div', 'toggle-row-label');
    left.appendChild(el('span', 'toggle-name', meta.label || key));
    if (meta.danger) {
      var dot = el('span', 'toggle-danger-dot', '⚠');
      A.tooltip(dot, '高风险开关：变更会影响门禁判定或引擎行为');
      left.appendChild(dot);
    }
    if (meta.source) left.appendChild(el('span', 'toggle-src', meta.source));
    row.appendChild(left);
    row.appendChild(makeSwitch(key, meta));
    return row;
  }

  /** 按 group 分组渲染全部开关。 */
  function renderToggles() {
    var host = A.$('toggleArea');
    if (!host) return;
    clear(host);
    var keys = Object.keys(state.flags);
    if (keys.length === 0) {
      host.appendChild(el('div', 'quality-note', '未取到开关列表'));
      return;
    }
    var groups = {}, order = [];
    for (var i = 0; i < keys.length; i++) {
      var g = state.flags[keys[i]].group || '其他';
      if (!groups[g]) { groups[g] = []; order.push(g); }
      groups[g].push(keys[i]);
    }
    for (var j = 0; j < order.length; j++) {
      host.appendChild(el('div', 'quality-toggle-group-title', order[j]));
      var grid = el('div', 'quality-toggle-grid');
      var list = groups[order[j]];
      for (var k = 0; k < list.length; k++) {
        grid.appendChild(makeToggleRow(list[k], state.flags[list[k]]));
      }
      host.appendChild(grid);
    }
  }

  /** 构造二次确认文案。danger 开关追加红色警示（验收 #10 由 A.confirm 渲染）。 */
  function confirmToggle(key, meta, next) {
    var action = next ? '开启' : '关闭';
    var opts = {
      danger: !!meta.danger,
      okText: action,
      detail: '配置键：' + key + '　当前来源：' + (meta.source || 'default'),
    };
    if (meta.danger) {
      opts.title = '高风险开关变更确认';
      opts.warning = '「' + (meta.label || key) + '」属于高风险开关：'
        + '变更后会直接影响门禁判定与引擎执行行为，可能改变后续运行的通过/拦截结论。'
        + '请确认已知晓影响范围再继续。';
    }
    return A.confirm('确定要' + action + '「' + (meta.label || key) + '」吗？', opts);
  }

  /**
   * 开关切换主流程（验收 #7 / #8 / #11）。
   * ★ 关键约定：POST 成功不等于生效 —— toggle 写的是 runtime 层，会被 user_db / env
   *   压制（§8.6）。因此必须回读 /api/config/flags 校验，一致才更新 UI，
   *   不一致回滚并 toast 报错，绝不让用户以为"点了就生效了"。
   */
  function onToggle(key, next, srcEl) {
    var meta = state.flags[key];
    if (!meta) { A.toast('未知开关：' + key, 'error'); return; }
    if (meta.locked) { A.toast('该开关被环境变量锁定，无法在此修改', 'warning'); return; }
    if (state.busy[key]) return;

    confirmToggle(key, meta, next).then(function (agreed) {
      if (!agreed) return;
      state.busy[key] = true;
      if (srcEl) { srcEl.classList.add('busy'); srcEl.disabled = true; }
      return A.api('POST', '/api/config/toggle', { key: key, value: !!next })
        .then(function (resp) {
          if (!resp || resp.ok !== true) {
            A.toast((resp && resp.error) || '开关切换失败', 'error');
            return null;
          }
          if (resp.warning) A.toast(resp.warning, 'warning');
          // 回读校验（验收 #8）
          return A.api('GET', '/api/config/flags').then(function (fresh) {
            return verifyToggle(key, !!next, fresh);
          });
        })
        .finally(function () {
          state.busy[key] = false;
          if (srcEl) { srcEl.classList.remove('busy'); srcEl.disabled = false; }
        });
    });
  }

  /** 回读比对：一致才刷新 UI，不一致回滚并报错。 */
  function verifyToggle(key, desired, fresh) {
    var flags = (fresh && fresh.flags) || null;
    if (!flags || !flags[key]) {
      A.toast('回读 /api/config/flags 失败，界面已回滚到上一状态', 'error');
      renderToggles();
      return false;
    }
    state.flags = flags;
    var actual = !!flags[key].value;
    renderToggles();
    if (actual !== desired) {
      // 不一致：runtime 写入被更高优先级层压制，如实告知，不静默欺骗
      A.toast('切换未生效：期望 ' + desired + '，实际 ' + actual
        + '（来源 ' + (flags[key].source || 'default') + '），已回滚显示', 'error');
      return false;
    }
    A.toast('「' + (flags[key].label || key) + '」已' + (desired ? '开启' : '关闭'), 'success');
    loadSummary();
    return true;
  }

  // ══════════════════════════════════════════════════════
  // 数据加载（★ 全部 GET）
  // ══════════════════════════════════════════════════════

  function loadFlags() {
    return A.dedupedApi('GET', '/api/config/flags').then(function (r) {
      state.flags = (r && r.flags) || {};
      renderToggles();
      return state.flags;
    });
  }

  /** 拉取 W3 只读摘要。整体恒 200，异常已由后端降级为块内 error 字段。 */
  function loadSummary() {
    var grid = A.$('qualityGrid');
    if (grid && grid.children.length === 0) A.skeleton(grid, 'card');
    var url = '/api/quality/w3-summary?caps=' + encodeURIComponent(WANTED_CAPS.join(','));
    return A.api('GET', url).then(function (r) {
      if (!r || r.ok !== true) {
        state.summary = {};
        renderCards();
        A.toast((r && r.error) || '质量摘要加载失败', 'error');
        return;
      }
      state.summary = r.summary || {};
      renderCards();
      var stamp = A.$('genTime');
      if (stamp) stamp.textContent = r.generated_at || '—';
    });
  }

  // ══════════════════════════════════════════════════════
  // ★ T2 流水线强化专项：Step3 方法论标签校验标记
  // ══════════════════════════════════════════════════════

  function renderMethodology(r) {
    var host = A.$('methodologyArea');
    if (!host) return;
    clear(host);
    if (!r || r.ok !== true) {
      host.appendChild(el('div', 'quality-note', (r && r.error) || '方法论校验数据加载失败'));
      return;
    }
    var stats = r.stats || {};
    var head = el('div', 'methodology-summary');
    head.appendChild(el('span', 'methodology-stat', '总数 ' + (stats.total || 0)));
    head.appendChild(el('span', 'methodology-stat methodology-stat-fixed', '已自动修正 ' + (stats.auto_fixed || 0)));
    head.appendChild(el('span', 'methodology-stat methodology-stat-blocked', '高危阻断 ' + (stats.blocked || 0)));
    host.appendChild(head);

    var items = r.items || [];
    if (items.length === 0) {
      host.appendChild(el('div', 'quality-note', '暂无非法/已修正标签记录（全部通过白名单校验）'));
      return;
    }
    var list = el('div', 'methodology-list');
    items.forEach(function (it) {
      var row = el('div', 'methodology-row');
      var bad = it.method_valid === 0;
      var tag = el('span', 'methodology-tag ' + (bad ? 'methodology-tag-blocked' : 'methodology-tag-fixed'),
        bad ? 'BLOCKED' : 'AUTO_FIX');
      row.appendChild(tag);
      row.appendChild(el('span', 'methodology-case', it.case_id || ''));
      row.appendChild(el('span', 'methodology-note', it.method_fix_note || ''));
      list.appendChild(row);
    });
    host.appendChild(list);
  }

  function loadMethodology() {
    return A.api('GET', '/api/quality/methodology-validation').then(function (r) {
      renderMethodology(r);
    }).catch(function (e) {
      var host = A.$('methodologyArea');
      if (host) {
        clear(host);
        host.appendChild(el('div', 'quality-note', '方法论校验加载异常: ' + String(e)));
      }
    });
  }

  /** 首屏：先取开关（卡片引导态依赖 locked 判定），再取摘要。 */
  function init() {
    loadFlags().then(loadSummary);
    loadMethodology();
    var btn = A.$('btnRefreshQuality');
    if (btn) {
      btn.addEventListener('click', function () {
        A.withBtnLoading(btn, '刷新中', function () {
          return loadFlags().then(loadSummary).then(loadMethodology);
        });
      });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // 供单测/调试使用的最小导出面（不污染全局其他命名空间）
  window._QualityPanel = {
    svgBar: svgBar,
    svgLine: svgLine,
    renderEmptyState: renderEmptyState,
    renderCard: renderCard,
    renderToggles: renderToggles,
    onToggle: onToggle,
    loadSummary: loadSummary,
    _state: state,
  };
}());
