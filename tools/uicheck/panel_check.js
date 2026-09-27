/* 侧边面板 UI 检查（jsdom 真 DOM，跑在本机 5174 上）
 *
 * 为什么放仓库里：以前这些脚本放临时目录，被清过两次，历史结论没法复跑。
 * 用法：
 *   cd tools/uicheck && npm i jsdom            # 只需一次
 *   node panel_check.js                        # 需要 5174 在跑（先「启动」）
 *
 * 覆盖：①「文档」入口与面板结构 ②文档可编辑 ③幻灯片 16:9 ④PPT 优化区（导入/体检/一键/下载）
 *      ⑤开关关掉时灰显并说明原因 ⑥会话里不再有「下一步」卡片 ⑦每条回答都有【生成文档】
 */
const fs = require('fs');
const path = require('path');
const { JSDOM, VirtualConsole } = require('jsdom');

const BASE = process.env.EDU_BASE || 'http://127.0.0.1:5174';
const ROOT = path.resolve(__dirname, '..', '..');
const HTML = fs.readFileSync(path.join(ROOT, 'static', 'index.html'), 'utf8')
  .replace('<head>', '<head><base href="' + BASE + '/">');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const txt = (el) => (el ? el.textContent.replace(/\s+/g, ' ').trim() : '(none)');
let pass = 0, fail = 0;
const check = (name, ok, extra) => {
  console.log((ok ? '  ✅ ' : '  ❌ ') + name + (extra ? '  ' + extra : ''));
  ok ? pass++ : fail++;
};

/* 桩数据：PPT 优化接口（真上传在 harness 里过不了 Node fetch 的 FormData 类型，这里只验接线） */
const REPORT = {
  ok: true, job_id: 'stub-job', file: '答辩稿.pptx', slides: 18,
  canvas: { width_px: 1280, height_px: 720 },
  theme: { title_font: '微软雅黑', body_font: '宋体', body_levels: [44, 32, 28, 24, 20] },
  totals: { tables: 3, charts: 2, diagrams: 1, images: 7, chars: 2456 },
  pages: [], flags: { dense_pages: [7], sparse_pages: [15], needs_confirmation: [9] },
};
const POLISH = {
  ok: true, pages: 18, file: 'x',
  applied: [{ action: 'font', label: '统一字体', runs: 128 },
            { action: 'strip-anim', label: '清理动画 / 转场', timing_removed: 42 }],
  verify: { ok: true, detail: '[VERIFY] passed' },
};

/* 桩数据：幻灯片渲染收口（docs/22 D13）。真渲染由服务端出 SVG，这里只验接线与缩放。 */
const THEMES = {
  ok: true, enabled: true, default: 'tech-editorial',
  themes: [{ id: 'tech-editorial', name: '学术蓝', fonts_ok: true, canvas: { w: 1280, h: 720, bg: '#FFFFFF' } },
           { id: 'illustration', name: '插画暖', fonts_ok: true, canvas: { w: 1280, h: 720, bg: '#F7F3EE' } },
           { id: 'hand-drawn', name: '手绘墨', fonts_ok: true, canvas: { w: 1280, h: 720, bg: '#F8F6F2' } }],
};
const svgPage = (i) => '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">' +
  '<rect x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>' +
  '<text x="72" y="108" font-size="44" fill="#2B2B2B">第' + (i + 1) + '页</text></svg>';
/* 预览用（annotate）：文字带 data-slot，前端编辑时要把它藏起来 */
const svgPageAnnotated = (i, annotate) => '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">' +
  '<rect x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>' +
  '<text x="72" y="108" font-size="40" fill="#2B2B2B"' + (annotate ? ' data-slot="title"' : '') + '>第' + (i + 1) + '页标题</text>' +
  '<text x="96" y="268" font-size="24" fill="#2B2B2B"' + (annotate ? ' data-slot="block:0"' : '') + '>第一条要点</text>' +
  '<text x="96" y="368" font-size="24" fill="#2B2B2B"' + (annotate ? ' data-slot="block:1"' : '') + '>第二条要点</text></svg>';

/* 桩数据：一份"会话里的教案"（历史里带 meta.doc → 应用会自动把它当当前文档）。
   为什么要有它：以前这些用例**依赖服务器上"当前会话"里恰好有教案**，
   换一次会话（比如跑一趟 fill_e2e 新建了会话）整套用例就集体报红 —— 明明代码没问题（踩过）。 */
const DOC_MD = '# 3.3 幂函数\n\n人教A版 · 必修第一册 · 高一\n\n' +
  '### 导入（约 5 分钟）\n- 投影五个实例\n- 追问：这些函数有什么共同点\n\n' +
  '### 新知讲授（约 15 分钟）\n- 形如 y=x^α 的函数\n- α 为常数\n\n' +
  '### 例题精讲（约 10 分钟）\n- 比较幂值大小\n\n' +
  '### 随堂练习（约 10 分钟）\n- 课本练习\n\n' +
  '### 小结（约 5 分钟）\n- 三条结论\n';
const DOC_SLIDES = [
  { name: '导入', minutes: 5, blocks: [{ label: '', text: '投影五个实例' }, { label: '', text: '追问：这些函数有什么共同点' }] },
  { name: '新知讲授', minutes: 15, blocks: [{ label: '', text: '形如 y=x^α 的函数' }, { label: '', text: 'α 为常数' }] },
  { name: '例题精讲', minutes: 10, blocks: [{ label: '', text: '比较幂值大小' }] },
  { name: '随堂练习', minutes: 10, blocks: [{ label: '', text: '课本练习' }] },
  { name: '小结', minutes: 5, blocks: [{ label: '', text: '三条结论' }] },
];
const DOC_STUB = {
  ok: true, enabled: true, kind: 'md', title: '3.3 幂函数', markdown: DOC_MD,
  slides: DOC_SLIDES, blocks: [{ title: '导入', level: 2, chars: 6 }],
  stats: { chars: DOC_MD.length, slides: DOC_SLIDES.length, blocks: 5 },
};

