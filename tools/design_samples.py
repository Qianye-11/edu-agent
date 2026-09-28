# -*- coding: utf-8 -*-
"""R27 交付证据：把"设计规格通道"渲出来的页面出成 PNG（三套视觉风格 × 两种版式）。

为什么单独写一个：`docs/13` 要求界面/观感类改动**留截图证据**。数据版式与视觉风格都是"看"的东西，
所以这里用**同一个预览接口的那条渲染路径**（`design_spec.resolve` → `slide_deck.build_deck`）
出 SVG，再用 `slide_media.render_png`（Edge 无头，1280×720）转成 PNG，拼成一张对比图。

用法：.venv\\Scripts\\python.exe tools/design_samples.py
产物：data/ppt_out/design_samples/{index.html, *.png}
"""

from __future__ import annotations

import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from edu_agent import design_spec as ds  # noqa: E402
from edu_agent import slide_deck, slide_media  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "ppt_out" / "design_samples"

#: 三套视觉风格（R27a 里人工调过的那三套）+ 两种数据版式
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
    OUT.mkdir(parents=True, exist_ok=True)
    cards: list[str] = []
    for sid in STYLES:
        r = ds.resolve({"visual_style": sid, "mode": "instructional",
                        "per_page": [{"layout": LAYOUTS[0]}, {"layout": LAYOUTS[1]}]})
        slides = ds.apply_to_slides(SLIDES, r)
        built = slide_deck.build_deck(sid, slides, title="3.3 幂函数", tokens=r.as_tokens())
        for i, svg in enumerate(built["pages"]):
            png = OUT / f"{sid}-{i + 1}.png"
            slide_media.render_png(svg, png, scale=1)
            print(f"{sid} 第 {i + 1} 页 → {png.name}（{png.stat().st_size // 1024} KB）"
                  f"  版式={built['layouts'][i]}  主色={r.palette['accent']}")
            st = slide_media.png_stats(png)
            cards.append(
                f'<figure><img src="{png.name}" width="560">'
                f'<figcaption><b>{html.escape(sid)}</b> · {built["layouts"][i]} · '
                f'主色 {r.palette["accent"]} · 墨迹 {st.get("ink", 0):.1%}</figcaption></figure>')
    doc = ("<!doctype html><meta charset='utf-8'><title>R27 设计规格样张</title>"
           "<style>body{font:14px/1.6 system-ui,'微软雅黑';margin:24px}"
           "figure{display:inline-block;margin:0 16px 20px 0}figcaption{color:#555;font-size:12px}"
           "h1{font-size:20px}</style>"
           "<h1>R27 设计规格通道样张（视觉风格 × 数据版式）</h1>" + "".join(cards))
    (OUT / "index.html").write_text(doc, encoding="utf-8")
    print("对比页：", OUT / "index.html")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
