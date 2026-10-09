# -*- coding: utf-8 -*-
"""图片与 PDF 导出（R23）：**每页 SVG → Edge 无头 --screenshot（1280×720）→ Pillow 拼 PDF**。

为什么这么走（`docs/26` D11）：预览、pptx、PNG、PDF 全部来自**同一份 SVG** ——
所以"看到什么样，导出就是什么样"不是口号，而是结构上只有一个渲染源。
为什么不引 cairosvg / rsvg 之类：本机已经有 Chromium 内核的 Edge（Windows 自带），
它渲染 SVG 的保真度与面板里看到的一致，而且**中文不会变豆腐块**（字体栈跟浏览器一致）。

三条约束：

1. **不落盘到项目数据区之外**：PNG/JPEG/PDF 都写到调用方给的输出目录（导出的产物目录）；
2. **不改 SVG**：只把它放进一个 1280×720 的空白 HTML 里截图，页面上没有别的元素；
3. **缺 Edge 就如实报**：给一句"这台机器没找到 Edge，图片导出用不了"，而不是抛异常或出半张图。
"""

from __future__ import annotations

import base64
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

#: Edge 常见安装位置（Windows 自带；两个都可能在）
EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)
#: 与设计坐标一致（导出尺寸 = 预览尺寸）
PAGE_W, PAGE_H = 1280, 720
#: 截图超时（每页）—— 视频级等待也没必要，SVG 是静态的
SHOT_TIMEOUT = 120


def edge_exe() -> str:
    """找 Edge（找不到返回空串 —— 调用方据此给"这台机器用不了"的明确提示）。"""
    for p in EDGE_CANDIDATES:
        if Path(p).is_file():
            return p
    found = shutil.which("msedge") or shutil.which("chrome") or shutil.which("chromium")
    return found or ""


def page_html(svg: str) -> str:
    """把一页 SVG 放进空白页面里（**除了这张 SVG 什么都没有**，免得截进多余的东西）。"""
    return ("<!doctype html><html><head><meta charset=\"utf-8\">"
            "<style>html,body{margin:0;padding:0;background:#fff;overflow:hidden}"
            "svg{display:block}</style></head><body>" + (svg or "") + "</body></html>")


def render_png(svg_pages: list[str], out_dir: Path, *, prefix: str = "slide",
               scale: float = 1.0) -> dict:
    """每页 SVG → 一张 PNG（1280×720 × scale）。返回 `{ok, files[], edge, error}`。"""
    exe = edge_exe()
    if not exe:
        return {"ok": False, "files": [], "error": "这台机器没找到 Edge（Chromium），图片导出用不了"}
    out_dir.mkdir(parents=True, exist_ok=True)
    # 页面尺寸恒为 1280×720；要更大就靠 device-scale-factor —— **不能两个都乘**
    # （踩过：窗口给 2560×1440 + 缩放 2 → 出来 5120×2880）
    w, h = PAGE_W, PAGE_H
    files: list[str] = []
    nonce = str(int(time.time() * 1000) % 10 ** 8)
    with tempfile.TemporaryDirectory(prefix="edu_png_") as tmp:
        tmpd = Path(tmp)
        for i, svg in enumerate(svg_pages):
            page = tmpd / f"p{i + 1}-{nonce}.html"
            page.write_text(page_html(svg), encoding="utf-8")
            png = out_dir / f"{prefix}-{i + 1:02d}.png"
            cmd = [exe, "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
                   "--force-device-scale-factor=%s" % scale,
                   "--window-size=%d,%d" % (w, h),
                   "--virtual-time-budget=8000",
                   "--screenshot=" + str(png), page.as_uri()]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                      errors="replace", timeout=SHOT_TIMEOUT)
            except (OSError, subprocess.TimeoutExpired) as exc:
                return {"ok": False, "files": files, "edge": exe,
                        "error": f"第 {i + 1} 页截图失败：{type(exc).__name__}"}
            if not png.is_file() or png.stat().st_size <= 0:
                # 重试一次：机器繁忙时（整套回归并行跑、Edge 暂时起不来）
                # 会出现"进程退出码 0 但没写文件"，重试一次即可恢复。
                # 只重试一次，失败仍如实报错 —— 不掩盖真的截不出图。
                try:
                    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                          errors="replace", timeout=SHOT_TIMEOUT)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            if not png.is_file() or png.stat().st_size <= 0:
                tail = (proc.stderr or proc.stdout or "").strip()[-160:]
                return {"ok": False, "files": files, "edge": exe,
                        "error": f"第 {i + 1} 页没截出来（{tail or 'Edge 没写文件'}）"}
            files.append(str(png))
    return {"ok": True, "files": files, "edge": exe, "size": [w, h]}


