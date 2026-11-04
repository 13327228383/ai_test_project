/* ============================================================================
 * metrics-def.js —— KPI 单一定义源（T05 / P2-1）
 * ----------------------------------------------------------------------------
 * 背景：同一个 KPI 曾在 dashboard.html / charts.html / metrics.html 三处各写一遍，
 *       导致同名指标的**阈值与单位互相打架**。P2-1 之前的实测差异：
 *
 *   ┌ 用例通过率  charts.html:656 判 ≥90 绿  ·  metrics.html:289 判 ≥80 绿
 *   ┌ Flaky 率    dashboard 提示 <5/5-15/≥15 三档  ·  metrics.html:105 只有 <5 两档
 *   ┌ 需求覆盖率  dashboard 提示 ≥90 正常  ·  metrics.html:382 判 ≥80 绿（同一数据源！）
 *
 * 本文件把「口径」从模板里抽出来集中定义。三处模板只负责取值与渲染，
 * **不再自带任何阈值数字**。新增指标 / 调整阈值 → 只改本文件一处。
 *
 * 依赖：无。纯 ES5，挂 window.MetricsDef，与 app-common.js 的 `A` 同级并列。
 * 约束：禁止外部 CDN；本文件不发起任何网络请求，只描述「取哪里、怎么判」。
 * ==========================================================================*/
