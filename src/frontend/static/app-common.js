/* ════════════════════════════════════════════════════════════════
   E2E 智能测试引擎 — 公共 JS（多页面架构）
   作用：全局命名空间 _A + 工具函数 + 登录态 + 权限 + Toast + 跳转
   来源：从 app.js / app-user.js 提取公共部分
   每个页面都应加载此文件
   ════════════════════════════════════════════════════════════════ */
(function() {
'use strict';

// ── 命名空间 ──
window._A = window._A || {};

var A = window._A;

// ── 常量 ──
var MIN_PASSWORD_LEN = 8;
var LOGOUT_GUARD_MS = 2000;
// tooltip 气泡宽度：长文本卡片（.tooltip-wide）与普通提示各自的宽度，
// 估算与实际渲染共用此值，避免两处硬编码漂移
var TOOLTIP_WIDE_W = 440;
var TOOLTIP_NORMAL_W = 280;

// ── DOM 工具 ──
A.$ = function(id) {
  if (!id) return null;
  if (typeof id !== 'string') return id;
  if (id.charAt(0) === '#' || id.charAt(0) === '.') {
    return document.querySelector(id);
  }
  return document.getElementById(id);
};

A.$$ = function(sel, ctx) {
  return Array.prototype.slice.call((ctx || document).querySelectorAll(sel));
};

A.escHtml = function(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
};

A.escAttr = function(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
};

// ── 时间格式化（全局通用）──
// ★ 统一把各种来源的时间渲染为「年月日 时分秒」：YYYY-MM-DD HH:MM:SS
//   背景：后端 Flask 默认 JSON 会把 MySQL DATETIME 序列化成 RFC1123/httpdate
//   字符串（如 "Tue, 01 Sep 2026 20:13:45 GMT"），旧代码 substring(0,19) 截断成
//   英文半截 "Tue, 01 Sep 2026 20"。此处做根因级统一，供各页面复用。
A._pad2 = function(n) { return (n < 10 ? '0' : '') + n; };
A.fmtDateTime = function(v) {
  if (v == null || v === '') return v == null ? '' : v;
  var str = String(v).trim();

  // 1) 已是标准 YYYY-MM-DD HH:MM:SS 或 YYYY-MM-DDTHH:MM:SS（含毫秒/时区后缀）
  var m = str.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})/);
  if (m) {
    return m[1] + '-' + m[2] + '-' + m[3] + ' ' + m[4] + ':' + m[5] + ':' + m[6];
  }

  // 2) RFC1123 / JS Date.toString：如 "Tue, 01 Sep 2026 20:13:45 GMT"
  //    直接按字符串成分重建墙钟时间，避免经 Date 解析引入时区偏移
  var months = { Jan:1, Feb:2, Mar:3, Apr:4, May:5, Jun:6, Jul:7, Aug:8, Sep:9, Oct:10, Nov:11, Dec:12 };
  var r = str.match(/^[A-Za-z]{3},\s+(\d{2})\s+([A-Za-z]{3})\s+(\d{4})\s+(\d{2}):(\d{2}):(\d{2})\s+GMT/i);
  if (r && months[r[2]]) {
    return r[3] + '-' + A._pad2(months[r[2]]) + '-' + r[1] + ' ' + r[4] + ':' + r[5] + ':' + r[6];
  }

  // 3) 兜底：交给 Date 解析后按本机时区输出
  var d = new Date(v);
  if (!isNaN(d.getTime())) {
    return d.getFullYear() + '-' + A._pad2(d.getMonth() + 1) + '-' + A._pad2(d.getDate()) +
      ' ' + A._pad2(d.getHours()) + ':' + A._pad2(d.getMinutes()) + ':' + A._pad2(d.getSeconds());
  }
  return str;
};

// ── API 工具 ──
// ★ 修复：加 AbortController 支持，页面切换时中止未完成请求，防止 waitress 线程耗尽
A._activeRequests = [];

// ── GET 微缓存（2026-09-21 根因修复"每页进入都要重新加载一遍数据"）──
// MPA 架构下每次导航都是全新 JS 上下文，内存缓存无效；数据全部现拉，
// 用户每次进页都要看一遍"骨架→数据"过程。这里用 sessionStorage 做项目隔离的
// 短 TTL 缓存（同标签页跨页面存活）：命中则立即渲染，未命中才发请求。
// 失效策略（三重，防脏读）：
//   ① TTL 仅 8 秒；② 任何非 GET 写请求成功后清空当前项目命名空间；
//   ③ 项目切换 invalidateProjectsCache() 时清空。
// 实时端点（status/progress/log/poll 等）永久豁免，绝不回陈旧值。
A._APICACHE_KEY = 'e2e_apicache_v1';
A._APICACHE_TTL = 8000;
A._APICACHE_LIVE_RE = /status|progress|stream|sse|poll|log|running|active_runs|snapshot|health/;

A._apiCacheRead = function(key) {
  try {
    var raw = sessionStorage.getItem(A._APICACHE_KEY);
    if (!raw) return null;
    var store = JSON.parse(raw) || {};
    var hit = store[key];
    if (!hit) return null;
    if (Date.now() - hit.ts > A._APICACHE_TTL) { delete store[key]; sessionStorage.setItem(A._APICACHE_KEY, JSON.stringify(store)); return null; }
    return hit.data;
  } catch(e) { return null; }
};
A._apiCacheWrite = function(key, data) {
  try {
    var raw = sessionStorage.getItem(A._APICACHE_KEY);
    var store = (raw && JSON.parse(raw)) || {};
    store[key] = { ts: Date.now(), data: data };
    sessionStorage.setItem(A._APICACHE_KEY, JSON.stringify(store));
  } catch(e) {}
};
A.invalidateApiCache = function() {
  try { sessionStorage.removeItem(A._APICACHE_KEY); } catch(e) {}
};

A.api = function(method, url, body) {
  var ctrl = new AbortController();
  A._activeRequests.push(ctrl);
  var opts = {
    method: method,
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    signal: ctrl.signal
  };
  if (body !== undefined && body !== null) {
    opts.body = JSON.stringify(body);
  }
  // GET 微缓存：实时端点豁免；命中立即返回（不发起网络请求）
  var isGET = (!method || method.toUpperCase() === 'GET');
  var cacheKey = null;
  if (isGET && !A._APICACHE_LIVE_RE.test(url)) {
    cacheKey = (A._currentProject || '') + '::' + url;
    var cached = A._apiCacheRead(cacheKey);
    if (cached) {
      var idx0 = A._activeRequests.indexOf(ctrl);
      if (idx0 >= 0) A._activeRequests.splice(idx0, 1);
      return Promise.resolve(cached);
    }
  }
  return fetch(url, opts).then(function(r) {
    return r.json().then(function(data) {
      if (data && typeof data === 'object' && !('ok' in data)) {
        data.ok = r.ok;
      }
      if (isGET && r.ok && cacheKey) A._apiCacheWrite(cacheKey, data);
      // 写请求（非 GET）成功后清缓存，防止跨页脏读
      if (!isGET && r.ok) A.invalidateApiCache();
      return data;
    }).catch(function() { return { ok: false, error: '响应解析失败' }; });
  }).catch(function(e) {
    // AbortError 是正常的中止，不算错误
    if (e && e.name === 'AbortError') return { ok: false, error: '请求已中止', aborted: true };
    return { ok: false, error: (e && e.message) || '网络错误' };
  }).finally(function() {
    var idx = A._activeRequests.indexOf(ctrl);
    if (idx >= 0) A._activeRequests.splice(idx, 1);
  });
};

// ★ 修复：中止所有未完成的请求（页面切换前调用）
A.abortAllRequests = function() {
  while (A._activeRequests.length > 0) {
    var ctrl = A._activeRequests.pop();
    try { ctrl.abort(); } catch(e) {}
  }
};

// ── Toast ──
A._toastContainer = null;
A._toast = function(msg, type) {
  if (!A._toastContainer) {
    A._toastContainer = document.createElement('div');
    A._toastContainer.className = 'toast-container';
    document.body.appendChild(A._toastContainer);
  }
  var t = document.createElement('div');
  t.className = 'toast ' + (type || 'info');
  var icon = type === 'success' ? '✓' : (type === 'error' ? '✕' : (type === 'warning' ? '⚠' : 'ℹ'));
  t.innerHTML = '<span>' + icon + '</span><span>' + A.escHtml(msg) + '</span>';
  A._toastContainer.appendChild(t);
  setTimeout(function() {
    t.style.opacity = '0';
    t.style.transform = 'translateX(20px)';
    t.style.transition = 'opacity 0.25s ease, transform 0.25s ease';
    setTimeout(function() { if (t.parentNode) t.parentNode.removeChild(t); }, 250);
  }, 3000);
};

A.toast = A._toast;

// ── 日志输出兼容函数 ──
// 原 model-config/pipeline 等页面使用 appendLog 往日志面板追加内容。
// 统一为 A.appendLog(msg, type)：优先输出到 #logBody；不存在则 console + toast。
A.appendLog = function(msg, type) {
  var text = (msg && (msg.text || msg.message || String(msg))) || String(msg || '');
  var level = type || (msg && msg.level) || 'info';
  var body = document.getElementById('logBody');
  if (body) {
    var empty = body.querySelector('.log-empty');
    if (empty) empty.remove();
    var line = document.createElement('div');
    line.className = 'log-line log-' + level;
    var ts = msg && msg.ts ? '<span class="log-ts">' + A.escHtml(msg.ts) + '</span>' : '';
    line.innerHTML = ts + A.escHtml(text);
    body.appendChild(line);
    body.scrollTop = body.scrollHeight;
  }
  console.log('[A] ' + text);
};

// ── Tooltip 工具函数 ──
// 给元素动态设置/获取/移除 tooltip 内容
// 用法：
//   A.tooltip(el, '提示内容')           — 设置 tooltip
//   A.tooltip(el, '提示', {pos:'top'})  — 设置带方向的 tooltip
//   A.tooltip(el)                       — 获取当前 tooltip 内容
//   A.tooltip(el, '') 或 null           — 移除 tooltip
A.tooltip = function(el, text, opts) {
  if (!el || el.nodeType !== 1) return;
  if (text === undefined) return el.getAttribute('data-tooltip');
  if (!text) {
    el.removeAttribute('data-tooltip');
    el.removeAttribute('data-tooltip-pos');
    return;
  }
  el.setAttribute('data-tooltip', String(text));
  if (opts && opts.pos) {
    el.setAttribute('data-tooltip-pos', opts.pos);
  } else {
    el.removeAttribute('data-tooltip-pos');
  }
  if (opts && opts.wide) {
    el.classList.add('tooltip-wide');
  } else {
    el.classList.remove('tooltip-wide');
  }
};

// 批量设置 tooltip：A.tooltipAll([{el:el1, text:'t1'}, ...])
A.tooltipAll = function(items) {
  if (!items || !items.length) return;
  items.forEach(function(it) {
    if (it.el && it.text) A.tooltip(it.el, it.text, it);
  });
};

