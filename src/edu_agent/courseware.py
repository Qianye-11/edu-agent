# -*- coding: utf-8 -*-
"""课件生成（对话链路）—— 把「教案 / 会话内容」变成原生可编辑 PPTX。

链路：对话里出现**显式指令**（生成课件 / 做一份 PPT …）
    → done 事件带 ``deck=True``（web_server 三处 done 都挂）
    → 前端自动调 ``POST /api/ppt/courseware``
    → 本模块把内容转成「4 种版式」的页面数据 → ``ppt_master.generate()``（WSL 渲染原生 pptx）
    → ``register_download()`` 发一次性令牌 → 前端出「下载课件」链接。

三条设计取舍（别改，改前先读 docs/20）：
1. **只认显式指令** —— ``wants_deck()`` 要求「动作词 + 课件/PPT」相邻，并对
   「怎么做PPT」这类疑问句做否定前置。普通答疑**一个按钮都不多**：老师明确讨厌每条
   回答弹卡片（docs/12 顶部现状变更 + docs/16 §5.5）。
2. **零重复实现** —— 教案 Markdown → 页结构一律走 ``wps_export.slides_from_markdown()``
   （与「预览 PPT / 导出 PPT · 本地」同一个解析），本模块只负责「页结构 → 版式」映射。
3. **落盘过权限闸门** —— 复用 WPS 导出那个动作 id ``export_pptx``（不新造动作、不绕过
   ``permissions.guard``）。他现在的策略是 ask → 第一次会要求确认一次。
"""
from __future__ import annotations

import re

from edu_agent import ppt_master, wps_export

MAX_BULLETS_PER_PAGE = 6      # 与桥接层 bullets 版式上限一致
MAX_CARDS_PER_PAGE = 3        # 与桥接层 cards 版式上限一致

# 「动作词 + 课件/PPT」。两类动作词分开处理（2026-09-26 实测踩过）：
#   · 强动作词（生成/做/导出…）：本身就是指令，后面跟「吗」也算要（「能生成课件吗？」是请求）；
#   · 弱动作词（要/想要/来个）：老师原话「帮我备一节 3.3 幂函数的课，**要课件**」属这类，
#     但「要课件吗」是问句 —— 所以弱词后面紧跟疑问语气时不认。
# 两类都带否定前置，挡住「怎么做PPT / 如何生成课件 / 为啥要做PPT」。
_STRONG = r"(?:生成|做个|做一个|做一份|整一份|整一个|导出|出一份|来一份|搞一份|做|出)"
_WEAK = r"(?:要|想要|来个|来份)"
_TAIL = r"(?:一)?(?:份|个)?(?:成|为|到)?\s*(?:课件|ppt|幻灯片|演示文稿)"
DECK_RE = re.compile(r"(?<![怎么如何咋啥])" + _STRONG + _TAIL, re.I)
DECK_WEAK_RE = re.compile(r"(?<![怎么如何咋啥])" + _WEAK + _TAIL + r"(?!\s*[吗呢?？])", re.I)

# 卡片拆分：句读前 ≤14 字就当小标题，剩下的当正文
_SPLIT_RE = re.compile(r"^(.{2,14}?)[：:，,。；;、]\s*(.+)$")


def wants_deck(text: str) -> bool:
    """这句话是不是「显式要课件」的指令（决定要不要给「导出课件」入口）。"""
    t = text or ""
    return bool(DECK_RE.search(t) or DECK_WEAK_RE.search(t))


def _is_long(s: str, n: int = 24) -> bool:
    return len(re.sub(r"\s", "", str(s or ""))) > n


def _card(point: str) -> dict:
    """一条要点 → 一张卡片 {heading, body}。短的就只当标题（渲染层会自动居中）。"""
    p = str(point or "").strip()
    m = _SPLIT_RE.match(p)
    if m and _is_long(p):
        return {"heading": m.group(1).strip(), "body": [m.group(2).strip()]}
    return {"heading": p, "body": []}


def _chunks(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)] or [[]]


