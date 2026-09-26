"""出样张：把 3 页示例内容用每套主题渲染成 SVG → 用本机 Edge 无头截成 PNG → 生成一张对比页。

用法（在仓库根跑）：

    .venv\\Scripts\\python.exe tools\\slide_samples.py                 # 3 套主题全出
    .venv\\Scripts\\python.exe tools\\slide_samples.py --theme hand-drawn

产物（`data/` 不入库，随便删）：

    data/ppt_out/samples/<主题>/p1-cover.svg|png …
    data/ppt_out/samples/index.html      # 打开它就能三套并排挑

为什么用 Edge 截图而不是装 Playwright：本机 Edge 已在（docs/22 §7.3），
`--headless=new --screenshot` 就够用，不用下载 Chromium、不用装 LibreOffice。
"""

from __future__ import annotations

import argparse
import html
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from edu_agent import ppt_svg, ppt_tokens  # noqa: E402  （要先把 src 放进来）

EDGE_CANDIDATES = (
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
)

#: 示例内容：《3.3 幂函数》一节课的三页（封面 / 要点 / 图文）
SAMPLE_DECK: list[dict] = [
    {
        "layout": "cover",
        "label": "人教A版 · 必修第一册",
        "title": "3.3 幂函数",
        "subtitle": "数学 · 高一 · 新授课",
        "meta": "1 课时 · 情境导入 → 概念建构 → 图象探究 → 小结",
    },
    {
        "layout": "bullets",
        "label": "环节 2 · 12 分钟",
        "title": "幂函数的概念",
        "blocks": [
            {"label": "定义", "text": "形如 y=x^α 的函数，α 为常数"},
            {"label": "关键", "text": "底数是自变量，指数是常数"},
            {"label": "定义域", "text": "随 α 的取值变化，先看指数再定"},
            {"label": "五个常见", "text": "y=x、y=x²、y=x³、y=√x、y=x⁻¹"},
        ],
    },
    {
        "layout": "media",
        "label": "环节 3 · 15 分钟",
        "title": "图象与性质",
        "caption": "在同一坐标系里画出 y=x²，观察它的单调性与奇偶性。",
        "bullets": ["关于 y 轴对称，是偶函数", "在 [0,+∞) 上单调递增"],
    },
]


def find_edge() -> Path | None:
    """找本机 Edge（也可用环境变量 EDGE_PATH 指定）。"""
    import os

    env = os.environ.get("EDGE_PATH", "").strip()
    if env and Path(env).is_file():
        return Path(env)
    for p in EDGE_CANDIDATES:
        if p.is_file():
            return p
    found = shutil.which("msedge")
    return Path(found) if found else None


# ---------------------------------------------------------------- 导出 pdf→pptx（D2 排雷）

def ppt_master_scripts() -> Path | None:
    """找 ppt-master 的脚本目录（.deps 里那份；装到别处可用 PPT_MASTER_HOME 指定）。"""
    import os

    home = os.environ.get("PPT_MASTER_HOME", "").strip()
    roots = [Path(home)] if home else []
    roots.append(REPO / ".deps" / "ppt-master")
    roots.append(Path(os.environ.get("LOCALAPPDATA", "")) / "edu-agent" / "deps" / "ppt-master")
    for root in roots:
        scripts = root / "skills" / "ppt-master" / "scripts"
        if (scripts / "svg_to_pptx.py").is_file():
            return scripts
    return None


def svg_texts(svg: str) -> list[str]:
    """把一份 SVG 里的可见文字抠出来（导出后要比对内容没丢）。"""
    import re

    return [html.unescape(t) for t in re.findall(r"<text[^>]*>(.*?)</text>", svg, re.S) if t.strip()]


#: 允许作为中文字体的族名（本机已装：微软雅黑 / Noto Sans SC）——判据按"含中文的 run"逐条核
EA_OK = ("微软雅黑", "Microsoft YaHei", "Noto Sans SC", "Source Han Sans", "思源黑体")


def _has_cjk(s: str) -> bool:
    return any(ord(ch) > 0x2E80 for ch in s)