// ── Tooltip 边界检测（防止 tooltip 溢出视口） ──
// 在 hover 时动态检测元素位置，必要时添加 data-tooltip-flip 属性翻转方向
A._tooltipFlipTimer = null;
A._checkTooltipBoundary = function(el) {
  if (!el || !el.hasAttribute('data-tooltip')) return;
  var rect = el.getBoundingClientRect();
  var pos = el.getAttribute('data-tooltip-pos') || 'top';
  var flipDir = null;
  // 检测是否需要翻转（距离边缘 < 150px 时翻转）
  if (pos === 'top' && rect.top < 150) {
    flipDir = 'top'; // 翻转为 bottom
  } else if (pos === 'bottom' && window.innerHeight - rect.bottom < 150) {
    flipDir = 'bottom'; // 翻转为 top
  } else if (pos === 'left' && rect.left < 200) {
    flipDir = 'left'; // 翻转为 right
  } else if (pos === 'right' && window.innerWidth - rect.right < 200) {
    flipDir = 'right'; // 翻转为 left
  }
  // 应用翻转
  if (flipDir) {
    el.setAttribute('data-tooltip-flip', flipDir);
  } else {
    el.removeAttribute('data-tooltip-flip');
  }
};

// 全局事件委托：监听所有 tooltip 元素的 hover
document.addEventListener('mouseover', function(e) {
  var target = e.target.closest('[data-tooltip]');
  if (target) {
    // 延迟检测，确保元素已渲染
    clearTimeout(A._tooltipFlipTimer);
    A._tooltipFlipTimer = setTimeout(function() {
      A._checkTooltipBoundary(target);
    }, 10);
  }
});

document.addEventListener('mouseout', function(e) {
  var target = e.target.closest('[data-tooltip]');
  if (target) {
    target.removeAttribute('data-tooltip-flip');
    // ★ 清除 tooltip 偏移变量，避免残留影响下次 hover
    target.style.removeProperty('--tooltip-offset-x');
    target.style.removeProperty('--tooltip-offset-y');
  }
});

