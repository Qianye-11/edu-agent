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
     一次性：页面加载时播一遍「欢迎使用启思 / 启学，让思考发生」，
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
        '<div class="qs-subtitle">启学，让思考发生</div>' +
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
})();