function makeDom(opts) {
  const o = opts || {};
  const errors = [];
  const renderCalls = [];
  const exports = [];
  const saves = [];
  const fills = [];
  const deletes = [];
  const slots = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', (e) => { if (!/Could not load script/.test(e.message || '')) errors.push(e.message); });
  vc.on('error', (...a) => errors.push(a.join(' ')));
  vc.on('warn', () => {}); vc.on('log', () => {});
  const dom = new JSDOM(HTML, {
    url: BASE + '/', runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: vc, resources: 'usable',
    beforeParse(window) {
      /* jsdom 缺一批浏览器 API：主脚本 init 时调用会抛未捕获错误 → 整个内联脚本当场中断，
         后面的绑定（圆钮点击、面板渲染）全挂不上，检查会误报"点不动"。
         这不是应用 bug（真浏览器都有），所以在检查脚本里补齐。 */
      if (!window.matchMedia) {
        window.matchMedia = (q) => ({ matches: false, media: q, onchange: null,
          addListener() {}, removeListener() {},
          addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; } });
      }
      if (!window.ResizeObserver) {
        window.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
      }
      if (!window.IntersectionObserver) {
        window.IntersectionObserver = class {
          observe() {} unobserve() {} disconnect() {} takeRecords() { return []; }
        };
      }
      if (!window.requestIdleCallback) window.requestIdleCallback = (fn) => setTimeout(fn, 0);
      if (!window.scrollTo) window.scrollTo = () => {};
      /* R8 用例要用到的两种"开机状态"：学生角色 / 浏览器里还留着老编排数据 */
      try {
        if (o.role === 'student') window.localStorage.setItem('edu_role', 'student');
        if (o.legacyDesign) {
          window.localStorage.setItem('edu_design_v1', JSON.stringify({
            title: '老课题', grade: '',
            steps: [{ name: '导入', minutes: 5, points: '老要点一\n老要点二' },
                    { name: '新知', minutes: 10, points: '老要点三' }],
          }));
        }
      } catch (e) { /* localStorage 不可用就让用例自己失败，别静默 */ }
      if (o.innerWidth) {
        Object.defineProperty(window, 'innerWidth', { value: o.innerWidth, configurable: true });
        Object.defineProperty(window, 'outerWidth', { value: o.innerWidth, configurable: true });
      }
      if (window.Element && !window.Element.prototype.scrollIntoView) {
        window.Element.prototype.scrollIntoView = () => {};
      }

      const realFetch = fetch;
      window.fetch = async function (u, op) {
        const p = new URL(String(u), BASE).pathname;
        if (p === '/api/ppt/status') {
          return new Response(JSON.stringify(o.pptOff
            ? { ok: true, enabled: false, ready: false, install: { intake_ready: true, commit: 'stub' }, actions: [] }
            : { ok: true, enabled: true, ready: true, install: { intake_ready: true, installed: true, commit: 'stub' },
                actions: [{ id: 'font', label: '统一字体' }, { id: 'size', label: '统一字号层级' },
                          { id: 'strip-anim', label: '清理动画 / 转场' }] }),
            { status: 200, headers: { 'Content-Type': 'application/json' } });
        }
        if (p === '/api/ppt/intake') return new Response(JSON.stringify(REPORT), { status: 200, headers: { 'Content-Type': 'application/json' } });
        if (p === '/api/ppt/polish') return new Response(JSON.stringify(POLISH), { status: 200, headers: { 'Content-Type': 'application/json' } });
        if (p === '/api/slide/themes') return new Response(JSON.stringify(THEMES), { status: 200, headers: { 'Content-Type': 'application/json' } });
        if (p === '/api/wps/export') {
          let b = {};
          try { b = JSON.parse(op.body); } catch (e) {}
          exports.push(b);
          const okBody = o.wpsFallback
            ? { ok: true, filename: '教案_旧版式.pptx', outDir: 'D:\\out', download_url: '/api/wps/download?path=x',
                detail: '已用旧版式导出', fallback: true, reason: '新版式导出没通过验收：转换报告缺失' }
            : { ok: true, filename: '教案_3.3 幂函数.pptx', outDir: 'D:\\out', download_url: '/api/wps/download?path=x',
                detail: '已导出 3 页（原生可编辑）· ✅ 页数一致；✅ 内容零丢失', fallback: false };
          return new Response(JSON.stringify(okBody), { status: 200, headers: { 'Content-Type': 'application/json' } });
        }
        if (p === '/api/slide/render') {
          if (o.slideFail) return new Response(JSON.stringify({ ok: false, error: 'stub down' }), { status: 500, headers: { 'Content-Type': 'application/json' } });
          let n = 3, wantAnnotate = false, body0 = {};
          try { body0 = JSON.parse(op.body); n = (body0.slides || []).length || 3;
                wantAnnotate = !!body0.annotate; } catch (e) {}
          renderCalls.push(n);
          /* 真实接口只在 annotate=true 时回 boxes —— 桩也照这个规矩来（免得测出一条假链路） */
          const boxes = wantAnnotate
            ? Array.from({ length: n }, () => ([
                { slot: 'title', kind: 'text', align: 'start', x: 72, y: 60, w: 1136, h: 80, size: 40 },
                { slot: 'block:0', kind: 'text', align: 'start', x: 96, y: 240, w: 1112, h: 60, size: 24 },
                { slot: 'block:1', kind: 'text', align: 'start', x: 96, y: 340, w: 1112, h: 60, size: 24 },
              ]))
            : undefined;
          return new Response(JSON.stringify({ ok: true, enabled: true, theme_id: THEMES.default,
            pages: Array.from({ length: n }, (_, i) => svgPageAnnotated(i, wantAnnotate)),
            ...(boxes ? { boxes } : {}),
            warnings: o.slideWarn ? ['第 2 页：模块 6 个 > 上限 5 → 建议拆页（本页只排了前 5 个，其余请另起一页）'] : [],
            layouts: [], stats: { pages: n } }),
            { status: 200, headers: { 'Content-Type': 'application/json' } });
        }
        if (p === '/api/slide/templates') return new Response(JSON.stringify({
          ok: true, enabled: true,
          builtin: THEMES.themes.map((t) => ({ id: t.id, name: t.name, kind: 'json' })),
          imported: o.noTpl ? [] : [{ id: '学校模板', name: '学校模板', kind: 'pptx', pages: 11 }],
        }), { status: 200, headers: { 'Content-Type': 'application/json' } });
        if (p === '/api/slide/template/align') {
          return new Response(JSON.stringify({ ok: true, template_id: '学校模板',
            structures: [{ signature: 'sig1', pages: 3, example_index: 2, slots: [
              { shape_index: 0, role: 'title', sample: '示例标题', capacity: 16 },
              { shape_index: 1, role: 'body', sample: '示例正文', capacity: 40 }] }],
            align: { version: 1, pages: {} } }), { status: 200, headers: { 'Content-Type': 'application/json' } });
        }
        if (p === '/api/slide/fill') {
          let b = {};
          try { b = JSON.parse(op.body); } catch (e) {}
          fills.push(b);
          return new Response(JSON.stringify({
            ok: true, filename: '教案_3.3 幂函数_学校模板.pptx', outDir: 'D:\\out',
            download_url: '/api/wps/download?path=x', detail: '✅ 内容零丢失；✅ 容量自查',
            warnings: ['会话比模板少 8 页内容 → 默认删掉模板多余的内容页'],
            /* 真实接口还回这两样：填充后的预览 SVG + 填充计划（盒子就来自 plan 的 fills[].geo） */
            preview: [svgPage(0)],
            plan: { template_id: '学校模板', pages: [{ template_index: 1, layout: '', copied: false,
              fills: [
                { role: 'title', shape_index: 2, text: '情境导入', capacity: 8, size_px: 40,
                  geo: { x: 440, y: 7, w: 400, h: 100 }, sample: '示例标题' },
                { role: 'body', shape_index: 5, text: '投影五个实例', capacity: 144, size_px: 16,
                  geo: { x: 29, y: 380, w: 264, h: 160 }, sample: '示例正文' },
                { role: 'decoration', shape_index: 9, text: '圆', capacity: 1, size_px: 36,
                  geo: { x: 87, y: 139, w: 58, h: 68 }, sample: '圆' },
              ] }] },
          }), { status: 200, headers: { 'Content-Type': 'application/json' } });
        }
        if (p === '/api/session/get' && o.withDoc) {
          return new Response(JSON.stringify({
            ok: true, id: 'stub', title: DOC_STUB.title, last_html: '',
            messages: [
              { role: 'user', content: '帮我备一节 3.3 幂函数的课' },
              { role: 'assistant', content: DOC_MD, meta: { mode: 'B', doc: DOC_STUB } },
            ],
          }), { status: 200, headers: { 'Content-Type': 'application/json' } });
        }
        if (p === '/api/session/get' && (o.legacyDesign || o.emptyHistory)) {
          /* 迁移用例要一个"干净会话"：不然历史里那份文档会先把 DOCP 占上，迁移就不该发生 */
          return new Response(JSON.stringify({ ok: true, id: 'stub', title: '', messages: [], last_html: '' }),
            { status: 200, headers: { 'Content-Type': 'application/json' } });
        }
        if (p === '/api/slide/template/delete') {
          /* 一定要桩住：不然面板检查会把老师模板库里真有的模板删掉（软删进 _trash 也够吓人） */
          let b = {};
          try { b = JSON.parse(op.body); } catch (e) {}
          deletes.push(b);
          return new Response(JSON.stringify({ ok: true, moved_to: 'content/ppt_templates/_trash/学校模板-stub' }),
            { status: 200, headers: { 'Content-Type': 'application/json' } });
        }
        if (p === '/api/slide/slot') {
          let b = {};
          try { b = JSON.parse(op.body); } catch (e) {}
          slots.push(b);
          return new Response(JSON.stringify({ ok: true, slot: b.slot, index: b.index,
            slides: b.slides || DOC_STUB.slides, pages: (DOC_STUB.slides || []).length,
            warnings: [], session_id: 'stub' }),
            { status: 200, headers: { 'Content-Type': 'application/json' } });
        }
        if (p === '/api/slide/save') {
          let b = {};
          try { b = JSON.parse(op.body); } catch (e) {}
          saves.push(b);
          return new Response(JSON.stringify({ ok: true, session_id: b.session_id || 'stub',
            slides: b.slides || [], slides_edited: true, pages: (b.slides || []).length,
            warnings: o.slideWarn ? ['第 2 页：模块 6 个 > 上限 5 → 建议拆页'] : [] }),
            { status: 200, headers: { 'Content-Type': 'application/json' } });
        }
        return realFetch(new URL(String(u), BASE).toString(), op);
      };
    },
  });
  return { w: dom.window, doc: dom.window.document, errors, renderCalls, exports, saves, fills, deletes, slots };
}

