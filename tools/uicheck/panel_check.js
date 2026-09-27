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

function makeDom(opts) {
  const o = opts || {};
  const errors = [];
  const renderCalls = [];
  const exports = [];
  const saves = [];
  const fills = [];
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
          let n = 3;
          try { n = (JSON.parse(op.body).slides || []).length || 3; } catch (e) {}
          renderCalls.push(n);
          return new Response(JSON.stringify({ ok: true, enabled: true, theme_id: THEMES.default,
            pages: Array.from({ length: n }, (_, i) => svgPage(i)),
            warnings: o.slideWarn ? ['第 2 页：模块 6 个 > 上限 5 → 建议拆页（本页只排了前 5 个，其余请另起一页）'] : [],
            layouts: [], stats: { pages: n } }),
            { status: 200, headers: { 'Content-Type': 'application/json' } });
        }
        if (p === '/api/slide/templates') return new Response(JSON.stringify({
          ok: true, enabled: true,
          builtin: THEMES.themes.map((t) => ({ id: t.id, name: t.name, kind: 'json' })),
          imported: o.noTpl ? [] : [{ id: '学校模板', name: '学校模板', kind: 'pptx', pages: 11 }],
        }), { status: 200, headers: { 'Content-Type': 'application/json' } });
        if (p === '/api/slide/fill') {
          let b = {};
          try { b = JSON.parse(op.body); } catch (e) {}
          fills.push(b);
          return new Response(JSON.stringify({
            ok: true, filename: '教案_3.3 幂函数_学校模板.pptx', outDir: 'D:\\out',
            download_url: '/api/wps/download?path=x', detail: '✅ 内容零丢失；✅ 容量自查',
            warnings: ['会话比模板少 8 页内容 → 默认删掉模板多余的内容页'],
          }), { status: 200, headers: { 'Content-Type': 'application/json' } });
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
  return { w: dom.window, doc: dom.window.document, errors, renderCalls, exports, saves, fills };
}

async function openDocPanel(doc) {
  doc.querySelector('#spQuad .qbtn[data-v="doc"]').click();
  await sleep(900);
}

