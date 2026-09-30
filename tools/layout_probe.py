# -*- coding: utf-8 -*-
"""量一量教案中心的版面占比（结构评估用，只读）。

为什么单独做个工具：结构问题（"画布被一堆条挤小了""窄屏下主操作被挤到第二行"）光看源码
看不出来，得**在真浏览器里量**。

为什么把数字**画在页面上截图**、而不是用 `--dump-dom` 读回来：试过 —— `--dump-dom` 配
`--virtual-time-budget` 时，我们自己的定时器会跑（能插进标记），但页面里那套 async 初始化
（拉会话 / 能力 / 主题）还没完成，量到的全是 0/未挂载（`window.__edu` 都是 false）。
改成"等挂载 → 把数字写进一个浮层 → 截图"，量到的才是老师真正看到的版面。

用法：
    .venv\\Scripts\\python.exe tools\\layout_probe.py                      # 幻灯片页签，1600×1400
    .venv\\Scripts\\python.exe tools\\layout_probe.py --tab=plan --size=1180,1100
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EDGE_CANDIDATES = (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                   r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")
BASE = "http://127.0.0.1:5174/"
OUT = ROOT / "data" / "tmp" / "wzshots"

PROBE_JS = """
<script>(function(){
  var tab = '%(tab)s', tries = 0;
  function ready(){
    var cv = document.getElementById('dpCanvas');
    var pane = document.getElementById('wzDocPane');
    var plan = document.getElementById('wzPlanPane');
    if(tab === 'plan'){                     /* 教案页签没有画布：看列表挂出来没有 */
      return !!(plan && plan.style.display !== 'none' && plan.offsetHeight > 120 && window.__edu);
    }
    var shown = (pane && pane.style.display !== 'none' && pane.offsetHeight > 200);
    return !!(cv && shown && window.__edu);
  }
  function go(){
    tries++;
    var vd = document.getElementById('view-design');
    if(vd && !vd.classList.contains('active') && window.__edu) window.__edu.switchView('design');
    if(window.__edu && window.__edu.doc !== null) window.__edu.wzTabSet(tab);
    if(!ready() && tries < 60){ setTimeout(go, 250); return; }
    var L = [];
    var bar = document.querySelector('#dzEditBar .wz-bar');
    var t1 = document.getElementById('dzTitle'), dl = document.getElementById('wzDownBtn');
    L.push('view ' + window.innerWidth + 'x' + window.innerHeight + '  tries=' + tries);
    if(bar){
      var tops = {};
      Array.prototype.forEach.call(bar.querySelectorAll('input,button,.dp-dl'), function(e){
        if(e.offsetParent !== null && e.offsetHeight > 0) tops[e.offsetTop] = 1;   /* 只数看得见的 */
      });
      L.push('toolbar h=' + bar.offsetHeight + ' rows=' + Object.keys(tops).length +
             ' downloadWrapped=' + (function(){
               /* 比的是**外层容器** `.dp-dl` 的 offsetTop：按钮自己的 offsetTop 是相对它的定位
                  父级算的，拿它比永远 false（踩过：截图里明明换行了，探针说没换）。 */
               var box = document.querySelector('#wzDown');
               return !!(box && t1) && Math.abs(box.offsetTop - t1.offsetTop) > 4;
             })());
    }
    var pane = document.getElementById('wzDocPane'), plan = document.getElementById('wzPlanPane');
    var host = (pane && pane.offsetHeight > 200) ? pane : plan;
    var cv = document.getElementById('dpCanvas');
    if(host){
      L.push('pane h=' + host.offsetHeight);
      /* 幻灯片那套 UI 住在 #wzDocBody（dp-bd）里：#dpDocPane > dp-hd / dp-fmt / dp-bd */
      var body = host.querySelector('#wzDocBody') || host;
      var blocks = Array.prototype.filter.call(body.children, function(el){
        return el.offsetHeight > 0 || el.id === 'dpPgBar';
      });
      var chrome = 0;
      blocks.forEach(function(el){
        var has = el.contains(cv);
        if(!has) chrome += el.offsetHeight;
        L.push('   ' + String(el.offsetHeight).padStart(4) + 'px  ' +
               String(el.className || el.id).slice(0, 40) + (has ? '   <= 画布在这条里' : ''));
      });
      L.push('blockCount=' + blocks.length + '  chrome=' + chrome + '  body h=' + body.offsetHeight +
             '  canvas=' + ((cv && cv.offsetWidth) || 0) + 'x' + ((cv && cv.offsetHeight) || 0) +
             '  canvasShare=' + (body.offsetHeight ? Math.round(100 * ((cv && cv.offsetHeight) || 0) / body.offsetHeight) + '%%' : '-'));
      var wrap = document.getElementById('dpSlideWrap');
      var vis = (wrap && wrap.offsetHeight) || 0;
      L.push('舞台可见高=' + vis + 'px（缩放到 ' +
             Math.round(100 * vis / 720) + '%%）· 占内容区 ' +
             (body.offsetHeight ? Math.round(100 * vis / body.offsetHeight) + '%%' : '-') +
             ' · 其余 ' + chrome + 'px 是那 ' + (blocks.length - 1) + ' 条/张');
      var prow = document.getElementById('dpPropsRow');
      if(prow){
        L.push('属性条 h=' + prow.offsetHeight + ' w=' + prow.offsetWidth + ' 子项：');
        Array.prototype.forEach.call(prow.children, function(el){
          L.push('   ' + el.offsetWidth + 'x' + el.offsetHeight + '  ' +
                 String(el.className || el.id).slice(0, 34));
        });
      }
      L.push('paneChrome(头/格式条)=' + (host.offsetHeight - body.offsetHeight) + 'px');
    }
    var r = document.getElementById('wzLeft');
    L.push('rail=' + ((r && r.offsetParent !== null) ? (r.offsetWidth + 'x' + r.offsetHeight) : 'hidden'));
    var st = (document.getElementById('wzStatusBar') || {}).textContent || '';
    L.push('status: ' + st.slice(0, 70));
    var box = document.createElement('div');
    box.style.cssText = 'position:fixed;left:12px;top:12px;z-index:99999;background:#fff;' +
      'border:2px solid #2F6BFF;border-radius:8px;padding:10px 12px;font:12px/1.5 Consolas,monospace;' +
      'white-space:pre;box-shadow:0 8px 24px rgba(0,0,0,.25);max-width:520px';
    box.textContent = L.join('\\n');
    document.body.appendChild(box);
  }
  setTimeout(go, 800);
})();</script>
"""


def edge_exe() -> str:
    for p in EDGE_CANDIDATES:
        if Path(p).is_file():
            return p
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", default="1600,1400")
    ap.add_argument("--tab", default="slides")
    args = ap.parse_args()
    exe = edge_exe()
    if not exe:
        print("没找到 Edge，量不了")
        return 1
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    seed = ("<script>try{localStorage.setItem('edu_design_v1', JSON.stringify("
            "{title:'3.3 幂函数', grade:'重点班 45 分钟', steps:["
            "{name:'导入', minutes:5, points:'投影五个实例\\n追问：这些函数有什么共同点'},"
            "{name:'新知讲授', minutes:15, points:'形如 y=x^α 的函数'},"
            "{name:'例题精讲', minutes:10, points:'比较 1.5^0.3 与 1.7^0.3'},"
            "{name:'随堂练习', minutes:10, points:'课本练习'},"
            "{name:'小结', minutes:5, points:'三条结论'}]}));}catch(e){}</script>")
    page = ROOT / "static" / ("_probe_%s_%s.html" % (args.tab, int(time.time() * 1000) % 10 ** 7))
    # ⚠️ 必须插在**最后一个** `</body>` 前：index.html 里有 4 个 `</body>`（另外 3 个在 JS 字符串里，
    # 是"导出 HTML / 打印"那些拼接模板用的）。用 str.replace 一次换掉 4 个 → 脚本语法错误 →
    # 整个应用不初始化（量到的全是 0，截图里页面是半成品）。踩过，记在这里。
    cut = html.rfind("</body>")
    page.write_text(html[:cut].replace("<head>", "<head>" + seed, 1) +
                    (PROBE_JS % {"tab": args.tab}) + html[cut:], encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    png = OUT / ("probe-%s-%s.png" % (args.tab, args.size.replace(",", "x")))
    try:
        proc = subprocess.run([exe, "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
                               "--force-device-scale-factor=1", "--window-size=" + args.size,
                               "--virtual-time-budget=15000", "--screenshot=" + str(png),
                               BASE + "static/" + page.name],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=240)
    finally:
        page.unlink(missing_ok=True)
    if not png.is_file():
        print("没截出来：", (proc.stderr or "")[-200:])
        return 1
    print("量好的图：", png)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
