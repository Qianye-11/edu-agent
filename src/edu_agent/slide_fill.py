"""填充器（docs/19 §10）：**教案内容 → 模板槽位**，产出一份"模板的母版/版式 + 会话的文字"的 pptx。

规则是**确定性**的（§10.1）：按角色匹配，模板有什么角色用什么角色，**不硬造**封面/结尾。

页数不匹配（§10.2）：多 → 复制模板自己的内容页（母版继承不变）；少 → 默认删掉多余页。

填完必须过两道校验（§10.3）：
1. **内容零丢失**：我们打算写进去的每一串文字，都要在产物里找得到（缺一个即失败）；
2. **容量自查**：每个槽的容量（每行字数 × 行数）vs 实际填入，超出的页**列出来**（提示，不阻断）。

落盘：产物进 `data/ppt_out/`（或老师选的目录），中间件进 `data/tmp/ppt_jobs/fill-<时间戳>/`；
**模板原稿只读**（在副本上改），`content/plans/` 一个字不写。
"""

from __future__ import annotations

import copy
import re
import subprocess
import sys
import time
from pathlib import Path

from . import ppt_polish
from . import slide_deck
from . import slide_template as st

#: 模板页角色判定：按版式名 + 槽位角色猜
_COVER_HINTS = ("cover", "封面", "title slide", "首页")
_END_HINTS = ("end", "结尾", "thank", "谢谢", "closing")
_SECTION_HINTS = ("section", "章节", "目录", "agenda", "过渡")


def _is_role(name: str, hints: tuple[str, ...]) -> bool:
    low = (name or "").lower()
    return any(h in low for h in hints)


def template_pages(template_id: str) -> tuple[list[dict], str]:
    """读模板每一页的槽位 → `(每页 [{index, layout, slots}], 错误信息)`。

    **现场重新分类**（不是只读导入时那份 `slots.json`）：分类规则改了以后，老模板自动用新规则，
    不用重新导入一次（踩过：改了分类但计划没变，因为读的是旧缓存）。
    原稿不在时才退回缓存的 `slots.json`。
    """
    tpl_dir = st.root() / st._slug(template_id)                        # noqa: SLF001
    pptx = tpl_dir / "template.pptx"
    if pptx.is_file():
        fresh = st.slots_from_pptx(pptx)
        if fresh.get("slides"):
            return fresh["slides"], ""
        if fresh.get("error"):
            cached = st._read_json(tpl_dir / "slots.json")             # noqa: SLF001
            return cached.get("slides") or [], str(fresh["error"])
    cached = st._read_json(tpl_dir / "slots.json")                     # noqa: SLF001
    pages = cached.get("slides") or []
    if not pages and cached.get("error"):
        return [], str(cached["error"])
    return pages, ""


def _fit_text(text: str, cap: int) -> tuple[str, bool]:
    """**适度压缩**（不是极简）：超容量时按"先留主干"的顺序砍

    1. 去掉成对括号里的补充说明（（…）/(…)）；
    2. 按「，、；」切分，从**末尾**逐段去掉，直到放得下；
    3. 仍放不下才硬截到容量上限（并交给调用方记进 adjustments，属于最后手段）。

    注意：现在容量是按"允许缩到的最小字号"算的，所以走到第 3 步很少见；
    **砍掉一半以上就算"太过于精简"**，调用方应当改成换一页放（`LOSS_LIMIT`）。
    """
    s = str(text or "").strip()
    if cap <= 0 or len(s) <= cap:
        return s, False
    trimmed = s
    for a, b in (("（", "）"), ("(", ")")):
        while a in trimmed and b in trimmed[trimmed.index(a):]:
            i = trimmed.index(a)
            j = trimmed.find(b, i)
            if j < 0:
                break
            trimmed = (trimmed[:i] + trimmed[j + 1:]).strip()
    if len(trimmed) <= cap:
        return trimmed, True
    for sep in ("；", "。", "，", "、"):
        if len(trimmed) <= cap:
            break
        parts = [p for p in trimmed.split(sep) if p.strip()]
        while len(parts) > 1 and len(sep.join(parts)) > cap:
            parts.pop()
        trimmed = sep.join(parts)
    if len(trimmed) <= cap:
        return trimmed, True
    return trimmed[:cap], True


#: 压缩后剩下的字数低于原文这个比例 → 认为"太过于精简"，宁可换一页放（老师说不要容量不足）
LOSS_LIMIT = 0.6


def _too_lossy(text: str, fitted: str) -> bool:
    return bool(text) and len(fitted) < len(text) * LOSS_LIMIT


