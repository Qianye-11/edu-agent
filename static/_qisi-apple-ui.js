/* ============================================================================
   启思 · QISI —— UI 设计层的**行为脚本**
   用户要求（原文）：「任务栏的最下方不是有个常规设置吗，点击主题那个按钮，
                   可以把我们所有做的ui返回为原版，可以来回切换」

   背景：侧栏底部 ⚙ 打开的是「常规」设置面板，里面「外观与语言」卡片第一行
   就是「主题」+ 一颗开关。原版里所有 `.switch` 都是**纯装饰**——
   `wireToggles()` 只做 `classList.toggle('on')`，不接任何逻辑，
   所以把这颗开关改成「启思设计 ↔ 原版界面」的切换器，**不占用任何原有功能**。

   ★ 为什么非要脚本不可：
     设计层是一整张 <style>，「回到原版」最干净的做法就是把它整体停用
     （HTMLStyleElement.disabled）。CSS 自己做不到这件事，
     而伪元素也接不到点击。所以这里追加的是一段**极小的、等幂的**脚本。

   ★ 为什么文案也搬到这里：
     原本「启思」「老师」两处文案是写进 base HTML 的 PATCH —— 那样一旦切回原版，
     侧栏还写着「启思」，就不是真的原版了。搬进本脚本后：
       「停用注入块」 == 「原版」，逐字节、逐像素都成立。
     base HTML 因此回到 100% 原始状态（sync.py 的 PATCHES 已清空）。

   ★ 性能：只用两个 MutationObserver，且都不递归整棵文档 ——
       · `document.body` + childList（**不递归**）：设置面板是 body 的直接子节点，
         打开/关闭才会触发，聊天流式输出完全不会打到它。
       · `#greet` 单节点：问候语会被应用自己的函数重写，只盯这一个元素。
     另外所有 DOM 改动前都先比对当前值，不做无谓的写操作。
   ============================================================================ */
