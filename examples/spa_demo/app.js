/* spa_demo — 图书清单 SPA（纯前端 hash 路由 + 客户端渲染）
 * ============================================================
 * [未复核-需 GLM/DeepSeek 把关] 新靶子系统（2026-08-10 新增，E2E 通用性验证用）。
 *
 * SPA 型系统特征：
 *   1. 前端渲染（非服务端模板）：所有视图由 JS 动态构建 DOM
 *   2. 客户端路由：hash 切换（#/books、#/books/new、#/books/:id）
 *   3. 动态 DOM：列表/详情/表单全部运行时生成
 *   4. 表单校验：title 必填、price>0、stock>=0，错误内联提示
 *   5. 本地状态：localStorage 持久化图书数据
 *
 * 安全约定：渲染用户数据一律使用 textContent / createElement，
 *   不使用 innerHTML 拼接（规避 XSS，也通过 pre-commit innerHTML 检查）。
 */
(function () {
  'use strict';

  var STORAGE_KEY = 'spa_demo_books_v1';
  var DEFAULT_BOOKS = [
    { id: 1, title: 'E2E 智能测试引擎实战', author: 'Alice', price: 99.0, stock: 50 },
    { id: 2, title: '跨系统复用测试方法论', author: 'Bob', price: 59.9, stock: 10 },
    { id: 3, title: 'SPA 自动化测试入门', author: 'Carol', price: 39.5, stock: 0 }
  ];

  var state = { books: [], nextId: 4 };

  /* ── 本地状态（localStorage 持久化） ── */
  function loadState() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        state = JSON.parse(raw);
        if (!state.books || !state.nextId) throw new Error('bad state');
        return;
      }
    } catch (e) {
      // 状态损坏时回退默认种子
    }
    state = { books: DEFAULT_BOOKS.slice(), nextId: 4 };
    saveState();
  }

  function saveState() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  }

  function findBook(id) {
    for (var i = 0; i < state.books.length; i++) {
      if (state.books[i].id === id) return state.books[i];
    }
    return null;
  }

  /* ── DOM 工具（纯 textContent，无 innerHTML） ── */
  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function clearView() {
    var view = document.getElementById('view');
    while (view.firstChild) view.removeChild(view.firstChild);
    return view;
  }

  /* ── 路由：hash 解析 → 视图渲染 ── */
  function parseHash() {
    var hash = location.hash || '#/books';
    var parts = hash.replace(/^#\/?/, '').split('/');
    return {
      route: parts[0] || 'books',
      sub: parts[1] || '',
      id: parts[1] ? parseInt(parts[1], 10) : null
    };
  }

  function router() {
    var r = parseHash();
    var view = clearView();
    if (r.route === 'books' && r.sub === 'new') {
      renderForm(view);
    } else if (r.route === 'books' && r.id) {
      renderDetail(view, r.id);
    } else if (r.route === 'books') {
      renderList(view);
    } else if (r.route === '' || r.route === 'home') {
      renderList(view);
    } else {
      renderNotFound(view, r.route);
    }
  }

  /* ── 视图：列表（前端渲染 + 动态 DOM） ── */
  function renderList(view) {
    view.appendChild(el('h2', null, '图书列表（客户端渲染）'));
    var card = el('div', 'card');
    var ul = el('ul');
    if (state.books.length === 0) {
      ul.appendChild(el('li', null, '暂无图书，请新增。'));
    } else {
      state.books.forEach(function (book) {
        var li = el('li');
        var left = el('span');
        left.appendChild(el('strong', null, book.title));
        left.appendChild(el('div', 'meta', '作者: ' + (book.author || '-') +
          ' | 价格: ¥' + Number(book.price).toFixed(2) + ' | 库存: ' + book.stock));
        var right = el('span');
        var detailBtn = el('button', 'btn', '详情');
        detailBtn.addEventListener('click', function () {
          location.hash = '#/books/' + book.id;
        });
        var delBtn = el('button', 'btn danger', '删除');
        delBtn.addEventListener('click', function () {
          deleteBook(book.id);
          renderList(clearView());
        });
        right.appendChild(detailBtn);
        right.appendChild(delBtn);
        li.appendChild(left);
        li.appendChild(right);
        ul.appendChild(li);
      });
    }
    card.appendChild(ul);
    view.appendChild(card);
    var addLink = el('a', 'btn', '+ 新增图书');
    addLink.href = '#/books/new';
    view.appendChild(addLink);
  }

  /* ── 视图：详情 ── */
  function renderDetail(view, id) {
    var book = findBook(id);
    if (!book) {
      renderNotFound(view, 'book-' + id);
      return;
    }
    view.appendChild(el('h2', null, '图书详情'));
    var card = el('div', 'card');
    card.appendChild(el('p', null, '标题: ' + book.title));
    card.appendChild(el('p', null, '作者: ' + (book.author || '-')));
    card.appendChild(el('p', null, '价格: ¥' + Number(book.price).toFixed(2)));
    card.appendChild(el('p', null, '库存: ' + book.stock));
    view.appendChild(card);
    var back = el('a', 'btn', '← 返回列表');
    back.href = '#/books';
    view.appendChild(back);
  }

  /* ── 视图：表单 + 客户端校验 ── */
  function renderForm(view) {
    view.appendChild(el('h2', null, '新增图书'));
    var card = el('div', 'card');
    var form = el('form');

    var titleLabel = el('label', null, '标题 *');
    var titleInput = el('input');
    titleInput.type = 'text';
    titleInput.id = 'f_title';
    titleInput.placeholder = '请输入书名';
    var titleErr = el('div', 'error', '');

    var row = el('div', 'row');
    var authorBox = el('div');
    authorBox.appendChild(el('label', null, '作者'));
    var authorInput = el('input');
    authorInput.type = 'text';
    authorInput.id = 'f_author';
    authorBox.appendChild(authorInput);

    var priceBox = el('div');
    priceBox.appendChild(el('label', null, '价格 *'));
    var priceInput = el('input');
    priceInput.type = 'number';
    priceInput.id = 'f_price';
    priceInput.step = '0.01';
    priceBox.appendChild(priceInput);
    var priceErr = el('div', 'error', '');
    priceBox.appendChild(priceErr);

    var stockBox = el('div');
    stockBox.appendChild(el('label', null, '库存 *'));
    var stockInput = el('input');
    stockInput.type = 'number';
    stockInput.id = 'f_stock';
    stockInput.step = '1';
    stockBox.appendChild(stockInput);
    var stockErr = el('div', 'error', '');
    stockBox.appendChild(stockErr);

    row.appendChild(authorBox);
    row.appendChild(priceBox);
    row.appendChild(stockBox);

    var submit = el('button', 'btn', '保存');
    submit.type = 'submit';
    submit.id = 'f_submit';

    form.appendChild(titleLabel);
    form.appendChild(titleInput);
    form.appendChild(titleErr);
    form.appendChild(row);
    form.appendChild(submit);

    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var errors = validate(titleInput.value, priceInput.value, stockInput.value);
      titleErr.textContent = errors.title || '';
      priceErr.textContent = errors.price || '';
      stockErr.textContent = errors.stock || '';
      if (errors.valid) {
        state.books.push({
          id: state.nextId++,
          title: titleInput.value.trim(),
          author: authorInput.value.trim() || '-',
          price: parseFloat(priceInput.value),
          stock: parseInt(stockInput.value, 10)
        });
        saveState();
        location.hash = '#/books';
      }
    });

    card.appendChild(form);
    view.appendChild(card);
  }

  /* ── 表单校验：title 必填 / price>0 / stock>=0 ── */
  function validate(title, price, stock) {
    var errors = { title: '', price: '', stock: '', valid: true };
    if (!title || !title.trim()) {
      errors.title = '标题不能为空';
      errors.valid = false;
    } else if (title.trim().length > 120) {
      errors.title = '标题长度不能超过 120';
      errors.valid = false;
    }
    var p = parseFloat(price);
    if (price === '' || isNaN(p) || p <= 0) {
      errors.price = '价格必须大于 0';
      errors.valid = false;
    }
    var s = parseInt(stock, 10);
    if (stock === '' || isNaN(s) || s < 0) {
      errors.stock = '库存不能为负数';
      errors.valid = false;
    }
    return errors;
  }

  /* ── 操作：删除图书 ── */
  function deleteBook(id) {
    state.books = state.books.filter(function (b) { return b.id !== id; });
    saveState();
  }

  /* ── 视图：404 ── */
  function renderNotFound(view, what) {
    view.appendChild(el('h2', null, '404 未找到'));
    view.appendChild(el('p', null, '路由不存在: ' + (what || 'unknown')));
    var back = el('a', 'btn', '← 回列表');
    back.href = '#/books';
    view.appendChild(back);
  }

  /* ── 启动 ── */
  window.addEventListener('hashchange', router);
  loadState();
  router();
})();