(async () => {
  console.log('== 面板结构 / 入口 ==');
  {
    const { w, doc, errors } = makeDom();
    await sleep(2500);
    const shell = Array.from(doc.querySelector('.shell').children).map((c) => c.id || c.tagName);
    check('右侧只有一块面板（.shell 子元素）', shell.join(',') === 'ASIDE,MAIN,sidePanel', shell.join(' | '));
    check('没有旧的独立面板 #docPanel', !doc.getElementById('docPanel'));
    check('「文档」入口在侧边面板里', !!doc.querySelector('#spQuad .qbtn[data-v="doc"]'));
    await openDocPanel(doc);
    check('点它进文档视图', doc.getElementById('sidePanel').classList.contains('sp-doc'));
    const ed = doc.querySelector('#spBody .dp-word[contenteditable="true"]');
    check('文档页签可直接编辑', !!ed);
    check('编辑工具栏显示', doc.getElementById('docPanelFmt').style.display === 'flex');
    doc.querySelector('#spBody .dp-tab[data-dp="slides"]').click(); await sleep(900);
    check('幻灯片页签有 16:9 预览台（.dp-canvas）', !!doc.querySelector('#spBody .dp-canvas'));
    check('旧的深蓝预览台已退役', !doc.querySelector('#spBody .dp-stage'));
    check('预览台里是服务端给的 SVG（不是前端拼的 h4+ul）',
      !!doc.querySelector('#spBody .dp-canvas svg'), (doc.querySelector('#spBody .dp-canvas') || {}).innerHTML?.slice(0, 40));
    const wrap = doc.querySelector('#spBody .dp-slidewrap');
    check('等比缩放按 1280 宽度算（jsdom 量不到宽度 → 兜底 480px ⇒ 0.375）',
      !!wrap && wrap.dataset.scale === '0.3750', wrap ? wrap.dataset.scale : '(no wrap)');
    check('预览台高度跟着缩放走（720×0.375=270px）',
      !!wrap && wrap.style.height === '270px', wrap ? wrap.style.height : '(no wrap)');
    const cv = doc.querySelector('#spBody .dp-canvas');
    check('内部固定 1280×720、只用均匀缩放',
      !!cv && /scale\(0\.375\)/.test(cv.style.transform || ''), cv ? cv.style.transform : '(no canvas)');
    const sel = doc.getElementById('dpTheme');
    check('主题下拉有 3 套（来自 /api/slide/themes）', !!sel && sel.options.length === 3,
      sel ? Array.from(sel.options).map((o) => o.textContent).join('/') : '(no select)');
    sel.value = 'hand-drawn';
    sel.dispatchEvent(new w.Event('change', { bubbles: true }));
    await sleep(700);
    check('换主题会重新要一份 SVG', doc.querySelector('#spBody .dp-canvas svg') !== null &&
      /手绘墨/.test(txt(doc.querySelector('.dp-slstat'))), txt(doc.querySelector('.dp-slstat')));
    check('提示小字仍在', /右侧为内存预览效果/.test(txt(doc.querySelector('.dp-note'))));
    check('无运行时错误', errors.length === 0, errors.slice(0, 2).join(' | '));
    w.close();
  }

  console.log('== 幻灯片：就地编辑（改字 / 增删块 / 换版式 / 排序 / 删页） ==');
  {
    const { w, doc, errors, saves, renderCalls } = makeDom();
    await sleep(2500);
    await openDocPanel(doc);
    doc.querySelector('#spBody .dp-tab[data-dp="slides"]').click(); await sleep(1000);

    check('每页卡片都有编辑工具栏',
      doc.querySelectorAll('#spBody .dp-cardbar').length === doc.querySelectorAll('#spBody .dp-slide').length,
      doc.querySelectorAll('#spBody .dp-cardbar').length + ' 条');

    /* ① 改标题（第 2 页） */
    doc.querySelector('#spBody [data-edit="1"]').click(); await sleep(500);
    const box = doc.querySelector('#spBody .dp-edit');
    check('点「改」展开就地编辑器', !!box && box.dataset.editFor === '1');
    const titleInp = box.querySelector('input[data-f="name"]');
    check('编辑器里带出现有标题', !!titleInp && titleInp.value.length > 0, titleInp ? titleInp.value : '');
    titleInp.value = '情境导入（改过的）';
    titleInp.dispatchEvent(new w.Event('input', { bubbles: true }));
    for (let i = 0; i < 14 && !saves.length; i++) await sleep(250);
    const saved1 = saves[saves.length - 1] || {};
    check('改字 700ms 后自动存回会话', saves.length > 0 && saved1.slides[1].name === '情境导入（改过的）',
      saves.length + ' 次 · ' + (saved1.slides ? saved1.slides[1].name : ''));
    check('卡片标题就地更新', /情境导入（改过的）/.test(txt(doc.querySelector('#spBody .dp-slide[data-pg="1"] h4'))),
      txt(doc.querySelector('#spBody .dp-slide[data-pg="1"] h4')));
    check('改完重新取了一份 SVG（预览跟着变）', renderCalls.length >= 2, renderCalls.join('/'));

    /* ② 字数提示：超过 22 字要标出来 */
    const ta = doc.querySelector('#spBody .dp-edit textarea[data-f="text"]');
    ta.value = '这是一条特别长的要点'.repeat(4);
    ta.dispatchEvent(new w.Event('input', { bubbles: true }));
    await sleep(200);
    const cnt = doc.querySelector('#spBody .dp-blk .dp-cnt');
    check('字数计数实时更新', new RegExp(String(ta.value.length) + ' 字').test(txt(cnt)), txt(cnt));
    check('超 22 字标红提示', cnt.classList.contains('dp-over'));

    /* ③ 加一块 / 删一块 */
    const before = doc.querySelectorAll('#spBody .dp-edit .dp-blk').length;
    doc.querySelector('#spBody [data-b-add]').click(); await sleep(400);
    const afterAdd = doc.querySelectorAll('#spBody .dp-edit .dp-blk').length;
    check('「+ 加一块」生效', afterAdd === before + 1, before + ' → ' + afterAdd);
    doc.querySelector('#spBody .dp-edit [data-b-del]').click(); await sleep(400);
    const afterDel = doc.querySelectorAll('#spBody .dp-edit .dp-blk').length;
    check('「删这块」生效', afterDel === afterAdd - 1, afterAdd + ' → ' + afterDel);

    /* ④ 换版式 */
    const lay = doc.querySelector('#spBody [data-layout="1"]');
    lay.value = 'two-col';
    lay.dispatchEvent(new w.Event('change', { bubbles: true }));
    await sleep(700);
    const saved2 = saves[saves.length - 1] || {};
    check('换版式写进数据并回存', saved2.slides && saved2.slides[1].layout === 'two-col',
      saved2.slides ? String(saved2.slides[1].layout) : '');

    /* ⑤ 排序 / 加页 / 删页 */
    const first = txt(doc.querySelector('#spBody .dp-slide[data-pg="0"] h4'));
    doc.querySelector('#spBody [data-page-dn="0"]').click(); await sleep(600);
    check('「↓」把第 1 页换到第 2 位',
      txt(doc.querySelector('#spBody .dp-slide[data-pg="1"] h4')) === first,
      txt(doc.querySelector('#spBody .dp-slide[data-pg="1"] h4')));
    const n0 = doc.querySelectorAll('#spBody .dp-slide').length;
    doc.getElementById('dpAddPage').click(); await sleep(600);
    check('「+ 加一页」生效', doc.querySelectorAll('#spBody .dp-slide').length === n0 + 1,
      n0 + ' → ' + doc.querySelectorAll('#spBody .dp-slide').length);
    doc.querySelector('#spBody [data-page-del="0"]').click(); await sleep(600);
    check('「删」掉一页', doc.querySelectorAll('#spBody .dp-slide').length === n0,
      String(doc.querySelectorAll('#spBody .dp-slide').length));
    check('无运行时错误', errors.length === 0, errors.slice(0, 2).join(' | '));
    w.close();
  }

  console.log('== 幻灯片：拆页/字数提示显示在页签里 ==');
  {
    const { w, doc, errors } = makeDom({ slideWarn: true });
    await sleep(2500);
    await openDocPanel(doc);
    doc.querySelector('#spBody .dp-tab[data-dp="slides"]').click(); await sleep(1200);
    check('服务端的"建议拆页"提示显示出来了',
      /建议拆页/.test(txt(doc.querySelector('#spBody .dp-warn'))), txt(doc.querySelector('#spBody .dp-warn')));
    check('提示是提示、内容没被截断', /其余请另起一页|只排了前 5 个/.test(txt(doc.querySelector('#spBody .dp-warn'))));
    check('无运行时错误', errors.length === 0, errors.slice(0, 2).join(' | '));
    w.close();
  }

  console.log('== 模板：导入 / 选择 / 用模板填一遍（M4b） ==');
  {
    const { w, doc, errors, fills } = makeDom();
    await sleep(2500);
    await openDocPanel(doc);
    doc.querySelector('#spBody .dp-tab[data-dp="slides"]').click(); await sleep(1200);
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
      /教案_3\.3 幂函数_学校模板\.pptx/.test(txt(doc.getElementById('docPanelRes'))) &&
      !!doc.querySelector('#docPanelRes a[href*="/api/wps/download"]'),
      txt(doc.getElementById('docPanelRes')).slice(0, 60));
    check('填充提示（如"删掉多余页"）显示在结果条',
      /删掉模板多余的内容页/.test(txt(doc.getElementById('docPanelRes'))));
    check('无运行时错误', errors.length === 0, errors.slice(0, 2).join(' | '));
    w.close();
  }

  console.log('== 导出：新版式 PPT 接线（桩 /api/wps/export） ==');
  {
    const { w, doc, errors, exports } = makeDom();
    await sleep(2500);
    await openDocPanel(doc);
    doc.querySelector('#spBody .dp-tab[data-dp="slides"]').click(); await sleep(1000);
    const items = Array.from(doc.querySelectorAll('#docPanelMenu [data-dl]')).map((b) => b.dataset.dl);
    check('下载菜单 = Word + 新旧两条 PPT + HTML',
      items.join(',') === 'docx,pptx-deck,pptx,html', items.join(','));
    check('新版式那项写明了与预览同源', /与预览同一份/.test(txt(doc.querySelector('[data-dl="pptx-deck"]'))),
      txt(doc.querySelector('[data-dl="pptx-deck"]')));
    doc.getElementById('docPanelDownBtn').click();
    doc.querySelector('[data-dl="pptx-deck"]').click();
    for (let i = 0; i < 12 && !exports.length; i++) await sleep(300);
    for (let i = 0; i < 12 && /处理中/.test(txt(doc.getElementById('docPanelRes'))); i++) await sleep(300);
    const body = exports[0] || {};
    check('点它 POST 的是 pptx-deck', body.kind === 'pptx-deck', String(body.kind));
    check('带上面板那份 slides（预览=导出的关键）',
      Array.isArray(body.slides) && body.slides.length > 0, (body.slides || []).length + ' 页');
    check('带上当前主题 id', !!body.theme_id, String(body.theme_id || ''));
    check('结果条给文件名与下载链接',
      /教案_3\.3 幂函数\.pptx/.test(txt(doc.getElementById('docPanelRes'))) &&
      !!doc.querySelector('#docPanelRes a[href*="/api/wps/download"]'),
      txt(doc.getElementById('docPanelRes')).slice(0, 60));
    check('无运行时错误', errors.length === 0, errors.slice(0, 2).join(' | '));
    w.close();
  }

  console.log('== 导出：新版式失败时如实说明已回退 ==');
  {
    const { w, doc, errors } = makeDom({ wpsFallback: true });
    await sleep(2500);
    await openDocPanel(doc);
    doc.querySelector('#spBody .dp-tab[data-dp="slides"]').click(); await sleep(1000);
    doc.getElementById('docPanelDownBtn').click();
    doc.querySelector('[data-dl="pptx-deck"]').click();
    await sleep(900);
    check('结果条注明"已回退到旧版式"', /已回退到旧版式/.test(txt(doc.getElementById('docPanelRes'))),
      txt(doc.getElementById('docPanelRes')).slice(0, 80));
    check('无运行时错误', errors.length === 0, errors.slice(0, 2).join(' | '));
    w.close();
  }

  console.log('== 幻灯片：窄屏（模拟 320px 视口） ==');  {
    const { w, doc, errors } = makeDom({ innerWidth: 320 });
    await sleep(2500);
    await openDocPanel(doc);
    doc.querySelector('#spBody .dp-tab[data-dp="slides"]').click(); await sleep(1000);
    const pw = doc.documentElement.style.getPropertyValue('--docp-w');
    check('面板宽度被夹到下限 360px', pw === '360px', pw || '(empty)');
    const wrap = doc.querySelector('#spBody .dp-slidewrap');
    check('窄屏下缩放系数仍为正数', !!wrap && parseFloat(wrap.dataset.scale) > 0,
      wrap ? wrap.dataset.scale : '(no wrap)');
    check('窄屏下预览台仍在（没被隐藏）', !!doc.querySelector('#spBody .dp-canvas'));
    check('无运行时错误', errors.length === 0, errors.slice(0, 2).join(' | '));
    w.close();
  }

  console.log('== 幻灯片：服务端渲染不可用时退化成纯文本（不白屏） ==');
  {
    const { w, doc, errors } = makeDom({ slideFail: true });
    await sleep(2500);
    await openDocPanel(doc);
    doc.querySelector('#spBody .dp-tab[data-dp="slides"]').click(); await sleep(1200);
    check('渲染失败仍显示内容（纯文本兜底）', !!doc.querySelector('#spBody .dp-plain'));
    check('状态里说明用的是纯文本预览', /纯文本预览/.test(txt(doc.querySelector('.dp-slstat'))),
      txt(doc.querySelector('.dp-slstat')));
    check('无运行时错误', errors.length === 0, errors.slice(0, 2).join(' | '));
    w.close();
  }

  console.log('== R6-4/R6-5：PPT 优化不再作为独立模块 / 页面减肥 ==');
  {
    const { w, doc, errors } = makeDom();
    await sleep(2500);
    await openDocPanel(doc);
    doc.querySelector('#spBody .dp-tab[data-dp="slides"]').click(); await sleep(1000);
    check('面板里不再有独立的「PPT 优化」模块', !doc.querySelector('.ppt-box'));
    check('也不再有它的入口按钮', !doc.getElementById('pptPick') && !doc.getElementById('pptRun'));
    const head = doc.querySelectorAll('#spBody .dp-slhead');
    check('主题与模板合成一行（只剩一行头部控件）', head.length === 1, head.length + ' 行');
    check('这一行里有主题、模板与两个按钮',
      !!doc.getElementById('dpTheme') && !!doc.getElementById('dpTpl') &&
      !!doc.getElementById('dpTplImport') && !!doc.getElementById('dpTplFill'));
    check('无运行时错误', errors.length === 0, errors.slice(0, 2).join(' | '));
    w.close();
  }

  console.log('== 会话侧：无「下一步」卡片 + 每条回答有【生成文档】 ==');
  {
    const { w, doc, errors } = makeDom();
    await sleep(3000);
    const vis = (() => { const c = doc.body.cloneNode(true); c.querySelectorAll('script,style').forEach((n) => n.remove()); return c.textContent.replace(/\s+/g, ' '); })();
    check('页面上没有「下一步」字样', !/下一步/.test(vis));
    check('计划卡片不渲染', doc.querySelectorAll('.plan-choice').length === 0);
    const btns = Array.from(doc.querySelectorAll('.pill-btn')).map(txt);
    check('历史回答带【生成文档】', btns.filter((x) => x === '生成文档').length > 0, btns.filter((x) => x === '生成文档').length + ' 个');
    check('无运行时错误', errors.length === 0, errors.slice(0, 2).join(' | '));
    w.close();
  }

  console.log('\n结果：' + pass + ' 通过 / ' + fail + ' 失败');
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.log('CHECK ERROR:', (e && e.stack) || e); process.exit(1); });