def _paginate(items: list[str], n_slots: int, caps: list[int], out: dict) -> list[list[str]]:
    """（旧口径，保留给外部调用）把条目按容量分页：每页最多 `n_slots` 条，每条不超过对应槽的容量。"""
    cap_pages = [list(caps) for _ in range(max(1, n_slots // max(1, len(caps)) + 1))] if caps else [[]]
    return _paginate_pages(items, cap_pages or [[]], out)


def _paginate_pages(items: list[str], cap_pages: list[list[int]], out: dict) -> list[list[str]]:
    """把条目分页到**指定的若干页**上：第 i 页用 `cap_pages[i]` 的槽容量，用完了循环用最后一份。

    为什么要按"每页自己的槽"分（踩过）：老实现拿**模板第一张内容页**（常常是目录页，3 个 20 字的框）
    的容量去分**所有**页 —— 于是讲义里 40 字的一条被压成 3 个字，页数从 10 页涨到 19 页，
    老师看到的就是"正文没填进去 + 一堆空白页"。
    """
    pages: list[list[str]] = [[]]
    if not cap_pages or not any(cap_pages[0]):
        return [list(items)] if items else [[]]
    cur = [c for c in cap_pages[0] if c > 0]
    slot = 0
    for raw in items:
        text = str(raw or "").strip()
        if not text:
            continue
        if slot >= len(cur):                          # 本页槽用尽 → 另起一页
            pages.append([])
            slot = 0
            nxt = len(pages) - 1
            cur = [c for c in (cap_pages[nxt] if nxt < len(cap_pages) else cap_pages[-1]) if c > 0]
            out.setdefault("adjustments", []).append(
                {"action": "new-page", "why": "本页正文槽已用尽", "text": text[:20]})
        cap = max(cur) if cur else 0                  # 落位由 _page_fill 按"最合适的框"决定，这里只判放不放得下
        fitted, changed = _fit_text(text, cap)
        if changed and _too_lossy(text, fitted):      # 砍一半以上 → 宁可换一页放整条
            pages.append([])
            slot = 0
            nxt = len(pages) - 1
            cur = [c for c in (cap_pages[nxt] if nxt < len(cap_pages) else cap_pages[-1]) if c > 0]
            out.setdefault("adjustments", []).append(
                {"action": "new-page", "why": "这一条本页放不下（不砍字数）", "text": text[:20]})
            cap = max(cur) if cur else 0
            fitted, changed = _fit_text(text, cap)
            if changed and _too_lossy(text, fitted):  # 换页也放不下（模板的框就这么大）→ 只能中度压缩
                out.setdefault("adjustments", []).append(
                    {"action": "too-long", "cap": cap, "before": len(text), "after": len(fitted),
                     "text": text[:24]})
        if changed:
            out.setdefault("adjustments", []).append(
                {"action": "trim", "cap": cap, "before": len(text), "after": len(fitted),
                 "text": fitted[:24]})
        pages[-1].append(fitted)
        slot += 1
    return [p for p in pages if p] or [[]]


def _title_sample(page: dict) -> str:
    for s in page.get("slots") or []:
        if (s.get("role") or "") == "title":
            return str(s.get("sample") or "")
    return ""


def _looks_like_cover(page: dict) -> bool:
    """设计稿模板的版式名往往是空的（`layout=""`），靠名字认不出封面 —— 用结构认。

    封面的样子：标题框是**空的**（设计师留白，等老师写），而且几乎没有正文框。
    """
    if len(_body_caps(page)) > 1:
        return False
    head = _title_sample(page).strip()
    big = [s for s in (page.get("slots") or []) if (s.get("role") or "") == "title"]
    return not head or any(int(s.get("capacity") or 0) >= 60 for s in big)


def _looks_like_end(page: dict) -> bool:
    """结尾页：标题/正文里带"谢谢、感谢、再见"这类话（设计稿模板只能这么认）。"""
    text = " ".join([_title_sample(page)] + [str(s.get("sample") or "")
                                             for s in (page.get("slots") or [])])
    return _is_role(text, ("谢谢", "感谢", "再见", "thank", "the end"))


def _body_caps(page: dict) -> list[int]:
    """一页模板的正文槽容量（按阅读顺序）。"""
    return [int(s.get("capacity") or 0) for s in (page.get("slots") or [])
            if (s.get("role") or "") == "body" and int(s.get("capacity") or 0) > 0]


def _best_donor(content_pages: list[dict]) -> dict | None:
    """挑"最能装字"的内容页当续页母版（装不下的条要复制它）。

    为什么不固定用第一张（踩过）：模板第一张内容页常常是目录页（3 个 20 字的框），
    拿它当母版 → 续页只能装 3 条短句，页数暴涨。
    """
    if not content_pages:
        return None
    return max(content_pages, key=lambda p: sum(_body_caps(p)))


def plan(template_id: str, slides: list[dict] | None, title: str = "") -> dict:
    """算出"哪一页填到模板的哪一页、每个槽写什么"——**只算不写**（可单测、可给面板预览）。"""
    items = slide_deck.clean_slides(slides)
    pages, err = template_pages(template_id)
    out: dict = {"template_id": template_id, "pages": [], "warnings": [], "degraded": [],
                 "policy": {}}
    if err:
        out["degraded"].append(f"模板槽位读不出来（{err}）→ 只能用它的配色/字体渲染，不改模板文件")
    if not pages:
        out["degraded"].append("模板没有可用槽位 → 退回「只用配色」模式（页面用我们的 6 个版式）")
        return out

    cover_pages = [p for p in pages if _is_role(p.get("layout", ""), _COVER_HINTS)]
    end_pages = [p for p in pages if _is_role(p.get("layout", ""), _END_HINTS)]
    if not cover_pages and pages and _looks_like_cover(pages[0]):      # 版式名是空的 → 按结构认
        cover_pages = [pages[0]]
    if not end_pages:
        end_pages = [p for p in pages if p not in cover_pages and _looks_like_end(p)][:1]
    content_pages = [p for p in pages
                     if p not in cover_pages and p not in end_pages]

    # 会话这几页各自的"角色"（用与渲染同一套规则判，避免两处口径不一）
    roles = [slide_deck.pick_layout(s, i, len(items)) for i, s in enumerate(items)]
    cover_slide = items[0] if roles and roles[0] == "cover" else None
    body_slides = [(i, s) for i, s in enumerate(items) if not (i == 0 and cover_slide is not None)]

    # 1) 封面：模板有封面页才填，没有就"不硬造"
    if cover_slide is not None:
        if cover_pages:
            out["pages"].append(_page_fill(cover_pages[0], {
                "title": cover_slide.get("cover_title") or title or cover_slide.get("name") or "",
                "subtitle": cover_slide.get("subtitle") or "",
                "meta": cover_slide.get("meta") or "",
                "notes": cover_slide.get("notes") or "",
            }, out))
        else:
            out["degraded"].append("模板没有封面页 → 封面内容并入第一张内容页（不硬造封面）")
            if content_pages:
                body_slides.insert(0, (0, cover_slide))
            else:
                out["warnings"].append("模板一页都没有，无法填充")

    # 2) 内容页：逐页顺序对应；**按这一页自己的正文槽容量**分页，放不下才另起一页（复制模板的内容页）
    donor = _best_donor(content_pages) or (cover_pages[0] if cover_pages else pages[0])
    donor_caps = _body_caps(donor)
    for k, (idx, s) in enumerate(body_slides):
        items = [b["text"] for b in (slide_deck._blocks(s) or [])] or slide_deck._bullets(s)  # noqa: SLF001
        target = content_pages[k] if k < len(content_pages) else donor
        groups = _paginate_pages(items, [_body_caps(target), donor_caps], out)
        for gi, group in enumerate(groups):
            if k < len(content_pages) and gi == 0:
                tpl_page, copied = content_pages[k], False
            else:
                tpl_page, copied = donor, True
                out["policy"]["copied"] = int(out["policy"].get("copied") or 0) + 1
            page_title = (s.get("name") or s.get("title") or "")
            if gi > 0:
                page_title = f"{page_title}（续 {gi + 1}）"
            out["pages"].append(_page_fill(tpl_page, {
                "title": page_title,
                "body": group,
                "minutes": str(s.get("minutes") or "") if gi == 0 else "",
                "notes": s.get("notes") or "",          # R16：讲稿原样带走（续页也带同一份）
            }, out, copied=copied))

    # 3) 页数：模板里**没被计划用到的页**都删掉（含没用上的封面/结尾页）
    used = {p.get("template_index") for p in out["pages"] if not p.get("copied")}
    unused = [i for i in range(len(pages)) if i not in used]
    if unused:
        out["policy"]["deleted"] = len(unused)
        out["policy"]["delete_indices"] = unused
        out["warnings"].append(f"模板里有 {len(unused)} 页没被用到（含没用上的封面/结尾页）→ 默认删掉"
                               f"（想保留空页就说一声）")
    if out["policy"].get("copied"):
        out["warnings"].append(f"会话比模板多 → 复制了模板自己的内容页 {out['policy']['copied']} 张"
                               f"（母版/版式继承不变）")
    _plan_page_numbers(out, pages)                # 页码跟着页走（含复制页/重排后的页号）
    _warn_kept_texts(out)                         # 如实告诉老师"模板里还留着几处设计文字"
    return out


def _plan_page_numbers(out: dict, tpl_pages: list[dict]) -> None:
    """给每一页补一条"页码"填充：**按成品页号重写**模板里那个页码框。

    为什么必须做（老师反馈的 bug）：页码在设计稿里是普通文本框，被我们归成"装饰"（不许动），
    于是"教案页数比模板多"时，整页克隆出来的续页会一直显示母版的「06 / 10」——
    老师看到的就是"模板原来的内容没清除"。页码本来就应该跟着页走。

    注意：计划页里**不带** `slots`（只有 fills/kept），所以这里拿 `tpl_pages[template_index]` 去查。

    R14：装饰槽现在也在 `fills` 里（`kind: 'decoration'`），**原地改那一条**，别再追加一条
    （追加会让同一个形状被写两遍，明细里也会出现两行）。
    """
    by_index = {p.get("index"): p for p in tpl_pages or []}
    pages = out.get("pages") or []
    total = len(pages)
    for i, page in enumerate(pages):
        tpl = by_index.get(page.get("template_index"))
        if not tpl:
            continue
        for slot in (tpl.get("slots") or []):
            sample = str(slot.get("sample") or "")
            if not st.is_page_number(sample):
                continue
            text = st.page_number_text(sample, i, total)
            if text == sample:
                continue
            hit = next((f for f in (page.get("fills") or [])
                        if f.get("shape_index") == slot.get("shape_index")
                        and str(f.get("kind") or "") in ("decoration", "page-number")), None)
            if hit is not None:
                hit["text"] = text
                hit["kind"] = "page-number"
                hit["clear"] = True
                continue
            page.setdefault("fills", []).append(
                {"role": "decoration", "capacity": int(slot.get("capacity") or 0), "text": text,
                 "clear": True, "kind": "page-number", "shape_index": slot.get("shape_index"),
                 "size_px": slot.get("size_px") or 0,
                 "design_capacity": int(slot.get("design_capacity") or 0),
                 "sample": sample, "geo": slot.get("geo") or {}})


def _warn_kept_texts(out: dict) -> None:
    """把"模板里还留着哪些字、为什么留着"写进 warnings（老师不必猜）。"""
    kept: list[str] = []
    for page in out.get("pages") or []:
        for t in (page.get("kept") or []):
            t = str(t).strip()
            if t and t not in kept:
                kept.append(t)
    if not kept:
        return
    shown = "、".join("「%s」" % t[:10] for t in kept[:4])
    out["warnings"].append(
        f"模板里保留了 {len(kept)} 处**设计文字**（原样没动）：{shown}"
        f"{'…' if len(kept) > 4 else ''} —— 这些是页码/章节号一类的版式元素，"
        f"要改要删就在幻灯片页签上点它（现在点得着了）")


def _slot_rank(slot: dict) -> tuple[float, float, float]:
    """排框的优先级：**面积大的先**，并列时靠上、靠左的先（阅读顺序）。"""
    geo = slot.get("geo") or {}
    area = float(geo.get("w") or 0) * float(geo.get("h") or 0)
    return (-area, float(geo.get("y") or 0), float(geo.get("x") or 0))


def _assign_body(body_slots: list[dict], texts: list[str], out: dict,
                 tpl_page: dict) -> list[tuple[dict, str]]:
    """把每条正文放进**最合适的框**：装得下它的框里挑**最大**的那个（并列取靠上靠左）；没一个装得下就用容量最大的。

    为什么不是"一条一个框按顺序"（踩过）：卡片式模板一页有 4 组「小标题框 + 正文框」
    （小标题框容量 15、正文框容量 144），按顺序填会把整段正文塞进小标题框，
    再被压成十几个字 —— 老师看到的就是"乱填"+"正文没填进去"。
    为什么不是"挑刚好装得下的最小框"（也踩过）：游戏页上有个 121×130 的小框压在「圆」图形底下，
    按"最小且装得下"选就会把整段话塞到图形上，糊成一团。
    空着的正文框**清空**（模板示例文字属于内容，留着就和我们的混在一起了）。
    """
    pairs: list[tuple[dict, str]] = []
    free = list(body_slots)
    for text in texts:
        fit = [s for s in free if int(s.get("capacity") or 0) >= len(text)]
        pool = fit or free
        if not pool:
            break
        slot = min(pool, key=lambda s: _slot_rank(s)) if fit else \
            max(pool, key=lambda s: int(s.get("capacity") or 0))
        free.remove(slot)
        pairs.append((slot, text))
    left = len(texts) - len(pairs)
    if left > 0:
        if body_slots:
            out["warnings"].append(f"第 {tpl_page.get('index', 0) + 1} 页模板只有 {len(body_slots)} 个正文框，"
                                   f"{left} 条没排下（已尽量换页，请确认）")
        else:
            out["warnings"].append(f"第 {tpl_page.get('index', 0) + 1} 页模板没有可用正文框 → {len(texts)} 条没填进去"
                                   f"（内容没删，请手动确认）")
    pairs.extend((slot, "") for slot in free)          # 没填的框清空模板示例文字
    return pairs


def _page_fill(tpl_page: dict, data: dict, out: dict, *, copied: bool = False) -> dict:
    """把一页数据分配到模板页的槽上：标题槽 1 个、正文槽按阅读顺序一条一个，其余内容槽**清空**。

    `docs/19` §10.1 的映射在这里落地；设计稿（没有占位符的模板）靠 `classify_slots` 分好的
    `title/body/decoration` 三类，**装饰槽一个字都不动**。
    """
    slots = tpl_page.get("slots") or []
    fills: list[dict] = []
    body_texts = [t for t in (data.get("body") or []) if str(t).strip()]
    title = str(data.get("title") or "")
    sub = str(data.get("subtitle") or "")
    meta = str(data.get("meta") or "")
    minutes = str(data.get("minutes") or "")

    title_slots = [s for s in slots if (s.get("role") or "") == "title"]
    body_slots = [s for s in slots if (s.get("role") or "") == "body"]
    deco = [s for s in slots if (s.get("role") or "") == "decoration"]

    def emit(slot: dict, text: str, *, clear: bool = False) -> None:
        cap = int(slot.get("capacity") or 0)
        role = slot.get("role") or "body"
        if text and cap > 0 and len(text) > cap:      # 兜底：规划阶段理应已经压到容量内
            fitted, changed = _fit_text(text, cap)
            if changed:
                out.setdefault("adjustments", []).append(
                    {"action": "trim", "where": f"第 {tpl_page.get('index', 0) + 1} 页", "cap": cap,
                     "before": len(text), "after": len(fitted), "text": fitted[:24]})
                text = fitted
        fills.append({"role": role, "capacity": cap, "text": text, "clear": bool(clear),
                      "shape_index": slot.get("shape_index"),
                      "size_px": slot.get("size_px") or 0,
                      "design_capacity": int(slot.get("design_capacity") or 0),
                      "sample": slot.get("sample") or "", "geo": slot.get("geo") or {}})

    # ① 标题：第一个标题槽；副标题/元信息**并进正文槽**（模板没有更多槽时，别硬造、也别丢字）
    title_text = title or sub or meta
    if title_slots:
        emit(title_slots[0], title_text)
        for extra in title_slots[1:]:
            emit(extra, "", clear=True)
        for extra_text in ([sub] if sub and title else []) + ([meta] if meta and title else []):
            if body_slots:
                body_texts.append(extra_text)
            else:
                out["warnings"].append(f"第 {tpl_page.get('index', 0) + 1} 页模板没有多余文本框，"
                                       f"副标题「{extra_text[:16]}」没地方放（没丢，只是没显示）")
    elif body_slots:                       # 没有标题槽：正文第一条当标题（并把它从正文里去掉）
        first = body_texts.pop(0) if body_texts else (title or sub or meta)
        emit(body_slots[0], first)
        body_slots = body_slots[1:]

    # ② 正文：每条挑"装得下它、又最小"的那个框（best-fit），**按阅读顺序**用框
    rest = _assign_body(body_slots, body_texts, out, tpl_page)
    for slot, value in rest:
        emit(slot, value, clear=(value == ""))

    if minutes:
        out.setdefault("notes", []).append(f"第 {tpl_page.get('index', 0) + 1} 页时长 {minutes} 分钟"
                                           f"（模板没有时间槽，写进备注即可）")
    # ③ 装饰槽（页码、设计标签）：**默认一个字都不动**，但要让老师点得着、改得动（R14 硬要求）。
    #    所以照样进 `fills`（`kind: 'decoration'`、`text` = 模板原样），只是不填我们的内容；
    #    老师改了/清空了才生效 —— "模板残留看不见也改不掉"从根上没了。
    for slot in deco:
        sample = str(slot.get("sample") or "")
        if not sample.strip():
            continue
        fills.append({"role": "decoration", "kind": "decoration",
                      "capacity": int(slot.get("capacity") or 0), "text": sample, "clear": True,
                      "shape_index": slot.get("shape_index"), "size_px": slot.get("size_px") or 0,
                      "design_capacity": int(slot.get("design_capacity") or 0),
                      "sample": sample, "geo": slot.get("geo") or {}})
    # ④ 备注（R16）：老师写的讲稿原样带走；没写就把这一页的时长写进备注（信息不丢）
    note = str(data.get("notes") or "").strip() or (f"{minutes} 分钟" if minutes else "")
    return {"template_index": tpl_page.get("index", 0), "layout": tpl_page.get("layout") or "",
            "copied": copied, "fills": fills, "note": note,
            "kept": [s.get("sample") for s in deco if s.get("sample")],
            "replaced": [{"sample": f.get("sample"), "text": f["text"]}
                         for f in fills if f["text"] and f.get("sample")
                         and f["text"] != f.get("sample")]}


# ---------------------------------------------------------------- 真正写文件

def preview_pages(pptx: Path, job: Path, *, limit: int = 12) -> list[str]:
    """把填好的 pptx 转成 SVG，让面板能看到**填出来的真实样子**（老师最关心的那件事）。

    用 ppt-master 的 `pptx_to_svg.py`（约 2 秒）；图片**内联成 data URI**，这样面板里单独显示
    这些 SVG 时不会因为取不到 `images/...` 而破图。转不出来就返回空列表（预览退回我们的版式，不报错）。
    """
    import base64
    import mimetypes
    import re as _re

    scripts = ppt_polish.scripts_dir()
    tool = scripts / "pptx_to_svg.py"
    if not tool.is_file():
        return []
    out_dir = job / "svg_preview"
    cmd = [sys.executable, str(tool), str(pptx), "-o", str(out_dir), "--inheritance-mode", "both"]
    try:
        subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=300, cwd=str(job))
    except (OSError, subprocess.TimeoutExpired):
        return []
    files: list[Path] = []
    for sub in ("svg-flat", "svg"):
        files = sorted((out_dir / sub).glob("slide_*.svg"))
        if files:
            break
    pages: list[str] = []
    for f in files[:limit]:
        try:
            svg = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        def _inline(m: "_re.Match[str]") -> str:
            rel = m.group(2)
            # 图片在 <out>/images/ 下（不是 svg/ 或 svg-flat/ 旁边）——踩过：算错相对目录 → 一个都没内联 → 预览没背景
            cands = [(f.parent / rel).resolve(), (out_dir / rel).resolve(),
                     (out_dir / Path(rel).name).resolve(), (out_dir / "images" / Path(rel).name).resolve()]
            for img in cands:
                try:
                    if not img.is_file():
                        continue
                    raw = img.read_bytes()
                    mime = mimetypes.guess_type(img.name)[0] or "image/png"
                    if len(raw) > 400_000:          # 老师的设计稿底图常有 10MB+ → **为预览压小**再内联
                        try:
                            import io as _io

                            from PIL import Image

                            im = Image.open(_io.BytesIO(raw)).convert("RGB")
                            if im.width > 1280:
                                im = im.resize((1280, round(im.height * 1280 / im.width)))
                            buf = _io.BytesIO()
                            im.save(buf, format="JPEG", quality=80, optimize=True)
                            raw, mime = buf.getvalue(), "image/jpeg"
                        except Exception:  # noqa: BLE001 压不了就用原图（真拿不到就跳过）
                            if len(raw) > 8_000_000:
                                continue
                    b64 = base64.b64encode(raw).decode("ascii")
                    return f'{m.group(1)}="data:{mime};base64,{b64}"'
                except OSError:
                    continue
            return m.group(0)

        svg = _re.sub(r'(xlink:href|href)="((?!data:|https?:|#)[^"]+)"', _inline, svg)
        pages.append(svg)
    return pages


def fill_pptx(template_id: str, slides: list[dict] | None, title: str = "", *,
              out_dir: str | Path | None = None, with_preview: bool = True) -> dict:
    """按计划填一份 pptx（在副本上写；模板原稿只读）。"""
    src = st.root() / st._slug(template_id) / "template.pptx"      # noqa: SLF001
    if not src.is_file():
        return {"ok": False, "error": f"这个模板没有原稿文件：{src}（只有内置数值模板？）"}
    the_plan = plan(template_id, slides, title)
    if not the_plan["pages"]:
        return {"ok": False, "error": "模板没有可用槽位，无法填充（可改用它的配色渲染）",
                "plan": the_plan}

    stamp = time.strftime("%Y%m%d-%H%M%S")
    job = ppt_polish.workroot() / f"fill-{stamp}"
    job.mkdir(parents=True, exist_ok=True)
    work = job / "filled.pptx"
    work.write_bytes(src.read_bytes())                 # 副本；原稿一个字节都不动

    try:
        from pptx import Presentation
        from pptx.util import Pt
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"python-pptx 不可用：{exc}"}

    prs = Presentation(str(work))
    want: list[str] = []                               # 我们打算写进去的每一串（校验用）
    try:
        _apply(prs, the_plan, want)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"填充失败：{type(exc).__name__}: {exc}", "plan": the_plan}

    target_dir = Path(out_dir).expanduser().resolve() if out_dir else ppt_polish.outdir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"教案_{slide_deck.safe_name(title or '未命名')}_{stamp}.pptx"
    if target.exists():
        for n in range(2, 100):
            alt = target.with_name(f"{target.stem}-{n}{target.suffix}")
            if not alt.exists():
                target = alt
                break
    prs.save(str(target))

    checks = _verify_fill(target, want, the_plan)
    ok = all(c["ok"] for c in checks if c.get("blocking", True))
    post: dict = {}
    try:                                            # 内核：清动画 + 补 <a:ea>（设计稿的字体一律不动）
        post["normalize"] = ppt_polish.normalize_file(target)
    except Exception as exc:  # noqa: BLE001 内核失败不该让产物不可用
        post["normalize"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    post["checks"] = checks
    preview = preview_pages(target, job) if with_preview else []
    return {"ok": ok, "file": str(target), "filename": target.name, "outDir": str(target_dir),
            "template_id": template_id, "job": str(job), "plan": the_plan,
            "checks": checks, "preview": preview, "preview_pages": len(preview), "postcheck": post,
            "notes_written": sum(1 for p in the_plan["pages"] if (p.get("note") or "").strip()),
            "quality": {"fill": "ok" if ok else "fail", "template": "ok"},
            "detail": "；".join(f"{'✅' if c['ok'] else '❌'} {c['name']}" for c in checks),
            "warnings": the_plan["warnings"] + the_plan["degraded"],
            "download_url": "/api/wps/download?path=" + str(target)}


def _apply(prs, the_plan: dict, want: list[str]) -> None:
    """把计划落到 pptx 上：**按模板页号落位** + 复制页按需新建 + 最后按计划重排顺序。

    为什么不能"计划第 i 页就写第 i 张"（踩过）：模板常有封面页而会话没有 →
    那样会把正文写进封面，还会留下没用的封面/结尾页（页数对不上）。
    所以：先用 `template_index` 精确落位，再一次性算出"最终顺序 = 计划顺序"，其余页连关系一起删掉。
    """
    import copy as _copy

    fills = the_plan["pages"]
    if not fills:
        return
    sld_id_lst = prs.slides._sldIdLst                                   # noqa: SLF001
    slides = list(prs.slides)
    n0 = len(sld_id_lst)

    # 1) **先克隆续页**（母版必须是"还没被填过"的那张，踩过：先填原页再克隆 → 克隆页带着上一页的文字）
    copied_pages = [p for p in fills if p.get("copied")]
    donor_idx = next((p.get("template_index") for p in copied_pages), None)
    donor = slides[donor_idx] if donor_idx is not None and donor_idx < len(slides) else \
        (slides[-1] if slides else None)
    copy_elem: dict[int, object] = {}
    clone_of: dict[int, object] = {}         # id(计划页) → 克隆出来的那张（不能塞进计划里：计划要能 JSON 化）
    for k, page in enumerate(copied_pages):
        if donor is None:
            break
        new = _clone_slide(prs, donor)
        idx = n0 + k
        if idx < len(sld_id_lst):
            copy_elem[id(page)] = sld_id_lst[idx]
        clone_of[id(page)] = new

    # 2) 再写文字：用模板现有页的按 template_index 落位，克隆页写进各自的克隆件
    scale = 1.0
    try:                                             # 模板自己的画布尺寸 → 设计像素的换算比
        scale = float(prs.slide_width) / float(slide_deck.CANVAS_W)
    except (TypeError, ValueError, ZeroDivisionError):
        scale = 1.0
    for page in fills:
        target = clone_of.get(id(page))
        if target is None:
            ti = page.get("template_index")
            if ti is None or ti >= len(slides):
                continue
            target = slides[ti]
        _fill_slide(target, page, want, scale=scale)
        _write_note(target, page.get("note"))       # R16：讲稿写进备注（WPS 里能看到）

    # 3) 最终顺序 = 计划顺序（复制页用刚建的那张），其余模板页删掉
    order = []
    for page in fills:
        el = copy_elem.get(id(page))
        if el is None:
            ti = page.get("template_index")
            if ti is not None and ti < n0:
                el = sld_id_lst[ti]
        if el is not None and el not in order:
            order.append(el)
    keep_ids = {el.rId for el in order}
    for el in list(sld_id_lst):                                         # 先全部摘下，再按计划顺序挂回
        sld_id_lst.remove(el)                                           # noqa: SLF001
    for el in order:
        sld_id_lst.append(el)                                           # noqa: SLF001
    for el in list(sld_id_lst):                                         # 理论上已经没有多余项，稳妥起见
        if el.rId not in keep_ids:
            sld_id_lst.remove(el)                                       # noqa: SLF001
    # 没进最终顺序的模板页：连关系一起丢掉，避免包里留下孤儿页
    used_rids = {el.rId for el in sld_id_lst}                           # noqa: SLF001
    for rel in list(prs.part.rels.values()):
        if rel.reltype.endswith("/slide") and rel.rId not in used_rids:
            try:
                prs.part.drop_rel(rel.rId)
            except KeyError:
                pass


def _clone_slide(prs, donor):
    """**整页克隆**一张幻灯片：底图、图片、装饰形状、页面背景全部照抄，只有文字后面会被换掉。

    为什么必须整页克隆（踩过，老师报的"填充后有空白页、模板背景都没有"）：
    老实现只深拷贝 `has_text_frame` 的形状 → 续页只剩 3 个文本框、0 张图，
    底图/版式装饰全丢，成品里那些页看着就是白页。
    图片是**复用同一份 image part**（`relate_to` 指向原 part），所以 40MB 的设计稿不会翻倍。
    """
    import copy as _copy

    from pptx.oxml.ns import qn

    new = prs.slides.add_slide(donor.slide_layout)
    for shp in list(new.shapes):                       # 清掉 add_slide 从版式带来的空占位符
        shp._element.getparent().remove(shp._element)
    spTree = new.shapes._spTree
    for child in donor.shapes._spTree:                 # 形状树整棵抄（含 <p:pic>、组合、连接线）
        if child.tag in (qn("p:nvGrpSpPr"), qn("p:grpSpPr")):
            continue
        spTree.append(_copy.deepcopy(child))
    bg = donor._element.cSld.find(qn("p:bg"))          # 页面背景（有的设计稿放在这里）
    if bg is not None:
        cSld = new._element.cSld
        old = cSld.find(qn("p:bg"))
        if old is not None:
            cSld.remove(old)
        cSld.insert(list(cSld).index(spTree), _copy.deepcopy(bg))
    _remap_rels(donor, new)                            # 图片/媒体的关系 ID 要重新指一遍
    return new


def _remap_rels(donor, new) -> None:
    """把克隆页里指向母版的 `r:embed`/`r:link`/`r:id` 改写成新页自己的关系 ID。"""
    from pptx.oxml.ns import qn

    rels = dict(donor.part.rels)
    rid_map: dict[str, str] = {}
    for el in new._element.iter():
        for name, val in list(el.attrib.items()):
            if not name.startswith("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"):
                continue
            rel = rels.get(val)
            if rel is None:
                continue
            if val in rid_map:
                el.set(name, rid_map[val])
                continue
            try:
                if rel.is_external:
                    new_rid = new.part.relate_to(rel.target_ref, rel.reltype, is_external=True)
                else:
                    new_rid = new.part.relate_to(rel.target_part, rel.reltype)
            except (KeyError, ValueError, AttributeError):
                continue
            rid_map[val] = new_rid
            el.set(name, new_rid)


def _fill_slide(slide, page: dict, want: list[str], *, scale: float = 1.0) -> None:
    """把一页的填充内容写进这张幻灯片：**按 `shape_index` 精确落位**（槽位分类时记下的位置）。

    为什么要按 index 而不是"再猜一次角色"：填充与导入读的是同一个文件，形状顺序一致；
    按 index 落位能保证"计划里说要填那个框"和"实际填的就是那个框"，也避免二次猜测带来的漂移。
    `sample` 用来核对没拿错框（模板被改过时退化为按角色找）。

    R14 两件事：
    · **几何**（`dx/dy/dw/dh`，设计像素）先落到形状上：老师拖过/改过大小就照他说的来；
    · **装饰字原样时一个字都不写**（`text == sample`）—— 重写会把「06」和「/ 10」两段样式并成一段，
      模板设计就走样了；老师改过（不一样）才写。
    """
    shapes = list(slide.shapes)
    used: set[int] = set()                  # 本页已经写过的形状下标（退路不许抢刚写过的框）
    for fill in page["fills"]:
        idx = fill.get("shape_index")
        shape = None
        if isinstance(idx, int) and 0 <= idx < len(shapes):
            cand = shapes[idx]
            if getattr(cand, "has_text_frame", False):
                sample = (cand.text_frame.text or "").strip()[:40]
                if not fill.get("sample") or sample == fill["sample"] or not sample:
                    shape, _ = cand, used.add(idx)
        if shape is None:                       # 退路：按顺序找第一个没用过的文本框
            if (fill.get("role") or "") == "decoration":
                continue                        # 装饰槽**绝不**抢别人的框（宁可不动）
            for k, shp in enumerate(shapes):
                if k in used or not getattr(shp, "has_text_frame", False):
                    continue
                shape, _ = shp, used.add(k)
                break
        if shape is None:
            continue
        _apply_geo(shape, fill, scale)
        _apply_z(shape, int(fill.get("z") or 0))
        _apply_style(shape, fill, scale)      # 样式先落：文字原样时也要能改颜色/字体（R15）
        text = fill.get("text") or ""
        if not text and not fill.get("clear"):
            continue                            # 没内容且不需要清空 → 不动模板的框
        if text and text == (fill.get("sample") or ""):
            continue                            # 原样（装饰/页码没变）→ 一个字都不动
        _write_text(shape, text, want, fill)


def _write_note(slide, text: str) -> None:
    """把一页的讲稿写进 pptx 的备注（R16）：WPS 里打开就能看到"这一页要讲什么"。

    空文本不建备注页（不给模板添无用的 notesSlide）；写不进去不抛异常（备注不该拖垮填充），
    但 `_verify_fill` 会**读回来核对**，真写丢了会在结果条里报出来。
    """
    text = str(text or "").strip()
    if not text:
        return
    try:
        slide.notes_slide.notes_text_frame.text = text
    except Exception:  # noqa: BLE001 个别模板的备注页结构异常
        pass


def _note_of(slide) -> str:
    try:
        return (slide.notes_slide.notes_text_frame.text or "").strip()
    except Exception:  # noqa: BLE001 没有备注页
        return ""


def _apply_geo(shape, fill: dict, scale: float = 1.0) -> None:
    """把老师的拖动/改大小落到形状上（设计像素 × 模板缩放 = EMU）。

    为什么要有 `scale`：`geo` 与 `dx/dy` 都是**1280×720 设计坐标**，而老师的模板可能是
    别的尺寸（比如 960×540 或 1920×1080）；不换算就会出现"拖 60px，落下去只有 45px"。
    """
    dx, dy = float(fill.get("dx") or 0), float(fill.get("dy") or 0)
    dw, dh = float(fill.get("dw") or 0), float(fill.get("dh") or 0)
    if not any((dx, dy, dw, dh)):
        return
    try:
        k = float(scale) or 1.0
        if dx or dw:
            shape.left = int(shape.left + round(dx * k))
        if dy or dh:
            shape.top = int(shape.top + round(dy * k))
        if dw:
            shape.width = max(1, int(shape.width + round(dw * k)))
        if dh:
            shape.height = max(1, int(shape.height + round(dh * k)))
    except (AttributeError, ValueError, TypeError):
        pass                                     # 图片占位符之类的形状没有 left/width：不动它就是


def _apply_style(shape, fill: dict, scale: float = 1.0) -> None:
    """把右栏改的字号/字体/颜色/对齐/行距/粗斜体落到形状的 run/段落上（R15）。

    只动**老师点名改过**的那些键（不点名就不碰模板格式）。字号存的是**设计像素**
    （1280×720 下的 px），换算：`pt = px × (EMU/px) ÷ 12700` —— 对 13.3 英寸的常见画布正好是 ×0.75。

    段落级（`para.font`）与 run 级都设：模板里有的段落一个 run 都没有，只设 run 级会漏
    （写文字时才生成 run，那时才吃段落级的默认值）。
    """
    keys = ("size", "font", "bold", "italic", "color", "align", "lh")
    if not any(k in fill for k in keys):
        return
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Pt

    align_map = {"start": PP_ALIGN.LEFT, "middle": PP_ALIGN.CENTER, "end": PP_ALIGN.RIGHT}
    pt = (float(fill["size"]) * (float(scale) or 1.0) / 12700.0) if fill.get("size") else 0.0
    rgb = RGBColor.from_string(str(fill["color"]).lstrip("#")) if fill.get("color") else None
    try:
        for para in shape.text_frame.paragraphs:
            if fill.get("align") in align_map:
                para.alignment = align_map[fill["align"]]
            if fill.get("lh"):
                para.line_spacing = float(fill["lh"])
            for f in [para.font] + [r.font for r in para.runs]:
                if fill.get("bold"):
                    f.bold = True
                if fill.get("italic"):
                    f.italic = True
                if fill.get("font"):
                    f.name = str(fill["font"])
                if pt:
                    f.size = Pt(max(6.0, pt))
                if rgb is not None:
                    f.color.rgb = rgb
    except (AttributeError, ValueError, TypeError):
        pass                                     # 形状没有字体属性（图片之类）：跳过，不报错


def _apply_z(shape, steps: int) -> None:
    """把形状在幻灯片里往上/往下挪 `steps` 层（WPS 的「上移一层 / 下移一层」）。

    为什么用"相对步数"而不是绝对层号：每一次填充都是从**模板原稿**重新开始的，
    相对步数因此在每次填充里都指向同一个结果（绝对层号会在页与页之间漂）。
    实现就是 `p:spTree` 里换位置 —— 往后放 = 盖在最上面，往前插 = 压到下一个兄弟之前。
    """
    if not steps:
        return
    try:
        el = shape._element                                            # noqa: SLF001
        tree = el.getparent()
        kids = list(tree)[2:]                    # 前两个是 nvGrpSpPr / grpSpPr，不参与层序
        for _ in range(abs(int(steps))):
            if el not in kids:
                break
            i = kids.index(el)
            if steps > 0:
                if i == len(kids) - 1:
                    break
                tree.remove(el)
                kids[-1].addnext(el)
                kids.append(kids.pop(i))
            else:
                if i == 0:
                    break
                tree.remove(el)
                kids[i - 1].addprevious(el)
                kids.insert(i - 1, kids.pop(i))
    except (AttributeError, ValueError, TypeError):
        pass


def _write_text(shape, text: str, want: list[str], fill: dict | None = None) -> None:
    """写进一个文本框：**保留原有格式**，多行按段落写；一个字都不改内容。

    文字比框长时**先把字号缩一点**（最多缩到设计字号的 0.7、不低于 14px），
    而不是砍字数 —— 砍字数是老师最反感的那种"精简"（容量按缩完的字号算，所以通常不用缩）。
    """
    tf = shape.text_frame
    lines = [ln for ln in str(text or "").split("\n")] or [""]
    want.extend([ln for ln in lines if ln.strip()])
    first = tf.paragraphs[0]
    _set_para_text(first, lines[0])
    for extra in list(tf.paragraphs[1:]):
        extra._element.getparent().remove(extra._element)          # noqa: SLF001
    for ln in lines[1:]:
        p = tf.add_paragraph()
        _set_para_text(p, ln)
    if fill:
        _shrink_to_fit(shape, str(text or ""), fill)


def _shrink_to_fit(shape, text: str, fill: dict) -> None:
    """按需缩字号：`design_capacity`（设计字号下的容量）装不下这么多字时，按比例缩一点。"""
    from pptx.util import Pt

    from . import slide_template as _st

    design = float(fill.get("size_px") or 0) or _st._DEFAULT_SIZE_PX            # noqa: SLF001
    geo = fill.get("geo") or {}
    cap_design = int(fill.get("design_capacity") or 0)
    if not cap_design:
        cap_design = _st._slot_capacity(None, design, geo, size=design)          # noqa: SLF001
    need = len(text)
    if cap_design <= 0 or need <= cap_design or not text:
        return
    lo = _st.min_size_for(design)                                                # noqa: SLF001
    size = design
    while size > lo and _st._slot_capacity(None, design, geo, size=size) < need:  # noqa: SLF001
        size = max(lo, size * 0.94)
        if size <= lo:
            break
    factor = size / design if design else 1.0
    if factor >= 0.995:
        return
    try:
        for para in shape.text_frame.paragraphs:
            for run in para.runs:
                if run.font.size:
                    run.font.size = Pt(max(_st._MIN_FONT_PX, run.font.size.pt * factor))   # noqa: SLF001
            if para.font.size:
                para.font.size = Pt(max(_st._MIN_FONT_PX, para.font.size.pt * factor))
    except (AttributeError, ValueError):
        pass


def _set_para_text(para, text: str) -> None:
    """段落文字：第一个 run 留着（保字体字号），其余删掉。

    注意 python-pptx 的元素访问器各不一样（踩过）：`_Run` 是 `._r`，`_Paragraph` 是 `._p`，
    形状是 `._element` —— 写错就是 `AttributeError: '_Run' object has no attribute '_element'`。
    """
    runs = list(para.runs)
    if runs:
        runs[0].text = text
        for r in runs[1:]:
            r._r.getparent().remove(r._r)          # noqa: SLF001
    else:
        para.text = text


def _verify_fill(pptx: Path, want: list[str], the_plan: dict) -> list[dict]:
    """三道校验（§10.3）：内容零丢失（阻断）+ 页数 + **续页设计没丢**（阻断）。"""
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    prs = Presentation(str(pptx))
    got: list[str] = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                got.append((shape.text_frame.text or "").strip())
    blob = "\n".join(got)
    missing = [t for t in want if t.strip() and t.strip() not in blob]
    over = [w for w in the_plan.get("warnings") or [] if "容量" in w]
    product = list(prs.slides)
    # 母版形状数要**从模板文件**读：产物里的页已经被重排/删过了，
    # 拿 product[template_index] 去比是在比两张不同的页（踩过：假失败）。
    src_counts: list[int] = []
    try:
        tpl_path = st.root() / st._slug(str(the_plan.get("template_id") or "")) / "template.pptx"   # noqa: SLF001
        if tpl_path.is_file():
            src_counts = [len(list(s.shapes)) for s in Presentation(str(tpl_path)).slides]
    except Exception:  # noqa: BLE001 读不到模板就不做这条检查（不给假警报）
        src_counts = []
    thin: list[str] = []
    if src_counts:
        for i, page in enumerate(the_plan["pages"]):
            src_i = page.get("template_index")
            if i >= len(product) or src_i is None or src_i >= len(src_counts):
                continue
            if len(list(product[i].shapes)) < src_counts[src_i]:
                thin.append(f"第 {i + 1} 页（母版第 {src_i + 1} 页：{src_counts[src_i]} → "
                            f"{len(list(product[i].shapes))} 个形状）")
    blank = [i + 1 for i, s in enumerate(product)
             if not any(getattr(sh, "has_text_frame", False) and (sh.text_frame.text or "").strip()
                        for sh in s.shapes)
             and not any(sh.shape_type == MSO_SHAPE_TYPE.PICTURE for sh in s.shapes)]
    # 备注读回（R16，不阻断）：写了讲稿就要能在 pptx 里读出来
    want_notes = [(p.get("note") or "").strip() for p in the_plan["pages"]]
    got_notes = [_note_of(s) for s in product]
    note_lack = [i + 1 for i, (w, g) in enumerate(zip(want_notes, got_notes)) if w and w not in g]
    return [
        {"name": "内容零丢失", "ok": not missing, "blocking": True,
         "detail": f"拟写入 {len(want)} 串，缺 {len(missing)} 串" + (f"：{missing[:3]}" if missing else "")},
        {"name": "续页设计没丢", "ok": not thin, "blocking": True,
         "detail": "每页都保留了模板母版的全部形状（底图/图片/装饰）" if not thin
                   else "这些页比模板母版少形状（底图会丢）：" + "；".join(thin[:3])},
        {"name": "没有空白页", "ok": not blank, "blocking": True,
         "detail": "每页都有文字或图片" if not blank else f"这些页既没文字也没图片：{blank[:5]}"},
        {"name": "页数符合策略", "ok": len(prs.slides._sldIdLst) == len(the_plan["pages"]),   # noqa: SLF001
         "blocking": False,
         "detail": f"产物 {len(prs.slides._sldIdLst)} 页 / 计划 {len(the_plan['pages'])} 页"},
        {"name": "容量自查", "ok": not over, "blocking": False,
         "detail": f"{len(over)} 处超容量（只提示，不阻断）" if over else "没有超容量"},
        {"name": "备注读回", "ok": not note_lack, "blocking": False,
         "detail": f"{sum(1 for w in want_notes if w)} 页有讲稿，逐页读回通过"
                   if not note_lack else f"这些页的备注读不回来：{note_lack[:5]}"},
    ]


#: 接口契约版本（外部 agent 按它对齐；改了就要在 docs/24 里加一段变更记录）
SCHEMA = "edu-agent.fill-plan.v1"


def apply_mapping(the_plan: dict, mapping: list[dict] | None) -> dict:
    """把**外部 agent（或老师的页内编辑）**给的 `mapping` 收干净后套进计划（判断由调用方做，执行由我们做）。

    `mapping` 形状（与 `plan["pages"]` 同构，可以只给一部分页）：

        [{"page": 2, "fills": [{"shape_index": 5, "text": "改后的正文"}]}]
        [{"template_index": 1, "fills": [{"shape_index": 5, "dx": 40, "dy": -12}]}]     # 只挪不改字

    指哪一页（**R14 修正**）：
    · **`page` = 成品第几页（0 起，计划顺序）** —— 页内编辑一律用这个。
      为什么必须补它（真踩过）：续页是**整页克隆母版**出来的，于是"第 1 页"和"第 3 页"的
      `template_index` 都是 0；只按 `template_index` 找，改第 1 页会落到第 3 页上
      （老师看到的就是"我改的地方没变、别处变了"）。
    · `template_index`（老写法，外部 agent 用的）：取该模板页的**第一张**成品页；重复的那几页
      请改用 `page` 精确指。

    两条指槽写法：

    · **`shape_index` 精确指槽**（页内编辑与"一页多个同角色槽"必须用这个）：
      只改那一个框。为什么必须有它（R7 发现的真问题）：卡片式模板一页有 4 个 `body` 槽，
      而按 `role` 匹配会把**4 个框写成同一句话** —— 老师改一格就污染整页。
    · 只给 `role`：改该角色的**第一个**槽（老写法，语义收紧后写进 `docs/24`）。

    规则：只认计划里已有的模板页；文字超容量仍走 `_fit_text` 适度压缩并记 `adjustments`；
    没给到的页保持草案原样（所以"只改一页"是合法的）。

    R14 追加：一条 fill 里除了 `text` 还能给 `dx/dy/dw/dh`（拖动/改大小，设计像素；范围与
    槽位层同源 = `slide_deck.SLOT_OVERRIDE`）和 `hidden`（= 清空，等价于"删掉这处文字"）。
    **没给 `text` 键 = 不改字**（`text: ""` 才是"清空这一格"）—— 只挪位置不会把字抹掉。
    """
    if not isinstance(mapping, list) or not mapping:
        return the_plan
    pages = the_plan.get("pages") or []
    by_ti: dict = {}
    for p in pages:
        by_ti.setdefault(p.get("template_index"), p)      # 老写法取第一张（后补 page 精确指）
    touched = 0
    for row in mapping:
        if not isinstance(row, dict):
            continue
        page = None
        if isinstance(row.get("page"), int) and 0 <= int(row["page"]) < len(pages):
            page = pages[int(row["page"])]
        else:
            page = by_ti.get(row.get("template_index"))
        if page is None:
            continue
        by_shape: dict[int, dict] = {}
        by_role: dict[str, dict] = {}
        for f in (row.get("fills") or []):
            if not isinstance(f, dict):
                continue
            if isinstance(f.get("shape_index"), int):
                by_shape[int(f["shape_index"])] = f
            elif f.get("role"):
                by_role.setdefault(str(f["role"]), f)
        for fill in page.get("fills") or []:
            role = fill.get("role") or "body"
            shape = fill.get("shape_index")
            if isinstance(shape, int) and shape in by_shape:
                patch = by_shape[shape]                # 精确指槽（页内编辑走这条）
            elif role in by_role:
                patch = by_role.pop(role)              # 只给 role：**只动第一个**同角色槽
            else:
                continue
            if _apply_fill_patch(fill, patch, the_plan):
                touched += 1
    the_plan["mapping_applied"] = touched
    return the_plan


def _apply_fill_patch(fill: dict, patch: dict, the_plan: dict) -> bool:
    """把一条 mapping 落到一条 fill 上（文字 + 几何 + 删除）；返回是否真的动了它。"""
    moved = False
    if "text" in patch or patch.get("hidden"):     # hidden = 清空这处文字（"删"的语义）
        wanted = "" if patch.get("hidden") else str(patch.get("text") or "")
        text = wanted.strip()
        cap = int(fill.get("capacity") or 0)
        # 装饰槽（页码/设计标签）**不压缩**：它就那么几个字，压出来的不是老师要的；
        # 写不下会被审计报出来（宁可见报，也别悄悄改老师的话）。
        if text and cap > 0 and len(text) > cap and (fill.get("role") or "") != "decoration":
            text, changed = _fit_text(text, cap)
            if changed:
                the_plan.setdefault("adjustments", []).append(
                    {"action": "trim", "by": "mapping", "cap": cap,
                     "before": len(wanted), "after": len(text), "text": text[:24]})
        fill["text"] = text
        fill["clear"] = not text
        moved = True
    for key, (lo, hi) in slide_deck.SLOT_OVERRIDE.items():     # 数值范围与槽位层同源
        if key not in patch or key in ("size", "lh"):
            continue
        try:
            val = round(max(lo, min(hi, float(patch[key]))), 1)
        except (TypeError, ValueError):
            continue
        if val:
            fill[key] = val
            moved = True
    if "z" in patch:                                           # 层级：相对步数（±9）
        try:
            z = int(round(float(patch["z"])))
        except (TypeError, ValueError):
            z = 0
        z = max(slide_deck.SLOT_Z_RANGE[0], min(slide_deck.SLOT_Z_RANGE[1], z))
        if z:
            fill["z"] = z
            moved = True
        else:
            fill.pop("z", None)
    # 样式（R15）：字号/字体/颜色/对齐/行距/粗斜体 —— 与槽位层同一套键，落到 pptx 的 run/段落上
    if "size" in patch:
        try:
            size = round(max(8.0, min(200.0, float(patch["size"]))), 1)
        except (TypeError, ValueError):
            size = 0.0
        if size:
            fill["size"] = size
            moved = True
    if "lh" in patch:
        try:
            lh = round(max(0.8, min(3.0, float(patch["lh"]))), 2)
        except (TypeError, ValueError):
            lh = 0.0
        if lh:
            fill["lh"] = lh
            moved = True
    font = slide_deck.clean_font(patch.get("font"))
    if font:
        fill["font"] = font
        moved = True
    if patch.get("align") in slide_deck.SLOT_ALIGNS:
        fill["align"] = patch["align"]
        moved = True
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", str(patch.get("color") or ""))
    if m:
        fill["color"] = "#" + m.group(1).upper()
        moved = True
    for flag in ("bold", "italic"):
        if patch.get(flag):
            fill[flag] = True
            moved = True
    return moved


def plan_payload(template_id: str, slides: list[dict] | None, title: str = "") -> dict:
    """给**外部 agent** 的稳定载荷：模板槽位清单 + 我们的内容 + 确定性草案。

    这是"另一个 agent 生成教案 → 交给我们出 PPT"那条链路的读端；写端是 `apply_mapping()`。
    """
    pages, err = template_pages(template_id)
    the_plan = plan(template_id, slides, title)
    items = slide_deck.clean_slides(slides)
    return {"ok": not err and bool(pages), "schema": SCHEMA,
            "template": {"id": template_id, "pages": len(pages),
                         "slots": [{"index": p.get("index"), "layout": p.get("layout"),
                                    "slots": [{"role": s.get("role"), "capacity": s.get("capacity"),
                                               "sample": s.get("sample")} for s in (p.get("slots") or [])]}
                                   for p in pages]},
            "content": {"title": title, "slides": items},
            "draft": the_plan,
            "notes": ["draft 是我们按规则算出来的草案；你可以改 pages[].fills[].text 后回传给 /api/slide/fill",
                      "只给要改的页也可以（没给到的页保持草案）",
                      "指页用 page（成品第几页，0 起）最稳；template_index 是母版页号，"
                      "续页会和首页撞号，只按它会改错页（R14）",
                      "一条 fill 除了 text 还能给 dx/dy/dw/dh（拖动/改大小，设计像素）与 hidden（= 清空这处文字）",
                      "样式（R15）：size（设计像素，按画布换算成 pt）/font/color(#RRGGBB)/align(start|middle|end)"
                      "/lh(0.8~3)/bold/italic 都能给，只动你点名改的那些键",
                      "fills 里的 kind='decoration'/'page-number' 是模板自带的设计标签与页码："
                      "默认原样不动，老师改了/清空了才写",
                      "超容量的文字会被适度压缩（去括号补充、从末尾减分句），并在 adjustments 里留痕"],
            "error": err}


if __name__ == "__main__":        # 冒烟：python -m edu_agent.slide_fill <模板id>
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    tid = sys.argv[1] if len(sys.argv) > 1 else (st.list_templates() or [{}])[0].get("id", "")
    p = plan(tid, slide_deck.SAMPLE_DECK, "3.3 幂函数")
    print(f"模板 {tid}：计划 {len(p['pages'])} 页 · 警告 {len(p['warnings'])} · 退化 {len(p['degraded'])}")
    for w in p["warnings"] + p["degraded"]:
        print("  ·", w)