def pptx_font_audit(slides_xml: list[str], parts: list[str], theme_xml: str = "") -> tuple[bool, str]:
    """查"中文字体有没有掉回宋体"：只看**含中文的 run**，英文 run 的 ea 无所谓。

    为什么这么查：转换器会给纯西文 run 写 `<a:ea typeface="Arial">`（无害），
    真正会出问题的是含中文的 run 没写 ea 或写成非中文字体 —— 那时渲染器按主题缺省走，
    在中文 Windows 上就是宋体（docs/22 §1 那类"默认丑"）。
    """
    import re

    total = explicit = 0
    bad: list[str] = []
    eas: set[str] = set()
    latins: set[str] = set()
    for xml in slides_xml:
        for run in re.findall(r"<a:r>(.*?)</a:r>", xml, re.S):
            txt = html.unescape("".join(re.findall(r"<a:t>([^<]*)</a:t>", run)))
            lat = re.search(r'<a:latin typeface="([^"]*)"', run)
            ea = re.search(r'<a:ea typeface="([^"]*)"', run)
            if lat and lat.group(1):
                latins.add(lat.group(1))
            if not _has_cjk(txt):
                continue
            total += 1
            if ea and ea.group(1):
                explicit += 1
                eas.add(ea.group(1))
                if ea.group(1) not in EA_OK:
                    bad.append(f"「{txt[:8]}」→ ea={ea.group(1)}")
            else:
                bad.append(f"「{txt[:8]}」→ 没写 ea（会跟主题缺省，中文环境多半掉宋体）")
    theme_ea = ""
    if theme_xml:
        m = re.search(r"<a:minorFont>.*?<a:ea typeface=\"([^\"]*)\"", theme_xml, re.S)
        theme_ea = (m.group(1) if m else "") or "（空 = 跟随系统默认）"
    ok = total > 0 and not bad
    msg = (f"中文字体：含中文 run {total} 个，显式 ea {explicit} 个 → {sorted(eas) or '—'}"
           f"；主题缺省 ea={theme_ea}；西文 latin={sorted(latins) or '—'}")
    if bad:
        msg += "；问题：" + "；".join(bad[:3])
    return ok, msg


def export_pptx(theme: str, svg_files: list[Path], out_dir: Path) -> tuple[Path | None, list[str]]:
    """把某一套主题的 SVG 交给 ppt-master 出原生 pptx，并做 docs/22 §6.2 的四条硬验收。

    返回 (pptx 路径或 None, 结论行)。落盘规则：中间工作区在 `data/tmp/ppt_jobs/`，产物在 `data/ppt_out/`。
    """
    import re
    import zipfile

    scripts = ppt_master_scripts()
    if scripts is None:
        return None, ["✗ 没找到 ppt-master（跑 deploy\\setup_ppt_master.cmd，或设 PPT_MASTER_HOME）"]

    lines: list[str] = []
    job = REPO / "data" / "tmp" / "ppt_jobs" / f"sample_{theme}"
    svg_out = job / "svg_output"
    svg_out.mkdir(parents=True, exist_ok=True)
    for old in svg_out.glob("*.svg"):
        old.unlink()
    for i, src in enumerate(svg_files, 1):
        shutil.copyfile(src, svg_out / f"{i:02d}_{src.stem.split('-', 1)[-1]}.svg")

    py = sys.executable
    checker = scripts / "svg_quality_checker.py"
    exporter = scripts / "svg_to_pptx.py"
    for cmd, tag in (
        ([py, str(checker), str(job), "--canonical-authoring", "--stage", "final",
          "--quick-generate", "--json"], "质量检查"),
        ([py, str(exporter), str(job), "--quick-generate", "-o",
          str(out_dir / f"{theme}-sample.pptx")], "导出"),
    ):
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=600)
        tail = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()][-1:] or [""]
        lines.append(f"{'✓' if tag == '质量检查' or proc.returncode == 0 else '·'} {tag}：{tail[0][:100]}")

    pptx = out_dir / f"{theme}-sample.pptx"
    if not pptx.is_file():
        return None, lines + ["✗ 没有产出 pptx"]

    # 四条硬验收（docs/22 §6.2）
    report = job / "validation" / f"{pptx.stem}.report.json"
    status = ""
    if report.is_file():
        status = json.loads(report.read_text(encoding="utf-8")).get("status", "")
    z = zipfile.ZipFile(pptx)
    parts = z.namelist()
    slides = sorted(n for n in parts if re.fullmatch(r"ppt/slides/slide\d+\.xml", n))
    per_slide, shapes, slides_xml = [], 0, []
    for name in slides:
        xml = z.read(name).decode("utf-8")
        slides_xml.append(xml)
        per_slide.append([html.unescape(t) for t in re.findall(r"<a:t>([^<]*)</a:t>", xml)])
        shapes += len(re.findall(r"<p:sp>", xml))
    theme_xml = ""
    for name in parts:
        if re.fullmatch(r"ppt/theme/theme\d+\.xml", name):
            theme_xml = z.read(name).decode("utf-8")
            break
    embedded = [n for n in parts if "fntdata" in n]
    want = [svg_texts(f.read_text(encoding="utf-8")) for f in svg_files]

    font_ok, font_msg = pptx_font_audit(slides_xml, parts, theme_xml)
    lines.append(f"{'✓' if status.startswith('passed') else '✗'} 转换报告：status={status or '（没有报告）'}")
    lines.append(f"{'✓' if len(slides) == len(svg_files) else '✗'} 页数：pptx {len(slides)} 页 / SVG {len(svg_files)} 页")
    lines.append(f"{'✓' if shapes and all(p for p in per_slide) else '✗'} 原生文本：{shapes} 个形状、"
                 f"每页文字块 {[len(p) for p in per_slide]}")
    lines.append(f"{'✓' if not embedded else '✗'} 不嵌字体：包内 fntdata {len(embedded)} 个"
                 f"（WPS 不读内嵌裸 TTF）")
    lines.append(f"{'✓' if font_ok else '✗'} {font_msg}")
    missing = []
    for i, (w, got) in enumerate(zip(want, per_slide), 1):
        lack = [t for t in w if t.strip() and t.strip() not in [g.strip() for g in got]]
        if lack:
            missing.append(f"第 {i} 页缺 {lack[:3]}")
    lines.append(f"{'✓' if not missing else '✗'} 内容零丢失：{len(want)} 页逐串比对"
                 f"{'' if not missing else ' · ' + '；'.join(missing)}")
    return pptx, lines