def pages_from_markdown(md: str, title: str = "", subtitle: str = "") -> list[dict]:
    """教案 Markdown → 页面数据：封面 + 每个环节一页（要点 ≤6 条，超了自动分页）。

    页结构由 ``wps_export.slides_from_markdown`` 给出（课题 + slides[{name,minutes,bullets}]），
    所以这里生成的页数与「预览 PPT / 导出 PPT · 本地」看到的完全一致。
    """
    data: dict = {}
    if str(md or "").strip():
        try:
            data = wps_export.slides_from_markdown(md) or {}
        except Exception:  # noqa: BLE001 解析失败不该让对话/接口 500
            data = {}
    slides = [s for s in (data.get("slides") or []) if isinstance(s, dict)]
    topic = str(title or "").strip() or str(data.get("title") or "").strip() or "课堂课件"
    pages: list[dict] = [{"layout": "cover", "title": topic,
                          "subtitle": str(subtitle or "").strip() or "课堂课件"}]
    for s in slides:
        name = str(s.get("name") or "").strip() or "内容"
        mins = str(s.get("minutes") or "").strip()
        head = (name + ("（" + mins + " 分钟）" if mins and "分钟" not in name else ""))
        bullets = [str(b).strip() for b in (s.get("bullets") or []) if str(b).strip()]
        if not bullets:                                  # 空环节 → 章节页，别出空白要点页
            pages.append({"layout": "section", "title": head})
            continue
        for i, group in enumerate(_chunks(bullets, MAX_BULLETS_PER_PAGE)):
            pages.append({"layout": "bullets",
                          "title": head if i == 0 else head + "（续）",
                          "items": group})
    return pages


def pages_from_payload(payload: dict, title: str = "", subtitle: str = "") -> list[dict]:
    """教案中心 DZ 结构 → 页面数据：封面 + 每个环节一页卡片（≤3 张/页，超了分页）。

    payload 形状（前端 localStorage `edu_design_v1`）：
        {title, grade, steps: [{name, minutes, points: [..]}]}
    """
    p = payload if isinstance(payload, dict) else {}
    steps = [s for s in (p.get("steps") or []) if isinstance(s, dict)]
    topic = str(title or p.get("title") or "").strip() or "课堂课件"
    grade = str(p.get("grade") or "").strip()
    pages: list[dict] = [{"layout": "cover", "title": topic,
                          "subtitle": str(subtitle or "").strip() or
                                      (grade + " · 课堂课件" if grade else "课堂课件")}]
    for st in steps:
        name = str(st.get("name") or "").strip() or "环节"
        mins = str(st.get("minutes") or "").strip()
        head = name + ("（" + mins + " 分钟）" if mins and "分钟" not in name else "")
        pts = [str(x).strip() for x in (st.get("points") or []) if str(x).strip()]
        if not pts:
            pages.append({"layout": "section", "title": head})
            continue
        for i, group in enumerate(_chunks(pts, MAX_CARDS_PER_PAGE)):
            pages.append({"layout": "cards",
                          "title": head if i == 0 else head + "（续）",
                          "items": [_card(x) for x in group]})
    return pages


def pages_for(markdown: str = "", payload: dict | None = None, title: str = "",
              subtitle: str = "") -> list[dict]:
    """结构化 payload 优先（教案中心），否则用 Markdown（会话里那份教案）。"""
    if isinstance(payload, dict) and (payload.get("steps") or payload.get("title")):
        return pages_from_payload(payload, title=title, subtitle=subtitle)
    return pages_from_markdown(markdown, title=title, subtitle=subtitle)


def generate(markdown: str = "", payload: dict | None = None, title: str = "",
             out_dir: str | None = None) -> dict:
    """内容 → 原生 pptx。返回 ``{ok, pptx, pages, gate, title, ...}``（不落库、不发令牌）。"""
    pages = pages_for(markdown=markdown, payload=payload, title=title)
    if len(pages) < 2:
        return {"ok": False, "error": "内容太少（没解析出可成页的板块），先在对话里出一份教案或要点"}
    topic = str(title or "").strip() or pages[0].get("title") or "课堂课件"
    name = re.sub(r"[^\w\u4e00-\u9fa5-]+", "-", topic).strip("-")[:40] or "courseware"
    r = ppt_master.generate(pages, name=name, out_dir=out_dir)
    if r.get("ok"):
        r["title"] = topic
        r["page_count"] = len(pages)
    return r


def available() -> tuple[bool, str]:
    """课件能力是否可用（引擎 + WSL 就绪）。"""
    return ppt_master.available()