def make_pdf(png_files: list[str], out_pdf: Path) -> dict:
    """把每页 PNG 拼成一份 PDF（Pillow 直接支持多页 PDF，不必再引别的东西）。"""
    if not png_files:
        return {"ok": False, "error": "没有可拼的页面"}
    try:
        from PIL import Image
    except Exception as exc:                     # noqa: BLE001
        return {"ok": False, "error": f"缺 Pillow：{exc}"}
    imgs = []
    try:
        for f in png_files:
            im = Image.open(f).convert("RGB")
            im.load()
            imgs.append(im)
        out_pdf.parent.mkdir(parents=True, exist_ok=True)
        imgs[0].save(str(out_pdf), "PDF", save_all=True, append_images=imgs[1:],
                     resolution=96.0, title=out_pdf.stem)
    except Exception as exc:                     # noqa: BLE001
        return {"ok": False, "error": f"拼 PDF 失败：{type(exc).__name__}: {exc}"}
    finally:
        for im in imgs:
            try:
                im.close()
            except Exception:                    # noqa: BLE001
                pass
    return {"ok": True, "file": str(out_pdf), "pages": len(png_files)}


def make_jpegs(png_files: list[str], out_dir: Path, *, prefix: str = "slide",
               quality: int = 92) -> dict:
    """PNG → JPEG（给"体积小一点、能贴进微信/钉钉"的场景）。"""
    if not png_files:
        return {"ok": False, "error": "没有可转的页面"}
    try:
        from PIL import Image
    except Exception as exc:                     # noqa: BLE001
        return {"ok": False, "error": f"缺 Pillow：{exc}"}
    out_dir.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    try:
        for i, f in enumerate(png_files):
            with Image.open(f) as im:
                dst = out_dir / f"{prefix}-{i + 1:02d}.jpg"
                im.convert("RGB").save(str(dst), "JPEG", quality=quality, optimize=True)
                files.append(str(dst))
    except Exception as exc:                     # noqa: BLE001
        return {"ok": False, "files": files, "error": f"转 JPEG 失败：{exc}"}
    return {"ok": True, "files": files, "quality": quality}


def png_stats(path: str) -> dict:
    """一张图的体检：尺寸 / 墨迹占比（用来验"中文没变成空白或豆腐块"这类硬伤）。

    "墨迹占比" = 非纯白像素的比例。全是白 → 多半是渲染失败；比例过高 → 多半糊成一片。
    这不是审美判断，只是**抓硬伤**（单测与验收用它当"图不是空的"）。
    """
    try:
        from PIL import Image
    except Exception as exc:                     # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    with Image.open(path) as im:
        w, h = im.width, im.height
        small = im.convert("L").resize((160, 90))
        px = list(small.getdata())
    ink = sum(1 for v in px if v < 245) / float(len(px))
    return {"ok": True, "width": w, "height": h, "ink": round(ink, 4)}


def data_uri_of(path: Path) -> str:
    """把一张图读成 data URI（给"回显一张缩略图"之类的地方用，只读）。"""
    try:
        b = Path(path).read_bytes()
    except OSError:
        return ""
    return "data:image/png;base64," + base64.b64encode(b).decode("ascii")
