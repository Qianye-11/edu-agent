# -*- coding: utf-8 -*-
"""联网取图（R18 前置 · 与同事 agent 相连用）：**只做"把一张图安全地拿到本地并挂到某一页"**。

为什么需要它：老师说「计划与 agent 相连」—— 同事那边的教案 agent 找到/生成好的配图，
传给我们的是**一个 URL**；我们这边要能把它取回来、挂到某一页、预览与导出都带上。
这条路**不需要任何第三方图源 key**，只需要"能出网"（本机实测：外网通）。

三条红线（写在代码里，不靠自觉）：

1. **只写 `data/uploads/`**（`content/plans/` 一个字不写、模板库不碰）；
2. **只认公网 http/https**：拒绝本机/内网地址（SSRF 防护），拒绝非图片类型与超大文件；
3. **预览链路仍不落盘**：渲染时把图片读成 `data:` URI 内联进 SVG（读文件，不写文件）。

搜索类能力（openverse / wikimedia / pexels / pixabay）单独放在 `search()`：
免 key 的两家**本机实测连不上**（超时），要 key 的两家能连但没有 key —— 所以这里**如实报状态**，
不假装能搜到图。
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import os
import socket
from pathlib import Path
from urllib.parse import urlparse

#: 允许的图片类型 → 后缀（**白名单**：别的类型一律拒）
MIME_EXT = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
    "image/webp": ".webp", "image/gif": ".gif",
}
#: 单张上限（与 `docs/26` §6.3 一致）
MAX_BYTES = 10 * 1024 * 1024
#: 下载超时（秒）
TIMEOUT = 20.0
#: 图源清单。`reachable` = **本机实测**能不能连上（2026-09-27 实测：免 key 的两家握手超时，
#: 要 key 的两家能连但没 key）—— 状态要如实，不能因为"免 key"就说"能用"。
PROVIDERS = (
    {"id": "openverse", "name": "Openverse（CC 免 key）", "key_env": "", "reachable": False,
     "note": "免 key；本机实测连不上（连接超时）"},
    {"id": "wikimedia", "name": "维基共享（免 key）", "key_env": "", "reachable": False,
     "note": "免 key；本机实测连不上（TLS 握手超时）"},
    {"id": "pexels", "name": "Pexels", "key_env": "PEXELS_API_KEY", "reachable": True,
     "note": "要免费 key（申请约 1 分钟）；本机实测能连"},
    {"id": "pixabay", "name": "Pixabay", "key_env": "PIXABAY_API_KEY", "reachable": True,
     "note": "要免费 key；本机实测能连"},
)


def uploads_root() -> Path:
    """图片落地目录：`<EDU_DATA_DIR>/uploads/`（预览不写它，只有"取图/上传"写）。"""
    base = os.environ.get("EDU_DATA_DIR") or str(Path(__file__).resolve().parents[2] / "data")
    return Path(base) / "uploads"


def provider_status() -> dict:
    """图源就绪状态（给面板显示"为什么现在搜不了图"）：缺 key / 连不上，**分开说**。

    `usable` = **有 key 且本机连得上**。"免 key"不等于"能用"—— 本机实测那两家免 key 的超时。
    """
    rows = []
    for p in PROVIDERS:
        env = p["key_env"]
        has_key = bool(os.environ.get(env)) if env else True
        rows.append({"id": p["id"], "name": p["name"], "key_env": env, "has_key": has_key,
                     "reachable": bool(p["reachable"]),
                     "usable": bool(has_key and p["reachable"]), "note": p["note"]})
    usable = [r["id"] for r in rows if r["usable"]]
    return {"ok": True, "providers": rows, "usable": usable,
            "hint": ("可用的图源：" + "、".join(usable)) if usable else
                     "现在一个图源都用不了：免 key 的两家（openverse/wikimedia）本机连不上（实测超时），"
                     "要 key 的 Pexels/Pixabay 还没配 key。"
                     "→ 要么在 .env 里填 PEXELS_API_KEY / PIXABAY_API_KEY，"
                     "要么让同事的 agent 直接把图 URL 给你（「联网取图」走这条，不需要任何 key）"}


def _host_ok(host: str) -> bool:
    """只放行**公网**地址：本机/内网/保留地址一律拒（SSRF 防护）。

    为什么要单独做：这个接口会去"下载一个外部给的 URL" —— 不挡的话，一个 `http://127.0.0.1:5174/…`
    就能被拿来打自己的内网服务（那是安全漏洞，不是功能）。
    """
    if not host:
        return False
    if host.lower() in ("localhost", "localhost.localdomain"):
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False                      # 解析不了 = 不可用（也让离线时快速失败）
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def check_url(url: str) -> dict:
    """先验 URL（协议/主机），不下载 —— 让调用方在真下载前就能给出明确错误。"""
    u = str(url or "").strip()
    if not u:
        return {"ok": False, "error": "没有给图片地址"}
    p = urlparse(u)
    if p.scheme not in ("http", "https"):
        return {"ok": False, "error": f"只认 http/https 的图片地址（给的是 {p.scheme or '空'}）"}
    if not _host_ok(p.hostname or ""):
        return {"ok": False, "error": f"这个地址不可用（本机/内网/解析不了）：{p.hostname}"}
    return {"ok": True, "url": u, "host": p.hostname}


def fetch_url(url: str, session_id: str = "", *, max_bytes: int = MAX_BYTES,
              timeout: float = TIMEOUT, opener=None) -> dict:
    """把一个公网图片 URL 取回本地：落到 `data/uploads/<会话>/<时间戳>-<名字>.<后缀>`。

    返回 `{ok, path(相对 uploads 的路径), file, bytes, sha256, mime, width/height 读不到就 0}`。
    `opener` 只是为了单测能塞假实现（默认用 httpx）；**任何异常都转成 `{ok: False, error}`**，
    不抛给上层（老师那边要看到"哪一步失败"，而不是 500）。
    """
    bad = check_url(url)
    if not bad["ok"]:
        return bad
    url = bad["url"]
    sid = _safe_seg(session_id) or "shared"
    dst_dir = uploads_root() / sid
    try:
        if opener is not None:
            data, mime, final = opener(url, max_bytes, timeout)
        else:
            import httpx

            with httpx.Client(timeout=timeout, follow_redirects=True) as c:
                with c.stream("GET", url) as r:
                    if r.status_code >= 400:
                        return {"ok": False, "error": f"对方返回 {r.status_code}（这张图取不到）"}
                    mime = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
                    buf = bytearray()
                    for chunk in r.iter_bytes():
                        buf.extend(chunk)
                        if len(buf) > max_bytes:
                            return {"ok": False,
                                    "error": f"图太大（>{max_bytes // 1024 // 1024}MB），换一张或先压一下"}
                    data, final = bytes(buf), str(r.url)
    except Exception as exc:  # noqa: BLE001 网络/解析/超时都走这里
        return {"ok": False, "error": f"取图失败：{type(exc).__name__}: {exc}"}

    ext = MIME_EXT.get(mime)
    if not ext:
        sniff = _sniff_ext(data)
        if not sniff:
            return {"ok": False, "error": f"这不是图片（content-type={mime or '未知'}）"}
        ext = sniff
    if not data:
        return {"ok": False, "error": "取到的是空文件"}
    if len(data) > max_bytes:
        return {"ok": False, "error": f"图太大（>{max_bytes // 1024 // 1024}MB）"}

    dst_dir.mkdir(parents=True, exist_ok=True)
    import time as _time
    name = _safe_seg(Path(urlparse(final).path).stem)[:28] or "image"
    dst = dst_dir / f"{_time.strftime('%Y%m%d-%H%M%S')}-{name}{ext}"
    if dst.exists():
        dst = dst.with_name(f"{dst.stem}-{_time.time_ns() % 1000}{dst.suffix}")
    dst.write_bytes(data)
    rel = dst.relative_to(uploads_root()).as_posix()
    return {"ok": True, "path": rel, "file": str(dst), "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()[:16], "mime": mime or _mime_of(ext),
            "ext": ext}


def _safe_seg(s: str) -> str:
    """路径片段消毒：只留中英文数字与 -_（防 `../` 之类的越界写）。"""
    keep = [ch for ch in str(s or "") if ch.isalnum() or ch in "-_" or ord(ch) > 0x2E80]
    return "".join(keep).strip("-_")[:40]


def _mime_of(ext: str) -> str:
    for m, e in MIME_EXT.items():
        if e == ext:
            return m
    return ""


def _sniff_ext(data: bytes) -> str:
    """按文件头认图（有些图床 content-type 是 octet-stream）。"""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ""


def data_uri(rel_path: str, root: Path | None = None) -> str:
    """把 uploads 里的图读成 `data:` URI（**只读**，给预览/导出内联用）。

    为什么内联：预览与导出是同一份 SVG（D13），SVG 里只有内联图片才能在两边都显示，
    也才不会因为"取不到 images/xxx"而破图（R7 踩过）。
    """
    base = root or uploads_root()
    p = (base / rel_path).resolve()
    try:
        p.relative_to(base.resolve())          # 越界路径直接拒
    except ValueError:
        return ""
    if not p.is_file():
        return ""
    ext = p.suffix.lower()
    mime = _mime_of(ext) or "image/png"
    try:
        b = p.read_bytes()
    except OSError:
        return ""
    if not b or len(b) > MAX_BYTES:
        return ""
    return f"data:{mime};base64," + base64.b64encode(b).decode("ascii")


def search(query: str, *, provider: str = "", limit: int = 6) -> dict:
    """联网搜图（走 ppt-master 的 `image_search.py`）。

    **不假装能搜**：没 key / 图源连不上时，如实返回 `{ok: False, error, providers}`，
    并提示"让同事的 agent 直接给 URL"这条不需要 key 的路。
    """
    st = provider_status()
    want = str(provider or "").strip()
    usable = st["usable"] if not want else [want] if want in st["usable"] else []
    if not usable:
        return {"ok": False, "error": st["hint"], "providers": st["providers"], "usable": []}
    from . import ppt_polish, slide_deck   # 走它的脚本；不在导入期拉依赖
    scripts = ppt_polish.scripts_dir()
    tool = scripts / "image_search.py"
    if not tool.is_file():
        return {"ok": False, "error": f"ppt-master 未就绪（{tool}）", "providers": st["providers"]}
    job = ppt_polish.workroot() / f"imgsearch-{slide_deck.safe_name(str(query))[:16]}"
    job.mkdir(parents=True, exist_ok=True)
    import json
    import subprocess
    import sys
    cmd = [sys.executable, str(tool), str(query), "--filename", "candidate.jpg",
           "--provider", usable[0], "--orientation", "landscape", "--save-candidates",
           "--max-candidates", str(max(1, min(12, int(limit)))), "-o", str(job)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=120, cwd=str(job))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": f"搜图没跑起来：{exc}", "providers": st["providers"]}
    man = job / "image_sources.json"
    rows = []
    if man.is_file():
        try:
            raw = json.loads(man.read_text(encoding="utf-8"))
            rows = raw.get("candidates") or raw.get("images") or []
        except Exception:  # noqa: BLE001 报告坏了就当没候选
            rows = []
    if not rows:
        tail = " / ".join(ln.strip() for ln in (proc.stdout or "").splitlines()[-2:])
        return {"ok": False, "error": f"没搜到候选（{usable[0]}）。{tail}"[:300],
                "providers": st["providers"], "usable": usable}
    return {"ok": True, "provider": usable[0], "query": str(query), "candidates": rows[:limit],
            "job": str(job)}