def render_svg_pages(theme_id: str, out_dir: Path) -> tuple[list[Path], list[str]]:
    """写出一套主题的每页 SVG，返回 (文件列表, 提示信息)。"""
    t = ppt_tokens.load(theme_id)
    warnings: list[str] = []
    pages = ppt_svg.render_deck(t, SAMPLE_DECK, warnings)
    files: list[Path] = []
    for i, svg in enumerate(pages, 1):
        name = f"p{i}-{SAMPLE_DECK[i - 1]['layout']}.svg"
        path = out_dir / name
        path.write_text(svg, encoding="utf-8")
        files.append(path)
    return files, warnings


def screenshot(edge: Path, svg: Path, png: Path, *, width: int = 1280, height: int = 720,
               scale: int = 2, timeout: int = 90) -> bool:
    """用 Edge 无头把一份 SVG 截成 PNG（2 倍图，方便看清字）。"""
    with tempfile.TemporaryDirectory(prefix="edge_shot_") as profile:
        cmd = [
            str(edge), "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--no-first-run", "--no-default-browser-check", "--disable-extensions",
            f"--user-data-dir={profile}",
            f"--force-device-scale-factor={scale}",
            f"--window-size={width},{height}",
            f"--screenshot={png}",
            svg.resolve().as_uri(),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"  ✗ Edge 截图失败：{exc}")
            return False
    ok = png.is_file() and png.stat().st_size > 0
    if not ok:
        tail = (proc.stderr or b"")[-300:].decode("utf-8", "replace")
        print(f"  ✗ 没生成 PNG（exit={proc.returncode}）{tail}")
    return ok


