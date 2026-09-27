# -*- coding: utf-8 -*-
"""教案工作台截图（人眼验收用）：Edge 无头截四个页签，落到 data/tmp/wzshots/。

为什么要这个脚本：`docs/25` §8.1 记着"工作区自动截图没打通"—— 应用逻辑都在 IIFE 里，
脚本没法"先切到工作台再拍"。R9 加了只读调试钩子 `window.__edu`，这里就用它。

用法（先启动服务）：  .venv/Scripts/python.exe tools/wz_shot.py [--seed] [--pick=槽位] [--size=1600,1400] [页签...]
页签取值：plan / doc / md / slides（默认四个都拍）；`--seed` 塞一份老编排数据，让工作区里有 5 个环节。
`--pick=block:0`（默认自动挑第一个）会在幻灯片页签上**选中一格**再拍 —— 选框 + 8 个手柄要人眼看得见。
`--show` 在幻灯片页签上**按 F5 进放映**再拍（R17：全屏那一页）。
`--size=1600,1400` 拍高一点：幻灯片页签底下还有备注栏与页操作，1100 高会截掉。
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
OUT = ROOT / "data" / "tmp" / "wzshots"
BASE = "http://127.0.0.1:5174/"

# 注入的脚本要点（都是踩过的坑）：
#  · 必须插在**最后一个** `</body>` 之前（源码里 dzExportHTML 的字符串也含 `</body>`）
#  · 用 __edu 钩子切视图（switchView 不是全局函数）
#  · 把 `.view` 里非目标视图强制 display:none —— 会话恢复是异步的，两个视图同时 display 时
#    截到的是聊天页（view-design 在 DOM 里排在 chat 之后）
#  · ⚠️ 写 DOM 前先**判断是否已经是目标态**：不判断的话，observer 被自己的改动再次触发 →
#    死循环 → Edge 一直不结束（踩过：整个截图进程 5 分钟超时）
SHOT_JS = """
<script>(function(){
  var task = %(task)s, busy = false, n = 0, pick = %(pick)s, show = %(show)s;
  function step(){
    if(busy) return; busy = true;
    try{
      n++;
      var vd = document.getElementById('view-design');
      var needSwitch = !vd || !vd.classList.contains('active');
      if(needSwitch && window.__edu) window.__edu.switchView('design');
      var cur = document.querySelector('#wzTabs .wz-tab.wz-on');
      if((!cur || cur.dataset.wz !== task) && window.__edu) window.__edu.wzTabSet(task);
      document.querySelectorAll('.view').forEach(function(v){
        var mine = (v.id === 'view-design');
        if(v.classList.contains('active') !== mine) v.classList.toggle('active', mine);
        var want = mine ? '' : 'none';
        if(v.style.display !== want) v.style.display = want;
      });
      var st = document.getElementById('sidePanel');
      if(st && st.style.display !== 'none') st.style.display = 'none';
      window.scrollTo(0,0);
      /* R14：选中一格再拍（选框 + 8 手柄）。判断"是否已经是目标态"是硬要求 ——
         否则 observer 被自己的改动再次触发 → 死循环 → Edge 不退出（踩过）。 */
      if(task === 'slides' && pick && !show){
        var cv = document.querySelector('#wzDocPane .dp-canvas');
        if(cv && !document.getElementById('dpSel')){
          var b = cv.querySelector('.dp-box[data-slot="' + pick + '"]') ||
                  cv.querySelector('.dp-box:not(.dp-deco)') || cv.querySelector('.dp-box');
          if(b) b.click();
        }
      }
      /* R17：全屏放映层（走老师那条路：按 F5）。同样要判断目标态，否则循环。 */
      if(task === 'slides' && show && !document.getElementById('dpShow')){
        var pg = document.querySelector('#wzDocPane .dp-canvas svg');
        if(pg) document.dispatchEvent(new KeyboardEvent('keydown',
          { key: 'F5', bubbles: true, cancelable: true }));
      }
    }catch(e){ document.title = 'SHOT-ERR ' + e.message; }
    finally{ busy = false; }
  }
  step();
  var mo = new MutationObserver(function(){ if(n > 40){ mo.disconnect(); return; } step(); });
  mo.observe(document.body, {attributes:true, attributeFilter:['class'], subtree:true, childList:true});
})();</script>
"""


def main() -> int:
    args = [a for a in sys.argv[1:]]
    seed = "--seed" in args                      # 塞一份老编排数据 → 工作区有 5 个环节可看
    pick = next((a.split("=", 1)[1] for a in args if a.startswith("--pick=")), "block:0")
    size = next((a.split("=", 1)[1] for a in args if a.startswith("--size=")), "1600,1100")
    show = "--show" in args                      # 幻灯片页签：按 F5 进放映再拍（R17）
    args = [a for a in args if not a.startswith("--")]
    tabs = [t for t in args if t in ("plan", "doc", "md", "slides")] or \
           ["plan", "doc", "md", "slides"]
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    idx = html.rfind("</body>")
    if idx < 0:
        print("找不到 </body>")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    # 每次跑用**不同的文件名**：无头 Edge 会按 URL 走磁盘缓存，同名页面会拿到上一轮的 HTML
    # （踩过：加了 --seed 但截图字节数和没加时一模一样，就是缓存）
    nonce = str(int(time.time() * 1000) % 1000000)
    head = ""
    if seed:
        head = ("<script>try{localStorage.setItem('edu_design_v1', JSON.stringify("
                "{title:'3.3 幂函数', grade:'重点班 45 分钟', steps:["
                "{name:'导入', minutes:5, points:'投影五个实例\\n追问：这些函数有什么共同点'},"
                "{name:'新知讲授', minutes:15, points:'形如 y=x^α 的函数\\nα 为常数'},"
                "{name:'例题精讲', minutes:10, points:'比较 1.5^0.3 与 1.7^0.3 的大小'},"
                "{name:'随堂练习', minutes:10, points:'课本练习（当堂完成）'},"
                "{name:'小结', minutes:5, points:'三条结论\\n易错点提醒'}]}));}catch(e){}</script>")
    for tab in tabs:
        page = ROOT / "static" / ("_shot_%s_%s.html" % (tab, nonce))
        # ⚠️ --seed 必须塞进 <head>：应用的主脚本在 </body> 前，appInit 一跑就把
        # localStorage 读走了 —— 把 seed 放在 </body> 前就太晚了（踩过：截出来还是空工作区）
        doc_html = html.replace("<head>", "<head>" + head, 1) if head else html
        page.write_text(doc_html[:doc_html.rfind("</body>")] +
                        (SHOT_JS % {"task": "'%s'" % tab, "pick": "'%s'" % pick,
                                    "show": "true" if show else "false"}) +
                        doc_html[doc_html.rfind("</body>"):],
                        encoding="utf-8")
        png = OUT / ("wz_%s%s%s%s.png" % (tab, "-sel" if (tab == "slides" and pick and not show) else "",
                                          "-show" if (tab == "slides" and show) else "",
                                          "" if size == "1600,1100" else "-tall"))
        r = subprocess.run([EDGE, "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
                            "--force-device-scale-factor=1", "--window-size=" + size,
                            "--virtual-time-budget=12000", "--screenshot=" + str(png),
                            BASE + "static/" + page.name],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=240)
        page.unlink(missing_ok=True)
        size = png.stat().st_size if png.is_file() else -1
        print("截图 %-7s → %s（%d 字节）%s" % (tab, png.relative_to(ROOT), size,
              "" if size > 0 else "  ← Edge 没写出来，stderr：" + (r.stderr or "").strip()[-200:]))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