(function () {
  'use strict';

  var KEY = 'qisi-ui';                 /* 'qisi'(默认) | 'original' */
  var STYLE_ID = 'qisi-apple-ui';
  var on = true;                        /* 设计层是否启用 */

  /* ---- 文案微调：逐条都是用户点名的，严格按字面，不顺手改同名处 ----
     ① 侧栏品牌「教育 Agent」→「启思」（全站另有 5 处同名，不动）
     ② 问候语「王老师」→「老师」（只动 hero 那处；设置面板里的默认用户名不动）*/
  function brand() {
    var el = document.querySelector('.side-brand .name');
    if (!el) return;
    var want = on ? '启思' : '教育 Agent';
    if (el.textContent !== want) el.textContent = want;
  }
  function greet() {
    var g = document.getElementById('greet');
    if (!g) return;
    var s = g.textContent;
    if (on) {
      if (s.indexOf('王老师') > -1) g.textContent = s.split('王老师').join('老师');
    } else if (/，老师\s*$/.test(s)) {
      /* 反向要精确到行尾：学生的问候语是「…，同学」，不能误伤 */
      g.textContent = s.replace(/，老师(\s*)$/, '，王老师$1');
    }
  }

  /* ---- 开关状态回读：面板副标题兼作状态显示 ----
     文案长度比原文案更短，不会撑出第二行（行高不变）。
     ★ 刻意在两种模式下都写状态文案，而不是在原版态还原成「选择教育 Agent 的界面外观」：
        这颗开关被我们改了用途，它必须自己说清「现在是什么状态」，
        否则用户点完不知道自己在哪一边。这是有意的取舍，不是漏改。 */
  function subtitle(sw) {
    var row = sw.closest('.set-row');
    var s = row && row.querySelector('.sl .s');
    if (!s) return;
    var want = on ? '正在使用：启思设计' : '正在使用：原版界面';
    if (s.textContent !== want) s.textContent = want;
  }

  function themeSwitch() {
    var rows = document.querySelectorAll('.modal .set-row');
    for (var i = 0; i < rows.length; i++) {
      var t = rows[i].querySelector('.sl .t');
      if (t && t.textContent.replace(/\s+/g, '') === '主题') {
        var sw = rows[i].querySelector('.switch');
        if (sw) return sw;
      }
    }
    return null;
  }
  /* 面板每次打开都是新建的，所以每次都要把开关拨到真实状态 */
  function syncSwitch() {
    var sw = themeSwitch();
    if (!sw) return;
    sw.classList.toggle('on', on);
    subtitle(sw);
  }

  function apply() {
    var el = document.getElementById(STYLE_ID);
    if (el) {
      /* 双重保险：disabled 是正解（HTMLStyleElement.disabled），
         media='not all' 兜底那些忽略 disabled 的环境。两者语义一致，不会打架。 */
      el.disabled = !on;
      el.media = on ? '' : 'not all';
    }
    document.documentElement.classList.toggle('qisi-original', !on);
    brand();
    greet();
    mountFavicon();          /* 标签页图标跟着一起切（函数声明提升，可先用后定义） */
  }

  /* 点击走**捕获阶段**：要先于应用自己的 wireToggles 拿到事件。
     ⚠️⚠️ 这里刻意**只改副标题、绝不碰开关的 class**：
         应用的监听器紧跟其后会做一次 `classList.toggle('on')`，
         开关的 class 由应用自己翻，方向正好对；
         如果这里也去 syncSwitch() 强制一次 class，就会被应用再翻回去，
         结果开关显示与实际状态**正好相反**。
     ⚠️ 副标题必须在这里手动刷新：body 的 observer 只监听 childList，
         点开关不新增/删除 body 子节点，observer 根本不会响。 */
  document.addEventListener('click', function (e) {
    var sw = e.target && e.target.closest
      ? e.target.closest('.modal .set-row .switch') : null;
    if (!sw) return;
    var row = sw.closest('.set-row');
    var t = row && row.querySelector('.sl .t');
    if (!(t && t.textContent.replace(/\s+/g, '') === '主题')) return;
    on = !on;
    try { localStorage.setItem(KEY, on ? 'qisi' : 'original'); } catch (err) {}
    apply();
    subtitle(sw);
  }, true);

  /* ==================== 入场动画（开屏 splash） ====================
     一次性：页面加载时播一遍「欢迎使用启思 / 启学，让思考自然发生」，
     播完淡出、摘除 DOM。样式在 _qisi-apple-ui.css 的 .qisi-splash 段。
     ★ 双条件退场：动画自然播完（≈3.2s）+ 一个 3.5s 的兜底定时器，
       谁先到谁摘除。就算 CSS 动画因为某种原因没跑，兜底也会把遮罩拿掉，
       绝不会把用户挡在开屏后面。
     ★ prefers-reduced-motion 时直接不挂（CSS 里也有 display:none 兜底）。 */
  function mountSplash() {
    if (!on) return;                            /* 原版界面：不播 */
    if (document.querySelector('.qisi-splash')) return;   /* 已挂，幂等 */
    var mq;
    try { mq = window.matchMedia('(prefers-reduced-motion: reduce)'); }
    catch (err) { mq = null; }
    if (mq && mq.matches) return;               /* 无动画偏好：跳过 */

    var chars = ['欢', '迎', '使', '用', '启', '思'];
    var w = '';
    for (var i = 0; i < chars.length; i++) {
      w += '<span class="qs-w"><i>' + chars[i] + '</i></span>';
    }
    var el = document.createElement('div');
    el.className = 'qisi-splash';
    el.setAttribute('aria-hidden', 'true');
    el.innerHTML =
      '<span class="qs-si">' +
        '<span class="qs-glow" aria-hidden="true">' +
          '<i class="qs-gb qs-gb1"></i><i class="qs-gb qs-gb2"></i>' +
          '<i class="qs-gb qs-gb3"></i><i class="qs-gb qs-gb4"></i>' +
        '</span>' +
        '<span class="qs-vrule" aria-hidden="true"></span>' +
        '<span class="qs-streak" aria-hidden="true"></span>' +
        '<div class="qs-title">' + w + '</div>' +
        '<div class="qs-hrule" aria-hidden="true"></div>' +
        '<div class="qs-subtitle">启学，让思考自然发生</div>' +
      '</span>';
    document.body.appendChild(el);

    /* 兜底：无论动画跑没跑完，3.5s 后一定摘除 */
    setTimeout(function () {
      if (el.parentNode) el.parentNode.removeChild(el);
    }, 3500);
  }

  /* ==================== 主题色调色（Tint） ====================
     在「外观与语言」卡片末尾追加一行「主题色」，6 个色块药丸并排。
     点色块 → html[data-tint="..."] 覆盖 --blue 系变量 → 全站即时换色。
     ★ 只追加、不改任何既有控件；几何零位移。
     ★ 面板每次打开都是新建的，所以每次都要重新注入 + 回显当前色。 */
  var TINT_KEY = 'qisi-tint';
  var TINTS = [
    { k: 'red',    n: '红' },
    { k: 'orange', n: '橙' },
    { k: 'yellow', n: '黄' },
    { k: 'green',  n: '绿' },
    { k: 'blue',   n: '蓝' },
    { k: 'cyan',   n: '青' }
  ];
  function tintLabel(k) {
    for (var i = 0; i < TINTS.length; i++) if (TINTS[i].k === k) return TINTS[i].n;
    return '';
  }
  function applyTint(k) {
    var root = document.documentElement;
    if (k) root.setAttribute('data-tint', k);
    else root.removeAttribute('data-tint');
  }
  function syncTint() {
    /* 面板里的色块高亮跟上当前色 */
    var sws = document.querySelectorAll('.modal .tint-swatch');
    for (var i = 0; i < sws.length; i++) {
      sws[i].classList.toggle('active', sws[i].getAttribute('data-tint') === (document.documentElement.getAttribute('data-tint') || ''));
    }
  }
  function mountThemeTint() {
    /* 找到「语言」行所在的 set-card，往里追加主题色行（幂等） */
    var rows = document.querySelectorAll('.modal .set-row');
    var langCard = null;
    for (var i = 0; i < rows.length; i++) {
      var t = rows[i].querySelector('.sl .t');
      if (t && t.textContent.replace(/\s+/g, '') === '语言') {
        langCard = rows[i].closest('.set-card');
        break;
      }
    }
    if (!langCard) return;
    if (langCard.querySelector('.qisi-tint-row')) return;   /* 已注入 */

    var sw = '';
    for (var j = 0; j < TINTS.length; j++) {
      sw += '<button type="button" class="tint-swatch" data-tint="' + TINTS[j].k +
            '" title="' + TINTS[j].n + '" aria-label="主题色：' + TINTS[j].n + '"></button>';
    }
    var row = document.createElement('div');
    row.className = 'set-row qisi-tint-row';
    row.innerHTML =
      '<div class="sl"><div class="t">主题色</div>' +
      '<div class="s">选择教育 Agent 的强调色</div></div>' +
      '<div class="tint-swatches">' + sw + '</div>';
    langCard.appendChild(row);
    syncTint();
  }
  /* 捕获阶段接管色块点击（先于应用的通用监听器） */
  document.addEventListener('click', function (e) {
    var sw = e.target && e.target.closest ? e.target.closest('.modal .tint-swatch') : null;
    if (!sw) return;
    var k = sw.getAttribute('data-tint');
    applyTint(k);
    try { localStorage.setItem(TINT_KEY, k); } catch (err) {}
    syncTint();
  }, true);

  /* ==================== 浏览器标签页图标（第 ㊲ 条 · 方案 A） ====================
     把 /static/favicon.ico 换成与侧栏品牌标记同源的「C + 青点」。
     用 JS 动态改 href 而不是改 HTML，两个原因：
       ① 不用碰产物 index.html（它每次由 sync_ui.py 重建，手改会被抹掉）；
       ② 切回「原版界面」时这一段会走还原分支 ⇒ 可逆性成立。
     首次运行时记住原始 href，避免把它的地址写死。 */
  var origIcon = null;
  function mountFavicon() {
    var links = document.querySelectorAll('link[rel="icon"], link[rel="shortcut icon"]');
    if (origIcon === null) {
      origIcon = links.length ? (links[0].getAttribute('href') || '/static/favicon.ico') : '/static/favicon.ico';
    }
    if (!on) {
      for (var j = 0; j < links.length; j++) {
        links[j].type = 'image/x-icon';
        links[j].href = origIcon;
      }
      return;
    }
    var svg = "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none'>"
      + "<path d='M16.53 7.47A6.4 6.4 0 1 0 16.53 16.53' stroke='#111114' stroke-width='3.1' stroke-linecap='round'/>"
      + "<circle cx='17.9' cy='12' r='2.15' fill='#0B93BE'/></svg>";
    var href = 'data:image/svg+xml,' + encodeURIComponent(svg);
    if (!links.length) {
      var l = document.createElement('link');
      l.rel = 'icon'; l.type = 'image/svg+xml'; l.href = href;
      document.head.appendChild(l);
      return;
    }
    for (var i = 0; i < links.length; i++) {
      links[i].type = 'image/svg+xml';
      links[i].href = href;
    }
  }

  /* ---- 启动 ---- */
  try { on = localStorage.getItem(KEY) !== 'original'; } catch (err) {}
  /* 回读上次的主题色 */
  var _tint = null;
  try { _tint = localStorage.getItem(TINT_KEY); } catch (err) {}
  applyTint(_tint);
  apply();
  /* 等样式层就绪后再挂载（appendChild 到 body 即可，DOM 已就绪；
     但首帧画面前挂上，动画才从 0 开始播） */
  mountSplash();

  /* 设置面板出现/消失（body 的直接子节点变动，不递归） */
  try {
    new MutationObserver(function () { syncSwitch(); mountThemeTint(); })
      .observe(document.body, { childList: true });
  } catch (err) {}

  /* 问候语被应用重写时（只盯 #greet 一个节点） */
  try {
    var g = document.getElementById('greet');
    if (g) {
      new MutationObserver(function () { greet(); })
        .observe(g, { childList: true, characterData: true, subtree: true });
    }
  } catch (err) {}

  /* ==================== Skill 市场 · 滚动叙事浮现（第㉙条） ====================
     iPhone 官网式：卡片进入视口才浮现（opacity 0→1 + 上移→归位）。
     buildMarket 每次重建都产出全新 .skill-card，故 observer 回调里
     幂等地给新卡挂 IntersectionObserver：视口外加 .mk-wait（隐藏），
     进入视口换 .mk-in（浮现，CSS transition 丝滑收尾）。
     ★ class 增删是 attributes 变化，不触发 childList observer —— 无死循环。
     ★ IntersectionObserver 一次性 unobserve（浮过即定格，不回退）。
     ★ prefers-reduced-motion：CSS 里 .mk-wait 直接可见，JS 照常挂不碍事。 */
  var marketIO = null;
  function mountNarrative() {
    if (!on) return;
    if (!('IntersectionObserver' in window)) return;   /* 老环境：卡片常显 */
    var body = document.getElementById('marketBody');
    if (!body) return;
    if (!marketIO) {
      try {
        marketIO = new IntersectionObserver(function (entries) {
          for (var i = 0; i < entries.length; i++) {
            if (entries[i].isIntersecting) {
              entries[i].target.classList.add('mk-in');
              entries[i].target.classList.remove('mk-wait');
              marketIO.unobserve(entries[i].target);   /* 浮过即定格 */
            }
          }
        }, { rootMargin: '0px 0px -10% 0px', threshold: 0.06 });
      } catch (err) { return; }
    }
    var cards = body.querySelectorAll('.skill-card');
    for (var j = 0; j < cards.length; j++) {
      var c = cards[j];
      if (c.dataset.mkObs) continue;                   /* 幂等：已挂过 */
      c.dataset.mkObs = '1';
      if (!c.classList.contains('mk-in')) c.classList.add('mk-wait');
      marketIO.observe(c);
    }
  }

  /* ==================== Skill 市场 · 分类计数标签 ====================
     在「教学辅助 / UI 美化」分类标题后补一个「N」计数药丸。
     计数随搜索过滤变化，且 buildMarket 是应用自己的函数（不能碰），
     故用 observer 盯 #marketBody 子树：每次重建后幂等地注入/刷新计数。
     ★ 只在设计层启用（on）时做；切回原版即停。 */
  /* ⚠️ 防死循环：observer 回调里「只读不写」。
     textContent 赋值会触发 childList 变化 → observer 再次回调 → 无限递归，
     主线程锁死、页面永远转圈（2026-09-29 实踩，用户反馈「一直加载」）。
     故：值相同则绝不写 DOM；值不同才写，写完后 observer 会再进来一轮，
     但第二轮读到值相同 → 不再写 → 收敛。另加 syncBusy 防重入双保险。 */
  var syncBusy = false;
  function syncMarketCount() {
    if (!on || syncBusy) return;
    syncBusy = true;
    try {
      var body = document.getElementById('marketBody');
      if (!body) return;
      var cats = body.querySelectorAll('.mk-cat');
      for (var i = 0; i < cats.length; i++) {
        var name = cats[i].querySelector('.mk-cat-name');
        if (!name) continue;
        var n = String(cats[i].querySelectorAll('.skill-card').length);
        var badge = name.querySelector('.mk-count');
        if (!badge) {
          badge = document.createElement('span');
          badge.className = 'mk-count';
          badge.textContent = n;
          name.appendChild(badge);
        } else if (badge.textContent !== n) {
          badge.textContent = n;
        }
      }
    } finally {
      syncBusy = false;
    }
  }
  try {
    var mb = document.getElementById('marketBody');
    if (mb) {
      new MutationObserver(function () { syncMarketCount(); mountNarrative(); })
        .observe(mb, { childList: true, subtree: true });
    }
  } catch (err) {}
  syncMarketCount();
  mountNarrative();

  /* ==================== 批量处理对话（第 ㊷ 条） ====================
     用户要「在项目那里加一个可以批量处理对话的按钮」。
     后端接口是现成的（`POST /api/session/del`，右键菜单里的「删除对话」就在用它），
     所以这一层只做**入口 + 批量模式**，不碰后端。

     ★ 为什么不复用业务自己的 `delSess()`：它每条会话都要 `window.confirm` 一次 ——
       批量场景下会连弹 N 次；而且它删完会调 `buildTree()`（业务内部函数，外部调不到）。
       所以自己实现：一次确认 → 逐条 POST → 再自己收拾 localStorage 与 DOM。

     ★ 选中态用 `Object.create(null)` 当集合存、**只改 class**：
       `buildTree()` 会整体重建会话列表，注入进去的子节点每次都会丢（伪元素才不会）；
       而 class 是 attributes 变化，**不触发 childList observer** ⇒ 无死循环。
       重建后由下面的 observer 幂等地把选中态重新套回去。

     ★ 删到「当前正在看的那个会话」时，业务内部指针不会跟着更新 ⇒ 删完直接 reload，
       让应用自己去后端对齐（避免后续发消息发到已删会话上）。 */
  (function mountBatch() {
    if (!on) return;
    if (window.__qkBatch) return;
    window.__qkBatch = 1;

    var PROJ_KEY = 'edu_proj_v1';                 /* 与业务保持一致 */
    var sel = Object.create(null);
    var selN = 0, busy = false;

    function lsGet(k, d) {
      try { var v = JSON.parse(localStorage.getItem(k)); return (v === null || v === undefined) ? d : v; }
      catch (e) { return d; }
    }
    function lsSet(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }

    function sessList() {
      var box = document.getElementById('projectBox');
      return box ? box.querySelectorAll('.proj-sess') : [];
    }
    function syncSel() {
      var a = sessList();
      for (var i = 0; i < a.length; i++) {
        a[i].classList.toggle('qk-on', !!sel[a[i].getAttribute('data-sid')]);
      }
      var bar = document.getElementById('qkBatchBar');
      if (bar) {
        bar.querySelector('.n').textContent = selN ? ('已选 ' + selN + ' 个对话') : '选择要处理的对话';
        var d = bar.querySelector('.del');
        d.disabled = selN === 0;
        d.textContent = selN ? ('删除 ' + selN + ' 项') : '删除';
      }
    }
    function clearSel() { sel = Object.create(null); selN = 0; }
    function enter() { document.body.classList.add('qk-batch'); clearSel(); syncSel(); }
    function exit() { document.body.classList.remove('qk-batch'); clearSel(); syncSel(); }

    function mountBtn() {
      var add = document.getElementById('projAdd');
      if (!add || document.getElementById('qkBatchBtn')) return;
      var b = document.createElement('button');
      b.id = 'qkBatchBtn'; b.className = 'qk-batch-btn'; b.type = 'button';
      b.title = '批量处理对话';
      b.setAttribute('aria-label', '批量处理对话');
      b.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">'
        + '<path d="M4 7.5l2.2 2.2L10.5 5.5"/><path d="M4 17.5l2.2 2.2L10.5 15.5"/>'
        + '<path d="M13.5 8h7M13.5 18h7"/></svg>';
      add.parentNode.insertBefore(b, add);      /* 放在 + 号左边，与它并排 */
    }
    function mountBar() {
      var sb = document.querySelector('.sidebar');
      if (!sb || document.getElementById('qkBatchBar')) return;
      var bar = document.createElement('div');
      bar.id = 'qkBatchBar'; bar.className = 'qk-batch-bar';
      bar.innerHTML = '<span class="n">选择要处理的对话</span>'
        + '<button class="del" type="button" disabled>删除</button>'
        + '<button class="cancel" type="button">取消</button>';
      sb.appendChild(bar);
    }

    function doDelete() {
      if (busy || !selN) return;
      var ids = Object.keys(sel);
      if (!window.confirm('删除选中的 ' + ids.length + ' 个对话？\n\n删除后不可恢复。')) return;
      busy = true;
      var bar = document.getElementById('qkBatchBar');
      var del = bar.querySelector('.del');
      del.disabled = true; del.textContent = '处理中…';

      var curEl = document.querySelector('.proj-sess.active');
      var hitCurrent = !!(curEl && sel[curEl.getAttribute('data-sid')]);

      var done = 0;
      function finish() {
        /* ① localStorage：把删掉的 sid 从各个项目里摘掉，否则刷新后它们又回来 */
        var projs = lsGet(PROJ_KEY, {});
        Object.keys(projs).forEach(function (pn) {
          if (!Array.isArray(projs[pn])) return;
          projs[pn] = projs[pn].filter(function (x) { return !sel[x]; });
        });
        lsSet(PROJ_KEY, projs);
        /* ② DOM：直接把对应的行摘掉（buildTree 是业务内部的，调不到） */
        var a = sessList();
        for (var i = 0; i < a.length; i++) {
          if (sel[a[i].getAttribute('data-sid')] && a[i].parentNode) {
            a[i].parentNode.removeChild(a[i]);
          }
        }
        busy = false;
        exit();
        if (hitCurrent) location.reload();
      }
      function step() {
        if (done >= ids.length) { finish(); return; }
        var sid = ids[done++];
        fetch('/api/session/del', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: sid })
        }).then(step, step);          /* 单条失败也继续，别卡住整批 */
      }
      step();
    }

    /* 捕获阶段接管：批量模式下点会话项 = 切换勾选，不再跳转 */
    document.addEventListener('click', function (e) {
      var t = e.target;
      if (!t || !t.closest) return;
      if (t.closest('#qkBatchBtn')) { e.preventDefault(); e.stopPropagation(); enter(); return; }
      var bar = t.closest('#qkBatchBar');
      if (bar) {
        e.preventDefault(); e.stopPropagation();
        if (t.closest('.cancel')) exit();
        else if (t.closest('.del')) doDelete();
        return;
      }
      var s = t.closest('.proj-sess');
      if (s && document.body.classList.contains('qk-batch')) {
        e.preventDefault(); e.stopPropagation();
        var sid = s.getAttribute('data-sid');
        if (sel[sid]) { delete sel[sid]; selN--; } else { sel[sid] = 1; selN++; }
        syncSel();
      }
    }, true);

    mountBtn();
    mountBar();
    /* 列表被应用重建后，把选中态重新套回去。
       只改 class ⇒ 不触发 childList observer ⇒ 不会自激。 */
    try {
      var box = document.getElementById('projectBox');
      if (box) {
        new MutationObserver(function () {
          if (document.body.classList.contains('qk-batch')) syncSel();
        }).observe(box, { childList: true });
      }
    } catch (err) {}
  })();

  /* ==================== 对话界面 · 会话加载骨架屏（第㉜条-b） ====================
     用户报「打开对话页面看不到任何变化」—— 第㉜条那套骨架屏只在
     「发消息后的检索期」出现（几秒即逝），而**打开历史会话**与**刷新后自动恢复
     上次会话**这两条路都不经过它，所以用户永远看不到。
     这里把它接到「加载会话」这一段上：
       ① fetch 钩子命中 POST /api/session/get（刷新恢复与点侧栏会话都会走）
       ② 立刻在 #chatMsgBox 里铺骨架屏
       ③ **把响应交给业务前先兜够最短可见时间** —— 本地服务毫秒级返回，
          不兜底就是一帧闪过，等于没做
       ④ 业务拿到响应后会自己 msgBox.innerHTML=''，骨架屏随之收尾（故 hide 只是兜底）
     ★ 钩子必须**原样返回**业务的 Promise，只做旁挂，绝不截断链。
     ★ 幂等：反复触发不重复插入；`on` 为假（原版界面）时完全不干预，
       所以原地热切回原版后钩子自动失效，不需要卸载。
     ★ 收尾方式与上面那套一致：不用 observer，规避死循环风险。 */
  (function mountHistorySkeleton() {
    if (!on) return;
    if (window.__qkHistSk) return;              /* 幂等，只装一次 */
    window.__qkHistSk = 1;

    var MIN_VISIBLE = 420;                      /* 最短可见 ms，防止「闪一下」 */
    var t0 = 0;

    function show() {
      var mb = document.getElementById('chatMsgBox');
      if (!mb) return;
      var cur = mb.querySelector('.qk-hist-sk');
      if (cur) { t0 = Date.now(); return; }      /* 已在显示：只重新计时（连续两次请求） */
      mb.innerHTML = '';                         /* 清掉上一个会话的残留，避免新旧叠着 */
      var sk = document.createElement('div');
      sk.className = 'qk-hist-sk';
      sk.innerHTML = '<i></i><i></i><i></i><i></i>';
      mb.appendChild(sk);
      t0 = Date.now();
    }
    function hide() {                            /* 兜底：业务没清时自己收 */
      var el = document.querySelector('.qk-hist-sk');
      if (el && el.parentNode) el.parentNode.removeChild(el);
    }

    var originFetch = window.fetch;
    window.fetch = function (u, o) {
      var p = originFetch.apply(this, arguments);
      if (!on) return p;                         /* 原版界面：不干预 */
      try {
        var url = (typeof u === 'string') ? u : ((u && u.url) || '');
        var m = String((o && o.method) || 'GET').toUpperCase();
        if (m === 'POST' && /\/api\/session\/get(\?|$)/.test(url)) {
          show();
          p = p.then(function (r) {
            var wait = Math.max(0, MIN_VISIBLE - (Date.now() - t0));
            if (!wait) return r;                 /* 已经显示够了，直接放行 */
            return new Promise(function (res) {
              setTimeout(function () { res(r); }, wait);
            });
          });
          p.then(hide, hide);
        }
      } catch (err) {}
      return p;                                  /* ★ 原样返回，一个字都别改 */
    };
  })();
})();