// ── 按钮防重放 / 加载态 ──
// A.withBtnLoading(btn, '保存中...', async function() { ... })
// 自动 disabled + 改文案 + try/finally 恢复，避免短时间内重复点击
// 支持 BUTTON/INPUT/SELECT/TEXTAREA 等原生表单元素，也兼容 div/span 等自定义控件
A.withBtnLoading = function(btn, loadingText, action) {
  var el = (typeof btn === 'string') ? A.$(btn) : btn;
  if (!el) return Promise.resolve();
  var tag = el.tagName;
  var isFormElement = (tag === 'BUTTON' || tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA');
  var isInput = (tag === 'INPUT');
  if (isFormElement) {
    if (el.disabled) return Promise.resolve();
  } else {
    if (el.dataset.loading === 'true') return Promise.resolve();
  }
  var originalHTML = el.innerHTML;
  var originalValue = el.value;
  var originalWidth = el.style.width;
  var originalCursor = el.style.cursor;
  var originalPointerEvents = el.style.pointerEvents;
  if (isFormElement) {
    el.disabled = true;
  } else {
    el.dataset.loading = 'true';
    el.style.pointerEvents = 'none';
    el.style.cursor = 'not-allowed';
    el.style.opacity = '0.6';
  }
  if (loadingText) {
    var rect = el.getBoundingClientRect();
    if (rect && rect.width > 0) {
      el.style.width = rect.width + 'px';
    }
    if (isInput) {
      el.value = loadingText;
    } else {
      el.innerHTML = '<span style="display:inline-flex;align-items:center;gap:4px;">' +
        '<span class="loading-spinner-inline"></span>' + A.escHtml(loadingText) + '</span>';
    }
  }
  var done = function() {
    if (isFormElement) {
      el.disabled = false;
    } else {
      el.dataset.loading = 'false';
      el.style.pointerEvents = originalPointerEvents;
      el.style.cursor = originalCursor;
      el.style.opacity = '';
    }
    if (isInput) {
      el.value = originalValue;
    } else {
      el.innerHTML = originalHTML;
    }
    el.style.width = originalWidth;
  };
  try {
    var result = action(el);
    if (result && typeof result.then === 'function') {
      return result.then(function(v) { done(); return v; }, function(e) { done(); throw e; });
    }
    done();
    return Promise.resolve(result);
  } catch (e) {
    done();
    return Promise.reject(e);
  }
};

// 防抖：A.debounce(fn, wait)
A.debounce = function(fn, wait) {
  var timer = null;
  return function() {
    var ctx = this, args = arguments;
    clearTimeout(timer);
    timer = setTimeout(function() { fn.apply(ctx, args); }, wait || 300);
  };
};

// ── Loading ──
A._loadingEl = null;
A.showLoading = function(text) {
  A.hideLoading();
  A._loadingEl = document.createElement('div');
  A._loadingEl.className = 'loading-overlay';
  A._loadingEl.innerHTML = '<div style="display:flex;flex-direction:column;align-items:center;gap:16px">' +
    '<div class="loading-spinner"></div>' +
    (text ? '<div style="color:var(--text-secondary);font-size:var(--fs-small)">' + A.escHtml(text) + '</div>' : '') +
    '</div>';
  document.body.appendChild(A._loadingEl);
};

A.hideLoading = function() {
  if (A._loadingEl && A._loadingEl.parentNode) {
    A._loadingEl.parentNode.removeChild(A._loadingEl);
    A._loadingEl = null;
  }
};

// ── 登录态 ──
A._currentUser = null;
A._permissions = [];
A._projectPermissions = [];
A._projectLevel = '';
A._isAdmin = false;
A._isProjectAdmin = false;
A._isProjectMember = false;
A._systemRole = '';
A._hasKey = false;
A._keyMode = '';
A._keyProvider = '';
A._logoutGuard = 0;
A._userInited = false;

// ── 权限加载 ──
A.loadPermissions = function() {
  if (!A._currentUser) return Promise.resolve();
  return A.api('GET', '/api/permissions').then(function(r) {
    A._permissions = (r && r.permissions) || [];
    A._projectPermissions = (r && r.project_permissions) || [];
    A._projectLevel = (r && r.project_level) || '';
    A._isAdmin = !!(r && r.is_admin);
    A._isProjectAdmin = !!(r && r.is_project_admin);
    A._isProjectMember = !!(r && r.is_project_member);
    A._systemRole = (r && r.system_role) || '';
    A._savePermCache();
  }).catch(function() {
    A._permissions = [];
    A._projectPermissions = [];
    A._projectLevel = '';
    A._isAdmin = false;
    A._isProjectAdmin = false;
    A._isProjectMember = false;
    A._systemRole = '';
  });
};

// ── 权限首帧快照 ──
// 目的：侧栏菜单的显隐由权限决定，而权限原本只在 /api/permissions 返回后（首帧之后）
//   才应用 → 菜单会在首帧后二次变化，用户点在"正在变形的菜单"上就会落空或进错页。
//   这里缓存上次的权限，页面加载时**同步**用于首帧渲染；接口返回后再用真实权限做校正
//   （通常无差异 → 零 DOM 写 → 零位移）。
// ⚠ 安全边界：该缓存**只用于视觉渲染**，绝不作为鉴权/授权依据；
//   所有真实操作的服务端校验不受影响（越权请求仍会被后端拒绝）。
A.PERM_CACHE_KEY = 'e2e_perm_cache_v1';
A._permCacheKey = function() {
  var uid = '';
  try { uid = localStorage.getItem('_session_user_id') || ''; } catch(e) {}
  return A.PERM_CACHE_KEY + (uid ? ':' + uid : '');
};
A._savePermCache = function() {
  try {
    localStorage.setItem(A._permCacheKey(), JSON.stringify({
      permissions: A._permissions || [],
      project_permissions: A._projectPermissions || [],
      project_level: A._projectLevel || '',
      is_admin: !!A._isAdmin,
      is_project_admin: !!A._isProjectAdmin,
      is_project_member: !!A._isProjectMember,
      system_role: A._systemRole || ''
    }));
  } catch(e) {}
};
A._restorePermCache = function() {
  if (A._permissions && A._permissions.length) return;  // 已有真实权限则不覆盖
  var c = null;
  try {
    var raw = localStorage.getItem(A._permCacheKey());
    if (raw) c = JSON.parse(raw);
  } catch(e) { c = null; }
  if (!c) return;
  A._permissions = c.permissions || [];
  A._projectPermissions = c.project_permissions || [];
  A._projectLevel = c.project_level || '';
  A._isAdmin = !!c.is_admin;
  A._isProjectAdmin = !!c.is_project_admin;
  A._isProjectMember = !!c.is_project_member;
  A._systemRole = c.system_role || '';
};

A.hasPerm = function(perm) {
  if (A._isAdmin) return true;
  if (!perm) return true;
  return A._permissions.indexOf(perm) !== -1 || A._projectPermissions.indexOf(perm) !== -1;
};

// ── 权限驱动显隐：无权限的元素直接隐藏 ──
A.applyPermissions = function() {
  // ★ DEBUG：打印当前权限状态
  console.log('[perm-debug] applyPermissions 执行 — isAdmin:', A._isAdmin,
    'systemRole:', A._systemRole,
    'permissions:', A._permissions.slice(0, 10),
    'projectPermissions:', A._projectPermissions.slice(0, 10));
  var permCount = 0, roleCount = 0, shownCount = 0, hiddenCount = 0;
  // 通用：[data-perm] 属性驱动
  A.$$('[data-perm]').forEach(function(el) {
    var perm = el.getAttribute('data-perm');
    if (A.hasPerm(perm)) {
      el.style.removeProperty('display');
      shownCount++;
    } else {
      el.style.setProperty('display', 'none', 'important');
      hiddenCount++;
    }
    permCount++;
  });
  // 角色：[data-role] 属性驱动（仅特定角色可见）
  A.$$('[data-role]').forEach(function(el) {
    var roles = el.getAttribute('data-role').split(/\s+/);
    var visible = roles.indexOf(A._systemRole) !== -1 || (A._isAdmin && roles.indexOf('admin') !== -1);
    if (visible) {
      el.style.removeProperty('display');
      shownCount++;
    } else {
      el.style.setProperty('display', 'none', 'important');
      hiddenCount++;
    }
    roleCount++;
  });
  console.log('[perm-debug] 结果 — data-perm元素:', permCount, 'data-role元素:', roleCount,
    '显示:', shownCount, '隐藏:', hiddenCount);
  // ★ 隐藏空 section：如果可折叠分组内所有受权限控制的 nav-item 都被隐藏，则隐藏整个 section
  A.$$('.sidebar-section.collapsible').forEach(function(section) {
    var navItems = section.querySelectorAll('.nav-item');
    var hasVisible = false;
    for (var i = 0; i < navItems.length; i++) {
      var item = navItems[i];
      var display = item.style.display;
      // 未设置 display:none 的项视为可见
      if (display !== 'none') {
        hasVisible = true;
        break;
      }
    }
    if (!hasVisible) {
      section.style.setProperty('display', 'none', 'important');
    } else {
      section.style.removeProperty('display');
    }
  });
};

// ── ★ 2026-09-01：侧边栏一键隐藏（顶栏按钮切换，localStorage 记忆）──
A.initSidebarToggle = function() {
  var btn = document.getElementById('btnToggleSidebar');
  if (!btn) return;
  try {
    if (localStorage.getItem('sidebar-hidden') === '1') {
      document.body.classList.add('sidebar-hidden');
    }
  } catch(e) {}
  btn.addEventListener('click', function() {
    var hidden = document.body.classList.toggle('sidebar-hidden');
    try { localStorage.setItem('sidebar-hidden', hidden ? '1' : '0'); } catch(e) {}
  });
};

// ── 侧边栏手风琴式折叠分区导航 ──
//   手风琴语义保留（同一时刻只展开一个分区），但**"展开哪个"由用户最后的操作决定**，
//   不再每次页面加载都被重置为"只有当前页所在分区展开"。
//
//   根因：侧栏原本要经过 3 个时机不同的阶段才会定型 ——
//     ① 服务端 Jinja 按 active_nav 决定 .expanded
//     ② DOMContentLoaded 里注入导航项 + 重置展开态
//     ③ 鉴权返回后按权限隐藏菜单项
//   于是每次切页，菜单都会在首帧前后被改动，用户点的那个菜单项正从光标下移走 →
//   表现为「点一下没反应 / 点错了 / 界面很乱」。
//   修复：展开态与滚动位置记忆到 sessionStorage，并在**首帧前**一次性恢复；
//        权限用本地缓存做首帧过滤；注入项与展开态放在同一个幂等函数里完成。
A.SB_STATE_KEY = 'e2e_sidebar_state_v1';

A.readSidebarState = function() {
  try {
    var raw = sessionStorage.getItem(A.SB_STATE_KEY);
    if (raw) {
      var o = JSON.parse(raw);
      if (o && typeof o === 'object') return o;
    }
  } catch(e) {}
  return { expanded: [], scroll: 0 };
};

A.writeSidebarState = function(patch) {
  var st = A.readSidebarState();
  for (var k in patch) { if (Object.prototype.hasOwnProperty.call(patch, k)) st[k] = patch[k]; }
  try { sessionStorage.setItem(A.SB_STATE_KEY, JSON.stringify(st)); } catch(e) {}
  return st;
};

A._sidebarExpandedKeys = function() {
  var out = [];
  A.$$('.sidebar-section.collapsible').forEach(function(sec) {
    if (sec.classList.contains('expanded')) {
      var k = sec.getAttribute('data-section');
      if (k) out.push(k);
    }
  });
  return out;
};

A._sidebarActiveKey = function() {
  var key = null;
  A.$$('.sidebar-section.collapsible').forEach(function(sec) {
    if (!key && sec.querySelector('.nav-item.active')) key = sec.getAttribute('data-section');
  });
  return key;
};

A.initSidebarSections = function() {
  var sections = A.$$('.sidebar-section.collapsible');
  if (!sections.length) return;

  // ★ 定型态：优先恢复用户上次展开的分区；没有记忆时退回"当前页所在分区"。
  //   当前页所在分区恰好就是记忆分区时 → 完全没有位移。
  var st = A.readSidebarState();
  var activeKey = A._sidebarActiveKey();
  var want = (st.expanded && st.expanded.length) ? st.expanded[0] : activeKey;

  sections.forEach(function(sec) {
    var k = sec.getAttribute('data-section');
    if (k && k === want) sec.classList.add('expanded');
    else sec.classList.remove('expanded');
  });

  // ★ 恢复侧栏滚动位置：与展开态同帧应用，避免"先滚到顶再跳回"。
  var sb = document.querySelector('.sidebar');
  if (sb && st.scroll) { try { sb.scrollTop = st.scroll; } catch(e) {} }

  // 点击标题：手风琴切换 + 记忆（供下一次页面加载恢复）
  sections.forEach(function(sec) {
    var title = sec.querySelector('.sidebar-section-title');
    if (!title || title.__sbBound) return;
    title.__sbBound = true;
    title.addEventListener('click', function(e) {
      e.preventDefault();
      var willExpand = !sec.classList.contains('expanded');
      // 并行切换，避免"先全收起再展开"造成的中间空帧
      sections.forEach(function(s) { if (s !== sec) s.classList.remove('expanded'); });
      if (willExpand) sec.classList.add('expanded');
      var key = sec.getAttribute('data-section');
      A.writeSidebarState({ expanded: willExpand && key ? [key] : [] });
    });
  });

  // 记忆侧栏滚动位置（节流写入，切页时由 readSidebarState 恢复）
  if (sb && !sb.__sbScrollBound) {
    sb.__sbScrollBound = true;
    sb.addEventListener('scroll', function() {
      if (A._sbScrollTimer) return;
      A._sbScrollTimer = setTimeout(function() {
        A._sbScrollTimer = null;
        A.writeSidebarState({ scroll: Math.round(sb.scrollTop) });
      }, 200);
    }, { passive: true });
  }
};

// ── ★ 侧栏统一定型入口（幂等，可在首帧前调用）──
// 一次做完：注入导航项 → 权限首帧过滤 → 展开态/滚动恢复 → 开交互门。
// 任何页面加载只走这一个入口，不再让侧栏在首帧后被反复修改。
A.initSidebar = function() {
  var sidebar = document.querySelector('.sidebar');
  if (!sidebar) { A._openSidebarGate(); return; }
  if (A._sidebarInited) { A._openSidebarGate(); return; }
  A._sidebarInited = true;
  try {
    A.injectTestMethodNav();      // 必须在展开态判定之前，让手风琴能看到注入项
    A._restorePermCache();        // 首帧视觉用缓存权限（仅渲染，不作鉴权依据）
    A.applyPermissions();         // 首帧即按权限过滤 → 首帧后不再二次变化
    A.initSidebarToggle();
    A.initSidebarSections();
  } catch(e) {
    // 侧栏定型失败不得阻断页面：至少把交互门打开
  }
  A._openSidebarGate();
};

// 交互就绪门：定型前侧栏不接受点击（避免点在"正在变形的菜单"上而进错页），
// 定型完成后打开；另有兜底超时，任何异常都不会让菜单永久不可点。
A._openSidebarGate = function() {
  try { document.documentElement.setAttribute('data-sb-ready', '1'); } catch(e) {}
};
setTimeout(function() { A._openSidebarGate(); }, 1200);

// ── ★ JS 注入：测试方法论清单 导航项（指标区） ──
//   背景：前端布局锁（FRONTEND_LOCK.json）锁定 _layout_base.html，不直接改模板；
//   故在公共 JS 中动态追加导航项，仅在 _initAll（DOMContentLoaded）中调用一次，
//   且须早于 initSidebarSections，使手风琴能识别 .nav-item.active 并展开指标区。
A.injectTestMethodNav = function() {
  var sec = document.querySelector('.sidebar-section[data-section="metrics"]');
  if (!sec) return;
  if (document.getElementById('nav-test-methods')) return; // 已注入，防重复
  var active = window.location.pathname.indexOf('/test-methods') === 0;
  var el = document.createElement('a');
  el.id = 'nav-test-methods';
  el.className = 'nav-item' + (active ? ' active' : '');
  el.href = '/test-methods';
  el.setAttribute('data-tooltip', '12 种测试方法论清单（生成/执行统计 + 用例筛选入口）');
  el.setAttribute('data-tooltip-pos', 'right');
  el.innerHTML = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 3H5a2 2 0 0 0-2 2v4m6-6h10a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-6m6-10v8"/><path d="m9 14 10-2"/></svg>方法论清单';
  sec.appendChild(el);
};

// ── 初始化登录态（每个页面加载时调用）──
A.initUser = function() {
  var isBackForward = false;
  try {
    var navEntries = performance.getEntriesByType('navigation');
    if (navEntries.length > 0) {
      isBackForward = (navEntries[0].type === 'back_forward');
    }
  } catch(e) {}
  if (A._userInited && !isBackForward) return;
  if (isBackForward) {
    A._currentUser = null;
    A._permissions = [];
    A._isAdmin = false;
  }
  A._userInited = true;

  // ★ 1. 从 localStorage 立即恢复用户信息（消除 sidebar "未登录" 闪现）
  _restoreCachedUser();

  var fallbackTimer = setTimeout(function() {
    A._onAuthFail();
  }, 8000);

  // ★ 2. 项目/版本加载在后台进行，不阻塞 _onAuthOk
  A.loadCurrentProject();
  A.loadCurrentVersion();

  // ★ 3. 用户鉴权 → 权限加载 → 立即触发 _onAuthOk（不等项目/版本）
  fetch('/api/current-user', { credentials: 'same-origin' })
    .then(function(r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then(function(d) {
      if (d && d.user) {
        A._currentUser = d.user;
        _saveCachedUser(d.user);
        // 只等权限（1 次 API），不等项目/版本（已在后台加载）
        A.loadPermissions().then(function() {
          A._onAuthFramework();
          A._runPageInit();
          try { document.dispatchEvent(new CustomEvent('app:auth-ok')); } catch(e) {}
          A.applyPersonalConfig();
          A.applyInputStyle();
        });
      } else {
        A._clearLocalSession();
        A._onAuthFail();
      }
    })
    .catch(function() {
      A._onAuthFail();
    })
    .finally(function() { clearTimeout(fallbackTimer); });
};

A._clearLocalSession = function() {
  try {
    localStorage.removeItem('_session_user_id');
    localStorage.removeItem('_session_user_name');
    localStorage.removeItem('_session_login_ts');
  } catch(e) {}
};

// ★ 从 localStorage 恢复用户信息（页面加载时立即调用，消除 "未登录" 闪现）
function _restoreCachedUser() {
  try {
    var id = localStorage.getItem('_session_user_id');
    var name = localStorage.getItem('_session_user_name');
    if (id && name) {
      if (!A._currentUser) { A._currentUser = {}; }
      A._currentUser.id = id;
      A._currentUser.name = name;
      A._renderSidebarUser();
    }
  } catch(e) {}
}

// ★ 缓存用户信息到 localStorage
function _saveCachedUser(user) {
  try {
    localStorage.setItem('_session_user_id', user.id);
    localStorage.setItem('_session_user_name', user.name);
    localStorage.setItem('_session_login_ts', Date.now().toString());
  } catch(e) {}
}

// ── 加载项目数据（共享缓存去重，避免 loadCurrentProject 和 loadEnvProjects 各请求一次）──
// ★ 多处调用 /api/projects（app-common.js 的 loadCurrentProject + _layout_base.html 的 loadEnvProjects）
//   用结果缓存（30 秒 TTL）保证同一页面加载周期内只发一次请求
//   切换项目后需手动调用 A.invalidateProjectsCache() 清除缓存
A._projectsData = null;
A._projectsDataTs = 0;
A._PROJECTS_TTL = 30000;  // 30 秒
// ★ 项目下拉首帧前置：把项目列表快照写入 localStorage，供 _layout_base.html 中
//   <select id="envSelect"> 之后的同步内联脚本在解析期就渲染出正确选项。
//   原实现是等鉴权完成后异步填 option（实测首帧后 ~509ms 才整组替换 OPTION），
//   用户会看到下拉先显示错误内容再被换掉。此缓存仅用于渲染，切换项目仍走服务端接口。
A.PROJ_CACHE_KEY = 'e2e_proj_cache_v1';
A.getProjectsData = function() {
  // 30 秒内的缓存直接返回
  if (A._projectsData && (Date.now() - A._projectsDataTs) < A._PROJECTS_TTL) {
    return Promise.resolve(A._projectsData);
  }
  return A.api('GET', '/api/projects').then(function(r) {
    A._projectsData = r;
    A._projectsDataTs = Date.now();
    try {
      localStorage.setItem(A.PROJ_CACHE_KEY, JSON.stringify({
        current: (r && r.current) || '',
        project_list: (r && r.project_list) || []
      }));
    } catch(e) {}
    return r;
  });
};
A.invalidateProjectsCache = function() {
  A._projectsData = null;
  A._projectsDataTs = 0;
  try { localStorage.removeItem(A.PROJ_CACHE_KEY); } catch(e) {}
  // ★ 项目切换必须连带清 GET 微缓存（项目隔离键，防跨项目脏读）
  A.invalidateApiCache();
};

// ── 加载当前项目（后台加载，不阻塞 _onAuthOk）──
A.loadCurrentProject = async function() {
  try {
    var r = await A.getProjectsData();
    if (r && r.current) {
      A._currentProject = r.current;
      // ★ 修复：_currentProjectName 存 display_name（中文名），而非编码
      var pl = r.project_list || [];
      var found = pl.find(function(p) { return p.name === r.current; });
      A._currentProjectName = (found && found.display_name) || r.current;
    }
  } catch(e) {
    // 静默失败，各页面自行 fallback 到 '-'
  }
};

// ── 加载当前版本（后台加载，不阻塞 _onAuthOk）──
A.loadCurrentVersion = async function() {
  try {
    var r = await A.api('GET', '/api/project/versions/active');
    if (r && !r.error) {
      A._currentVersion = r.version_label || r.label || '';
      A._currentVersionLabel = A._currentVersion;
    }
  } catch(e) {
    // 静默失败，无活跃版本时正常为空
  }
  // ★ 通知 UI 更新版本显示
  try { document.dispatchEvent(new CustomEvent('app:version-loaded')); } catch(e) {}
};

// ── 鉴权后的框架级回调（与"页面初始化"分离）──
// 框架只负责刷新侧栏用户信息、应用权限、启动徽标轮询；
// 页面自己的初始化统一走 A._onAuthOk（见文件末尾的单次调度器），两者不再互相覆盖。
A._onAuthFramework = function() {
  A._renderSidebarUser();
  A._restorePermCache();
  A.applyPermissions();
  A._startStepBadgePolling();
  if (typeof A.onAuthOk === 'function') A.onAuthOk();
};

A._onAuthFail = function() {
  // 默认：跳转登录页（除非已在登录页）
  var path = window.location.pathname;
  if (path === '/login' || path === '/register') return;
  A._currentUser = null;
  A._permissions = [];
  A._isAdmin = false;
  // 给一个短暂延迟，避免刷新风暴
  if (!A._redirecting) {
    A._redirecting = true;
    setTimeout(function() {
      window.location.href = '/login?redirect=' + encodeURIComponent(window.location.pathname + window.location.search);
    }, 100);
  }
};

// ── 渲染 sidebar 用户信息（公共布局用）──
A._renderSidebarUser = function() {
  if (!A._currentUser) return;
  var name = A._currentUser.name || A._currentUser.id || '';
  // 用户名
  A.$$('.sidebar-user-name').forEach(function(el) {
    el.textContent = name;
  });
  // 头像（取首字母）
  A.$$('.sidebar-user-avatar').forEach(function(el) {
    el.textContent = (name || '?').charAt(0).toUpperCase();
  });
  // 角色
  var role = A._systemRole || '';
  var label = '';
  if (A._isAdmin) label = '管理员';
  else if (A._isProjectAdmin) label = '项目管理员';
  else if (A._isProjectMember) label = '项目成员';
  else label = role || '用户';
  A.$$('.sidebar-user-role').forEach(function(el) {
    el.textContent = label;
  });
  // ★ 首帧前置：把用户名/角色写进 localStorage，供 _layout_base.html 中侧栏标记
  //   之后的同步内联脚本在解析期填入，避免"未登录/游客 → 真名"的首帧后跳变。
  try {
    if (name) localStorage.setItem('_session_user_name', name);
    if (label) localStorage.setItem('_session_user_role', label);
  } catch(e) {}
};

// ── 退出登录 ──
A.logout = async function() {
  try {
    var st = await A.api('GET', '/api/status');
    var runs = (st && st.active_runs) || [];
    if (runs.length > 0) {
      if (!confirm('有 ' + runs.length + ' 个任务正在执行，退出将自动停止。确认退出？')) return;
      try { await A.api('POST', '/api/stop'); } catch(_e) {}
    }
    try { await A.api('POST', '/api/logout'); } catch(_e) {}
  } catch(e) {}
  A._currentUser = null;
  A._permissions = [];
  A._isAdmin = false;
  A._logoutGuard = Date.now();
  A._clearLocalSession();
  window.location.href = '/login';
};

// ── 侧边栏 Step 徽章全局轮询（所有页面共享，每 5 秒更新 stepBadge1-5）──
A._stepBadgeTimer = null;
A._stepBadgeLastText = {};
A._updateStepBadges = async function() {
  try {
    // 优先使用 /api/pipeline-progress（含 DB 产物推断，能正确显示已完成步骤）
    // ★ 修复：改用 dedupedApi，与 loadStatus/pollStatus 的请求去重
    var pp = await A.dedupedApi('GET', '/api/pipeline-progress');
    if (pp && pp.ok && pp.steps) {
      var labelMap = { pending: '等待中', active: '进行中', completed: '已完成', error: '失败' };
      var classMap = {
        pending: 'step-badge step-badge-pending',
        active: 'step-badge step-badge-running',
        completed: 'step-badge step-badge-done',
        error: 'step-badge step-badge-error'
      };
      pp.steps.forEach(function(s) {
        var n = s.step;
        var el = document.getElementById('stepBadge' + n);
        if (!el) return;
        var text = labelMap[s.status] || '等待';
        if (A._stepBadgeLastText[n] !== text) {
          el.textContent = text;
          el.className = classMap[s.status] || classMap.pending;
          A._stepBadgeLastText[n] = text;
        }
      });
      // 更新"已执行"计数
      var execCount = pp.steps.filter(function(s) { return s.status === 'completed'; }).length;
      var execEl = document.getElementById('stepBadgeExec');
      if (execEl) {
        execEl.textContent = execCount;
        execEl.className = execCount > 0 ? 'step-badge step-badge-done' : 'step-badge step-badge-pending';
      }
      return;
    }
    // 降级：使用 /api/status（仅反映活跃任务）
    // ★ 修复：改用 dedupedApi，与 loadStatus/pollStatus 的请求去重
    var st = await A.dedupedApi('GET', '/api/status');
    if (!st || !st.ok) return;
    var activeRuns = st.active_runs || [];
    var stepStatus = { 1: 'pending', 2: 'pending', 3: 'pending', 4: 'pending', 5: 'pending' };
    activeRuns.forEach(function(r) {
      var step = r.step;
      if (step && stepStatus[step]) stepStatus[step] = 'running';
    });
    if (st.current_step) {
      var cs = parseInt(st.current_step, 10);
      if (cs && stepStatus[cs]) stepStatus[cs] = 'running';
    }
    var labelMap = { pending: '等待中', running: '进行中', done: '已完成', error: '失败' };
    var classMap = {
      pending: 'step-badge step-badge-pending',
      running: 'step-badge step-badge-running',
      done: 'step-badge step-badge-done',
      error: 'step-badge step-badge-error'
    };
    [1, 2, 3, 4, 5].forEach(function(n) {
      var el = document.getElementById('stepBadge' + n);
      if (!el) return;
      var status = stepStatus[n];
      var text = labelMap[status] || '等待';
      if (A._stepBadgeLastText[n] !== text) {
        el.textContent = text;
        el.className = classMap[status] || classMap.pending;
        A._stepBadgeLastText[n] = text;
      }
    });
  } catch (e) {
    // 静默失败，不影响页面主流程
  }
};
A._startStepBadgePolling = function() {
  if (A._stepBadgeTimer) return; // 已启动
  A._updateStepBadges();
  // ★ 使用智能自适应轮询替代固定 5 秒 setInterval
  A.adaptivePoll('sidebar-badges', A._updateStepBadges, { fastMs: 5000, slowMs: 15000, idleMs: 30000 });
  A._stepBadgeTimer = 1; // 标记已启动
};
A._stopStepBadgePolling = function() {
  A.stopAdaptivePoll('sidebar-badges');
  A._stepBadgeTimer = null;
};

// ═══════════════════════════════════════════════════════════════════
// 首帧配置快照 — 消灭「先默认渲染、再整体换皮」导致的全页重排
// ───────────────────────────────────────────────────────────────────
// 根因：--ui-scale / 主题色 / 卡片透明度 / 文字色 / 背景模式 原本只在
//   /api/bg-config、/api/input-style 返回后（首帧之后 ~100-250ms）才写入
//   <html> 的行内样式。--ui-scale 由 1 变为 1.3 会让 app-design.css 中所有
//   --fs-* 令牌重算，整个侧栏与内容区尺寸瞬间变化（实测侧栏高度 966→1154px、
//   首个菜单项 top 位移 +126px），用户在"正在变形的菜单"上点击就会落空或打错。
//
// 机制：把"上一次真正生效的配置"快照存 localStorage，页面在 <head> 中
//   （CSS 之前）同步恢复；拿到接口响应后与快照做**差异比对**，一致则完全不碰
//   DOM（零重排），只有用户真的改了配置时才应用一次。
// 注意：user_id 参与 key，避免多账号在同一浏览器串味。
A.BOOT_CFG_KEY = 'e2e_boot_cfg_v1';
A._bootCfg = null;

A._bootCfgKey = function() {
  var uid = '';
  try { uid = localStorage.getItem('_session_user_id') || ''; } catch(e) {}
  return A.BOOT_CFG_KEY + (uid ? ':' + uid : '');
};

A.readBootCfg = function() {
  if (A._bootCfg) return A._bootCfg;
  var cfg = null;
  try {
    var raw = localStorage.getItem(A._bootCfgKey());
    if (raw) cfg = JSON.parse(raw);
  } catch(e) { cfg = null; }
  A._bootCfg = (cfg && typeof cfg === 'object') ? cfg : {};
  return A._bootCfg;
};

A.writeBootCfg = function(patch) {
  var cfg = A.readBootCfg();
  for (var k in patch) { if (Object.prototype.hasOwnProperty.call(patch, k)) cfg[k] = patch[k]; }
  try { localStorage.setItem(A._bootCfgKey(), JSON.stringify(cfg)); } catch(e) {}
  return cfg;
};

// 同步应用快照到 <html> 行内样式（不做任何网络请求、不建/删节点）
A._applyBootCfgLocal = function(cfg) {
  cfg = cfg || A.readBootCfg();
  var s = document.documentElement.style;
  if (cfg.ui_scale) s.setProperty('--ui-scale', cfg.ui_scale);
  if (cfg.text_primary) s.setProperty('--text-primary', cfg.text_primary);
  if (cfg.text_secondary) s.setProperty('--text-secondary', cfg.text_secondary);
  if (cfg.text_tertiary) s.setProperty('--text-tertiary', cfg.text_tertiary);
  if (cfg.color_primary) s.setProperty('--color-primary', cfg.color_primary);
  if (cfg.color_primary_soft) s.setProperty('--color-primary-soft', cfg.color_primary_soft);
  if (cfg.color_primary_border) s.setProperty('--color-primary-border', cfg.color_primary_border);
  if (cfg.color_primary_hover) s.setProperty('--color-primary-hover', cfg.color_primary_hover);
  if (cfg.card_bg) s.setProperty('--card-bg', cfg.card_bg);
  if (cfg.card_bg_elevated) s.setProperty('--card-bg-elevated', cfg.card_bg_elevated);
  if (cfg.bg_surface) s.setProperty('--bg-surface', cfg.bg_surface);
  if (cfg.bg_elevated) s.setProperty('--bg-elevated', cfg.bg_elevated);
  if (cfg.bg_css) document.documentElement.style.background = cfg.bg_css;
};

// 差异比对：只比较"会影响渲染"的键
A._bootCfgDiff = function(cfg, next) {
  var changed = [];
  for (var k in next) {
    if (!Object.prototype.hasOwnProperty.call(next, k)) continue;
    if (cfg[k] !== next[k]) changed.push(k);
  }
  return changed;
};

// ── 背景"纯绘制"应用 ──
// 只写不影响布局尺寸的属性（<html> 背景、<body> 透明），因此每次页面加载都可安全重放；
// 与"会触发整页重排的 CSS 变量"严格分开，后者走首帧快照 + 差异闸门。
A._bgHexToRgba = function(hex, a) {
  var h = String(hex).replace('#', '');
  return 'rgba(' + parseInt(h.substring(0,2),16) + ',' + parseInt(h.substring(2,4),16) +
         ',' + parseInt(h.substring(4,6),16) + ',' + a + ')';
};
A._applyBgPaint = function(r) {
  var root = document.documentElement;
  if (document.body) document.body.style.backgroundColor = 'transparent';
  var mode = r.mode || 'custom';
  if (mode === 'default') mode = 'custom';
  var alpha = (r.opacity !== undefined ? r.opacity : 100) / 100;
  // ★ 先清旧定时器再建新的：多次调用若叠加，会出现多个 30fps 定时器，
  //   每个 tick 都改写 <html> 背景 = 全屏重绘，叠加即表现为界面卡顿。
  if (A._bgRotateInterval) { clearInterval(A._bgRotateInterval); A._bgRotateInterval = null; }
  if (mode === 'custom' && r.color1) {
    var colors = [r.color1, r.color2, r.color3, r.color4].filter(Boolean);
    var rgbaColors = colors.map(function(c) { return A._bgHexToRgba(c, alpha); });
    if (r.auto_rotate) {
      var angle = r.angle || 64;
      var speed = r.rotate_speed || 3;
      A._bgRotateInterval = setInterval(function() {
        angle = (angle + speed * 30 * 0.033) % 360;
        root.style.background = 'linear-gradient(' + Math.round(angle) + 'deg, ' + rgbaColors.join(', ') + ')';
      }, 33);
    } else {
      root.style.background = 'linear-gradient(' + (r.angle || 64) + 'deg, ' + rgbaColors.join(', ') + ')';
    }
  } else if (mode === 'gallery') {
    root.style.background = "url('/api/bg-image?t=" + Date.now() + "') center/cover fixed no-repeat";
    if (alpha < 1) {
      root.style.backgroundColor = 'rgba(0,0,0,' + (1 - alpha) + ')';
      root.style.backgroundBlendMode = 'overlay';
    } else {
      root.style.backgroundBlendMode = '';
    }
  } else if (mode === 'none') {
    root.style.background = 'var(--bg-base)';
  }
};
// 页面隐藏/卸载即停掉背景旋转：否则旧页面在导航期间继续全屏重绘，拖慢新页面首帧。
window.addEventListener('pagehide', function() {
  if (A._bgRotateInterval) { clearInterval(A._bgRotateInterval); A._bgRotateInterval = null; }
});

// ── 个性化配置全局应用 ──
// applyPersonalConfig: 应用用户保存的背景配置 + 辅助文字色 + 卡片透明度到当前页面
A.applyPersonalConfig = function() {
  // ★ 首帧前置：先用本地快照同步补齐（若 <head> 内联脚本已应用则为幂等空操作），
  //   保证即使接口慢/失败，页面也不会停在"默认皮肤"上再跳变。
  A._applyBootCfgLocal();
  // ★ 注入全局 CSS：让所有页面内联样式中卡片/面板类的 background 引用 --card-bg / --card-bg-elevated
  // 这样 applyPersonalConfig 设置 --card-bg 为半透明 rgba 时，所有卡片都会变透明让背景透过来
  // ★ 页面切换后 A._cardBgStyleEl 变量还在但 DOM 节点已丢失，必须用 contains 检查
  if (!A._cardBgStyleEl || !document.body.contains(A._cardBgStyleEl)) {
    A._cardBgStyleEl = document.createElement('style');
    A._cardBgStyleEl.id = '_card_bg_global_override';
    // ★ 卡片类（用 --bg-surface 别名 --card-bg）：覆盖所有页面自定义卡片类
    // ★ KPI/数据卡片类（用 --bg-elevated 别名 --card-bg-elevated）
    A._cardBgStyleEl.textContent = [
      // ── 用 --card-bg（surface 系列）的卡片/面板/条目 ──
      // 通用卡片
      '.quality-card, .pipeline-card, .datasource-item,',
      '.left-panel, .detail-panel, .pipeline-bar,',
      '.logs-list-panel, .logs-detail-panel, .logs-search-bar, .logs-status-bar, .logs-replay-bar, .logs-filter-tabs,',
      // pipeline.html AI 日志面板（原 --bg-base，需随卡片透明度联动）
      '.log-body,',
      '.skeleton-card, .cp-card, .np-card, .doc-card, .docs-grid-panel, .docs-preview-panel, .docs-chain-panel,',
      '.ie-panel, .pm-new-form-card, .cf-panel, .cb-panel, .ds-panel, .fu-panel, .pc-card, .navtree-info-panel,',
      // 配置历史 config-history.html（ch- 前缀）
      '.ch-info-bar, .ch-filter-bar,',
      // 清除数据 clear-files.html 信息条
      '.cf-info-bar,',
      // 项目管理 project-mgmt.html
      '.pm-left-pane, .pm-right-pane, .pm-empty, .pm-project-item, .pm-version-empty, .pm-project-bar, .pm-ver-item,',
      // Prompt 模板管理 prompt-templates.html
      '.pt-info-bar, .pt-table-wrap, .pt-modal, .pt-modal-footer,',
      // 审计日志 audit-logs.html + 指令库 action-lib.html（共用 al- 前缀）
      '.al-tabs, .al-filter-bar, .al-table-wrap, .al-stats-bar, .al-drawer,',
      // 用户管理 user-mgmt.html
      '.um-stats-bar, .um-col-left, .um-col-right,',
      // 角色管理 role-mgmt.html
      '.rm-stats-bar, .rm-col-left, .rm-col-right, .rm-modal,',
      // 执行配置 Step4 step4-config.html
      '.s4-info-bar, .s4-modal, .s4-case-list, .s4-parallel-info,',
      // 项目配置 config.html
      '.cfg-group, .cfg-info-bar, .cfg-section-header, .cfg-group-header, .cfg-action-bar,',
      '.mc-section, .mc-info-bar, .mc-models-group,',
      // AI 日志面板/健康检查 ai-health.html
      '.ah-panel, .ah-tab-panel,',
      // metrics.html（AI 决策日志 + 详情面板）
      '.metrics-domain-card, .metrics-detail-panel, .metrics-detail-header, .metrics-detail-tabs, .metrics-tab,',
      '.metrics-log-section, .metrics-log-header, .metrics-log-box, .metrics-log-toolbar, .metrics-raw-box,',
      // 需求审核 review.html
      '.rv-info-bar, .rv-panel, .rv-cols, .rv-list-col, .rv-list-header, .rv-search, .rv-filter, .rv-list-wrap, .rv-detail-col,',
      // 用例审核 intent-review.html
      '.ir-info-bar, .ir-status-banner, .ir-panel, .ir-toolbar, .ir-search, .ir-filter, .ir-toolbar-info, .ir-table-wrap, .ir-chain-panel,',
      // 通用左列表+右详情组件（review/intent-review 共用骨架）
      '.md-panel, .md-list-header, .md-list-footer, .md-list-item, .md-detail-empty, .md-empty-state,',
      '{ background: var(--card-bg) !important; }',
      // ── 用 --card-bg-elevated（elevated 系列）的 KPI/数据/条目卡片 ──
      '.charts-kpi-card, .chart-card, .chart-card-header,',
      '.metrics-kpi-card, .metrics-domain-strip, .metrics-card-bar, .metrics-card-header,',
      '.docs-stat-card, .navtree-stat-card, .navtree-module-card, .ie-info-card,',
      '.ah-kpi-item, .ah-metric-item, .ah-trend-area,',
      '.pt-table thead th, .pt-modal-header, .al-table thead, .al-inst-card, .al-drawer-stat,',
      '.um-user-card, .rm-role-card, .s4-version-item, .s4-radio-card, .mc-radio-label,',
      '.rv-req-item, .rv-detail, .rv-detail-header, .rv-badge, .ir-chain-card,',
      // 通用 master-detail 详情组件（intent-review/review 共用）
      '.md-detail, .md-step-item, .md-metric-item, .md-chain-container, .md-quote-block,',
      // 配置历史条目 config-history.html
      '.ch-history-item,',
      // 清除数据分组条目 clear-files.html
      '.cf-group,',
      '{ background: var(--card-bg-elevated) !important; }'
    ].join('\n');
    // ★ 注入到 body 末尾（而不是 head），确保覆盖页面 body 内 <style> 块的卡片 background 定义
    // 因为 pipeline.html 等页面的 <style> 在 {% block content %} 内（body 中）
    document.body.appendChild(A._cardBgStyleEl);
  }
  // 清理之前的自动旋转定时器
  if (A._bgRotateInterval) { clearInterval(A._bgRotateInterval); A._bgRotateInterval = null; }
  A.api('GET', '/api/bg-config').then(function(r) {
    if (!r || !r.ok) return;
    // ★ 纯绘制属性（<html> 背景 / <body> 透明）：每次加载都重放，不影响布局尺寸
    A._applyBgPaint(r);
    // ── 影响布局的 CSS 变量：先算目标值，再与首帧快照做差异比对 ──
    // 这些变量会改变字号令牌/卡片底色，一旦写入即触发重排；与快照一致时
    // 必须"一个都不写"，否则每次切页都会重演一次全页跳变。
    var _next = {};
    var _modeIsCustom = false, _colorsArr = null;
    _next.ui_scale = (r.ui_scale !== undefined && Number(r.ui_scale) > 0) ? Number(r.ui_scale) : 1;
    // text_color 先写，custom 模式下由背景推导的色板覆盖（与原实现顺序一致）
    if (r.text_color) _next.text_tertiary = r.text_color;
    var _cardAlpha = (r.card_opacity !== undefined ? r.card_opacity : 100) / 100;
    if (_cardAlpha < 1) {
      _next.card_bg = 'rgba(17, 24, 39, ' + _cardAlpha + ')';
      _next.card_bg_elevated = 'rgba(30, 41, 59, ' + _cardAlpha + ')';
      _next.bg_surface = _next.card_bg;
      _next.bg_elevated = _next.card_bg_elevated;
    } else {
      _next.card_bg = '#111827';
      _next.card_bg_elevated = '#1e293b';
      _next.bg_surface = '#111827';
      _next.bg_elevated = '#1e293b';
    }
    var _mode = r.mode || 'custom';
    if (_mode === 'default') _mode = 'custom';
    if (_mode === 'custom' && r.color1) {
      _modeIsCustom = true;
      _colorsArr = [r.color1, r.color2, r.color3, r.color4].filter(Boolean);
      var _tp0 = A.deriveTextPalette(_colorsArr);
      _next.text_primary = _tp0.primary;
      _next.text_secondary = _tp0.secondary;
      _next.text_tertiary = _tp0.tertiary;
      // 静态渐变背景可以进快照（首帧直接画对）；自动旋转/图库模式每帧或异步变化，
      // 不进快照（它们只影响绘制、不影响布局，晚一步出现不会造成位移）。
      if (!r.auto_rotate) {
        _next.bg_css = 'linear-gradient(' + (r.angle || 64) + 'deg, ' +
          _colorsArr.map(function(c) { return A._bgHexToRgba(c, _alpha); }).join(', ') + ')';
      }
    } else if (_mode === 'none') {
      _next.bg_css = 'var(--bg-base)';
    }
    if (A._bootCfgDiff(A.readBootCfg(), _next).length === 0) return;  // 与首帧一致 → 零 DOM 写
    A.writeBootCfg(_next);
    // ★ 全局字体大小（2026-09-01）：ui_scale 驱动 --ui-scale，仅缩放 --fs-* 字号令牌
    //   （app-design.css :root 中 calc(px * var(--ui-scale,1))），不再用 body zoom 整页放大，
    //   以免放大字体时把整个页面布局撑宽、触发横向滚动条。
    //   1=标准 / 1.15=大 / 1.3=超大；旧配置或异常值回退 1（标准）。
    var _uiScale = (r.ui_scale !== undefined && Number(r.ui_scale) > 0) ? Number(r.ui_scale) : 1;
    document.documentElement.style.setProperty('--ui-scale', _uiScale);
    // ★ 手动"辅助文字色"：覆盖 --text-tertiary（次要文字），保留用户显式选择
    if (r.text_color) {
      document.documentElement.style.setProperty('--text-tertiary', r.text_color);
    }
    // ★ 卡片透明度：同时覆盖 --card-bg / --card-bg-elevated / --bg-surface / --bg-elevated
    // 默认值 100（不透明）。card_opacity 越小越透明，让背景透过来
    // ★ 通用修复：很多页面卡片直接用 var(--bg-surface) / var(--bg-elevated)（而非 --card-bg），
    //   只覆盖 --card-bg 会遗漏大量卡片（配置历史、清除数据、可视化报告等）。
    //   同时覆盖 --bg-surface / --bg-elevated，让所有用这两个变量的元素自动透明，无需维护覆盖列表。
    //   --bg-base 保持不透明（html/body 背景及图表内部小元素用，避免影响可读性）。
    var cardAlpha = (r.card_opacity !== undefined ? r.card_opacity : 100) / 100;
    if (cardAlpha < 1) {
      // #111827 = rgb(17,24,39)；#1e293b = rgb(30,41,59)
      var surfaceRgba = 'rgba(17, 24, 39, ' + cardAlpha + ')';
      var elevatedRgba = 'rgba(30, 41, 59, ' + cardAlpha + ')';
      document.documentElement.style.setProperty('--card-bg', surfaceRgba);
      document.documentElement.style.setProperty('--card-bg-elevated', elevatedRgba);
      document.documentElement.style.setProperty('--bg-surface', surfaceRgba);
      document.documentElement.style.setProperty('--bg-elevated', elevatedRgba);
    } else {
      // ★ 100% 透明度：恢复为不透明色，避免累加 rgba 半透明效果
      document.documentElement.style.setProperty('--card-bg', '#111827');
      document.documentElement.style.setProperty('--card-bg-elevated', '#1e293b');
      document.documentElement.style.setProperty('--bg-surface', '#111827');
      document.documentElement.style.setProperty('--bg-elevated', '#1e293b');
    }
    // ★ 背景（html/body）已在方法开头由 A._applyBgPaint 重放：纯绘制属性，不影响布局，
    //   与"会影响布局的 CSS 变量"分离，因此这里不再重复写背景。
    // ★ 4色块背景 ↔ 文字颜色联动（重接）：依据渐变亮度推导文字色板
    //   深色背景 → 纯白文字（--text-primary/secondary/tertiary），浅色背景 → 深字，
    //   使侧边栏步骤/菜单文字（--text-secondary）在深色背景下保持纯白可读。
    if (_modeIsCustom && _colorsArr) {
      var _tp = A.deriveTextPalette(_colorsArr);
      document.documentElement.style.setProperty('--text-primary', _tp.primary);
      document.documentElement.style.setProperty('--text-secondary', _tp.secondary);
      document.documentElement.style.setProperty('--text-tertiary', _tp.tertiary);
    }
  }).catch(function() {});
};

// applyInputStyle: 应用主题配色方案到全站 --color-primary（含 custom 自定义色）
// 配色数据统一由 A.getSchemeColors 提供（personal-config 本地预览共用同一数据源）
// ★ 同样走首帧快照 + 差异闸门：主题色一致时零 DOM 写，避免切页时的重排/重绘跳变。
A.applyInputStyle = function() {
  A._applyBootCfgLocal();
  A.api('GET', '/api/input-style').then(function(r) {
    if (!r || !r.ok || !r.scheme) return;
    var s = A.getSchemeColors(r.scheme, r.custom_color);
    if (!s) return;
    var next = {
      color_primary: s.primary,
      color_primary_soft: s.soft,
      color_primary_border: s.border,
      color_primary_hover: s.hover
    };
    if (A._bootCfgDiff(A.readBootCfg(), next).length === 0) return;  // 与首帧一致 → 零 DOM 写
    A.writeBootCfg(next);
    var root = document.documentElement;
    root.style.setProperty('--color-primary', s.primary);
    root.style.setProperty('--color-primary-soft', s.soft);
    root.style.setProperty('--color-primary-border', s.border);
    root.style.setProperty('--color-primary-hover', s.hover);
  }).catch(function() {});
};

// 主题配色方案数据源（单一来源，供 applyInputStyle 与 personal-config 预览共用）
A.getSchemeColors = function(scheme, custom_color) {
  var schemes = {
    qingluan: { primary: '#00d4aa', soft: 'rgba(0, 212, 170, 0.12)', border: 'rgba(0, 212, 170, 0.35)', hover: '#00eabc' },
    moyun:    { primary: '#64748b', soft: 'rgba(100, 116, 139, 0.12)', border: 'rgba(100, 116, 139, 0.35)', hover: '#7c8aa5' },
    songshi:  { primary: '#f59e0b', soft: 'rgba(245, 158, 11, 0.12)', border: 'rgba(245, 158, 11, 0.35)', hover: '#fbbf24' }
  };
  var s = schemes[scheme];
  // ★ custom 方案：根据 custom_color 动态生成 soft/border/hover
  if (scheme === 'custom' && custom_color) {
    var hex = custom_color.replace('#', '');
    var cr = parseInt(hex.substring(0,2),16);
    var cg = parseInt(hex.substring(2,4),16);
    var cb = parseInt(hex.substring(4,6),16);
    s = {
      primary: custom_color,
      soft: 'rgba(' + cr + ',' + cg + ',' + cb + ',0.12)',
      border: 'rgba(' + cr + ',' + cg + ',' + cb + ',0.35)',
      hover: 'rgb(' + Math.min(cr+30,255) + ',' + Math.min(cg+30,255) + ',' + Math.min(cb+30,255) + ')'
    };
  }
  return s;
};

// 通用颜色工具：hex → 指定透明度的颜色字符串（供模板做运行时透明度，避免模板内出现裸色字面量）
A.mixAlpha = function(hex, a) {
  var h = String(hex).replace('#', '');
  return 'rgba(' + parseInt(h.substring(0,2),16) + ',' + parseInt(h.substring(2,4),16) + ',' + parseInt(h.substring(4,6),16) + ',' + a + ')';
};

// ── ★ 4色块背景 ↔ 文字颜色联动（2026-09-01 重接）──
// 依据背景渐变色亮度自动推导高对比文字色板，实现"深背景→白字 / 浅背景→深字"。
// 这替代此前固定灰色文本（--text-secondary=#94a3b8）在深色背景下可读性差的缺陷。
// 计算颜色的相对亮度（0-255），用于判断背景深浅。
A.getLuminance = function(hex) {
  var h = String(hex || '').replace('#', '');
  if (h.length !== 6) return 128; // 非法/缺省值回退中间亮度
  var r = parseInt(h.substring(0,2),16);
  var g = parseInt(h.substring(2,4),16);
  var b = parseInt(h.substring(4,6),16);
  return 0.299 * r + 0.587 * g + 0.114 * b;
};

// 依据 4 个渐变色推导文字色板（primary/secondary/tertiary）。
// 取 4 色中最高亮度判断整体明暗：整体偏暗→白字系，整体偏亮→深字系。
A.deriveTextPalette = function(colors) {
  var maxLum = 0;
  (colors || []).forEach(function(c) {
    var l = A.getLuminance(c);
    if (l > maxLum) maxLum = l;
  });
  var dark = maxLum < 128; // 背景整体偏暗
  if (dark) {
    return {
      primary: '#ffffff',
      secondary: 'rgba(255,255,255,0.88)',
      tertiary: 'rgba(255,255,255,0.68)'
    };
  }
  return {
    primary: '#0f172a',
    secondary: 'rgba(15,23,42,0.85)',
    tertiary: 'rgba(15,23,42,0.62)'
  };
};

// ═══════════════════════════════════════════════════════════════════
// 页面生命周期：首帧前定型 + 页面初始化全局只执行一次
// ───────────────────────────────────────────────────────────────────
// 1) 侧栏定型前置：app-common.js 在 body 末尾、侧栏标记之后同步执行，此时侧栏 DOM
//    已可用且通常尚未首次绘制。把"注入导航项 / 权限过滤 / 展开态与滚动恢复"全部
//    放进 A.initSidebar()（幂等），可保证第一帧看到的就是最终菜单，加载后不再变形。
// 2) 页面初始化单次执行：各页面把"鉴权后初始化"写在 A._onAuthOk 上，这里把它
//    降级为**页面初始化回调**，由框架统一调度且全局只跑一次 ——
//      · 页面里 `if (A._currentUser) loadX()` 的快路径与 _onAuthOk 不会都触发；
//      · 浏览器前进/后退（bfcache）返回时不再重跑页面初始化，
//        避免菜单被重复注入、表格重建、图表重画（这些都会让"切回来"时界面很乱）。
var _pageInitFn = null;
var _pageInitDone = false;
Object.defineProperty(A, '_onAuthOk', {
  configurable: true,
  get: function() { return _pageInitFn || function() {}; },
  set: function(fn) { _pageInitFn = fn; }
});
A._runPageInit = function() {
  if (_pageInitDone) return;
  _pageInitDone = true;
  if (_pageInitFn) {
    try { _pageInitFn(); } catch(e) { console.error('[page-init] 页面初始化异常:', e); }
  }
};

// ── 页面引导（在首帧之前完成侧栏定型）──
function _initAll() {
  A.initSidebar();
  A.initUser();
}
_initAll();                       // ★ 立即执行（脚本位于 body 末尾 → 早于首次绘制）
if (document.readyState === 'loading') {
  // 兜底：幂等补一次，覆盖脚本被提前执行/动态插入的情况
  document.addEventListener('DOMContentLoaded', function() {
    A.initSidebar();
    if (!A._userInited) A.initUser();
  });
}

// ── BFCache 恢复：只刷新鉴权与徽标，不重跑页面初始化 ──
window.addEventListener('pageshow', function(e) {
  if (e.persisted) {
    A._userInited = false;
    A._currentUser = null;
    A.initUser();
  }
});

// ── 自动 ellipsis tooltip ──
// 给被 text-overflow:ellipsis 截断且没有 data-tooltip 的元素自动补上 data-tooltip 显示完整值
// 这样 hover 截断位置时优先显示完整内容，而非空白或父级说明
var ELLIPSIS_SEL = [
  '.ai-card-value', '.ai-card-label',
  '.metrics-kpi-value', '.metrics-kpi-label', '.metrics-kpi-sub',
  '.metrics-card-title', '.metrics-card-kpi-label', '.metrics-card-kpi-value',
  '.charts-kpi-value', '.charts-kpi-label', '.charts-kpi-sub',
  '.docs-stat-value', '.docs-stat-label', '.docs-stat-sub',
  '.ah-kpi-value', '.ah-kpi-label',
  '.kpi-card-value', '.kpi-card-label', '.kpi-card-sub',
  '.collapse-mini-kpi-value', '.collapse-mini-kpi-label',
  '.info-row-value'
].join(',');

A._autoTipTimer = null;
A.autoEllipsisTooltip = function() {
  if (!ELLIPSIS_SEL) return;
  var els;
  try { els = document.querySelectorAll(ELLIPSIS_SEL); } catch(e) { return; }
  for (var i = 0; i < els.length; i++) {
    var el = els[i];
    if (el.getAttribute('data-tooltip')) continue;
    if (el.scrollWidth > el.clientWidth + 1) {
      var txt = (el.textContent || '').trim();
      if (txt) {
        el.setAttribute('data-tooltip', txt);
        el.setAttribute('data-tooltip-pos', 'top');
        el.classList.add('tooltip-wide');
        el.setAttribute('data-auto-tip', '1');
      }
    } else {
      // 未截断则移除自动添加的 tooltip
      if (el.getAttribute('data-auto-tip') === '1') {
        el.removeAttribute('data-tooltip');
        el.removeAttribute('data-tooltip-pos');
        el.removeAttribute('data-auto-tip');
        el.classList.remove('tooltip-wide');
      }
    }
  }
};

A._scheduleAutoTip = function() {
  if (A._autoTipTimer) clearTimeout(A._autoTipTimer);
  A._autoTipTimer = setTimeout(function() {
    A.autoEllipsisTooltip();
    A._autoTipTimer = null;
  }, 100);
};

// DOM 就绪 + 内容变化时自动检测
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', A._scheduleAutoTip);
} else {
  A._scheduleAutoTip();
}
window.addEventListener('load', A._scheduleAutoTip);
if (window.MutationObserver) {
  var _tipObs = new MutationObserver(function() { A._scheduleAutoTip(); });
  _tipObs.observe(document.body || document.documentElement, {childList: true, subtree: true, characterData: true});
}

// ── Tooltip 自动避让视口边界 ──
// mouseover 时检测 tooltip 是否会溢出视口，自动翻转方向避免遮挡
// ★ 同时检测水平溢出（top/bottom 方向）和垂直溢出（left/right 方向），
//    通过 CSS 自定义属性 --tooltip-offset-x / --tooltip-offset-y 偏移气泡位置
A._tipOrigPos = new WeakMap();
A.initTooltipDodge = function() {
  document.addEventListener('mouseover', function(e) {
    var el = e.target;
    if (!el || el.nodeType !== 1 || !el.closest) return;
    var tipEl = el.closest('[data-tooltip]');
    if (!tipEl) return;

    // 保存原始位置（仅首次进入时记录）
    if (!A._tipOrigPos.has(tipEl)) {
      A._tipOrigPos.set(tipEl, tipEl.getAttribute('data-tooltip-pos') || 'top');
    }
    var origPos = A._tipOrigPos.get(tipEl);

    var rect = tipEl.getBoundingClientRect();
    var text = tipEl.getAttribute('data-tooltip') || '';
    var lines = text.split('\n').length;
    var estHeight = Math.max(32, lines * 20 + 16);
    var estWidth = tipEl.classList.contains('tooltip-wide') ? TOOLTIP_WIDE_W : TOOLTIP_NORMAL_W;
    var margin = 16;

    // 方向翻转
    var newPos = origPos;
    if (origPos === 'top' && rect.top < estHeight + margin) {
      newPos = 'bottom';
    } else if (origPos === 'bottom' && (window.innerHeight - rect.bottom) < estHeight + margin) {
      newPos = 'top';
    } else if (origPos === 'left' && rect.left < estWidth + margin) {
      newPos = 'right';
    } else if (origPos === 'right' && (window.innerWidth - rect.right) < estWidth + margin) {
      newPos = 'left';
    }

    // 仅在变化时写入 DOM，避免不必要的重绘
    var curPos = tipEl.getAttribute('data-tooltip-pos') || 'top';
    if (curPos !== newPos) {
      tipEl.setAttribute('data-tooltip-pos', newPos);
    }

    // ★ 水平溢出检测（top/bottom 方向）：气泡中心在元素中心，检测左右边界
    if (newPos === 'top' || newPos === 'bottom') {
      var centerX = rect.left + rect.width / 2;
      var tipRight = centerX + estWidth / 2;
      var tipLeft = centerX - estWidth / 2;
      var offsetX = 0;
      if (tipRight > window.innerWidth - margin) {
        offsetX = (window.innerWidth - margin) - tipRight;
      } else if (tipLeft < margin) {
        offsetX = margin - tipLeft;
      }
      tipEl.style.setProperty('--tooltip-offset-x', offsetX + 'px');
      tipEl.style.removeProperty('--tooltip-offset-y');
    }
    // ★ 垂直溢出检测（left/right 方向）：气泡垂直居中于元素，检测上下边界
    else if (newPos === 'left' || newPos === 'right') {
      var centerY = rect.top + rect.height / 2;
      var tipBottom = centerY + estHeight / 2;
      var tipTop = centerY - estHeight / 2;
      var offsetY = 0;
      if (tipBottom > window.innerHeight - margin) {
        offsetY = (window.innerHeight - margin) - tipBottom;
      } else if (tipTop < margin) {
        offsetY = margin - tipTop;
      }
      tipEl.style.setProperty('--tooltip-offset-y', offsetY + 'px');
      tipEl.style.removeProperty('--tooltip-offset-x');
    }
  });
};

// 自动初始化 tooltip 避让
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', A.initTooltipDodge);
} else {
  A.initTooltipDodge();
}

// ── 鼠标跟随 tooltip（全局） ──
// 将 data-tooltip 的提示内容在鼠标位置渲染为浮动气泡，替代 CSS 伪元素
(function() {
  var tipEl = null;
  var currentTarget = null;

  function getTip() {
    if (!tipEl) {
      tipEl = document.createElement('div');
      tipEl.className = 'mouse-tooltip';
      tipEl.setAttribute('data-mouse-tooltip', '1');
      tipEl.style.cssText = 'position:fixed;z-index:99999;background:#1e293b;color:#f1f5f9;padding:6px 10px;border-radius:4px;font-size:11px;line-height:1.4;min-width:100px;pointer-events:none;white-space:pre-wrap;word-break:keep-all;overflow-wrap:break-word;box-shadow:0 4px 12px rgba(0,0,0,0.25);display:none;font-family:var(--font-sans,sans-serif);';
      document.body.appendChild(tipEl);
    }
    return tipEl;
  }

  function _decodeEntities(str) {
    var txt = document.createElement('textarea');
    txt.innerHTML = str;
    return txt.value;
  }

  function showTip(text) {
    var tip = getTip();
    tipEl.textContent = _decodeEntities(text);
    // 长文本卡片（tooltip-wide）用更宽的气泡，避免长 tooltip 频繁换行
    tipEl.style.maxWidth = (currentTarget && currentTarget.classList.contains('tooltip-wide')) ? (TOOLTIP_WIDE_W + 'px') : (TOOLTIP_NORMAL_W + 'px');
    tipEl.style.display = 'block';
  }

  function hideTip() {
    if (tipEl) tipEl.style.display = 'none';
    currentTarget = null;
  }

  document.addEventListener('mouseover', function(e) {
    var target = e.target.closest('[data-tooltip]');
    if (!target || target === currentTarget) return;
    // 跳过由 JS 自动生成的 ellipsis tooltip（避免和鼠标跟随 tooltip 闪烁）
    currentTarget = target;
    var text = target.getAttribute('data-tooltip');
    if (!text) { hideTip(); return; }
    showTip(text);
  });

  document.addEventListener('mousemove', function(e) {
    if (!currentTarget || !tipEl || tipEl.style.display === 'none') return;
    var x = e.clientX + 14;
    var y = e.clientY + 14;
    var tw = tipEl.offsetWidth;
    var th = tipEl.offsetHeight;
    if (x + tw > window.innerWidth - 8) x = e.clientX - tw - 14;
    if (y + th > window.innerHeight - 8) y = e.clientY - th - 14;
    tipEl.style.left = x + 'px';
    tipEl.style.top = y + 'px';
  });

  // mouseout 延迟隐藏，避免在元素间快速移动时闪烁
  var hideTimer = null;
  document.addEventListener('mouseout', function(e) {
    var target = e.target.closest('[data-tooltip]');
    if (!target || target !== currentTarget) return;
    clearTimeout(hideTimer);
    hideTimer = setTimeout(function() {
      var hovered = document.querySelector('[data-tooltip]:hover');
      if (!hovered) hideTip();
    }, 80);
  });

  // ── 鼠标跟随 tooltip 点击或滚动时立即隐藏
  document.addEventListener('click', function() { hideTip(); });
  document.addEventListener('scroll', function() { hideTip(); }, true);
})();

// ═══════════════════════════════════════════════
// 多页面导航 — 浏览器原生跳转，每页独立加载
// 白屏优化：见 app-design.css 中 .content-body 的 fadeIn 动画
// ═══════════════════════════════════════════════

// ── 导航加载条（即时视觉反馈）──
// 点击侧边栏/内部链接后立即显示，让用户感知"正在跳转"
// 不拦截导航，页面卸载时自然消失
A._navBar = null;
A._showNavBar = function() {
  if (!A._navBar) {
    A._navBar = document.createElement('div');
    A._navBar.id = 'nav-loading-bar';
    A._navBar.style.cssText = 'position:fixed;top:0;left:0;height:2px;background:var(--color-primary);z-index:99999;width:0;box-shadow:0 0 6px rgba(0,212,170,0.4);pointer-events:none;';
    document.body.appendChild(A._navBar);
  }
  A._navBar.style.transition = 'none';
  A._navBar.style.width = '0';
  A._navBar.style.opacity = '1';
  // 强制重排后启动动画
  A._navBar.offsetHeight;
  A._navBar.style.transition = 'width 0.8s cubic-bezier(0.1, 0.7, 0.3, 1)';
  A._navBar.style.width = '75%';
};

// 判断是否为内部页面导航（排除外部链接、下载、锚点、登出等）
function _isInternalNav(link) {
  var href = link.getAttribute('href');
  if (!href) return false;
  if (link.target === '_blank') return false;
  if (link.hasAttribute('download')) return false;
  if (href.startsWith('#')) return false;
  if (href.startsWith('javascript:')) return false;
  if (href.startsWith('mailto:')) return false;
  if (href.startsWith('http') && !href.startsWith(window.location.origin)) return false;
  if (href.indexOf('/logout') !== -1 || href.indexOf('/login') !== -1) return false;
  if (href.indexOf('/api/') === 0) return false;
  return true;
}

// 全局委托：内部链接点击 → 关闭 SSE/轮询 → 显示加载条 → 浏览器正常跳转
document.addEventListener('click', function(e) {
  try {
    var link = e.target.closest('a');
    if (!link) return;
    if (!_isInternalNav(link)) return;
    // ★ 关键：导航前主动关闭 SSE 连接和轮询，释放浏览器连接池
    // 否则新页面请求可能排队等待 5 秒直到旧连接超时
    if (A._cleanupPipeline) { A._cleanupPipeline(); }
    A.stopAllAdaptivePolls();
    A._stopStepBadgePolling();
    A.abortAllRequests();
    // 不阻止默认行为，只在跳转前给视觉反馈
    A._showNavBar();
  } catch(ex) {}
});

// ── 骨架屏工具 ──
// A.skeleton(el, 'kpi') — 在容器内显示骨架屏占位
// A.skeletonClear(el) — 清除骨架屏
A._skeletonPresets = {
  kpi: '<div class="skeleton skeleton-value"></div><div class="skeleton skeleton-text narrow"></div>',
  metric: '<div class="skeleton skeleton-value-sm"></div>',
  list: '<div class="skeleton skeleton-text wide"></div><div class="skeleton skeleton-text"></div><div class="skeleton skeleton-text narrow"></div>',
  card: '<div class="skeleton skeleton-text wide"></div><div class="skeleton skeleton-text"></div><div class="skeleton skeleton-text narrow"></div>',
  badge: '<div class="skeleton" style="width:48px;height:20px;display:inline-block;border-radius:var(--radius-full);"></div>',
};
A.skeleton = function(el, preset) {
  var container = (typeof el === 'string') ? A.$(el) : el;
  if (!container) return;
  var html = A._skeletonPresets[preset] || preset || '';
  if (html) container.innerHTML = html;
};
A.skeletonClear = function(el) {
  var container = (typeof el === 'string') ? A.$(el) : el;
  if (!container) return;
  container.innerHTML = '';
};

// ── 异步区域占位契约（2026-09-21，根因修复 CLS）──────────────────────
// 根因（实测，见 logs/_um_cls_trace_out.json）：异步区域首帧不占位，
//   经历「空 → 16px『加载中』spinner → 真实内容」多次高度变化，
//   每变化一次就把下方内容整体推移一次 → 单次访问可产生 2~3 次位移。
//   既有 A.skeleton 只填内容、**不预留高度**，所以挡不住位移。
// 这里不造新轮子，只在既有骨架屏之上补「空间预留」这一层。
// 契约：A.asyncHold(el, preset) —— 打 data-async 标记 + 画骨架。
//       预留高度由 CSS 的 [data-async="..."] 定义（单一真源，app-design.css），
//       首帧即生效，不依赖 JS 执行时机。
// ★ 故意**不提供 release**：预留是容器的最小高度 —— 内容更高时它是 no-op，
//   内容更矮时它恰好挡住塌陷位移，两个方向都零位移。实测"数据就绪后摘掉
//   预留"反而把最大位移从 0.036 推高到 0.16（logs/_ft_um3.log 隔离对照），
//   释放这条路被证据否决，故不暴露这个 API。
A.asyncHold = function(el, preset) {
  var c = (typeof el === 'string') ? A.$(el) : el;
  if (!c) return;
  preset = preset || c.getAttribute('data-async') || 'card';
  c.setAttribute('data-async', preset);
  A.skeleton(c, preset);
};

// ── 智能自适应轮询 ──
// A.adaptivePoll(key, fn, { fastMs: 2000, slowMs: 10000, idleMs: 30000 })
// 自动根据页面可见性、用户活跃度调整轮询频率
A._pollTimers = {};
A._pollStates = {};
A._lastUserActivity = Date.now();
A._pollActivityBound = false;

A.adaptivePoll = function(key, fn, opts) {
  opts = opts || {};
  var fastMs = opts.fastMs || 2000;
  var slowMs = opts.slowMs || 10000;
  var idleMs = opts.idleMs || 30000;

  // 绑定全局用户活动检测（仅一次）
  if (!A._pollActivityBound) {
    A._pollActivityBound = true;
    ['mousemove', 'keydown', 'scroll', 'click', 'touchstart'].forEach(function(evt) {
      document.addEventListener(evt, function() { A._lastUserActivity = Date.now(); }, { passive: true });
    });
  }

  // 停止旧定时器
  A.stopAdaptivePoll(key);

  // ★ 修复：加失败退避机制，防止后端 5xx 时前端永不停止地以固定节奏重试
  var maxFails = opts.maxFails || 10;
  var state = A._pollStates[key] = { running: true, fn: fn, opts: opts, failCount: 0 };

  function schedule() {
    if (!state.running) return;
    var now = Date.now();
    var idleSince = now - A._lastUserActivity;
    var hidden = document.hidden;

    var delay = fastMs;
    if (hidden) {
      delay = idleMs; // 页面隐藏时最慢
    } else if (idleSince > 60000) {
      delay = slowMs; // 用户 1 分钟无操作
    } else if (idleSince > 300000) {
      delay = idleMs; // 用户 5 分钟无操作
    }

    // ★ 修复：连续失败时指数退避，最多 maxFails 次后停止轮询
    if (state.failCount > 0) {
      delay = Math.min(delay * Math.pow(2, state.failCount), 60000); // 上限 60 秒
    }

    A._pollTimers[key] = setTimeout(function() {
      if (!state.running) return;
      // ★ 修复：支持 async fn，检测失败后退避
      var result;
      try { result = fn(); } catch(e) { result = null; }
      if (result && typeof result.then === 'function') {
        result.then(function(r) {
          // 检测失败：返回 ok:false 表示失败
          if (r && r.ok === false) {
            state.failCount++;
          } else {
            state.failCount = 0;
          }
          if (state.failCount >= maxFails) {
            // 连续失败超过上限，停止轮询
            A.stopAdaptivePoll(key);
            return;
          }
          schedule();
        }).catch(function() {
          state.failCount++;
          if (state.failCount >= maxFails) {
            A.stopAdaptivePoll(key);
            return;
          }
          schedule();
        });
      } else {
        // 同步 fn，成功则重置失败计数
        state.failCount = 0;
        schedule();
      }
    }, delay);
  }

  // 首次立即执行
  try { fn(); } catch(e) {}
  schedule();
};

A.stopAdaptivePoll = function(key) {
  var state = A._pollStates[key];
  if (state) state.running = false;
  if (A._pollTimers[key]) {
    clearTimeout(A._pollTimers[key]);
    delete A._pollTimers[key];
  }
};

// 停止所有自适应轮询（SPA 导航前调用）
A.stopAllAdaptivePolls = function() {
  Object.keys(A._pollStates || {}).forEach(function(k) { A._pollStates[k].running = false; });
  Object.keys(A._pollTimers || {}).forEach(function(k) {
    clearTimeout(A._pollTimers[k]);
    delete A._pollTimers[k];
  });
};

// ── 请求去重器 ──
// A.dedupedApi(method, url, body) — 相同 URL+method 的请求共享同一个 Promise
A._dedupedRequests = {};
A.dedupedApi = function(method, url, body) {
  var key = method + ':' + url + ':' + (body ? JSON.stringify(body) : '');
  if (A._dedupedRequests[key]) return A._dedupedRequests[key];
  var promise = A.api(method, url, body).finally(function() {
    delete A._dedupedRequests[key];
  });
  A._dedupedRequests[key] = promise;
  return promise;
};

// ── 批量渲染调度器 ──
// A.batchRender(fn) — 将 DOM 更新延迟到下一个 requestAnimationFrame
// 多次调用自动合并为一次渲染，避免频繁重排
A._batchQueue = [];
A._batchScheduled = false;
A.batchRender = function(fn) {
  A._batchQueue.push(fn);
  if (!A._batchScheduled) {
    A._batchScheduled = true;
    requestAnimationFrame(function() {
      var queue = A._batchQueue;
      A._batchQueue = [];
      A._batchScheduled = false;
      for (var i = 0; i < queue.length; i++) {
        try { queue[i](); } catch(e) {}
      }
    });
  }
};

// ── 二次确认弹层（T04/P1-5 新增，替代原生 confirm()）──
// A.confirm(msg, opts) → Promise<bool>
//   opts.danger   = true   → 红色警示样式 + 警示条
//   opts.title    : 标题，缺省「操作确认」
//   opts.okText / opts.cancelText : 按钮文案
//   opts.detail   : 补充说明（次要文字，可选）
// ★ 全程 textContent 赋值，不使用 innerHTML —— msg/detail 可能含用户数据
//   （如开关 label、用例名），走 innerHTML 会成为 XSS 注入点（AGENTS 防线 3）。
// ★ 为 T05/P2-2 全站 confirm( → A.confirm() 收敛打底，故签名与原生保持兼容语义。
A._confirmOpen = false;
A.confirm = function(msg, opts) {
  opts = opts || {};
  // 同一时刻只允许一个确认弹层，重复调用直接判否，避免叠层与状态错乱
  if (A._confirmOpen) return Promise.resolve(false);
  A._confirmOpen = true;

  return new Promise(function(resolve) {
    var danger = !!opts.danger;

    var overlay = document.createElement('div');
    overlay.className = 'confirm-modal-overlay';

    var box = document.createElement('div');
    box.className = 'confirm-modal' + (danger ? ' danger' : '');
    box.setAttribute('role', 'alertdialog');
    box.setAttribute('aria-modal', 'true');

    var title = document.createElement('div');
    title.className = 'confirm-modal-title';
    title.textContent = opts.title || (danger ? '高风险操作确认' : '操作确认');
    box.appendChild(title);

    if (danger) {
      var warn = document.createElement('div');
      warn.className = 'confirm-modal-warning';
      warn.textContent = opts.warning
        || '该操作会改变引擎运行行为，可能影响后续执行结果与门禁判定，请确认已知晓风险。';
      box.appendChild(warn);
    }

    var body = document.createElement('div');
    body.className = 'confirm-modal-body';
    body.textContent = msg == null ? '' : String(msg);
    box.appendChild(body);

    if (opts.detail) {
      var detail = document.createElement('div');
      detail.className = 'confirm-modal-detail';
      detail.textContent = String(opts.detail);
      box.appendChild(detail);
    }

    var actions = document.createElement('div');
    actions.className = 'confirm-modal-actions';
    var btnCancel = document.createElement('button');
    btnCancel.type = 'button';
    btnCancel.className = 'confirm-modal-btn cancel';
    btnCancel.textContent = opts.cancelText || '取消';
    var btnOk = document.createElement('button');
    btnOk.type = 'button';
    btnOk.className = 'confirm-modal-btn ok' + (danger ? ' danger' : '');
    btnOk.textContent = opts.okText || '确认';
    actions.appendChild(btnCancel);
    actions.appendChild(btnOk);
    box.appendChild(actions);

    overlay.appendChild(box);
    document.body.appendChild(overlay);
    try { btnCancel.focus(); } catch (e) { /* 焦点设置失败不影响可用性 */ }

    var settled = false;
    function close(result) {
      if (settled) return;
      settled = true;
      A._confirmOpen = false;
      document.removeEventListener('keydown', onKey, true);
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
      resolve(!!result);
    }
    function onKey(e) {
      if (e.key === 'Escape') { e.preventDefault(); close(false); }
      else if (e.key === 'Enter' && !danger) { e.preventDefault(); close(true); }
    }
    btnOk.addEventListener('click', function() { close(true); });
    btnCancel.addEventListener('click', function() { close(false); });
    overlay.addEventListener('click', function(e) { if (e.target === overlay) close(false); });
    document.addEventListener('keydown', onKey, true);
  });
};

// 点击"管理上传文件"时在当前页面内打开模态框，而非跳转页面
A.openUploadModal = function() {
  // 移除已存在的模态框
  var existing = document.getElementById('uploadModal');
  if (existing) existing.remove();

  // 模态框遮罩层
  var overlay = document.createElement('div');
  overlay.id = 'uploadModal';
  overlay.className = 'modal-overlay';

  // 模态框容器
  var container = document.createElement('div');
  container.className = 'modal-container';

  // 标题栏
  var header = document.createElement('div');
  header.className = 'modal-header';
  header.innerHTML = '<div class="modal-header-title">文件上传</div>' +
    '<button class="modal-close-btn" id="closeUploadModal">✕</button>';

  // 内容区（iframe 加载上传页面）
  var body = document.createElement('div');
  body.className = 'modal-body';
  var iframe = document.createElement('iframe');
  iframe.src = '/file-upload';
  body.appendChild(iframe);

  container.appendChild(header);
  container.appendChild(body);
  overlay.appendChild(container);
  document.body.appendChild(overlay);

  // 关闭按钮事件
  document.getElementById('closeUploadModal').addEventListener('click', function() {
    overlay.remove();
  });

  // 点击背景关闭
  overlay.addEventListener('click', function(e) {
    if (e.target === overlay) overlay.remove();
  });
};

})();
