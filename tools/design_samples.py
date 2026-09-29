# -*- coding: utf-8 -*-
"""R27 交付证据：把"设计规格通道"渲出来的页面做成**样张对比页**（视觉风格 × 数据版式）。

为什么默认不转 PNG（`docs/13` 要的截图证据在这里用 SVG 更好）：
* 预览＝导出的那份源就是 **SVG**（`docs/22` D13），HTML 里**内联**它就能"打开即看、且与导出逐字节同一份"；
* 转 PNG 要拉 Edge 无头 —— 本机 Edge 常被别的任务占住（实测卡在 0KB 不动），而**样张是给人挑口味的，
  不该依赖浏览器**。要看 PNG 再单独加 `--png`。

用法：
    .venv\\Scripts\\python.exe tools\\design_samples.py            # 只出 SVG + index.html（秒级）
    .venv\\Scripts\\python.exe tools\\design_samples.py --png      # 顺带出 PNG（需要 Edge 空闲）
产物：data/ppt_out/design_samples/{index.html, <风格>-<页>.svg[, .png]}
"""

from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from edu_agent import design_spec as ds  # noqa: E402
from edu_agent import slide_deck  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "ppt_out" / "design_samples"

#: 先上人工调过数值的三套视觉风格 × 两种数据版式（R27b 的 16:9 版式）
STYLES = ["ink-wash", "swiss-minimal", "editorial"]
LAYOUTS = ["hero_statement", "title_content"]

SLIDES = [
    {"name": "3.3 幂函数", "cover_title": "3.3 幂函数", "subtitle": "人教A版 · 高一", "meta": "1 课时"},
    {"name": "幂函数的概念", "minutes": "12",
     "blocks": [{"label": "定义", "text": "形如 y=x^α 的函数，α 为常数"},
                {"label": "关键", "text": "底数是自变量，指数是常数"},
                {"label": "注意", "text": "定义域与值域都随 α 变"}]},
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--png", action="store_true", help="顺带出 PNG（要 Edge 空闲，会慢）")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    cards: list[str] = []
    for sid in STYLES:
        r = ds.resolve({"visual_style": sid, "mode": "instructional",
                        "per_page": [{"layout": LAYOUTS[0]}, {"layout": LAYOUTS[1]}]})
        slides = ds.apply_to_slides(SLIDES, r)
        built = slide_deck.build_deck(sid, slides, title="3.3 幂函数", tokens=r.as_tokens())
        for i, svg in enumerate(built["pages"]):
            name = f"{sid}-{i + 1}"
            (OUT / f"{name}.svg").write_text(svg, encoding="utf-8")
            note = f"{sid} · 版式 {built['layouts'][i]} · 主色 {r.palette['accent']}"
            if args.png:
                from edu_agent import slide_media

                try:
                    png = OUT / f"{name}.png"
                    slide_media.render_png(svg, png, scale=1)
                    st = slide_media.png_stats(png)
                    note += f" · PNG {png.stat().st_size // 1024}KB · 墨迹 {st.get('ink', 0):.1%}"
                except Exception as exc:  # noqa: BLE001 出不了 PNG 不影响样张
                    note += f" · PNG 失败（{type(exc).__name__}）"
            print(f"{name}: 版式={built['layouts'][i]} 主色={r.palette['accent']} SVG={len(svg)} 字节")
            cards.append(f'<figure><div class="frame">{svg}</div>'
                         f'<figcaption>{html.escape(note)}</figcaption></figure>')

    doc = ("<!doctype html><html lang='zh'><meta charset='utf-8'>"
           "<title>R27 设计规格样张（视觉风格 × 数据版式）</title>"
           "<style>body{font:14px/1.7 system-ui,'微软雅黑';margin:24px;background:#f6f7f9}"
           "h1{font-size:20px;margin:0 0 4px}p.sub{color:#666;margin:0 0 20px}"
           "figure{display:inline-block;margin:0 18px 22px 0;vertical-align:top}"
           ".frame{width:640px;border:1px solid #dfe3e8;border-radius:8px;overflow:hidden;background:#fff}"
           ".frame svg{display:block;width:100%;height:auto}"
           "figcaption{color:#555;font-size:12px;margin-top:6px}</style>"
           "<h1>R27 设计规格通道样张</h1>"
           "<p class='sub'>同一份 <code>slides</code> 数据，只换 <b>visual_style</b> 与 <b>per_page.layout</b> —— "
           "页面是服务端渲染的 SVG（预览与导出同一份源）。</p>" + "".join(cards) + "</html>")
    (OUT / "index.html").write_text(doc, encoding="utf-8")
    print("对比页：", OUT / "index.html")
    print("SVG 数：", len(list(OUT.glob("*.svg"))))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