(function (global) {
  'use strict';

  /** 统一数据源：/api/metrics-summary 一次性返回 13 域，优先走它。 */
  var EP_SUMMARY = '/api/metrics-summary';
  /** charts 页专用的图表聚合接口（仅在 summary 缺字段时兜底）。 */
  var EP_CHART = '/api/report/chart-data';

  /**
   * KPI 定义表。字段语义：
   *   key        程序内唯一标识
   *   label      全站统一展示名（三处必须一致，这是"同名 KPI"的定义依据）
   *   unit       单位；'%' | '' | 'ms' | 'token'
   *   thresholds { dir, good, warn }
   *              dir='up'   → 值越大越好，>=good 绿、>=warn 黄、否则红
   *              dir='down' → 值越小越好，<=good 绿、<=warn 黄、否则红
   *              null       → 纯计数类，不做健康度着色
   *   endpoint   首选数据源
   *   path       在 endpoint 响应体中的取值路径（点号分隔）
   *   fallback   { endpoint, path } 首选缺失时的兜底源，可选
   *   desc       Tooltip 文案，统一三段式：是什么 / 怎么算 / 大小意义
   */
  var DEFS = {
    /* ── 规模类（无阈值，纯计数） ── */
    total_cases: {
      key: 'total_cases', label: '总用例', unit: '', thresholds: null,
      endpoint: EP_CHART, path: 'overview.total',
      desc: '总用例\n是什么：当前项目 Step3 已建模的测试用例总数（含正向+反向+安全类）\n怎么算：SELECT COUNT(*) FROM test_cases WHERE project=当前项目\n大小意义：参考值，决定本次执行的用例规模'
    },
    passed_cases: {
      key: 'passed_cases', label: '通过', unit: '', thresholds: null,
      endpoint: EP_CHART, path: 'overview.passed',
      desc: '通过用例数\n是什么：Step4 执行后结论为 PASS 的用例数量（含 Flaky 重试后通过）\n怎么算：SELECT COUNT(*) FROM executions WHERE run_id=当前run AND status="passed"\n大小意义：配合"用例通过率"一起看，绝对值参考用'
    },
    failed_cases: {
      key: 'failed_cases', label: '失败', unit: '', thresholds: null,
      endpoint: EP_CHART, path: 'overview.failed',
      desc: '失败用例数\n是什么：Step4 执行后结论为 FAIL 的用例数量（排除 SKIP/ERROR）\n怎么算：SELECT COUNT(*) FROM executions WHERE run_id=当前run AND status="failed"\n大小意义：0 为理想值，>0 需逐条查看失败原因（靶子缺陷 vs 引擎缺陷）'
    },
    skipped_cases: {
      key: 'skipped_cases', label: '跳过', unit: '', thresholds: null,
      endpoint: EP_CHART, path: 'overview.skipped',
      desc: '跳过用例数\n是什么：因前置条件不满足（如靶子未提供某模块）而未执行的用例数\n怎么算：SELECT COUNT(*) FROM executions WHERE run_id=当前run AND status="skipped"\n大小意义：应尽量为 0；持续出现需检查靶子配置或数据源完整性'
    },

    /* ── 通过率族（★ P2-1 统一：全部 ≥90 绿 / ≥70 黄） ── */
    case_pass_rate: {
      key: 'case_pass_rate', label: '用例通过率', unit: '%',
      thresholds: { dir: 'up', good: 90, warn: 70 },
      endpoint: EP_SUMMARY, path: 'step4_plugins.case_pass_rate',
      fallback: { endpoint: EP_CHART, path: 'overview.pass_rate' },
      desc: '用例通过率\n是什么：Step4 执行阶段结束后，通过用例占全部已执行用例的比例\n怎么算：通过用例数 / 已执行用例总数 × 100%（数据源 ai_health_metrics step=4）\n大小意义：≥90% 绿（校验可靠），70~90% 黄（部分用例有失败），<70% 红（需排查失败根因）'
    },
    step_pass_rate: {
      key: 'step_pass_rate', label: '步骤通过率', unit: '%',
      thresholds: { dir: 'up', good: 90, warn: 70 },
      endpoint: EP_SUMMARY, path: 'step4_plugins.step_pass_rate',
      desc: '步骤通过率\n是什么：Step4 执行阶段中，单步操作（click/fill/navigate/assert）成功完成的比例\n怎么算：通过步骤数 / 总步骤数 × 100%（每个用例含多步，逐 step 聚合）\n大小意义：≥90% 绿（执行引擎稳定），70~90% 黄（部分步骤失败，可能环境问题），<70% 红（需排查执行器稳定性）'
    },
    recipe_only_pass_rate: {
      key: 'recipe_only_pass_rate', label: 'Recipe-only 通过率', unit: '%',
      thresholds: { dir: 'up', good: 90, warn: 70 },
      endpoint: EP_SUMMARY, path: 'step4_plugins.recipe_only_pass_rate',
      desc: 'Recipe-only 通过率\n是什么："Recipe 缓存"命中后仅靠历史记录就能执行通过的用例占比（不经过 AI 重新推理）\n怎么算：recipe_only_pass 用例数 / 总用例数 × 100%\n大小意义：≥90% 绿（操作缓存丰富，引擎效率高），<70% 红（缓存命中率低，需丰富操作指令库）'
    },

    /* ── 稳定性（越小越好） ── */
    flaky_rate: {
      key: 'flaky_rate', label: 'Flaky 率', unit: '%',
      thresholds: { dir: 'down', good: 5, warn: 15 },
      endpoint: EP_SUMMARY, path: 'ai_overall.flaky.rate',
      fallback: { endpoint: EP_CHART, path: 'overview.flaky_rate' },
      desc: 'Flaky 率\n是什么：同一用例在相同条件下重复执行出现"有时通过有时失败"不稳定现象的比例\n怎么算：标记为 flaky 的用例数 / 总用例数 × 100%（需至少 3 次运行记录才可判定）\n大小意义：<5% 绿（环境稳定），5~15% 黄（部分用例受时序/资源影响），≥15% 红（环境或引擎不稳定）'
    },
    three_track_inconsistency: {
      key: 'three_track_inconsistency', label: '三轨不一致率', unit: '%',
      thresholds: { dir: 'down', good: 10, warn: 20 },
      endpoint: EP_SUMMARY, path: 'step4_plugins.verification_disagree_rate',
      desc: '三轨不一致率\n是什么：Step4 同时走 UI（页面观察）、API（接口返回）、DB（数据库直接查询）三条校验轨道，出现任意两条结论不一致的比例\n怎么算：disagreement 次数 / 总校验次数 × 100%\n大小意义：<10% 绿（数据一致性良好），10~20% 黄（需排查哪一轨有延迟），≥20% 红（靶子存在严重数据不一致）'
    },

    /* ── 覆盖率族（★ P2-1 统一：同一数据源只保留一个口径） ── */
    req_coverage: {
      key: 'req_coverage', label: '需求覆盖率', unit: '%',
      thresholds: { dir: 'up', good: 90, warn: 80 },
      endpoint: EP_SUMMARY, path: 'step2_breakdown.coverage.forward_rate',
      desc: '需求覆盖率\n是什么：Step1 拉取的原始需求中，有明确测试用例覆盖到的需求条目比例\n怎么算：forward_rate = 已被用例引用的 req_id 数 / requirements 表总数 × 100%\n大小意义：≥90% 绿（需求拆解充分），80~90% 黄（部分需求缺口），<80% 红（需检查 Step1→Step2 需求拆解完整性）'
    },
    evidence_coverage: {
      key: 'evidence_coverage', label: '证据覆盖率', unit: '%',
      thresholds: { dir: 'up', good: 80, warn: 60 },
      endpoint: EP_SUMMARY, path: 'ai_overall.evidence_coverage',
      desc: '证据覆盖率\n是什么：Step4 每个检查点（pass/fail 判定）有佐证支撑的比例——佐证包括截图、断言文字匹配、原始 API/DB 数据\n怎么算：有 evidence 字段填充的 oracle_result 数 / 总 oracle_result 数 × 100%\n大小意义：≥80% 绿（判定有实证支撑），60~80% 黄，<60% 红（大量判定无证据，"假 PASS"风险高）'
    },
    case_completeness: {
      key: 'case_completeness', label: '用例完整度', unit: '%',
      thresholds: { dir: 'up', good: 80, warn: 60 },
      endpoint: EP_SUMMARY, path: 'ai_overall.case_completeness',
      desc: '用例完整度\n是什么：Step3 建模产出的每条测试用例，其六个关键字段（标题/类型/风险等级/操作步骤/检查点/预期结果）的平均填充率\n怎么算：单条用例 score = (标题非空+类型非空+风险等级非空+步骤非空+检查点非空+预期结果非空)/6；所有用例均值\n大小意义：≥80% 绿（用例可直接执行），<60% 红（大量字段缺失，可能导致执行器跳过或 AI 补造）'
    },

    /* ── AI 质量 ── */
    ai_confidence: {
      key: 'ai_confidence', label: 'AI 置信度', unit: '',
      thresholds: { dir: 'up', good: 70, warn: 50 },
      endpoint: EP_SUMMARY, path: 'step2_breakdown.confidence.mean',
      desc: 'AI 置信度\n是什么：Step2 需求拆解阶段 AI 对每条拆解结果自评的置信度均值（0-100，越高越自信输出正确）\n怎么算：SELECT AVG(confidence) FROM ai_health_metrics WHERE metric_type="req_confidence"\n大小意义：≥70 绿（AI 输出稳定可靠），50~70 黄（部分拆解存疑），<50 红（需人工复核需求拆解结果）'
    },
    ai_loop_pass_rate: {
      key: 'ai_loop_pass_rate', label: 'AI 兜底成功率', unit: '%',
      thresholds: { dir: 'up', good: 80, warn: 60 },
      endpoint: EP_SUMMARY, path: 'fusion.ai_loop_pass_rate',
      desc: 'AI 兜底成功率\n是什么：常规 Recipe 缓存未命中时，AI 触发"智能兜底"（LLM 动态决策下一步动作）后执行通过的比例\n怎么算：AI 兜底成功用例数 / 进入 AI 兜底的用例总数 × 100%\n大小意义：≥80% 绿（AI 决策质量高），<60% 红（兜底常失败，需丰富 Recipe 缓存或优化 prompt）'
    },
    hallucination_total: {
      key: 'hallucination_total', label: '幻觉行数', unit: '',
      thresholds: { dir: 'down', good: 0, warn: 5 },
      endpoint: EP_SUMMARY, path: 'ai_overall.hallucination.total',
      desc: '幻觉行数\n是什么：AI 输出的与真实靶子不符的"幻觉"内容总数，分为三类：①接口不存在/参数无效；②页面上找不到的元素选择器；③不合法的枚举值\n怎么算：hallucination.total = interface_mismatch + element_missing + enum_invalid 三类总和\n大小意义：0 绿（AI 完全无幻觉），1~5 黄（少量幻觉，可忽略），>5 红（需逐项排查幻觉来源，可能是 Step2 页面扫描不全）'
    },

    /* ── 安全治理（中间值最优：过低=守卫未触发，过高=AI输出质量差） ── */
    guard_reject_rate: {
      key: 'guard_reject_rate', label: '守卫拦截率', unit: '%',
      // 注意：此指标是中间值最优，但现有 thresholds 只支持 up/down 两种方向。
      // 这里用 dir='down' + 放宽 good/warn 阈值实现"合理范围内都算绿"的效果：
      // ≤30% 绿（守卫在有效拦截危险动作），30~50% 黄（AI动作偏激进），>50% 红（AI输出大量不合规）
      thresholds: { dir: 'down', good: 30, warn: 50 },
      endpoint: EP_SUMMARY, path: 'step4_plugins.guard_reject_rate',
      desc: '守卫拦截率\n是什么：Step4 执行时 AI 发起的动作被安全守卫层拦截的比例——衡量守卫有效性 + AI 动作合规性（不是靶子系统安全性）\n怎么算：guard_rejection 次数 / action_attempt 总次数 × 100%\n拦截原因：危险动作白名单外（delete/drop/truncate/exec）、ai_id 越界、元素类型与动作不匹配（如对 button 做 fill）、必填参数缺失\n大小意义：5~30% 绿（守卫正常拦截到危险动作），<1% 需检查守卫是否未启用或 AI 太保守，>50% 红（AI 输出大量不合规动作，质量差）'
    }
  };

  // ── 工具函数 ────────────────────────────────────────────────────────────

  /** 按点号路径取值，任一层缺失返回 null（绝不抛错、绝不返回 0 冒充有数据）。 */
  function pick(obj, path) {
    if (!obj || !path) return null;
    var cur = obj;
    var parts = String(path).split('.');
    for (var i = 0; i < parts.length; i++) {
      if (cur === null || cur === undefined || typeof cur !== 'object') return null;
      cur = cur[parts[i]];
    }
    return (cur === undefined) ? null : cur;
  }

  /** 取定义；未登记的 key 返回 null（调用方需自行处理，不静默造假）。 */
  function get(key) {
    return Object.prototype.hasOwnProperty.call(DEFS, key) ? DEFS[key] : null;
  }

  /** 全部定义（数组形式，便于遍历渲染）。 */
  function all() {
    var out = [];
    for (var k in DEFS) {
      if (Object.prototype.hasOwnProperty.call(DEFS, k)) out.push(DEFS[k]);
    }
    return out;
  }

  /**
   * 从 /api/metrics-summary 响应体里按定义取值。
   * @param {string} key  KPI key
   * @param {object} summary  metrics-summary 响应体
   * @param {object} [chart]  chart-data 响应体（用于 fallback）
   * @returns {number|null}  取不到返回 null
   */
  function value(key, summary, chart) {
    var d = get(key);
    if (!d) return null;
    var v = pick(d.endpoint === EP_SUMMARY ? summary : chart, d.path);
    if ((v === null || v === undefined) && d.fallback) {
      var src = d.fallback.endpoint === EP_SUMMARY ? summary : chart;
      v = pick(src, d.fallback.path);
    }
    return (typeof v === 'number' && isFinite(v)) ? v : null;
  }

  /**
   * 健康度状态：'success' | 'warning' | 'error' | 'neutral'。
   * 无阈值定义或取不到值一律 'neutral' —— 不允许把「没数据」渲染成「达标」。
   */
  function state(key, v) {
    var d = get(key);
    if (!d || !d.thresholds || typeof v !== 'number' || !isFinite(v)) return 'neutral';
    var t = d.thresholds;
    if (t.dir === 'down') {
      if (v <= t.good) return 'success';
      if (v <= t.warn) return 'warning';
      return 'error';
    }
    if (v >= t.good) return 'success';
    if (v >= t.warn) return 'warning';
    return 'error';
  }

  /** 状态 → CSS 变量色值，与 app-design.css 的语义色令牌保持一致。 */
  var STATE_COLOR = {
    success: 'var(--state-success)',
    warning: 'var(--state-warning)',
    error: 'var(--state-error)',
    neutral: 'var(--text-tertiary)'
  };

  function color(key, v) {
    return STATE_COLOR[state(key, v)] || STATE_COLOR.neutral;
  }

  /**
   * 统一格式化：百分比固定 2 位小数 + '%'，计数类取整并千分位。
   * 取不到值统一渲染 '—'（全站占位符，禁止用 0 冒充）。
   */
  function format(key, v) {
    var d = get(key);
    if (typeof v !== 'number' || !isFinite(v)) return '—';
    if (!d) return String(v);
    if (d.unit === '%') return v.toFixed(2) + '%';
    if (d.unit === 'ms') return Math.round(v) + ' ms';
    if (d.unit === '') return Math.round(v).toLocaleString();
    return v + ' ' + d.unit;
  }

  /** 一步到位：返回 {label, unit, text, state, color, desc, raw}，供模板直接铺卡片。 */
  function render(key, summary, chart) {
    var d = get(key);
    var v = value(key, summary, chart);
    return {
      key: key,
      label: d ? d.label : key,
      unit: d ? d.unit : '',
      raw: v,
      text: format(key, v),
      state: state(key, v),
      color: color(key, v),
      desc: d ? d.desc : ''
    };
  }

  global.MetricsDef = {
    ENDPOINT_SUMMARY: EP_SUMMARY,
    ENDPOINT_CHART: EP_CHART,
    defs: DEFS,
    get: get,
    all: all,
    pick: pick,
    value: value,
    state: state,
    color: color,
    format: format,
    render: render
  };
})(window);