def gallery(out_dir: Path, themes: list[str], notes: dict[str, list[str]]) -> Path:
    """生成一张能同时看三套主题的对比页（双击就能开，不依赖服务）。"""
    cards = []
    for theme in themes:
        t = ppt_tokens.load(theme)
        p = t["palette"]
        swatches = "".join(
            f'<span class="sw"><i style="background:{p[k]}"></i>{k} {p[k]}</b></span>'
            for k in ("text", "accent", "sub", "line", "soft") if k in p
        )
        shots = "".join(
            f'<a href="{theme}/{f.name}" target="_blank"><img src="{theme}/{f.name}" alt="{f.stem}"></a>'
            for f in sorted((out_dir / theme).glob("*.png"))
        )
        warn = "".join(f"<li>{html.escape(w)}</li>" for w in notes.get(theme, []))
        cards.append(f"""
  <section>
    <h2>{html.escape(t['name'])} <code>{html.escape(theme)}</code></h2>
    <p class="meta">画布 {t['canvas']['w']}×{t['canvas']['h']} · 安全边距 {t['canvas']['margin']}px ·
       标题 {t['type']['title']}px · 正文 {t['type']['body']}px · 注释 {t['type']['caption']}px<br>
       数值出处：<code>{html.escape(t['source'])}</code></p>
    <p class="sws">{swatches}</p>
    <div class="shots">{shots}</div>
    {f'<ul class="warn">{warn}</ul>' if warn else ''}
  </section>""")

    doc = f"""<!doctype html>
<html lang="zh-CN"><meta charset="utf-8">
<title>样张：3 套主题 × 3 个版式</title>
<style>
  body{{margin:0;padding:28px 32px 60px;background:#F4F5F7;color:#1F2430;
       font:14px/1.7 "Microsoft YaHei",system-ui,sans-serif}}
  h1{{font-size:20px;margin:0 0 6px}}
  h2{{font-size:17px;margin:0 0 4px}}
  .lead{{color:#5A6472;margin:0 0 22px}}
  section{{background:#fff;border:1px solid #E3E6EB;border-radius:12px;padding:16px 18px;margin:0 0 18px}}
  .meta{{color:#5A6472;font-size:12.5px;margin:2px 0 8px}}
  .sws{{margin:6px 0 12px}}
  .sw{{display:inline-flex;align-items:center;gap:6px;margin-right:14px;font-size:12px;color:#39404B}}
  .sw i{{width:14px;height:14px;border:1px solid #CFD4DB;border-radius:3px;display:inline-block}}
  .shots{{display:grid;grid-template-columns:1fr;gap:12px}}
  .shots img{{width:100%;max-width:1000px;border:1px solid #E3E6EB;border-radius:8px;display:block}}
  .warn{{color:#8A5A00;font-size:12.5px}}
  code{{background:#F1F3F6;border-radius:4px;padding:1px 5px;font-size:12px}}
</style>
<h1>样张：3 套主题 × 3 个版式（封面 / 要点 / 图文）</h1>
<p class="lead">这些图是用面板将要使用的那份 SVG 源渲染出来的（不是示意图）。挑一套告诉我，
或指出哪里要调（字大/小、留白多少、颜色）——数值改一行就行。</p>
{''.join(cards)}
</html>
"""
    path = out_dir / "index.html"
    path.write_text(doc, encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="渲染样张（SVG + PNG + 对比页）")
    ap.add_argument("--theme", action="append", default=None,
                    help=f"主题 id，可重复；默认全部（可选：{', '.join(ppt_tokens.available())}）")
    ap.add_argument("--out", default=str(REPO / "data" / "ppt_out" / "samples"), help="输出目录")
    ap.add_argument("--no-png", action="store_true", help="只出 SVG，不截图")
    ap.add_argument("--to-pptx", action="store_true",
                    help="再走一遍导出（ppt-master → 原生 pptx）并按 docs/22 §6.2 做四条硬验收")
    args = ap.parse_args()

    themes = args.theme or ppt_tokens.available()
    if not themes:
        print("没有可用的主题（content/ppt_templates/_builtin/*.json 是空的？）")
        return 2

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    edge = None if args.no_png else find_edge()
    if not args.no_png and edge is None:
        print("！没找到 Edge，跳过截图（设 EDGE_PATH 指定 msedge.exe）")

    notes: dict[str, list[str]] = {}
    for theme in themes:
        d = out_dir / theme
        d.mkdir(parents=True, exist_ok=True)
        files, warnings = render_svg_pages(theme, d)
        notes[theme] = warnings
        print(f"[{theme}] {len(files)} 页 SVG → {d}")
        for w in warnings:
            print(f"  ！{w}")
        if edge:
            for svg in files:
                png = svg.with_suffix(".png")
                if screenshot(edge, svg, png):
                    print(f"  ✓ {png.name}  {png.stat().st_size // 1024} KB")
        if args.to_pptx:
            pptx, lines = export_pptx(theme, files, d)
            for ln in lines:
                print(f"  {ln}")
            if pptx:
                print(f"  → {pptx}")

    page = gallery(out_dir, themes, notes)
    print(f"\n对比页：{page}\n双击打开就能三套并排挑。")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
