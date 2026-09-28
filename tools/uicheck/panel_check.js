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
  const images = [];
  const finds = [];
  const objects = [];
  /* R14：桩要像真服务端那样把 `patch`（拖动/改大小/层级/删除）应用到盒子上 ——
     真服务端在 ppt_svg.Canvas.box 里套覆盖，预览才会跟着动；桩不套就成了"假链路"（测不出回归）。 */
  const slotPatch = {};
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
      /* R19：jsdom 没有排版，`getBoundingClientRect` 恒为 0 —— 左栏拖动排序要靠它算"插到哪一条缝"。
         这里给缩略图造一份**确定性几何**（每张 96 高、间距 100、从 y=200 起），
         否则拖动用例只能测到"拖到列表末尾"这一条路径。 */
      if (window.Element && window.Element.prototype.getBoundingClientRect) {
        window.Element.prototype.getBoundingClientRect = function () {
          const el = this;
          if (el.classList && el.classList.contains('wz-thumb')) {
            const i = parseInt(el.dataset.thumb || '0', 10);
            const top = 200 + i * 100;
            return { top, bottom: top + 96, left: 0, right: 180, width: 180, height: 96,
                     x: 0, y: top, toJSON() { return this; } };
          }
          return { top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0, x: 0, y: 0,
                   toJSON() { return this; } };
        };
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
          /* 真实接口只在 annotate=true 时回 boxes —— 桩也照这个规矩来（免得测出一条假链路）。
             覆盖有两处来源（真服务端也一样）：请求里 slides[i].slots（老师刚改的）+ 之前 patch 过的。
             boxes 也要**按内容算**（真服务端就是 build_deck 从 blocks 出盒子）：写死三条的话，
             "加一块 / 删一块"这类结构改动在面板检查里永远看不出来（R20 踩过）。 */
          const baseFor = (s) => {
            const rows = (s && Array.isArray(s.blocks) && s.blocks.length)
              ? s.blocks
              : (((s && s.bullets) || []).map((t) => ({ text: t })));
            const out = [{ slot: 'title', kind: 'text', align: 'start', x: 72, y: 60, w: 1136, h: 80, size: 40 }];
            rows.forEach((b, i) => out.push({ slot: 'block:' + i, kind: 'text', align: 'start',
              x: 96, y: 240 + i * 100, w: 1112, h: 60, size: 24 }));
            /* R21：自由层对象也要出盒子（真服务端就是这么做的），否则面板上选不中/拖不动 */
            ((s && s.objects) || []).forEach((o) => {
              out.push({ slot: 'obj:' + o.id, kind: o.kind === 'text' ? 'obj-text' : 'obj-shape',
                         align: 'start', x: o.x, y: o.y, w: o.w, h: o.h, size: o.size || 24,
                         obj: { shape: o.shape || 'text' } });
            });
            return out.length > 1 ? out : [{ slot: 'title', kind: 'text', align: 'start',
              x: 72, y: 60, w: 1136, h: 80, size: 40 }];
          };
          const sentList = body0.slides;
          const boxes = wantAnnotate
            ? Array.from({ length: n }, (_, pi) => {
                const base = baseFor(sentList ? sentList[pi] : DOC_STUB.slides[pi]);
                return base.map((b) => {
                /* 覆盖只认**这次请求里带的 slides**（真服务端就是这么做的：build_deck 用 slides[i].slots）；
                    没带 slides 的请求（只回传了一格文字）才退回 patch 记录。 */
                const sentSlides = body0.slides;
                const sent = (((sentSlides || [])[pi] || {}).slots || {})[b.slot];
                const p = sentSlides ? Object.assign({}, sent)
                                     : Object.assign({}, slotPatch[pi + ':' + b.slot] || {});
                if (!Object.keys(p).length) return { ...b };
                if (p.hidden) return null;
                return { ...b, x: b.x + (p.dx || 0), y: b.y + (p.dy || 0),
                         w: Math.max(40, b.w + (p.dw || 0)), h: Math.max(24, b.h + (p.dh || 0)),
                         ...(p.dx ? { dx: p.dx } : {}), ...(p.dy ? { dy: p.dy } : {}),
                         ...(p.dw ? { dw: p.dw } : {}), ...(p.dh ? { dh: p.dh } : {}),
                         ...(p.z ? { z: p.z } : {}) };
              }).filter(Boolean);
              })
            : undefined;
          return new Response(JSON.stringify({ ok: true, enabled: true, theme_id: THEMES.default,
            pages: Array.from({ length: n }, (_, i) => svgPageAnnotated(i, wantAnnotate)),
            ...(boxes ? { boxes } : {}),
            canvas: { w: 1280, h: 720, margin: 72, cols: 12, gutter: 24, content_w: 1136 },
            fonts: ['微软雅黑', '黑体', '宋体', '楷体'],
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
        if (p === '/api/slide/object') {
          let b = {};
          try { b = JSON.parse(op.body); } catch (e) {}
          objects.push(b);
          const list = (b.slides || DOC_STUB.slides).map((s, i) => {
            if (i !== b.index) return s;
            const objs = (s.objects || []).slice();
            if ((b.action || 'add') === 'add') {
              const id = 'o' + (objs.length + 1);
              objs.push(b.kind === 'text'
                ? { kind: 'text', id, x: 430, y: 315, w: 420, h: 90, text: '点这里改字', size: 28 }
                : { kind: 'shape', id, shape: b.shape || 'rect', x: 480, y: 280, w: 320, h: 160,
                    fill: '#EEF2F8', stroke: '#2F6BFF' });
              return { ...s, objects: objs };
            }
            if (b.action === 'patch') {
              return { ...s, objects: objs.map((o) => (o.id === b.id
                ? Object.assign({}, o, b.patch || {}) : o)) };
            }
            if (b.action === 'delete') {
              return { ...s, objects: objs.filter((o) => o.id !== b.id) };
            }
            if (b.action === 'up' || b.action === 'down') {
              const at = objs.findIndex((o) => o.id === b.id);
              const to = at + (b.action === 'up' ? 1 : -1);
              if (at < 0 || to < 0 || to >= objs.length) {
                return { ...s, __bad: b.action === 'up' ? '已经在最上面了' : '已经在最下面了' };
              }
              const copy = objs.slice();
              const tmp = copy[at]; copy[at] = copy[to]; copy[to] = tmp;
              return { ...s, objects: copy };
            }
            if (b.action === 'set') return { ...s, objects: b.objects || [] };
            return s;
          });
          const bad = (list[b.index] || {}).__bad;
          if (bad) {
            return new Response(JSON.stringify({ ok: false, error: bad }),
              { status: 200, headers: { 'Content-Type': 'application/json' } });
          }
          const clean = list.map((s) => { const c = { ...s }; delete c.__bad; return c; });
          return new Response(JSON.stringify({ ok: true, action: b.action || 'add', index: b.index,
            id: b.id || 'o' + ((clean[b.index].objects || []).length), objects: clean[b.index].objects || [],
            slides: clean, count: (clean[b.index].objects || []).length, session_id: 'stub',
            detail: (b.action || 'add') === 'add' ? '加了一个' + (b.kind === 'text' ? '文本框' : '形状')
                                                 : '对象已更新' }),
            { status: 200, headers: { 'Content-Type': 'application/json' } });
        }
        if (p === '/api/slide/find') {
          let b = {};
          try { b = JSON.parse(op.body); } catch (e) {}
          finds.push(b);
          const q = String(b.query || '');
          const all = [];
          (b.slides || DOC_STUB.slides).forEach((s, i) => {
            (s.blocks || []).forEach((bl, j) => {
              if (q && String(bl.text || '').includes(q)) {
                all.push({ index: i, slot: 'block:' + j, text: bl.text, count: 1, snippet: bl.text });
              }
            });
            if (q && String(s.name || '').includes(q)) {
              all.push({ index: i, slot: 'title', text: s.name, count: 1, snippet: s.name });
            }
          });
          if ((b.do || 'find') === 'replace') {
            const out = (b.slides || DOC_STUB.slides).map((s) => {
              const c = { ...s };
              if (q) {
                c.name = String(c.name || '').split(q).join(b.replace || '');
                c.blocks = (c.blocks || []).map((bl) => ({ ...bl,
                  text: String(bl.text || '').split(q).join(b.replace || '') }));
              }
              return c;
            });
            const pages = [...new Set(all.map((h) => h.index))];
            return new Response(JSON.stringify({ ok: true, query: q, replace: b.replace, hits: all,
              changed: all.length, pages, page_count: pages.length, slides: out, session_id: 'stub' }),
              { status: 200, headers: { 'Content-Type': 'application/json' } });
          }
          return new Response(JSON.stringify({ ok: true, query: q, hits: all, changed: 0,
            hit_pages: [...new Set(all.map((h) => h.index))], slides: b.slides || DOC_STUB.slides,
            session_id: 'stub' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
        }
        if (p === '/api/slide/image/providers') {
          return new Response(JSON.stringify({
            ok: true, enabled: !o.imgOff, usable: o.imgNoSource ? [] : ['pexels'],
            providers: [
              { id: 'openverse', name: 'Openverse', key_env: '', has_key: true, reachable: false, usable: false, note: '本机连不上' },
              { id: 'pexels', name: 'Pexels', key_env: 'PEXELS_API_KEY', has_key: true, reachable: true, usable: true, note: '可用' },
            ],
            hint: o.imgNoSource ? '现在一个图源都用不了：…要么让同事的 agent 直接把图 URL 给你' : '可用的图源：pexels',
          }), { status: 200, headers: { 'Content-Type': 'application/json' } });
        }
        if (p === '/api/slide/image/attach') {
          let b = {};
          try { b = JSON.parse(op.body); } catch (e) {}
          images.push(b);
          /* 桩也照真服务端的规矩：本机/内网地址一律拒（SSRF 防护），好让失败路径也被测到 */
          if (o.imgFail || /127\.0\.0\.1|localhost/.test(String(b.url || ''))) {
            return new Response(JSON.stringify({ ok: false,
              error: '这个地址不可用（本机/内网/解析不了）：127.0.0.1' }),
              { status: 200, headers: { 'Content-Type': 'application/json' } });
          }
          const out = (b.slides || DOC_STUB.slides).map((s, i) => (i === b.index
            ? (b.action === 'detach' ? (() => { const c = { ...s }; delete c.image; return c; })()
                                     : { ...s, layout: 'media', image: { src: 'stub/a.png', credit: b.credit || '来源：x' } })
            : s));
          return new Response(JSON.stringify({ ok: true, action: b.action || 'attach', index: b.index,
            image: (out[b.index] || {}).image || null, slides: out, bytes: 12345, sha256: 'stub',
            detail: '已把这张图挂到第 ' + (b.index + 1) + ' 页（12 KB，存在 data/uploads/）',
            session_id: 'stub' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
        }
        if (p === '/api/slide/slot') {
          let b = {};
          try { b = JSON.parse(op.body); } catch (e) {}
          slots.push(b);
          /* 桩：把 patch 记下来（下次 render 时套到 boxes 上），并回一份"已落库"的 slides */
          if (b.patch && b.slot) {
            const k = b.index + ':' + b.slot;
            slotPatch[k] = Object.assign({}, slotPatch[k] || {}, b.patch);
            if (b.patch.hidden === false) delete slotPatch[k].hidden;
          }
          const out = (b.slides || DOC_STUB.slides).map((s, i) => {
            if (i !== b.index || !b.patch || !b.slot) return s;
            const slots2 = Object.assign({}, s.slots || {});
            const next = Object.assign({}, slots2[b.slot] || {}, b.patch);
            Object.keys(next).forEach((k2) => { if (!next[k2]) delete next[k2]; });
            if (Object.keys(next).length) slots2[b.slot] = next; else delete slots2[b.slot];
            return Object.assign({}, s, { slots: slots2 });
          });
          return new Response(JSON.stringify({ ok: true, slot: b.slot, index: b.index,
            slides: out, pages: out.length,
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
  return { w: dom.window, doc: dom.window.document, errors, renderCalls, exports, saves, fills, deletes, slots, images, finds, objects };
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
    check('文档页签的说明小字还在（改成一句实话）', /下载在顶栏/.test(txt(doc.querySelector('.dp-note'))), txt(doc.querySelector('.dp-note')));
    check('无运行时错误', errors.length === 0, errors.slice(0, 2).join(' | '));
    w.close();
  }

  console.log('== 幻灯片：就地编辑（改字 / 增删块 / 换版式 / 排序 / 删页） ==');
  {
    const { w, doc, errors, saves, renderCalls, slots, exports, images, finds, objects } = makeDom({ withDoc: true });
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

    /* ① 单击 = 选中（WPS 习惯）→ 双击 = 改字 → 回传 /api/slide/slot */
    const target = Array.from(boxes).find((b) => b.dataset.slot === 'title') || boxes[0];
    const slotName = target.dataset.slot;
    target.click(); await sleep(300);
    check('单击是"选中"（出现选框 #dpSel），不再直接进编辑态',
      !!doc.querySelector('#wzDocPane #dpSel') && !target.classList.contains('dp-editing'));
    check('选框有 8 个手柄（WPS 那样）',
      doc.querySelectorAll('#wzDocPane #dpSel .dp-h').length === 8,
      doc.querySelectorAll('#wzDocPane #dpSel .dp-h').length + ' 个');
    check('操作条显示选中的是哪一格', txt(doc.getElementById('dpSelName')) === slotName,
      txt(doc.getElementById('dpSelName')));
    target.dispatchEvent(new w.MouseEvent('dblclick', { bubbles: true, cancelable: true }));
    await sleep(200);
    check('双击进入编辑态（同位置出现输入框）',
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

    /* ②b R14：选中 → 拖动 / 手柄 / 方向键 / 层级 / 删除 / 回原位（本轮主验收） */
    {
      const scale = parseFloat(doc.querySelector('#wzDocPane .dp-slidewrap').dataset.scale || '0.375');
      const boxOf = (s) => doc.querySelector('#wzDocPane .dp-box[data-slot="' + s + '"]');
      const lastPatch = () => (slots.filter((s) => s.patch).pop() || {}).patch || {};
      const drag = async (el, dxPx, dyPx, opt) => {
        const o2 = opt || {};
        el.dispatchEvent(new w.MouseEvent('mousedown', { bubbles: true, cancelable: true,
          clientX: 200, clientY: 200, button: 0 }));
        doc.dispatchEvent(new w.MouseEvent('mousemove', { bubbles: true, cancelable: true,
          clientX: 200 + dxPx, clientY: 200 + dyPx, shiftKey: !!o2.shift, altKey: !!o2.alt }));
        return o2;
      };
      const drop = async () => {
        doc.dispatchEvent(new w.MouseEvent('mouseup', { bubbles: true }));
        await sleep(500);
      };

      check('有吸附开关与层级/删除按钮',
        !!doc.getElementById('dpSnap') && !!doc.getElementById('dpUp') &&
        !!doc.getElementById('dpDown') && !!doc.getElementById('dpDel') && !!doc.getElementById('dpReset'));
      doc.querySelector('#wzDocPane #dpCanvas').dispatchEvent(
        new w.MouseEvent('mousedown', { bubbles: true, cancelable: true, clientX: 5, clientY: 5, button: 0 }));
      await sleep(120);
      check('点空白取消选中；没选中时层级/删除按钮是灰的',
        !doc.querySelector('#wzDocPane #dpSel') && doc.getElementById('dpUp').disabled === true &&
        doc.getElementById('dpDel').disabled === true,
        'sel=' + txt(doc.getElementById('dpSelName')));

      /* 吸附先关掉：位置要能对得上账（dx = 屏幕位移 ÷ 缩放） */
      doc.getElementById('dpSnap').click(); await sleep(100);
      check('吸附可以关（按钮如实显示）', /吸附：关/.test(txt(doc.getElementById('dpSnap'))),
        txt(doc.getElementById('dpSnap')));
      const b0 = boxOf('block:0');
      b0.click(); await sleep(150);
      const x0 = parseFloat(b0.style.left);
      await drag(b0, 60 * scale, 30 * scale); await drop();
      check('拖动把 dx/dy 回传 /api/slide/slot（设计像素）',
        Math.abs(lastPatch().dx - 60) <= 1.5 && Math.abs(lastPatch().dy - 30) <= 1.5,
        JSON.stringify(lastPatch()));
      const b0b = boxOf('block:0');
      check('预览里的盒子跟着挪了（服务端 boxes 带覆盖）',
        Math.abs(parseFloat(b0b.style.left) - (x0 + 60)) <= 1.5,
        x0 + ' → ' + b0b.style.left);
      check('拖动落进 slides[i].slots（存的是覆盖，不重写字）',
        lastPatch() && slots.filter((s) => s.patch).length > 0);

      /* 拖手柄改大小：只动 dw/dh */
      const nBefore = slots.length;
      b0b.click(); await sleep(150);
      const hE = doc.querySelector('#wzDocPane #dpSel .dp-h-e');
      check('选框上有东侧手柄', !!hE);
      hE.dispatchEvent(new w.MouseEvent('mousedown', { bubbles: true, cancelable: true,
        clientX: 300, clientY: 300, button: 0 }));
      doc.dispatchEvent(new w.MouseEvent('mousemove', { bubbles: true, cancelable: true,
        clientX: 300 + 40 * scale, clientY: 300 }));
      doc.dispatchEvent(new w.MouseEvent('mouseup', { bubbles: true }));
      await sleep(500);
      check('拖手柄改大小 → dw 回传（上一次的 dx 保持不变）',
        slots.length > nBefore && Math.abs(lastPatch().dw - 40) <= 1.5 &&
        Math.abs(lastPatch().dx - 60) <= 1.5 && !lastPatch().dh,
        JSON.stringify(lastPatch()));

      /* 吸打开回来：拖到离安全边距 2px 的地方要被吸到 72 */
      doc.getElementById('dpSnap').click(); await sleep(100);
      const b1 = boxOf('block:1');
      b1.click(); await sleep(150);
      const x1 = parseFloat(b1.style.left);
      await drag(b1, (72 - x1 + 2) * scale, 0);
      const guides = doc.querySelectorAll('#wzDocPane .dp-gl').length;
      await drop();
      check('吸附时画出参考线（.dp-gl）', guides > 0, guides + ' 条');
      check('拖到边距附近被吸到 72px（安全边距）',
        Math.abs(parseFloat(boxOf('block:1').style.left) - 72) <= 0.5,
        x1 + ' → ' + boxOf('block:1').style.left + ' · patch=' + JSON.stringify(lastPatch()));

      /* 方向键微调：1px；Shift = 10px（dx 是"相对版式原位的偏移"，所以比的是增量） */
      b1.click(); await sleep(120);
      const dxBefore = parseFloat(boxOf('block:1').dataset.dx || '0');
      const nKey = slots.length;
      doc.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
      await sleep(400);
      check('方向键微调 1px', slots.length > nKey && Math.abs(lastPatch().dx - (dxBefore + 1)) <= 0.6,
        'dx ' + dxBefore + ' → ' + JSON.stringify(lastPatch()));
      const dyBefore = parseFloat(boxOf('block:1').dataset.dy || '0');
      const nKey2 = slots.length;
      doc.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'ArrowDown', shiftKey: true, bubbles: true }));
      await sleep(400);
      check('Shift+方向键微调 10px', slots.length > nKey2 && Math.abs(lastPatch().dy - (dyBefore + 10)) <= 0.6,
        'dy ' + dyBefore + ' → ' + JSON.stringify(lastPatch()));

      /* 层级：上移一层 → z 变大 */
      const nZ = slots.length;
      doc.getElementById('dpUp').click(); await sleep(400);
      check('上移一层写进 z（服务端按 z 重排出图顺序）',
        slots.length > nZ && lastPatch().z === 1, JSON.stringify(lastPatch()));
      doc.getElementById('dpDown').click(); await sleep(400);
      check('下移一层把 z 降回去', lastPatch().z === 0, JSON.stringify(lastPatch()));

      /* 回原位：覆盖清空 */
      doc.getElementById('dpReset').click(); await sleep(400);
      check('「回原位」把 dx/dy 归零（覆盖被清掉）',
        lastPatch().dx === 0 && lastPatch().dy === 0 && lastPatch().dw === 0,
        JSON.stringify(lastPatch()));

      /* 删除：hidden（不画、字还在、能撤销） */
      b1.click(); await sleep(120);
      const nDel = slots.length;
      doc.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Delete', bubbles: true }));
      await sleep(500);
      check('Delete 删掉这一处（回传 hidden）',
        slots.length > nDel && slots[slots.length - 1].patch &&
        slots[slots.length - 1].patch.hidden === true,
        JSON.stringify(slots[slots.length - 1] || {}));
      check('删掉的格子从页面上消失（服务端不再给它盒子）',
        !doc.querySelector('#wzDocPane .dp-box[data-slot="block:1"]'));
      doc.getElementById('dpUndo').click(); await sleep(600);
      check('撤销能把删掉的文字找回来',
        !!doc.querySelector('#wzDocPane .dp-box[data-slot="block:1"]'),
        'block:1 ' + (doc.querySelector('#wzDocPane .dp-box[data-slot="block:1"]') ? '在' : '没了') +
        ' · 最近回传 ' + JSON.stringify(slots.slice(-2)));
    }

    /* ②c R15：右栏属性 + 工具分组；R13：缩放 / 网格 / 浏览视图 */
    {
      const boxOf = (s) => doc.querySelector('#wzDocPane .dp-box[data-slot="' + s + '"]');
      const lastPatch = () => (slots.filter((s) => s.patch).pop() || {}).patch || {};
      const props = () => doc.querySelector('#wzDocPane #dpProps');

      check('工具按 WPS 分了四组（开始/插入/设计/放映）',
        Array.from(doc.querySelectorAll('#wzDocPane .dp-tt')).map((b) => txt(b)).join(',') ===
        '开始,插入,设计,放映',
        Array.from(doc.querySelectorAll('#wzDocPane .dp-tt')).map((b) => txt(b)).join('/'));
      check('四组工具都能点（没做的写在组里，不放假按钮）',
        !doc.querySelector('#wzDocPane .dp-tt[data-soon]'),
        Array.from(doc.querySelectorAll('#wzDocPane .dp-tt')).map((b) => txt(b)).join('/'));
      doc.querySelector('#wzDocPane .dp-tt[data-tool="insert"]').click(); await sleep(300);
      check('点「插入」能切过去（里面有插入文本框/形状 + 联网取图）',
        doc.querySelector('#wzDocPane .dp-tt[data-tool="insert"]').classList.contains('on') &&
        !!doc.getElementById('dpInsText') && !!doc.getElementById('dpImgGet'),
        txt(doc.querySelector('#wzDocPane .dp-toolgroup[data-group="insert"]')).slice(0, 60));
      doc.querySelector('#wzDocPane .dp-tt[data-tool="design"]').click(); await sleep(400);
      check('「设计」组里有网格/适应窗口/放大缩小',
        !!doc.getElementById('dpGrid2') && !!doc.getElementById('dpZoomFit2') &&
        !!doc.getElementById('dpZoomIn') && !!doc.getElementById('dpZoomOut'));
      doc.querySelector('#wzDocPane .dp-tt[data-tool="home"]').click(); await sleep(400);

      /* R18 前置：联网取图（同事的 agent 给 URL 那条路） */
      doc.querySelector('#wzDocPane .dp-tt[data-tool="insert"]').click(); await sleep(600);
      check('「插入」组有联网取图的输入与按钮',
        !!doc.getElementById('dpImgUrl') && !!doc.getElementById('dpImgCredit') &&
        !!doc.getElementById('dpImgGet') && !!doc.getElementById('dpImgDel'),
        ['dpImgUrl', 'dpImgCredit', 'dpImgGet', 'dpImgDel']
          .filter((id) => !doc.getElementById(id)).join(',') || 'all');
      check('联网状态行说清了图源情况（不假装能搜）',
        /取图可用|联网图源可用|是关的/.test(txt(doc.getElementById('dpImgState'))),
        txt(doc.getElementById('dpImgState')));
      const nImg0 = images.length;
      const toast = () => txt(doc.getElementById('edutoast'));
      doc.getElementById('dpImgUrl').value = 'file:///C:/x.png';
      doc.getElementById('dpImgGet').click(); await sleep(250);
      check('非 http(s) 地址当场拦下（不发请求）',
        images.length === nImg0 && /http\/https/.test(toast()), toast());
      doc.getElementById('dpImgUrl').value = '';
      doc.getElementById('dpImgGet').click(); await sleep(250);
      check('空地址也当场拦下（不发请求）', images.length === nImg0, '请求数 ' + (images.length - nImg0));
      /* 本机/内网地址：客户端管不了，由服务端拒（SSRF 防护）—— 失败要把版式还原，别留空图文页 */
      doc.getElementById('dpImgUrl').value = 'http://127.0.0.1/x.png';
      doc.getElementById('dpImgGet').click();
      for (let i = 0; i < 12 && images.length <= nImg0; i++) await sleep(250);
      await sleep(400);
      check('本机/内网地址被拒，并且如实报错、版式还原',
        images.length > nImg0 && /不可用|内网/.test(toast()) &&
        txt(doc.getElementById('dpLayout')) !== '图文',
        JSON.stringify(toast()) + ' · 版式=' + txt(doc.getElementById('dpLayout')));
      doc.getElementById('dpImgUrl').value = 'https://example.com/figure.png';
      doc.getElementById('dpImgCredit').value = '来源：同事的 agent';
      doc.getElementById('dpImgGet').click();
      for (let i = 0; i < 12 && images.length <= nImg0; i++) await sleep(250);
      const got = images[images.length - 1] || {};
      const pageNow = parseInt((txt(doc.querySelector('#wzDocPane #dpPgBar span')).match(/第\s*(\d+)/) || [])[1], 10) - 1;
      check('取图请求带上了地址/出处/页码与整册数据',
        got.url === 'https://example.com/figure.png' && got.credit === '来源：同事的 agent' &&
        got.index === pageNow && Array.isArray(got.slides),
        JSON.stringify({ url: got.url, credit: got.credit, index: got.index, pageNow,
                         pages: (got.slides || []).length }));
      check('挂图前先把这一页切成图文版式（图才有位置）',
        !!got.slides && (got.slides[got.index] || {}).layout === 'media',
        JSON.stringify(((got.slides || [])[got.index] || {}).layout));
      await sleep(400);
      check('状态行显示本页有图与出处',
        /本页有图/.test(txt(doc.getElementById('dpImgState'))), txt(doc.getElementById('dpImgState')));
      const nImg1 = images.length;
      doc.getElementById('dpImgDel').click();
      for (let i = 0; i < 12 && images.length <= nImg1; i++) await sleep(250);
      check('「卸下图」发的是 detach',
        (images[images.length - 1] || {}).action === 'detach',
        JSON.stringify(images[images.length - 1] || {}));
      doc.querySelector('#wzDocPane .dp-tt[data-tool="home"]').click(); await sleep(400);

      /* 右栏：选中才出现，随选中切换 */
      const b0 = boxOf('block:0');
      b0.click(); await sleep(300);
      check('选中后右栏出现（.dp-props）', !!props());
      check('右栏能改位置/大小/字号/字体/颜色/对齐/行距',
        ['dpPx', 'dpPy', 'dpPw', 'dpPh', 'dpPsize', 'dpPfont', 'dpPcolor', 'dpPlh', 'dpPbold', 'dpPitalic']
          .every((id) => !!doc.getElementById(id)),
        ['dpPx', 'dpPy', 'dpPw', 'dpPh', 'dpPsize', 'dpPfont', 'dpPcolor', 'dpPlh']
          .filter((id) => !doc.getElementById(id)).join(',') || 'all');
      check('右栏写着改的是哪一格', /block:0/.test(txt(props())), txt(props()).slice(0, 40));
      check('字体下拉来自服务端清单（含「跟随主题」）',
        Array.from(doc.getElementById('dpPfont').options).map((o) => o.value).join(',') ===
        ',微软雅黑,黑体,宋体,楷体',
        Array.from(doc.getElementById('dpPfont').options).map((o) => o.value).join('/'));
      check('字号下拉是设计阶梯（来自 boxes 的 size）',
        Array.from(doc.getElementById('dpPsize').options).map((o) => o.value).join(',') ===
        '24,40,__c',
        Array.from(doc.getElementById('dpPsize').options).map((o) => o.value).join('/'));

      /* 改样式 → 回传 patch（与拖动同一条路） */
      let n0 = slots.length;
      doc.getElementById('dpPbold').click(); await sleep(400);
      check('右栏点 B → 回传 bold', slots.length > n0 && lastPatch().bold === true,
        JSON.stringify(lastPatch()));
      n0 = slots.length;
      doc.getElementById('dpPfont').value = '楷体';
      doc.getElementById('dpPfont').dispatchEvent(new w.Event('change', { bubbles: true })); await sleep(400);
      check('右栏换字体 → 回传 font', slots.length > n0 && lastPatch().font === '楷体',
        JSON.stringify(lastPatch()));
      n0 = slots.length;
      doc.querySelector('#dpProps [data-align="middle"]').click(); await sleep(400);
      check('右栏点「中」→ 回传 align=middle', slots.length > n0 && lastPatch().align === 'middle',
        JSON.stringify(lastPatch()));
      n0 = slots.length;
      const lh = doc.getElementById('dpPlh');
      lh.value = '1.6'; lh.dispatchEvent(new w.Event('change', { bubbles: true })); await sleep(400);
      check('右栏改行距 → 回传 lh', slots.length > n0 && Math.abs(lastPatch().lh - 1.6) < 0.01,
        JSON.stringify(lastPatch()));
      n0 = slots.length;
      const psz = doc.getElementById('dpPsize');
      psz.value = '__c'; psz.dispatchEvent(new w.Event('change', { bubbles: true })); await sleep(200);
      check('字号选「自定义…」露出数字框',
        doc.getElementById('dpPsizeCustom').style.display !== 'none');
      const pnum = doc.getElementById('dpPsizeNum');
      pnum.value = '37'; pnum.dispatchEvent(new w.Event('change', { bubbles: true })); await sleep(400);
      check('自定义字号回传 size=37', slots.length > n0 && Math.abs(lastPatch().size - 37) < 0.01,
        JSON.stringify(lastPatch()));
      await sleep(600);
      check('偏离设计阶梯要给提示（不阻断）', !!doc.getElementById('dpPoff'),
        txt(doc.getElementById('dpPoff')));

      /* 位置/大小：右栏给绝对值，覆盖里存偏移 */
      n0 = slots.length;
      const px = doc.getElementById('dpPx');
      const xNow = parseFloat(boxOf('block:0').style.left);
      px.value = String(Math.round(xNow) + 24);
      px.dispatchEvent(new w.Event('change', { bubbles: true })); await sleep(400);
      const dxNow = parseFloat(boxOf('block:0').dataset.dx || '0');
      check('右栏改 X → 换算成 dx 回传', slots.length > n0 && Math.abs(lastPatch().dx - dxNow) < 0.6,
        'x0=' + xNow + ' · dx=' + dxNow + ' · patch=' + JSON.stringify(lastPatch()));

      /* 回到档位 / 本页复原 */
      n0 = slots.length;
      doc.getElementById('dpPreset').click(); await sleep(500);
      check('「回档位」把这一格的覆盖清掉（字号/字体/颜色都回默认）',
        slots.length > n0 && !!(lastPatch().size === 0 || lastPatch().size === undefined),
        JSON.stringify(lastPatch()));
      const nSaves0 = saves.length;
      boxOf('block:0').click(); await sleep(200);
      doc.getElementById('dpPresetPage').click();
      for (let i = 0; i < 12 && saves.length <= nSaves0; i++) await sleep(250);
      const lastSave = saves[saves.length - 1] || {};
      check('「本页复原」把本页覆盖一次清掉并存回会话',
        saves.length > nSaves0 && !((lastSave.slides || [])[0] || {}).slots,
        JSON.stringify(((lastSave.slides || [])[0] || {}).slots || null));

      /* R16：备注（编辑区下方写讲稿，导出后 WPS 里能看到） */
      const notesEl = () => doc.getElementById('dpNotes');
      check('有备注输入框（编辑区下方）', !!notesEl(), notesEl() ? 'ok' : '(没有)');
      const nSaves1 = saves.length;
      notesEl().value = '第 1 页的讲稿：先问面积怎么算';
      notesEl().dispatchEvent(new w.Event('input', { bubbles: true }));
      for (let i = 0; i < 12 && saves.length <= nSaves1; i++) await sleep(250);
      const savedN = saves[saves.length - 1] || {};
      check('备注写进 slides[i].notes 并自动回存',
        saves.length > nSaves1 && ((savedN.slides || [])[0] || {}).notes === '第 1 页的讲稿：先问面积怎么算',
        JSON.stringify(((savedN.slides || [])[0] || {}).notes || null));
      doc.querySelectorAll('#wzLeft .wz-thumb')[1].click(); await sleep(600);
      check('换页后备注框是空的（不把上一页的讲稿串过来）', notesEl().value === '',
        JSON.stringify(notesEl().value));
      notesEl().value = '第 2 页的讲稿';
      notesEl().dispatchEvent(new w.Event('input', { bubbles: true }));
      await sleep(900);
      doc.querySelectorAll('#wzLeft .wz-thumb')[0].click(); await sleep(600);
      check('换回第 1 页，备注还是第 1 页那份（各页各存各的）',
        notesEl().value === '第 1 页的讲稿：先问面积怎么算', JSON.stringify(notesEl().value));
      /* 导出载荷也要带备注（旧版式那份 pptx 才有讲稿） */
      const legacyBtn = doc.querySelector('#wzDownMenu [data-dl="pptx"]');
      const nExp = exports.length;
      legacyBtn.click();
      for (let i = 0; i < 12 && exports.length <= nExp; i++) await sleep(250);
      const expBody = exports[exports.length - 1] || {};
      const expSteps = ((expBody.payload || {}).steps) || [];
      check('旧版式导出把每一页的备注带上（payload.steps[].notes）',
        exports.length > nExp && (expSteps[0] || {}).notes === '第 1 页的讲稿：先问面积怎么算',
        'exports ' + nExp + '→' + exports.length + ' · steps0=' + JSON.stringify(expSteps[0] || null));
      doc.querySelector('#wzDownMenu [data-dl="pptx"]').blur?.();

      /* R13：缩放 */
      const wrap = () => doc.querySelector('#wzDocPane .dp-slidewrap');
      check('缩放条默认是"适应窗口"（0.375）', wrap().dataset.scale === '0.3750', wrap().dataset.scale);
      check('状态栏说了页码与缩放',
        /第 \d+ \/ \d+ 页/.test(txt(doc.getElementById('wzStatusBar'))) &&
        /38%/.test(txt(doc.getElementById('wzStatusBar'))),
        txt(doc.getElementById('wzStatusBar')));
      const zs = doc.getElementById('dpZoom');
      zs.value = '150'; zs.dispatchEvent(new w.Event('change', { bubbles: true })); await sleep(300);
      check('拖缩放条 → 画布 scale 变了并显示百分比',
        wrap().dataset.scale === '1.5000' && /150%/.test(txt(doc.getElementById('dpZoomVal'))),
        wrap().dataset.scale + ' / ' + txt(doc.getElementById('dpZoomVal')));
      check('放大后容器可滚动（.dp-zoombox 撑出滚动区）',
        !!doc.getElementById('dpZoomBox') && wrap().style.overflow === 'auto',
        wrap().style.overflow + ' / ' + (doc.getElementById('dpZoomBox') || {}).style?.width);
      doc.dispatchEvent(new w.KeyboardEvent('keydown', { key: '0', ctrlKey: true, bubbles: true }));
      await sleep(300);
      check('Ctrl+0 回到适应窗口', wrap().dataset.scale === '0.3750', wrap().dataset.scale);
      doc.getElementById('dpZoomFit').click(); await sleep(200);
      check('「适应窗口」按钮同样生效（并且按钮高亮）',
        wrap().dataset.scale === '0.3750' && doc.getElementById('dpZoomFit').classList.contains('dark'));

      /* R13：网格（Shift+F9） */
      doc.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'F9', shiftKey: true, bubbles: true }));
      await sleep(300);
      const gridCols = doc.querySelectorAll('#wzDocPane #dpCanvas .dp-grid .dp-gridcol').length;
      check('Shift+F9 出参考线（安全边距框 + 12 栏 + 中线）',
        !!doc.querySelector('#wzDocPane #dpCanvas .dp-grid .dp-gridbox') && gridCols === 12,
        gridCols + ' 栏');
      doc.getElementById('dpGrid').click(); await sleep(200);
      check('再点「网格」关掉（不残留在页面上）',
        !doc.querySelector('#wzDocPane #dpCanvas .dp-grid'));

      /* R13：浏览视图 */
      doc.getElementById('dpBrowse').click(); await sleep(600);
      const cards = doc.querySelectorAll('#wzDocPane .dp-wall .dp-wallcard');
      check('浏览视图是一屏缩略图墙', cards.length === 5 && !doc.querySelector('#wzDocPane #dpCanvas'),
        cards.length + ' 张');
      doc.querySelectorAll('#wzDocPane .dp-wall .dp-wallcard')[2].click(); await sleep(700);
      check('点浏览里的第 3 页 → 回普通视图并跳到那一页',
        !!doc.querySelector('#wzDocPane #dpCanvas') &&
        /第 3 \//.test(txt(doc.querySelector('#wzDocPane #dpPgBar span'))),
        txt(doc.querySelector('#wzDocPane #dpPgBar span')));
      check('无运行时错误（R15/R13 这一段）', errors.length === 0, errors.slice(0, 2).join(' | '));
      /* R17：放映（F5 从头 / Shift+F5 从本页 / 方向键翻页 / Esc 退出） */
      const showLayer = () => doc.getElementById('dpShow');
      const showNum = () => txt(doc.querySelector('#dpShow .bar #dpShowNum'));
      doc.querySelector('#wzDocPane .dp-tt[data-tool="show"]').click(); await sleep(400);
      check('「放映」组里有从头放映 / 从本页放映',
        !!doc.getElementById('dpShowAll') && !!doc.getElementById('dpShowHere'));
      doc.getElementById('dpShowAll').click(); await sleep(400);
      check('进入放映：铺一层全屏层，里面是同一份 SVG',
        !!showLayer() && !!doc.querySelector('#dpShow .pg svg'),
        (showLayer() ? 'ok' : '(没有 #dpShow)') + ' · 画布 svg=' +
        !!doc.querySelector('#wzDocPane #dpCanvas svg') + ' · err=' + errors.slice(-2).join(' | ') +
        ' · body 尾=' + (doc.body.lastElementChild || {}).className);
      check('放映层给页码读数', /第 1 \/ 5 页/.test(showNum()), showNum());
      check('放映时状态栏也说"放映中"', /放映中/.test(txt(doc.getElementById('wzStatusBar'))),
        txt(doc.getElementById('wzStatusBar')));
      doc.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
      await sleep(250);
      check('方向键翻到下一页', /第 2 \/ 5 页/.test(showNum()), showNum());
      doc.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
      await sleep(250);
      check('方向键能往回翻', /第 1 \/ 5 页/.test(showNum()), showNum());
      doc.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'End', bubbles: true }));
      await sleep(250);
      check('End 跳末页', /第 5 \/ 5 页/.test(showNum()), showNum());
      doc.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Home', bubbles: true }));
      await sleep(250);
      check('Home 回首页', /第 1 \/ 5 页/.test(showNum()), showNum());
      doc.getElementById('dpShowNext').click(); await sleep(250);
      check('点「下一页」也翻页', /第 2 \/ 5 页/.test(showNum()), showNum());
      doc.querySelector('#dpShow .pg').click(); await sleep(250);
      check('点画面翻页（WPS 习惯）', /第 3 \/ 5 页/.test(showNum()), showNum());
      doc.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      await sleep(300);
      check('Esc 退出放映（层拆掉，回到编辑视图）',
        !showLayer() && !!doc.querySelector('#wzDocPane #dpCanvas'));
      doc.querySelector('#wzDocPane .dp-tt[data-tool="home"]').click(); await sleep(300);
      doc.querySelectorAll('#wzLeft .wz-thumb')[3].click(); await sleep(500);
      doc.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'F5', shiftKey: true, bubbles: true }));
      await sleep(350);
      check('Shift+F5 从当前页开始放（第 4 页）', /第 4 \/ 5 页/.test(showNum()), showNum());
      doc.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      await sleep(300);
      check('再按 Esc 退出（可反复进出）', !showLayer());

      /* 选了几处：状态条会写"已选 N 处"，没多选就是主选那一处（R18/R19/R20 都用） */
      const docpSelCount = () => {
        const m = txt(doc.getElementById('dpSelName')).match(/已选\s*(\d+)/);
        return m ? parseInt(m[1], 10) : (doc.getElementById('dpSel') ? 1 : 0);
      };
      const boxSlotList = () => Array.from(doc.querySelectorAll('#wzDocPane .dp-canvas .dp-box'))
        .map((b) => b.dataset.slot);
      const boxAt = (slot) => doc.querySelector('#wzDocPane .dp-canvas .dp-box[data-slot="' + slot + '"]');

      /* R18：快捷键 + 剪贴板 + 查找替换 */
      {
        const key = (k, opt) => doc.dispatchEvent(new w.KeyboardEvent('keydown',
          Object.assign({ key: k, bubbles: true, cancelable: true }, opt || {})));
        const ctrl = (k, opt) => key(k, Object.assign({ ctrlKey: true }, opt || {}));
        const lastPatch2 = () => (slots.filter((s) => s.patch).pop() || {}).patch || {};

        /* 快捷键表：表里写的每一条都真的绑了（结构 + 逐条行为两段验） */
        ctrl('/'); await sleep(500);
        const kbox = doc.getElementById('dpKeyBox');
        check('Ctrl+/ 打开快捷键表', !!kbox && /Ctrl\+S/.test(txt(kbox)) && /F5/.test(txt(kbox)),
          kbox ? txt(kbox).slice(0, 40) : '(没打开)');
        const rows = kbox ? kbox.querySelectorAll('tr').length : 0;
        check('快捷键表逐条列全（≥15 条）', rows >= 15, rows + ' 条');
        doc.getElementById('dpKeyClose').click(); await sleep(400);
        check('能收起快捷键表', !doc.getElementById('dpKeyBox'));

        /* Ctrl+S：立刻存一次 */
        const nSave = saves.length;
        ctrl('s');
        for (let i = 0; i < 12 && saves.length <= nSave; i++) await sleep(250);
        check('Ctrl+S 立刻保存（不等防抖）', saves.length > nSave, saves.length + ' 次');

        /* Ctrl+A 多选 → 批量 Delete（选中的处数应与页面上盒子数一致） */
        const boxSlots = boxSlotList;
        boxOf(boxSlots()[0]).click(); await sleep(250);
        ctrl('a'); await sleep(350);
        const nBoxes = boxSlots().length;
        const nSel = docpSelCount();
        check('Ctrl+A 选中本页全部文字（细框标出其余几处）',
          nSel === nBoxes && nSel >= 2 && /已选/.test(txt(doc.getElementById('dpSelName'))),
          nSel + ' 处（页面共 ' + nBoxes + ' 个盒子）· ' + txt(doc.getElementById('dpSelName')));
        doc.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        await sleep(200);

        /* Ctrl+B/I/U + 对齐 + 字号：逐条验 */
        boxOf(boxSlots()[0]).click(); await sleep(200);
        let n0 = slots.length;
        ctrl('b'); await sleep(400);
        check('Ctrl+B 加粗', slots.length > n0 && lastPatch2().bold === true, JSON.stringify(lastPatch2()));
        n0 = slots.length;
        ctrl('i'); await sleep(400);
        check('Ctrl+I 斜体', slots.length > n0 && lastPatch2().italic === true, JSON.stringify(lastPatch2()));
        n0 = slots.length;
        ctrl('u'); await sleep(400);
        check('Ctrl+U 下划线', slots.length > n0 && lastPatch2().underline === true, JSON.stringify(lastPatch2()));
        n0 = slots.length;
        ctrl('e'); await sleep(400);
        check('Ctrl+E 居中', slots.length > n0 && lastPatch2().align === 'middle', JSON.stringify(lastPatch2()));
        n0 = slots.length;
        ctrl('l'); await sleep(400);
        check('Ctrl+L 左对齐', slots.length > n0 && lastPatch2().align === 'start', JSON.stringify(lastPatch2()));
        n0 = slots.length;
        ctrl('r'); await sleep(400);
        check('Ctrl+R 右对齐', slots.length > n0 && lastPatch2().align === 'end', JSON.stringify(lastPatch2()));
        n0 = slots.length;
        ctrl('='); await sleep(400);
        check('Ctrl+= 字号加一档', slots.length > n0 && lastPatch2().size > 0, JSON.stringify(lastPatch2()));
        n0 = slots.length;
        ctrl('-'); await sleep(400);
        check('Ctrl+- 字号减一档', slots.length > n0 && typeof lastPatch2().size === 'number',
          JSON.stringify(lastPatch2()));
        ctrl('0'); await sleep(300);
        check('Ctrl+0 缩放回适应窗口',
          doc.querySelector('#wzDocPane .dp-slidewrap').dataset.scale === '0.3750',
          doc.querySelector('#wzDocPane .dp-slidewrap').dataset.scale);

        /* 剪贴板：复制一处 → 粘到另一处（用页面上真实的盒子，别写死槽位名） */
        const bsNow = boxSlots();
        const srcSlot = bsNow[0], dstSlot = bsNow[bsNow.length - 1];
        boxOf(srcSlot).click(); await sleep(250);
        ctrl('c'); await sleep(250);
        check('Ctrl+C 复制一处（提示带"复制了"）', /复制了/.test(toast()), toast());
        boxOf(dstSlot).click(); await sleep(250);
        const nSlot = slots.length;
        ctrl('v');
        for (let i = 0; i < 12 && slots.length <= nSlot; i++) await sleep(250);
        check('Ctrl+V 粘到选中的那一格（文字 + 样式）',
          slots.length > nSlot && slots[slots.length - 1].slot === dstSlot,
          dstSlot + ' ← ' + srcSlot + ' · ' + JSON.stringify(slots[slots.length - 1] || {}).slice(0, 60));
        const nThumb0 = doc.querySelectorAll('#wzLeft .wz-thumb').length;
        ctrl('d'); await sleep(700);
        check('Ctrl+D 复制这一页', doc.querySelectorAll('#wzLeft .wz-thumb').length === nThumb0 + 1,
          nThumb0 + ' → ' + doc.querySelectorAll('#wzLeft .wz-thumb').length);
        doc.getElementById('dpUndo').click(); await sleep(700);   /* 撤回，别影响后面的用例 */

        /* 查找 / 替换（跨页） */
        ctrl('f'); await sleep(500);
        check('Ctrl+F 拉开查找条', !!doc.getElementById('dpFindBar') &&
          doc.getElementById('dpFindBar').style.display !== 'none');
        doc.getElementById('dpFindQ').value = '函数';
        const nFind0 = finds.length;
        doc.getElementById('dpFindGo').click();
        for (let i = 0; i < 12 && finds.length <= nFind0; i++) await sleep(250);
        await sleep(600);                       /* 等响应回来并重画命中列表（请求记录 ≠ 画好了） */
        const fReq = finds[finds.length - 1] || {};
        check('查找请求发给服务端（do=find，带整册数据）',
          fReq.do === 'find' && fReq.query === '函数' && Array.isArray(fReq.slides),
          JSON.stringify({ do: fReq.do, query: fReq.query, pages: (fReq.slides || []).length }));
        check('命中列表列出来了（点一条能跳页）',
          doc.querySelectorAll('#dpFindHits [data-hit]').length > 0,
          doc.querySelectorAll('#dpFindHits [data-hit]').length + ' 条命中');
        const hitBtns = doc.querySelectorAll('#dpFindHits [data-hit]');
        if (hitBtns.length) {
          hitBtns[hitBtns.length - 1].click(); await sleep(600);
          check('点命中项 → 跳到那一页', /第 \d+ \/ \d+ 页/.test(txt(doc.querySelector('#dpPgBar span'))),
            txt(doc.querySelector('#dpPgBar span')));
        }
        ctrl('h'); await sleep(500);
        check('Ctrl+H 露出替换框', doc.getElementById('dpFindRW').style.display !== 'none');
        doc.getElementById('dpFindQ').value = '函数';
        doc.getElementById('dpFindR').value = '函式';
        const nFind1 = finds.length;
        doc.getElementById('dpFindAll').click();
        for (let i = 0; i < 12 && finds.length <= nFind1; i++) await sleep(250);
        check('「全部替换」一次算完一次落库（do=replace）',
          (finds[finds.length - 1] || {}).do === 'replace' &&
          finds[finds.length - 1].replace === '函式',
          JSON.stringify(((finds[finds.length - 1]) || {}).replace || null));
        await sleep(400);
        check('替换后如实报数（提示里有"替换了"或"没找到"）',
          /替换了|没找到/.test(toast()), toast());
        key('Escape'); await sleep(200);
        doc.getElementById('dpFindClose').click(); await sleep(300);
        check('能关掉查找条', doc.getElementById('dpFindBar').style.display === 'none');
        check('无运行时错误（R18 这一段）', errors.length === 0, errors.slice(0, 2).join(' | '));
      }

      /* R19：左栏缩略图 —— Ctrl+点多选 / 拖动排序 / 批量操作 / 右键菜单 */
      {
        const thumbs = () => Array.from(doc.querySelectorAll('#wzLeft .wz-thumb[data-thumb]'));
        const key = (k, opt) => doc.dispatchEvent(new w.KeyboardEvent('keydown',
          Object.assign({ key: k, bubbles: true, cancelable: true }, opt || {})));
        const ctrlZ = () => key('z', { ctrlKey: true });
        const names = () => thumbs().map((t) => txt(t.querySelector('.n')).replace(/^\d+\s*\/\s*\d+\s*/, '').trim());
        const mouse = (el, type, y, opt) => el.dispatchEvent(new w.MouseEvent(type,
          Object.assign({ bubbles: true, cancelable: true, clientX: 20, clientY: y, button: 0 }, opt || {})));
        const startThumbs = thumbs().length;
        check('左栏缩略图都在（R19 的拖动与多选都要靠它们）', startThumbs >= 5, startThumbs + ' 张');

        /* 多选：Ctrl+点 两页 → 工具条出现、状态栏说清、细框两个 */
        mouse(thumbs()[0], 'click', 0);
        await sleep(300);
        check('单击一页只选一页（不出现批量工具条）',
          !doc.querySelector('#wzLeft [data-rail-batch]'), '有工具条=多选了');
        mouse(thumbs()[2], 'click', 0, { ctrlKey: true });
        await sleep(350);
        mouse(thumbs()[3], 'click', 0, { ctrlKey: true });
        await sleep(400);
        check('Ctrl+点 多选（选中样式 + 批量工具条 + 状态栏报数）',
          doc.querySelectorAll('#wzLeft .wz-thumb.wz-sel').length === 3 &&
          !!doc.querySelector('#wzLeft [data-rail-batch="del"]') &&
          /已选 3 页/.test(txt(doc.getElementById('wzStatusBar'))),
          doc.querySelectorAll('#wzLeft .wz-thumb.wz-sel').length + ' 选中 · ' +
          txt(doc.getElementById('wzStatusBar')));

        /* Shift+点 选一段 */
        mouse(thumbs()[0], 'click', 0);
        await sleep(250);
        mouse(thumbs()[3], 'click', 0, { shiftKey: true });
        await sleep(350);
        check('Shift+点 选中一段（1–4 页）',
          doc.querySelectorAll('#wzLeft .wz-thumb.wz-sel').length === 4,
          doc.querySelectorAll('#wzLeft .wz-thumb.wz-sel').length + ' 页');

        /* 批量复制 → 页数 +4；再撤销回来 */
        const nBefore = thumbs().length;
        doc.querySelector('#wzLeft [data-rail-batch="dup"]').click();
        await sleep(900);
        check('「复制选中」批量复制（页数 +4 且提示报数）',
          thumbs().length === nBefore + 4 && /复制了 4 页/.test(toast()),
          nBefore + ' → ' + thumbs().length + ' · ' + toast());
        ctrlZ(); await sleep(900);
        check('批量复制能整批撤回（Ctrl+Z）', thumbs().length === nBefore,
          thumbs().length + ' 页');

        /* 批量删除 → 页数 -2；再撤销回来（内容不能丢） */
        mouse(thumbs()[1], 'click', 0);
        await sleep(250);
        mouse(thumbs()[2], 'click', 0, { ctrlKey: true });
        await sleep(350);
        doc.querySelector('#wzLeft [data-rail-batch="del"]').click();
        await sleep(900);
        check('「删除选中」批量删除（页数 -2 + 提示能撤回）',
          thumbs().length === nBefore - 2 && /删掉了 2 页/.test(toast()),
          thumbs().length + ' 页 · ' + toast());
        const namesAfterDel = names();
        ctrlZ(); await sleep(900);
        check('批量删除能整批撤回，页名原样回来',
          thumbs().length === nBefore && names().join('|').length >= namesAfterDel.join('|').length - 2,
          thumbs().length + ' 页 · ' + names().slice(0, 3).join('/'));

        /* 拖动排序：把第 1 页拖到第 5 张下面 → 顺序变成 2,3,4,5,1 */
        const order0 = names();
        const first = thumbs()[0];
        mouse(first, 'mousedown', 200 + 10);
        mouse(doc.body, 'mousemove', 200 + 4 * 100 + 60);       /* 落在第 5 张的下半 → 插到它后面 */
        await sleep(150);
        check('拖动中画出插入指示线（.wz-dropline）',
          doc.querySelectorAll('#wzLeft .wz-dropline').length === 1,
          doc.querySelectorAll('#wzLeft .wz-dropline').length + ' 条');
        mouse(doc.body, 'mouseup', 200 + 4 * 100 + 60);
        await sleep(900);
        const order1 = names();
        check('拖动后顺序真的变了（第 1 页挪到最后）',
          order1.length === order0.length && order1[order1.length - 1] === order0[0] &&
          order1[0] === order0[1],
          order0.slice(0, 3).join('/') + ' → ' + order1.slice(0, 3).join('/'));
        ctrlZ(); await sleep(900);
        check('排序也能撤回（回到原顺序）', names().join('|') === order0.join('|'),
          names().slice(0, 3).join('/'));

        /* 右键菜单 */
        thumbs()[1].dispatchEvent(new w.MouseEvent('contextmenu',
          { bubbles: true, cancelable: true, clientX: 30, clientY: 300 }));
        await sleep(400);
        const menu = doc.getElementById('wzRailMenu');
        check('右键出菜单（复制/上移/下移/删除/从这页放映）',
          !!menu && ['dup', 'up', 'dn', 'del', 'show'].every((k) => !!menu.querySelector('[data-ctx="' + k + '"]')),
          menu ? Array.from(menu.querySelectorAll('[data-ctx]')).map((b) => b.dataset.ctx).join(',') : '(没菜单)');
        const orderBeforeMenu = names();
        menu.querySelector('[data-ctx="up"]').click();
        await sleep(800);
        check('菜单里「上移」生效且菜单收起',
          !doc.getElementById('wzRailMenu') &&
          names()[0] === orderBeforeMenu[1] && names()[1] === orderBeforeMenu[0],
          names().slice(0, 2).join('/'));
        ctrlZ(); await sleep(800);

        /* Delete 删页（没有选中对象时，Delete 删的是"选中的页"） */
        const nDel = thumbs().length;
        mouse(thumbs()[0], 'click', 0); await sleep(300);
        key('Delete'); await sleep(800);
        check('没选对象时 Delete 删选中的页（并能撤回）',
          thumbs().length === nDel - 1 && /删掉了 1 页/.test(toast()),
          nDel + ' → ' + thumbs().length + ' · ' + toast());
        ctrlZ(); await sleep(800);
        check('删页撤回后页数回来', thumbs().length === nDel, thumbs().length + ' 页');

        /* 不能把页删光 */
        const nAll = thumbs().length;
        mouse(thumbs()[0], 'click', 0); await sleep(200);
        for (let i = 1; i < nAll; i++) { mouse(thumbs()[i], 'click', 0, { ctrlKey: true }); await sleep(60); }
        await sleep(300);
        doc.querySelector('#wzLeft [data-rail-batch="del"]').click();
        await sleep(700);
        check('不许把页删光（至少留一页，并说明）',
          thumbs().length === nAll && /至少留一页|不能把页删光/.test(toast()), toast());
        doc.querySelector('#wzLeft [data-rail-batch="clear"]').click();
        await sleep(300);
        check('「取消选择」清空多选', !doc.querySelectorAll('#wzLeft .wz-thumb.wz-sel').length);
        check('无运行时错误（R19 这一段）', errors.length === 0, errors.slice(0, 2).join(' | '));
      }

      /* R20：操作栈统一 —— 拖动 / 插入 / 删除 / 属性 / 批量，各撤一次（批量只按一次） */
      {
        const key = (k, opt) => doc.dispatchEvent(new w.KeyboardEvent('keydown',
          Object.assign({ key: k, bubbles: true, cancelable: true }, opt || {})));
        const ctrlZ = () => key('z', { ctrlKey: true });
        const ctrlY = () => key('y', { ctrlKey: true });
        const boxOf = (slot) => boxAt(slot);
        const lastPatch3 = () => (slots.filter((s) => s.patch).pop() || {}).patch || {};
        const undoBtn = () => doc.getElementById('dpUndo');

        /* 撤销按钮的说明跟着栈走（有历史就写清"这一步是什么"；空栈的置灰在最后一条验） */
        check('撤销按钮的 title 跟着栈走（撤销：这一步是什么）',
          !!(undoBtn() && (undoBtn().disabled || /^撤销：/.test(undoBtn().title || ''))),
          undoBtn() ? (undoBtn().disabled + ' · ' + undoBtn().title) : '(没按钮)');

        /* 1) 拖动 → 撤一次 */
        boxOf('block:0').click(); await sleep(250);
        doc.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
        await sleep(500);
        check('方向键微调后按钮可点，title 写清"这一步是什么"',
          !!(undoBtn() && !undoBtn().disabled) && /撤销：/.test(undoBtn().title || ''),
          undoBtn() ? undoBtn().title : '(没按钮)');
        let n0 = slots.length;
        ctrlZ(); await sleep(600);
        check('拖动/微调能撤一次（回传的是回退值）',
          slots.length > n0 && lastPatch3().dx === 0, JSON.stringify(lastPatch3()));

        /* 2) 属性（加粗）→ 撤一次：看**页面上那一格**不再加粗（回传的可能是"清空覆盖"的通用值） */
        n0 = slots.length;
        key('b', { ctrlKey: true }); await sleep(500);
        const boldOn = lastPatch3().bold === true;
        n0 = slots.length;
        ctrlZ(); await sleep(700);
        const boxNow = boxOf('block:0');
        check('属性（Ctrl+B）能撤一次（页面上不再加粗）',
          boldOn && slots.length > n0 && !!boxNow &&
          String(boxNow.style.fontWeight || '') !== '700',
          'bold=' + boldOn + ' · font-weight=' + (boxNow ? boxNow.style.fontWeight : '?'));

        /* 3) 删除 → 撤一次 */
        boxOf('block:0').click(); await sleep(250);
        n0 = slots.length;
        key('Delete'); await sleep(600);
        const hid = lastPatch3().hidden === true;
        n0 = slots.length;
        ctrlZ(); await sleep(600);
        check('删除能撤一次（hidden 回 false）', hid && slots.length > n0 && lastPatch3().hidden === false,
          JSON.stringify(lastPatch3()));

        /* 4) 插入（加一块）→ 撤一次。先换回"要点"版式：别的版式下要点不叫 block，数不出来 */
        const blocksNow = () => doc.querySelectorAll('#wzDocPane .dp-box[data-slot^="block:"]').length;
        const laySel = doc.getElementById('dpLayout');
        laySel.value = 'bullets';
        laySel.dispatchEvent(new w.Event('change', { bubbles: true }));
        await sleep(900);
        const nBlk = blocksNow();
        check('换版式到「要点」后能数出要点盒子', nBlk >= 1, nBlk + ' 块');
        doc.getElementById('dpAddBlock').click(); await sleep(900);
        const allSlots = Array.from(doc.querySelectorAll('#wzDocPane .dp-canvas .dp-box'))
          .map((b) => b.dataset.slot).join(',');
        check('「加一块」页面上多了一块', blocksNow() === nBlk + 1,
          nBlk + ' → ' + blocksNow() + ' · 提示=' + toast() + ' · 盒子=' + allSlots +
          ' · 版式=' + txt(doc.getElementById('dpLayout')));
        ctrlZ(); await sleep(900);
        check('插入（加一块）能撤一次', blocksNow() === nBlk, blocksNow() + ' 块');

        /* 5) 批量：Ctrl+A 全选后改样式，**一次** Ctrl+Z 全回来（R20 的核心） */
        boxOf('block:0').click(); await sleep(250);
        key('a', { ctrlKey: true }); await sleep(400);
        const nSel = docpSelCount();
        n0 = slots.length;
        key('b', { ctrlKey: true });
        for (let i = 0; i < 16 && slots.length < n0 + nSel; i++) await sleep(200);
        check('批量改样式一次写 ' + nSel + ' 处（一步操作）', slots.length >= n0 + nSel,
          nSel + ' 处 → ' + (slots.length - n0) + ' 次回传');
        const nMid = slots.length;
        ctrlZ();
        for (let i = 0; i < 16 && slots.length < nMid + nSel; i++) await sleep(200);
        check('批量操作**按一次** Ctrl+Z 全部回来（不是一次撤一处）',
          slots.length >= nMid + nSel && /撤销了：/.test(toast()),
          (slots.length - nMid) + ' 次回退 · ' + toast());
        const nAfterUndo = slots.length;
        ctrlY();
        for (let i = 0; i < 16 && slots.length < nAfterUndo + nSel; i++) await sleep(200);
        check('批量操作也能一次 Ctrl+Y 重做', slots.length >= nAfterUndo + nSel,
          (slots.length - nAfterUndo) + ' 次重做');
        ctrlZ(); await sleep(800);                      /* 收拾干净，别影响后面的用例 */
        key('Escape'); await sleep(200);

        /* 6) 粘贴整页 → 撤一次（R18 的剪贴板 + R20 的栈） */
        const nThumb = doc.querySelectorAll('#wzLeft .wz-thumb').length;
        key('c', { ctrlKey: true }); await sleep(300);
        key('v', { ctrlKey: true }); await sleep(900);
        check('Ctrl+C/Ctrl+V 整页粘贴', doc.querySelectorAll('#wzLeft .wz-thumb').length === nThumb + 1,
          nThumb + ' → ' + doc.querySelectorAll('#wzLeft .wz-thumb').length);
        ctrlZ(); await sleep(900);
        check('粘贴整页能撤一次', doc.querySelectorAll('#wzLeft .wz-thumb').length === nThumb,
          doc.querySelectorAll('#wzLeft .wz-thumb').length + ' 页');

        /* 7) 一路撤到底：栈空了按钮又变灰 */
        for (let i = 0; i < 30 && !undoBtn().disabled; i++) { ctrlZ(); await sleep(220); }
        check('撤回到底后按钮变灰（栈空）', undoBtn().disabled, undoBtn().title || '');
        check('无运行时错误（R20 这一段）', errors.length === 0, errors.slice(0, 2).join(' | '));
      }

      /* R21：自由层 —— 插入文本框/形状 → 选中/拖动/改字/层级/删除 → 撤销 */
      {
        const key = (k, opt) => doc.dispatchEvent(new w.KeyboardEvent('keydown',
          Object.assign({ key: k, bubbles: true, cancelable: true }, opt || {})));
        const ctrlZ = () => key('z', { ctrlKey: true });
        const mouse = (el, type, x, y, opt) => el.dispatchEvent(new w.MouseEvent(type,
          Object.assign({ bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0 }, opt || {})));
        const objBoxes = () => Array.from(doc.querySelectorAll(
          '#wzDocPane .dp-canvas .dp-box[data-slot^="obj:"]'));
        const lastObj = () => objects[objects.length - 1] || {};
        const openInsert = async () => {
          doc.querySelector('#wzDocPane .dp-tt[data-tool="insert"]').click();
          await sleep(400);
        };
        await openInsert();
        check('「插入」里有文本框 + 8 个预设形状',
          !!doc.getElementById('dpInsText') &&
          doc.querySelectorAll('#wzDocPane [data-shape]').length === 8,
          doc.querySelectorAll('#wzDocPane [data-shape]').length + ' 个形状');

        /* 插入文本框 */
        let n0 = objects.length;
        doc.getElementById('dpInsText').click();
        for (let i = 0; i < 12 && objects.length <= n0; i++) await sleep(250);
        await sleep(600);
        check('点「文本框」发 add（kind=text）并选中新对象',
          lastObj().action === 'add' && lastObj().kind === 'text' &&
          docpSelCount() === 1 && /obj:/.test(txt(doc.getElementById('dpSelName'))),
          JSON.stringify(lastObj()).slice(0, 70) + ' · 选中=' + txt(doc.getElementById('dpSelName')));
        check('新对象在页面上有盒子（obj-text）',
          objBoxes().some((b) => b.classList.contains('dp-obj-text') || !!b.dataset.slot),
          objBoxes().map((b) => b.dataset.slot).join(','));

        /* 插入矩形（形状按钮） */
        n0 = objects.length;
        doc.querySelector('#wzDocPane [data-shape="ellipse"]').click();
        for (let i = 0; i < 12 && objects.length <= n0; i++) await sleep(250);
        await sleep(600);
        check('点形状按钮发 add（kind=shape + 预设名）',
          lastObj().action === 'add' && lastObj().kind === 'shape' && lastObj().shape === 'ellipse',
          JSON.stringify(lastObj()).slice(0, 70));
        check('两个对象都在页面上', objBoxes().length === 2,
          objBoxes().map((b) => b.dataset.slot).join(','));

        /* 右栏：对象属性（位置/大小/字号/颜色/文字/层级/删除）*/
        check('右栏切到"对象属性"（位置/大小/字号/填充/对齐/层级/删除都在）',
          /属性 · /.test(txt(doc.getElementById('dpProps'))) &&
          !!doc.getElementById('dpOx') && !!doc.getElementById('dpOw') &&
          !!doc.getElementById('dpOsize') && !!doc.getElementById('dpOfill') &&
          !!doc.getElementById('dpOup') && !!doc.getElementById('dpOdel') &&
          !!doc.getElementById('dpOtext'),
          txt(doc.getElementById('dpProps')).slice(0, 40));

        /* 改位置（右栏输入）→ patch 绝对几何 */
        n0 = objects.length;
        const ox = doc.getElementById('dpOx');
        ox.value = '123';
        ox.dispatchEvent(new w.Event('change', { bubbles: true }));
        for (let i = 0; i < 12 && objects.length <= n0; i++) await sleep(250);
        check('改位置发 patch（绝对 x，不是增量）',
          (lastObj().action === 'patch') && (lastObj().patch || {}).x === 123,
          JSON.stringify(lastObj()).slice(0, 70));

        /* 改文字（textarea，600ms 防抖）*/
        n0 = objects.length;
        const ta = doc.getElementById('dpOtext');
        ta.value = '我写的字';
        ta.dispatchEvent(new w.Event('input', { bubbles: true }));
        for (let i = 0; i < 16 && objects.length <= n0; i++) await sleep(250);
        check('改对象文字发 patch（防抖后一次）',
          (lastObj().patch || {}).text === '我写的字', JSON.stringify(lastObj()).slice(0, 70));

        /* 拖动对象：mousedown → mousemove → mouseup（几何走绝对值）*/
        const target = objBoxes()[objBoxes().length - 1];
        n0 = objects.length;
        const rect = target.getBoundingClientRect();
        mouse(target, 'mousedown', 0, 0);
        mouse(doc.body, 'mousemove', 300, 400);
        mouse(doc.body, 'mouseup', 300, 400);
        for (let i = 0; i < 12 && objects.length <= n0; i++) await sleep(250);
        check('拖动对象发 patch（x/y 是算出来的绝对值）',
          lastObj().action === 'patch' && typeof (lastObj().patch || {}).x === 'number' &&
          typeof (lastObj().patch || {}).y === 'number',
          JSON.stringify(lastObj()).slice(0, 70));

        /* 层级：上移一层 → action=up；再点一次要如实说"已经最上面了" */
        n0 = objects.length;
        doc.getElementById('dpOup').click();
        for (let i = 0; i < 12 && objects.length <= n0; i++) await sleep(250);
        check('「上移一层」发 action=up', lastObj().action === 'up', JSON.stringify(lastObj()).slice(0, 50));
        n0 = objects.length;
        doc.getElementById('dpOup').click();
        for (let i = 0; i < 12 && objects.length <= n0; i++) await sleep(250);
        await sleep(300);
        check('已经在最上面时如实报错（不假装成功）',
          /最上面/.test(toast()), toast());

        /* 删除对象 */
        n0 = objects.length;
        doc.getElementById('dpOdel').click();
        for (let i = 0; i < 12 && objects.length <= n0; i++) await sleep(250);
        await sleep(600);
        check('「删除这个对象」发 action=delete 且页面上少一个',
          lastObj().action === 'delete' && objBoxes().length === 1,
          objBoxes().length + ' 个对象');

        /* 撤销：删掉的那个要回来；再一路撤回，中间每一步（拖/改字/层级/插入）都该能撤 */
        ctrlZ(); await sleep(1000);
        check('删掉的对象能一次 Ctrl+Z 撤回', objBoxes().length === 2,
          objBoxes().map((b) => b.dataset.slot).join(','));
        let guard = 0;
        while (objBoxes().length > 0 && guard < 14) { ctrlZ(); await sleep(650); guard++; }
        check('一路撤回能把对象全撤掉（插入/拖动/改字/层级每步都可撤）',
          objBoxes().length === 0, '撤了 ' + guard + ' 次 · 还剩 ' +
          (objBoxes().map((b) => b.dataset.slot).join(',') || '0 个'));
        check('无运行时错误（R21 这一段）', errors.length === 0, errors.slice(0, 2).join(' | '));
        doc.querySelector('#wzDocPane .dp-tt[data-tool="home"]').click(); await sleep(300);
      }

      /* 回到第 1 页：下面的老用例（本页属性条那几条）默认在第 1 页上跑 */
      doc.querySelector('#wzLeft .wz-thumb[data-thumb="0"]').click(); await sleep(600);
      check('（收尾）回到第 1 页', /第 1 \//.test(txt(doc.querySelector('#wzDocPane #dpPgBar span'))),
        txt(doc.querySelector('#wzDocPane #dpPgBar span')));
    }

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
      saves.length + ' 次 · ' + (saved1.slides ? String(saved1.slides[0].layout) : '(无 slides)') +
      ' · 第0页键 ' + (saved1.slides && saved1.slides[0] ? Object.keys(saved1.slides[0]).join(',') : '-'));
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
    check('点缩略图换页', /第 3 \/ /.test(txt(doc.querySelector('#wzDocPane #dpPgBar span'))),
      txt(doc.querySelector('#wzDocPane #dpPgBar span')));
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
      /教案_3\.3 幂函数_学校模板\.pptx/.test(txt(doc.getElementById('dzExportResult'))) &&
      txt(doc.getElementById('dzExportResult')).slice(0, 60));
    check('填充提示（如"删掉多余页"）显示在结果条',
      /删掉模板多余的内容页/.test(txt(doc.getElementById('dzExportResult'))));

    /* R11/R14：**模板填充页也能点着改**（老师要求"ppt 有关的编辑都要能编辑"）——
       盒子来自填充计划的 fills[].geo；改完先记着、点「应用修改」才重填（重填要 3~6 秒）。
       R14 起**装饰槽（页码/设计标签）也有盒子**：老师："任何文字都要能改"。 */
    const fboxes = doc.querySelectorAll('#wzDocPane .dp-boxlayer .dp-box');
    check('填充页上每一处文字都有可编辑盒子（含装饰槽）', fboxes.length === 3, fboxes.length + ' 个');
    check('填充页的盒子带 shape_index 槽位名（回写要用它精确指槽）',
      Array.from(fboxes).every((b) => /^fill:\d+$/.test(b.dataset.slot)),
      Array.from(fboxes).map((b) => b.dataset.slot).join(','));
    const decoBox = doc.querySelector('#wzDocPane .dp-box.dp-deco');
    check('装饰槽（页码/设计标签）也画出盒子并标出来', !!decoBox && decoBox.dataset.slot === 'fill:9',
      decoBox ? decoBox.dataset.slot : '(无装饰盒)');
    decoBox.dispatchEvent(new w.MouseEvent('dblclick', { bubbles: true, cancelable: true }));
    await sleep(250);
    check('点开装饰槽能看到模板原来的字（老师才知道自己在改什么）',
      decoBox.textContent === '圆', JSON.stringify(decoBox.textContent));
    decoBox.textContent = '改过的设计标签';
    decoBox.dispatchEvent(new w.Event('blur', { bubbles: true }));
    await sleep(300);

    const fbox = fboxes[0];
    const fslot = fbox.dataset.slot;
    fbox.dispatchEvent(new w.MouseEvent('dblclick', { bubbles: true, cancelable: true }));
    await sleep(250);
    fbox.textContent = '填充页改过的字';
    fbox.dispatchEvent(new w.Event('blur', { bubbles: true }));
    await sleep(400);
    const applyBtn = doc.getElementById('dpFillApply');
    check('改完先"记着"（按钮上出现待应用数量，不立刻重填）',
      !!applyBtn && applyBtn.disabled === false && (/待应用/.test(txt(doc.getElementById('dpFillPending')) || '')
        || /应用修改（\d+）/.test(txt(applyBtn))),
      applyBtn ? txt(applyBtn) + ' / ' + txt(doc.getElementById('dpFillPending')) : '(无按钮)');

    /* R14：填充页也能拖 —— 位置先记着，一起交给「应用修改」 */
    const fScale = parseFloat(doc.querySelector('#wzDocPane .dp-slidewrap').dataset.scale || '0.375');
    const fx0 = parseFloat(fbox.style.left);
    fbox.dispatchEvent(new w.MouseEvent('mousedown', { bubbles: true, cancelable: true,
      clientX: 400, clientY: 400, button: 0 }));
    doc.dispatchEvent(new w.MouseEvent('mousemove', { bubbles: true, cancelable: true,
      clientX: 400 + 20 * fScale, clientY: 400 }));
    doc.dispatchEvent(new w.MouseEvent('mouseup', { bubbles: true }));
    await sleep(300);
    check('填充页拖动也先"记着"（不立刻重填）',
      /应用修改（\d+）/.test(txt(doc.getElementById('dpFillApply'))) &&
      Math.abs(parseFloat(fbox.style.left) - (fx0 + 20)) <= 1.5,
      fx0 + ' → ' + fbox.style.left + ' / ' + txt(doc.getElementById('dpFillApply')));

    /* 装饰槽也能改：改页码那种"模板残留"从根上没了 */
    const decoText = (doc.querySelector('#wzDocPane .dp-box[data-slot="fill:9"]') || {}).textContent || '';
    const nFills = fills.length;
    applyBtn.click();
    for (let i = 0; i < 16 && fills.length <= nFills; i++) await sleep(300);
    const lastFill = fills[fills.length - 1] || {};
    const rows = lastFill.mapping || [];
    const hit = rows.reduce((acc, r) => acc.concat((r.fills || []).map((f) => ({ ...f, page: r.page }))), []);
    const titleRow = hit.find((h) => String(h.shape_index) === fslot.replace('fill:', '')) || {};
    const decoRow = hit.find((h) => String(h.shape_index) === '9') || {};
    check('点「应用修改」→ 用 mapping 回传改动的那几格（文字 + 位置）',
      fills.length > nFills && titleRow.text === '填充页改过的字' && Math.abs(titleRow.dx - 20) <= 1.5,
      JSON.stringify(hit));
    check('装饰槽的文字改动也回传（页码/设计标签终于能改了）',
      decoRow.text === '改过的设计标签', JSON.stringify(decoRow));
    check('mapping 用 page（成品页号）指页，不用 template_index（续页会同号改错页）',
      hit.length > 0 && hit.every((h) => h.page === 0) &&
      !(lastFill.mapping || []).some((r) => 'template_index' in r),
      JSON.stringify((lastFill.mapping || []).map((r) => Object.keys(r))));
    check('重填后待应用清零', !/应用修改（\d+）/.test(txt(doc.getElementById('dpFillApply'))),
      txt(doc.getElementById('dpFillApply')) + ' · decoText=' + JSON.stringify(decoText));

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
    const items = Array.from(doc.querySelectorAll('#wzDownMenu [data-dl]')).map((b) => b.dataset.dl);
    check('下载菜单 = Word + 新旧两条 PPT + HTML',
      items.join(',') === 'docx,pptx-deck,pptx,html,outdir', items.join(','));
    check('新版式那项写明了与预览同源', /与预览同一份/.test(txt(doc.querySelector('[data-dl="pptx-deck"]'))),
      txt(doc.querySelector('[data-dl="pptx-deck"]')));
    doc.getElementById('wzDownBtn').click();
    doc.querySelector('[data-dl="pptx-deck"]').click();
    for (let i = 0; i < 12 && !exports.length; i++) await sleep(300);
    for (let i = 0; i < 12 && /处理中/.test(txt(doc.getElementById('dzExportResult'))); i++) await sleep(300);
    const body = exports[0] || {};
    check('点它 POST 的是 pptx-deck', body.kind === 'pptx-deck', String(body.kind));
    check('带上面板那份 slides（预览=导出的关键）',
      Array.isArray(body.slides) && body.slides.length > 0, (body.slides || []).length + ' 页');
    check('带上当前主题 id', !!body.theme_id, String(body.theme_id || ''));
    check('结果条给文件名与下载链接',
      /教案_3\.3 幂函数\.pptx/.test(txt(doc.getElementById('dzExportResult'))) &&
      !!doc.querySelector('#dzExportResult a[href*="/api/wps/download"]'),
      txt(doc.getElementById('dzExportResult')).slice(0, 60));
    check('无运行时错误', errors.length === 0, errors.slice(0, 2).join(' | '));
    w.close();
  }

  console.log('== 导出：新版式失败时如实说明已回退 ==');
  {
    const { w, doc, errors } = makeDom({ wpsFallback: true , withDoc: true });
    await sleep(2500);
    await openDocPanel(doc);
    doc.querySelector('#wzTabs .wz-tab[data-wz="slides"]').click(); await sleep(1000);
    doc.getElementById('wzDownBtn').click();
    doc.querySelector('[data-dl="pptx-deck"]').click();
    await sleep(900);
    check('结果条注明"已回退到旧版式"', /已回退到旧版式/.test(txt(doc.getElementById('dzExportResult'))),
      txt(doc.getElementById('dzExportResult')).slice(0, 80));
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
    check('默认只画左栏选中**那一个**环节（不再左右各列一遍）',
      doc.querySelectorAll('#wzPlanPane #dzSteps .dz-step').length === 1,
      doc.querySelectorAll('#wzPlanPane #dzSteps .dz-step').length + ' 张卡');
    check('左栏环节列表把选中那个标出来了',
      !!doc.querySelector('#wzLeft .wz-item.wz-cur'), txt(doc.querySelector('#wzLeft .wz-item.wz-cur')));
    doc.getElementById('wzPlanAll').click(); await sleep(400);
    const nAll = doc.querySelectorAll('#wzPlanPane #dzSteps .dz-step').length;
    check('点「全部展开」把所有环节列出来', nAll > 1, nAll + ' 张卡');
    doc.getElementById('wzPlanAll').click(); await sleep(400);
    check('再点回到"只看选中"',
      doc.querySelectorAll('#wzPlanPane #dzSteps .dz-step').length === 1);

    /* ② 环节编辑 = 改会话里那份 slides（不再有第二套本地数据） */
    const nameInp = doc.querySelector('#wzPlanPane #dzSteps input.name');
    const hasSteps = !!nameInp;
    if (hasSteps) {
      nameInp.value = '导入（改过的）';
      nameInp.dispatchEvent(new w.Event('input', { bubbles: true }));
      for (let i = 0; i < 14 && !saves.length; i++) await sleep(250);
      const s0 = (saves[saves.length - 1] || {}).slides || [];
      check('状态栏说清了篇幅（全页只在这里说一次）',
      /共 \d+ 个环节/.test(txt(doc.getElementById('wzStatusBar'))),
      txt(doc.getElementById('wzStatusBar')));
    check('「生成骨架/＋添加环节」只在教案页签出现',
      doc.getElementById('wzPlanOps').hidden === false);
    check('保存状态显示在顶栏（已自动保存）',
      /已自动保存/.test(txt(doc.getElementById('wzSaveState'))),
      txt(doc.getElementById('wzSaveState')));
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