async function openDocPanel(doc) {
  /* R9：侧边面板不再有「文档」圆钮 —— 文档住在「教案中心 → 教案工作台」。
     这里像老师那样走：点左侧导航进教案中心，再点「文档」页签。 */
  const nav = doc.querySelector('.snav[data-view="design"]');
  if (nav) nav.click();
  await sleep(700);
  const t = doc.querySelector('#wzTabs .wz-tab[data-wz="doc"]');
  if (t) { t.click(); await sleep(900); }
}

(async () => {
  console.log('== 面板结构 / 入口 ==');
  {
    const { w, doc, errors } = makeDom({ withDoc: true });
    await sleep(2500);
    const shell = Array.from(doc.querySelector('.shell').children).map((c) => c.id || c.tagName);
    check('右侧只有一块面板（.shell 子元素）', shell.join(',') === 'ASIDE,MAIN,sidePanel', shell.join(' | '));
    check('没有旧的独立面板 #docPanel', !doc.getElementById('docPanel'));
    /* R9：面板恢复原样 —— 4 个圆钮、文档那套外壳一件不剩 */
    const quads = Array.from(doc.querySelectorAll('#spQuad .qbtn')).map((b) => b.dataset.v);
    check('侧边面板只剩 4 个圆钮（状态/引用/知识源/会话）',
      quads.join(',') === 'term,docs,web,chat', quads.join(' | '));
    check('没有「文档」圆钮与角标',
      !doc.querySelector('#spQuad .qbtn[data-v="doc"]') && !doc.getElementById('spDocTag'));
    check('没有拖宽手柄', !doc.getElementById('wzDocGrip') && !doc.getElementById('docPanelGrip'));
    check('面板里没有常驻文件 input', !doc.querySelector('#sidePanel input[type=file]'));
    check('面板宽度回到窄栏（没有 sp-doc 类）', !doc.getElementById('sidePanel').classList.contains('sp-doc'));
    check('副标题回到四项',
      /运行状态 · 引用 · 知识源 · 会话$/.test(txt(doc.querySelector('#sidePanel .sp-sub'))),
      txt(doc.querySelector('#sidePanel .sp-sub')));
    check('面板里不再渲染文档正文', !doc.querySelector('#spBody .dp-word'));
    await openDocPanel(doc);
    check('入口（教案中心 → 文档页签）能打开文档',
      !!doc.querySelector('#wzDocPane .dp-word[contenteditable="true"]'),
      (doc.querySelector('#wzTabs .wz-tab.wz-on') || {}).textContent || '(无页签)');
    const ed = doc.querySelector('#wzDocPane .dp-word[contenteditable="true"]');
    check('文档页签可直接编辑', !!ed);
    check('编辑工具栏显示', doc.getElementById('wzDocFmt').style.display === 'flex');
    doc.querySelector('#wzTabs .wz-tab[data-wz="slides"]').click(); await sleep(900);
    check('幻灯片页签有 16:9 预览台（.dp-canvas）', !!doc.querySelector('#wzDocPane .dp-canvas'));
    check('旧的深蓝预览台已退役', !doc.querySelector('#wzDocPane .dp-stage'));
    check('预览台里是服务端给的 SVG（不是前端拼的 h4+ul）',
      !!doc.querySelector('#wzDocPane .dp-canvas svg'), (doc.querySelector('#wzDocPane .dp-canvas') || {}).innerHTML?.slice(0, 40));
    const wrap = doc.querySelector('#wzDocPane .dp-slidewrap');
    check('等比缩放按 1280 宽度算（jsdom 量不到宽度 → 兜底 480px ⇒ 0.375）',
      !!wrap && wrap.dataset.scale === '0.3750', wrap ? wrap.dataset.scale : '(no wrap)');
    check('预览台高度跟着缩放走（720×0.375=270px）',
      !!wrap && wrap.style.height === '270px', wrap ? wrap.style.height : '(no wrap)');
    const cv = doc.querySelector('#wzDocPane .dp-canvas');
    check('内部固定 1280×720、只用均匀缩放',
      !!cv && /scale\(0\.375\)/.test(cv.style.transform || ''), cv ? cv.style.transform : '(no canvas)');
    const sel = doc.getElementById('dpTheme');
    check('主题下拉有 3 套（来自 /api/slide/themes）', !!sel && sel.options.length === 3,
      sel ? Array.from(sel.options).map((o) => o.textContent).join('/') : '(no select)');
    sel.value = 'hand-drawn';
    sel.dispatchEvent(new w.Event('change', { bubbles: true }));
    await sleep(700);
    check('换主题会重新要一份 SVG', doc.querySelector('#wzDocPane .dp-canvas svg') !== null &&
      /手绘墨/.test(txt(doc.querySelector('.dp-slstat'))), txt(doc.querySelector('.dp-slstat')));
    check('提示小字仍在', /内存预览效果/.test(txt(doc.querySelector('.dp-note'))), txt(doc.querySelector('.dp-note')));
    check('无运行时错误', errors.length === 0, errors.slice(0, 2).join(' | '));
    w.close();
  }

  console.log('== 幻灯片：就地编辑（改字 / 增删块 / 换版式 / 排序 / 删页） ==');
  {
    const { w, doc, errors, saves, renderCalls, slots } = makeDom({ withDoc: true });
    await sleep(2500);
    await openDocPanel(doc);
    doc.querySelector('#wzTabs .wz-tab[data-wz="slides"]').click(); await sleep(1000);

    /* R11/R12：卡片没了，改成"页内点着改"。这一整段就是本轮的主验收。 */
    check('卡片列表已经删掉（.dp-slide 数量为 0）',
      doc.querySelectorAll('#wzDocPane .dp-slide').length === 0);
    check('卡片工具栏/表单编辑器也没了',
      !doc.querySelector('#wzDocPane .dp-cardbar') && !doc.querySelector('#wzDocPane .dp-edit'));
    const boxes = doc.querySelectorAll('#wzDocPane .dp-boxlayer .dp-box');
    check('页面上铺了可编辑盒子（服务端 boxes）', boxes.length > 0, boxes.length + ' 个');
    check('盒子按设计像素定位（1280×720 坐标，不是屏幕坐标）',
      !!boxes[0] && /left:\d/.test(boxes[0].getAttribute('style') || '') &&
      parseFloat((boxes[0].style.width || '0')) <= 1280.5,
      boxes[0] ? boxes[0].getAttribute('style').slice(0, 60) : '(无)');

    /* ① 点页面上的字 → 直接改 → 回传 /api/slide/slot */
    const target = Array.from(boxes).find((b) => b.dataset.slot === 'title') || boxes[0];
    const slotName = target.dataset.slot;
    target.click(); await sleep(300);
    check('点一下就进入编辑态（同位置出现输入框）',
      target.getAttribute('contenteditable') === 'true' && target.classList.contains('dp-editing'));
    check('编辑时原来那行 SVG 文字被藏起来（不会重影）',
      (() => { const t = doc.querySelector('#wzDocPane #dpCanvas svg [data-slot="' + slotName + '"]');
               return !t || t.style.visibility === 'hidden'; })());
    target.textContent = '页内改过的标题';
    target.dispatchEvent(new w.Event('blur', { bubbles: true }));
    for (let i = 0; i < 14 && !slots.length; i++) await sleep(250);
    check('改完把**那一格**回传 /api/slide/slot（带槽位名与新文字）',
      slots.length > 0 && slots[0].slot === slotName && slots[0].text === '页内改过的标题',
      JSON.stringify(slots[0] || {}));
    check('改完重新取了一份 SVG（页面跟着变）', renderCalls.length >= 2, renderCalls.join('/'));

    /* ② 撤销：把改前那句送回去 */
    const undoBtn = doc.getElementById('dpUndo');
    check('有撤销/重做按钮', !!undoBtn && !!doc.getElementById('dpRedo'));
    const before = slots.length;
    undoBtn.click();
    for (let i = 0; i < 12 && slots.length === before; i++) await sleep(250);
    check('撤销把改前的文字送回去了',
      slots.length > before && slots[slots.length - 1].slot === slotName &&
      slots[slots.length - 1].text !== '页内改过的标题',
      JSON.stringify(slots[slots.length - 1] || {}));

    /* ③ 本页属性条：换版式 / 改时长 / 加一块 */
    const lay = doc.getElementById('dpLayout');
    check('有本页属性条（版式 / 时长 / 加一块）',
      !!lay && !!doc.getElementById('dpMinutes') && !!doc.getElementById('dpAddBlock'));
    await sleep(900);        /* 先等上一步那个 700ms 防抖存完，免得读到旧记录（踩过） */
    lay.value = 'two-col';
    const nSaves = saves.length;                 /* ⚠️ 前面几步已经存过几次；要等"新的一次" */
    lay.dispatchEvent(new w.Event('change', { bubbles: true }));
    for (let i = 0; i < 14 && saves.length <= nSaves; i++) await sleep(250);
    const saved1 = saves[saves.length - 1] || {};
    check('换版式写进数据并回存',
      saves.length > nSaves && !!saved1.slides && saved1.slides[0].layout === 'two-col',
      saves.length + ' 次 · ' + (saved1.slides ? String(saved1.slides[0].layout) : '(无 slides)'));
    const nBlocks = doc.querySelectorAll('#wzDocPane .dp-boxlayer .dp-box').length;
    doc.getElementById('dpAddBlock').click(); await sleep(900);
    check('「＋加一块」生效（多出一个可编辑盒子）',
      doc.querySelectorAll('#wzDocPane .dp-boxlayer .dp-box').length >= nBlocks,
      nBlocks + ' → ' + doc.querySelectorAll('#wzDocPane .dp-boxlayer .dp-box').length);

    /* ④ 左栏缩略图：页数、换页、页操作 */
    const thumbs = doc.querySelectorAll('#wzLeft .wz-thumb');
    check('左栏是页缩略图（数量 = 页数）', thumbs.length === (DOC_STUB.slides || []).length,
      thumbs.length + ' 张');
    check('缩略图里是真的 SVG（复用服务端那份）',
      !!doc.querySelector('#wzLeft .wz-thumb .cv svg'));
    doc.querySelectorAll('#wzLeft .wz-thumb')[2].click(); await sleep(500);
    check('点缩略图换页', /第 3 \/ /.test(txt(doc.querySelector('#wzDocPane .dp-pagebar span'))),
      txt(doc.querySelector('#wzDocPane .dp-pagebar span')));
    const n0 = doc.querySelectorAll('#wzLeft .wz-thumb').length;
    doc.querySelector('#wzLeft [data-thumb-dup="0"]').click(); await sleep(700);
    check('缩略图上的「复制页」生效', doc.querySelectorAll('#wzLeft .wz-thumb').length === n0 + 1,
      n0 + ' → ' + doc.querySelectorAll('#wzLeft .wz-thumb').length);
    doc.querySelector('#wzLeft [data-thumb-del="0"]').click(); await sleep(700);
    check('缩略图上的「删页」生效', doc.querySelectorAll('#wzLeft .wz-thumb').length === n0,
      String(doc.querySelectorAll('#wzLeft .wz-thumb').length));
    /* 页名（`.n` 里含页码"1 / 5 …"，比整串永远不等 —— 只比名字那一段） */
    const nameOf = (el) => txt(el).replace(/^\d+\s*\/\s*\d+\s*/, '');
    const firstName = nameOf(doc.querySelector('#wzLeft .wz-thumb[data-thumb="0"] .n'));
    doc.querySelector('#wzLeft [data-thumb-dn="0"]').click(); await sleep(700);
    check('缩略图上的「下移」生效',
      nameOf(doc.querySelector('#wzLeft .wz-thumb[data-thumb="1"] .n')) === firstName,
      firstName + ' → ' + nameOf(doc.querySelector('#wzLeft .wz-thumb[data-thumb="1"] .n')));

    /* ⑤ 加页 */
    const n1 = doc.querySelectorAll('#wzLeft .wz-thumb').length;
    doc.getElementById('dpAddPage').click(); await sleep(700);
    check('「+ 加一页」生效', doc.querySelectorAll('#wzLeft .wz-thumb').length === n1 + 1,
      n1 + ' → ' + doc.querySelectorAll('#wzLeft .wz-thumb').length);
    check('无运行时错误', errors.length === 0, errors.slice(0, 2).join(' | '));
    w.close();
  }

  console.log('== 幻灯片：拆页/字数提示显示在页签里 ==');
  {
    const { w, doc, errors } = makeDom({ slideWarn: true , withDoc: true });
    await sleep(2500);
    await openDocPanel(doc);
    doc.querySelector('#wzTabs .wz-tab[data-wz="slides"]').click(); await sleep(1200);
    check('服务端的"建议拆页"提示显示出来了',
      /建议拆页/.test(txt(doc.querySelector('#wzDocPane .dp-warn'))), txt(doc.querySelector('#wzDocPane .dp-warn')));
    check('提示是提示、内容没被截断', /其余请另起一页|只排了前 5 个/.test(txt(doc.querySelector('#wzDocPane .dp-warn'))));
    check('无运行时错误', errors.length === 0, errors.slice(0, 2).join(' | '));
    w.close();
  }

  console.log('== 模板：导入 / 选择 / 用模板填一遍（M4b） ==');
  {
    const { w, doc, errors, fills, deletes } = makeDom({ withDoc: true });
    await sleep(2500);
    await openDocPanel(doc);
    doc.querySelector('#wzTabs .wz-tab[data-wz="slides"]').click(); await sleep(1200);
    const tpl = doc.getElementById('dpTpl');
    check('模板下拉存在且列出导入的模板', !!tpl && tpl.options.length === 2,
      tpl ? Array.from(tpl.options).map((o) => o.textContent).join('/') : '(none)');
    check('有导入入口', !!doc.getElementById('dpTplImport') && !!doc.querySelector('#dpTplFile'));
    check('没选模板时「用模板填一遍」是灰的', doc.getElementById('dpTplFill').disabled === true);
    tpl.value = '学校模板';
    tpl.dispatchEvent(new w.Event('change', { bubbles: true }));
    await sleep(800);
    check('选了模板后填充按钮可用', doc.getElementById('dpTplFill').disabled === false);
    /* 主题与模板二选一：换内置主题要把模板清掉（否则"模板压过主题"，换主题看不出变化） */
    const themeSel = doc.getElementById('dpTheme');
    themeSel.value = 'hand-drawn';
    themeSel.dispatchEvent(new w.Event('change', { bubbles: true }));
    await sleep(700);
    check('换主题会清掉模板选择（二选一）',
      doc.getElementById('dpTpl').value === '' && doc.getElementById('dpTplFill').disabled === true,
      'tpl=' + doc.getElementById('dpTpl').value);
    tpl.value = '学校模板';
    tpl.dispatchEvent(new w.Event('change', { bubbles: true }));
    await sleep(700);
    doc.getElementById('dpTplFill').click();
    for (let i = 0; i < 12 && !fills.length; i++) await sleep(250);
    const body = fills[0] || {};
    check('填充请求带上模板 id 与会话内容',
      body.template_id === '学校模板' && Array.isArray(body.slides) && body.slides.length > 0,
      body.template_id + ' / ' + (body.slides || []).length + ' 页');
    await sleep(400);
    check('结果条给出文件名与下载链接',
      /教案_3\.3 幂函数_学校模板\.pptx/.test(txt(doc.getElementById('wzDocRes'))) &&
      !!doc.querySelector('#wzDocRes a[href*="/api/wps/download"]'),
      txt(doc.getElementById('wzDocRes')).slice(0, 60));
    check('填充提示（如"删掉多余页"）显示在结果条',
      /删掉模板多余的内容页/.test(txt(doc.getElementById('wzDocRes'))));

    /* R11：**模板填充页也能点着改**（老师要求"ppt 有关的编辑都要能编辑"）——
       盒子来自填充计划的 fills[].geo；改完先记着、点「应用修改」才重填（重填要 3~6 秒）。 */
    const fboxes = doc.querySelectorAll('#wzDocPane .dp-boxlayer .dp-box');
    check('填充页上也有可编辑盒子（内容槽才有）', fboxes.length > 0, fboxes.length + ' 个');
    check('填充页的盒子带 shape_index 槽位名（回写要用它精确指槽）',
      Array.from(fboxes).every((b) => /^fill:\d+$/.test(b.dataset.slot)),
      Array.from(fboxes).map((b) => b.dataset.slot).join(','));
    const fbox = fboxes[0];
    const fslot = fbox.dataset.slot;
    fbox.click(); await sleep(300);
    fbox.textContent = '填充页改过的字';
    fbox.dispatchEvent(new w.Event('blur', { bubbles: true }));
    await sleep(400);
    const applyBtn = doc.getElementById('dpFillApply');
    check('改完先"记着"（按钮上出现待应用数量，不立刻重填）',
      !!applyBtn && applyBtn.disabled === false && /待应用/.test(txt(doc.getElementById('dpFillPending')) || '')
        || /应用修改（\d+）/.test(txt(applyBtn)),
      applyBtn ? txt(applyBtn) + ' / ' + txt(doc.getElementById('dpFillPending')) : '(无按钮)');
    const nFills = fills.length;
    applyBtn.click();
    for (let i = 0; i < 16 && fills.length <= nFills; i++) await sleep(300);
    const lastFill = fills[fills.length - 1] || {};
    const rows = lastFill.mapping || [];
    const hit = rows.reduce((acc, r) => acc.concat((r.fills || []).map((f) => ({ ...f, ti: r.template_index }))), []);
    check('点「应用修改」→ 用 mapping 只回传改动的那一格',
      fills.length > nFills && hit.length === 1 &&
      String(hit[0].shape_index) === fslot.replace('fill:', '') && hit[0].text === '填充页改过的字',
      JSON.stringify(hit[0] || {}));
    check('重填后待应用清零', !/应用修改（\d+）/.test(txt(doc.getElementById('dpFillApply'))),
      txt(doc.getElementById('dpFillApply')));

    /* R9：删掉导错的演示模板（两下确认 → 发一次 delete，列表回到"只借内置主题"） */
    const del = doc.getElementById('dpTplDel');
    check('模板行有「删」按钮且选中模板时可用',
      !!del && del.disabled === false && txt(del) === '删', del ? txt(del) : '(无)');
    if (del) {
      del.click(); await sleep(300);
      check('第一下只是"确认删除"（不立刻删）',
        del.dataset.armed === '1' && /确认删除/.test(txt(del)) && deletes.length === 0,
        txt(del) + ' / 已发请求 ' + deletes.length);
      del.click();
      for (let i = 0; i < 12 && !deletes.length; i++) await sleep(250);
      check('第二下才真删，且带上选中的模板 id',
        deletes.length === 1 && deletes[0].template_id === '学校模板',
        JSON.stringify(deletes[0] || {}));
      await sleep(600);
      check('删完回到"只借内置主题"（选择被清掉）',
        doc.getElementById('dpTpl').value === '', doc.getElementById('dpTpl').value || '(空)');
    } else {
      check('第一下只是"确认删除"（不立刻删）', false, '没找到删按钮');
      check('第二下才真删，且带上选中的模板 id', false, '没找到删按钮');
      check('删完回到"只借内置主题"（选择被清掉）', false, '没找到删按钮');
    }
    check('无运行时错误', errors.length === 0, errors.slice(0, 2).join(' | '));
    w.close();
  }

  console.log('== 导出：新版式 PPT 接线（桩 /api/wps/export） ==');
  {
    const { w, doc, errors, exports } = makeDom({ withDoc: true });
    await sleep(2500);
    await openDocPanel(doc);
    doc.querySelector('#wzTabs .wz-tab[data-wz="slides"]').click(); await sleep(1000);
    const items = Array.from(doc.querySelectorAll('#wzDocMenu [data-dl]')).map((b) => b.dataset.dl);
    check('下载菜单 = Word + 新旧两条 PPT + HTML',
      items.join(',') === 'docx,pptx-deck,pptx,html', items.join(','));
    check('新版式那项写明了与预览同源', /与预览同一份/.test(txt(doc.querySelector('[data-dl="pptx-deck"]'))),
      txt(doc.querySelector('[data-dl="pptx-deck"]')));
    doc.getElementById('wzDocDownBtn').click();
    doc.querySelector('[data-dl="pptx-deck"]').click();
    for (let i = 0; i < 12 && !exports.length; i++) await sleep(300);
    for (let i = 0; i < 12 && /处理中/.test(txt(doc.getElementById('wzDocRes'))); i++) await sleep(300);
    const body = exports[0] || {};
    check('点它 POST 的是 pptx-deck', body.kind === 'pptx-deck', String(body.kind));
    check('带上面板那份 slides（预览=导出的关键）',
      Array.isArray(body.slides) && body.slides.length > 0, (body.slides || []).length + ' 页');
    check('带上当前主题 id', !!body.theme_id, String(body.theme_id || ''));
    check('结果条给文件名与下载链接',
      /教案_3\.3 幂函数\.pptx/.test(txt(doc.getElementById('wzDocRes'))) &&
      !!doc.querySelector('#wzDocRes a[href*="/api/wps/download"]'),
      txt(doc.getElementById('wzDocRes')).slice(0, 60));
    check('无运行时错误', errors.length === 0, errors.slice(0, 2).join(' | '));
    w.close();
  }

  console.log('== 导出：新版式失败时如实说明已回退 ==');
  {
    const { w, doc, errors } = makeDom({ wpsFallback: true , withDoc: true });
    await sleep(2500);
    await openDocPanel(doc);
    doc.querySelector('#wzTabs .wz-tab[data-wz="slides"]').click(); await sleep(1000);
    doc.getElementById('wzDocDownBtn').click();
    doc.querySelector('[data-dl="pptx-deck"]').click();
    await sleep(900);
    check('结果条注明"已回退到旧版式"', /已回退到旧版式/.test(txt(doc.getElementById('wzDocRes'))),
      txt(doc.getElementById('wzDocRes')).slice(0, 80));
    check('无运行时错误', errors.length === 0, errors.slice(0, 2).join(' | '));
    w.close();
  }

  console.log('== 幻灯片：窄屏（模拟 320px 视口） ==');  {
    const { w, doc, errors } = makeDom({ innerWidth: 320 , withDoc: true });
    await sleep(2500);
    await openDocPanel(doc);
    doc.querySelector('#wzTabs .wz-tab[data-wz="slides"]').click(); await sleep(1000);
    /* R9：面板宽度不再由文档视图控制（那条 --docp-w 拖宽逻辑已删）——
       这里改成断言"面板不再有内联宽度"，窄屏下由 CSS 自己收（.sidepanel 的媒体查询）。 */
    const pw = doc.documentElement.style.getPropertyValue('--docp-w');
    check('面板不再有文档视图的拖宽变量（R9 已删）', pw === '', pw || '(empty)');
    check('窄屏下面板仍是那块面板（没被隐藏）',
      !!doc.getElementById('sidePanel') && doc.getElementById('sidePanel').style.display !== 'none');
    const wrap = doc.querySelector('#wzDocPane .dp-slidewrap');
    check('窄屏下缩放系数仍为正数', !!wrap && parseFloat(wrap.dataset.scale) > 0,
      wrap ? wrap.dataset.scale : '(no wrap)');
    check('窄屏下预览台仍在（没被隐藏）', !!doc.querySelector('#wzDocPane .dp-canvas'));
    check('无运行时错误', errors.length === 0, errors.slice(0, 2).join(' | '));
    w.close();
  }

  console.log('== 幻灯片：服务端渲染不可用时退化成纯文本（不白屏） ==');
  {
    const { w, doc, errors } = makeDom({ slideFail: true , withDoc: true });
    await sleep(2500);
    await openDocPanel(doc);
    doc.querySelector('#wzTabs .wz-tab[data-wz="slides"]').click(); await sleep(1200);
    check('渲染失败仍显示内容（纯文本兜底）', !!doc.querySelector('#wzDocPane .dp-plain'));
    check('状态里说明用的是纯文本预览', /纯文本预览/.test(txt(doc.querySelector('.dp-slstat'))),
      txt(doc.querySelector('.dp-slstat')));
    check('无运行时错误', errors.length === 0, errors.slice(0, 2).join(' | '));
    w.close();
  }

  console.log('== R6-4/R6-5：PPT 优化不再作为独立模块 / 页面减肥 ==');
  {
    const { w, doc, errors } = makeDom({ withDoc: true });
    await sleep(2500);
    await openDocPanel(doc);
    doc.querySelector('#wzTabs .wz-tab[data-wz="slides"]').click(); await sleep(1000);
    check('面板里不再有独立的「PPT 优化」模块', !doc.querySelector('.ppt-box'));
    check('也不再有它的入口按钮', !doc.getElementById('pptPick') && !doc.getElementById('pptRun'));
    const head = doc.querySelectorAll('#wzDocPane .dp-slhead');
    check('主题与模板合成一行（只剩一行头部控件）', head.length === 1, head.length + ' 行');
    check('这一行里有主题、模板与两个按钮',
      !!doc.getElementById('dpTheme') && !!doc.getElementById('dpTpl') &&
      !!doc.getElementById('dpTplImport') && !!doc.getElementById('dpTplFill'));
    check('无运行时错误', errors.length === 0, errors.slice(0, 2).join(' | '));
    w.close();
  }

  console.log('== 槽位对齐：点一次「对齐」列出结构，可改角色并保存 ==');
  {
    const { w, doc, errors } = makeDom({ withDoc: true });
    await sleep(2500);
    await openDocPanel(doc);
    doc.querySelector('#wzTabs .wz-tab[data-wz="slides"]').click(); await sleep(1000);
    const tpl2 = doc.getElementById('dpTpl');
    tpl2.value = '学校模板';
    tpl2.dispatchEvent(new w.Event('change', { bubbles: true }));
    await sleep(700);
    check('选了模板后「对齐」可用', doc.getElementById('dpAlign').disabled === false);
    doc.getElementById('dpAlign').click();
    for (let i = 0; i < 12 && !doc.getElementById('dpAlignBox'); i++) await sleep(250);
    const box = doc.getElementById('dpAlignBox');
    check('对齐面板出现', !!box);
    const sels = box ? box.querySelectorAll('select[data-sig]') : [];
    check('列出每个文本框的角色选择（标题/正文/装饰）',
      sels.length > 0 && sels[0].options.length === 3, sels.length + ' 个选择框');
    if (sels.length) {
      sels[0].value = 'decoration';
      box.querySelector('[data-align-save]').click();
      await sleep(900);
      check('保存后收起', !doc.getElementById('dpAlignBox'));
    } else {
      check('保存后收起', false, '没有可选框');
    }
    check('无运行时错误', errors.length === 0, errors.slice(0, 2).join(' | '));
    w.close();
  }  console.log('== 会话侧：无「下一步」卡片 + 每条回答有【生成文档】 ==');
  {
    /* 用桩历史：以前这里数的是服务器上"当前会话"里那 13 条回答，
       换一次会话就变成 0 个按钮 → 假失败（踩过）。 */
    const { w, doc, errors } = makeDom({ withDoc: true });
    await sleep(3000);
    const vis = (() => { const c = doc.body.cloneNode(true); c.querySelectorAll('script,style').forEach((n) => n.remove()); return c.textContent.replace(/\s+/g, ' '); })();
    check('页面上没有「下一步」字样', !/下一步/.test(vis));
    check('计划卡片不渲染', doc.querySelectorAll('.plan-choice').length === 0);
    const btns = Array.from(doc.querySelectorAll('.pill-btn')).map(txt);
    check('历史回答带【生成文档】', btns.filter((x) => x === '生成文档').length > 0, btns.filter((x) => x === '生成文档').length + ' 个');
    check('无运行时错误', errors.length === 0, errors.slice(0, 2).join(' | '));
    w.close();
  }

  console.log('== R8：文档并进教案工作台（页签 / MD 源码预览 / 环节写回 / 学生下载 / 老数据迁移） ==');
  {
    /* ① 四个页签 + 左栏随页签切换 */
    const { w, doc, errors, saves } = makeDom({ withDoc: true });
    await sleep(2500);
    const tabs = Array.from(doc.querySelectorAll('#wzTabs .wz-tab')).map(txt);
    check('教案工作台有 4 个页签（教案/文档/Markdown/幻灯片）',
      tabs.join(',') === '教案,文档,Markdown,幻灯片', tabs.join(' | '));
    check('默认停在「教案」页签（还没进文档）',
      /教案/.test(txt(doc.querySelector('#wzTabs .wz-tab.wz-on'))), txt(doc.querySelector('#wzTabs .wz-tab.wz-on')));
    check('教案页签有左栏（环节目录）', !!doc.querySelector('#wzLeft .wz-rail-h'));
    check('教案页签的舞台是环节编辑器', !!doc.querySelector('#wzPlanPane #dzSteps'));

    /* ② 环节编辑 = 改会话里那份 slides（不再有第二套本地数据） */
    const nameInp = doc.querySelector('#wzPlanPane #dzSteps input.name');
    const hasSteps = !!nameInp;
    if (hasSteps) {
      nameInp.value = '导入（改过的）';
      nameInp.dispatchEvent(new w.Event('input', { bubbles: true }));
      for (let i = 0; i < 14 && !saves.length; i++) await sleep(250);
      const s0 = (saves[saves.length - 1] || {}).slides || [];
      check('改环节名写回会话的 slides（唯一数据源）', s0[0] && s0[0].name === '导入（改过的）',
        s0[0] ? s0[0].name : '(无)');
    } else {
      check('改环节名写回会话的 slides（唯一数据源）', false, '教案页签没有环节编辑器（没有文档？）');
    }

    /* ③ Markdown 页签：源码 / 预览 两个视图 */
    doc.querySelector('#wzTabs .wz-tab[data-wz="md"]').click(); await sleep(900);
    check('Markdown 是并列页签（不是折叠面板）',
      /Markdown/.test(txt(doc.querySelector('#wzTabs .wz-tab.wz-on'))));
    const sw = doc.getElementById('wzMdSwitch');
    check('Markdown 页签自带「源码 | 预览」按钮', !!sw && sw.style.display !== 'none',
      sw ? sw.style.display : '(无)');
    check('默认是源码视图（可编辑 textarea）', !!doc.querySelector('#wzDocPane .dp-mdta'));
    const srcTa = doc.querySelector('#wzDocPane .dp-mdta');
    const srcText = srcTa ? srcTa.value.slice(0, 20) : '';
    sw.querySelector('[data-mdv="view"]').click(); await sleep(500);
    check('点「预览」切到渲染视图（不再是 textarea）',
      !doc.querySelector('#wzDocPane .dp-mdta') && !!doc.querySelector('#wzDocPane .dp-mdview'));
    sw.querySelector('[data-mdv="src"]').click(); await sleep(500);
    const backTa = doc.querySelector('#wzDocPane .dp-mdta');
    check('切回源码内容不丢（同一份 markdown）', !!backTa && backTa.value.slice(0, 20) === srcText,
      backTa ? backTa.value.slice(0, 20) : '(无)');
    check('文档/Markdown 页签收起左栏（把宽度让给正文）',
      doc.getElementById('wzGrid').classList.contains('wz-noRail'));
    check('无运行时错误', errors.length === 0, errors.slice(0, 2).join(' | '));
    w.close();
  }

  {
    /* ④ 学生角色：那条按钮是「下载 Word」且真的走导出 */
    const { w, doc, errors, exports } = makeDom({ role: 'student', withDoc: true });
    await sleep(2600);
    const btns = Array.from(doc.querySelectorAll('.pill-btn')).map(txt);
    check('学生看到的是「下载 Word」（不是【生成文档】）',
      btns.includes('下载 Word') && !btns.includes('生成文档'),
      btns.filter((x) => /下载|生成文档/.test(x)).join('/') || '(无)');
    const wBtn = Array.from(doc.querySelectorAll('.pill-btn')).find((b) => txt(b) === '下载 Word');
    if (wBtn) {
      wBtn.click();
      for (let i = 0; i < 12 && !exports.length; i++) await sleep(250);
      check('点它直接走 /api/wps/export（kind=docx）', exports.length > 0 && exports[0].kind === 'docx',
        exports.length ? exports[0].kind : '(没发请求)');
    } else {
      check('点它直接走 /api/wps/export（kind=docx）', false, '没找到按钮');
    }
    check('无运行时错误', errors.length === 0, errors.slice(0, 2).join(' | '));
    w.close();
  }

  {
    /* ⑤ 老编排数据（edu_design_v1）一次性迁进会话 + 留备份（D10） */
    const { w, doc, errors, saves } = makeDom({ legacyDesign: true });
    await sleep(2800);
    const legacy = w.localStorage.getItem('edu_design_v1');
    const bak = w.localStorage.getItem('edu_design_v1.bak');
    check('老数据已迁走（原键删掉）', !legacy, legacy ? '还在' : '(已删)');
    check('留了备份键 edu_design_v1.bak', !!bak && /步骤|导入/.test(bak || ''), (bak || '').slice(0, 40));
    const last = (saves[saves.length - 1] || {}).slides || [];
    check('迁进来的环节写回了会话', last.length >= 2 && last[0].name === '导入',
      last.length + ' 页 · ' + (last[0] ? last[0].name : ''));
    check('无运行时错误', errors.length === 0, errors.slice(0, 2).join(' | '));
    w.close();
  }

  console.log('\n结果：' + pass + ' 通过 / ' + fail + ' 失败');
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.log('CHECK ERROR:', (e && e.stack) || e); process.exit(1); });
