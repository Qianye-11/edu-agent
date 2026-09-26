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
    """读模板的槽位清单 → `(每页 [{index, layout, slots}], 错误信息)`。"""
    theme = st._read_json(st.root() / st._slug(template_id) / "slots.json")   # noqa: SLF001
    pages = theme.get("slides") or []
    if not pages and theme.get("error"):
        return [], str(theme["error"])
    return pages, ""


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
            }, out))
        else:
            out["degraded"].append("模板没有封面页 → 封面内容并入第一张内容页（不硬造封面）")
            if content_pages:
                body_slides.insert(0, (0, cover_slide))
            else:
                out["warnings"].append("模板一页都没有，无法填充")

    # 2) 内容页：逐页顺序对应；不够就复制模板的内容页
    donor = content_pages[0] if content_pages else (cover_pages[0] if cover_pages else pages[0])
    for k, (idx, s) in enumerate(body_slides):
        if k < len(content_pages):
            tpl_page, copied = content_pages[k], False
        else:
            tpl_page, copied = donor, True
            out["policy"].setdefault("copied", 0)
            out["policy"]["copied"] += 1
        out["pages"].append(_page_fill(tpl_page, {
            "title": s.get("name") or s.get("title") or "",
            "body": [b["text"] for b in (slide_deck._blocks(s) or [])] or slide_deck._bullets(s),  # noqa: SLF001
            "minutes": str(s.get("minutes") or ""),
        }, out, copied=copied))

    # 3) 页数：模板内容页多出来 → 默认删掉（§10.2）
    extra = max(0, len(content_pages) - len(body_slides))
    if extra:
        out["policy"]["deleted"] = extra
        out["warnings"].append(f"会话比模板少 {extra} 页内容 → 默认删掉模板多余的内容页"
                               f"（想保留空页就说一声）")
    if out["policy"].get("copied"):
        out["warnings"].append(f"会话比模板多 → 复制了模板自己的内容页 {out['policy']['copied']} 张"
                               f"（母版/版式继承不变）")
    return out


def _page_fill(tpl_page: dict, data: dict, out: dict, *, copied: bool = False) -> dict:
    """把一页的数据分配到模板页的槽上（按角色；没有对应角色就并到 body）。"""
    slots = tpl_page.get("slots") or []
    fills: list[dict] = []
    body_texts = list(data.get("body") or [])
    title = str(data.get("title") or "")
    sub = str(data.get("subtitle") or "")
    meta = str(data.get("meta") or "")
    minutes = str(data.get("minutes") or "")

    title_done = False
    for slot in slots:
        role = slot.get("role") or "body"
        cap = int(slot.get("capacity") or 0)
        if 0 < cap < st.MIN_FILLABLE_CAPACITY and role not in ("title", "cover"):
            continue                       # 太小的框是标签/装饰，不往里写正文（也不报"容量不足"）
        text = ""
        if role == "title" and not title_done:
            text, title_done = (title or sub or meta), True
            if title and sub:
                out["degraded"].append("模板只有标题槽 → 副标题并入标题行（不硬造槽）")
        elif role in ("cover", "subtitle"):
            text = sub or (title if not title_done else "")
            title_done = title_done or bool(text)
        elif role == "meta":
            text = meta or minutes
        elif role == "footer":
            text = minutes
        else:
            text = "\n".join(body_texts)
            body_texts = []
        if cap > 0 and len(text) > cap:
            where = f"第 {tpl_page.get('index', 0) + 1} 页" + ("标题" if role in ("title", "cover") else "正文")
            how = "建议缩短标题" if role in ("title", "cover") else "建议拆页或精简"
            out["warnings"].append(f"{where}超出模板框（容量 {cap} 字 < 填入 {len(text)} 字）→ {how}")
        elif cap <= 0 and text and not out.get("_cap_unknown"):
            out["_cap_unknown"] = True
            out["degraded"].append("模板有槽位量不出容量（缺几何或字号）→ 容量自查对这类槽跳过")
        fills.append({"role": role, "capacity": cap, "text": text, "geo": slot.get("geo") or {}})
    if body_texts:                       # 模板槽不够装 → 不丢，记下来提示（不截断）
        out["warnings"].append(f"模板这一页没有足够的正文槽 → 多出 {len(body_texts)} 条没填进去"
                               f"（内容没删，但请手动确认）")
    return {"template_index": tpl_page.get("index", 0), "layout": tpl_page.get("layout") or "",
            "copied": copied, "fills": fills}


# ---------------------------------------------------------------- 真正写文件

def fill_pptx(template_id: str, slides: list[dict] | None, title: str = "", *,
              out_dir: str | Path | None = None) -> dict:
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

    target_dir = Path(out_dir) if out_dir else ppt_polish.outdir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"教案_{slide_deck.safe_name(title or '未命名')}_{stamp}.pptx"
    prs.save(str(target))

    checks = _verify_fill(target, want, the_plan)
    ok = all(c["ok"] for c in checks if c.get("blocking", True))
    return {"ok": ok, "file": str(target), "filename": target.name, "outDir": str(target_dir),
            "template_id": template_id, "job": str(job), "plan": the_plan,
            "checks": checks, "quality": {"fill": "ok" if ok else "fail", "template": "ok"},
            "detail": "；".join(f"{'✅' if c['ok'] else '❌'} {c['name']}" for c in checks),
            "warnings": the_plan["warnings"] + the_plan["degraded"],
            "download_url": "/api/wps/download?path=" + str(target)}


def _apply(prs, the_plan: dict, want: list[str]) -> None:
    """把计划落到 pptx 上：逐页写槽；模板页不够就复制（母版/版式继承保持不变）。"""
    slides = list(prs.slides)
    fills = the_plan["pages"]

    # 复制页：从最后一张内容页复制（只复制文本类形状，图片/图表不复制——见 docs/19 §10.2 备注）
    while len(slides) < len(fills):
        donor = slides[-1] if slides else None
        if donor is None:
            break
        new = prs.slides.add_slide(donor.slide_layout)
        for shp in list(new.shapes):
            shp._element.getparent().remove(shp._element)          # noqa: SLF001 清掉版式带来的空占位符
        for shp in donor.shapes:
            if getattr(shp, "has_text_frame", False):
                new.shapes._spTree.append(copy.deepcopy(shp._element))   # noqa: SLF001
        slides = list(prs.slides)

    for i, page in enumerate(fills):
        if i >= len(slides):
            break
        slide = slides[i]
        text_shapes = [s for s in slide.shapes if getattr(s, "has_text_frame", False)]
        layout_name = slide.slide_layout.name or ""
        used: set[int] = set()
        for fill in page["fills"]:
            # **按角色**挑形状（不是按顺序）：模板里页脚/页码排在前面的情况很常见，
            # 顺序写就会把标题写进页脚（踩过）。同角色里挑第一个没用过的，找不到再退回顺序。
            role = fill.get("role") or "body"
            if not str(fill.get("text") or "").strip():
                continue                       # 我们这页没有这个角色的内容 → **不动模板那个框**
            pick = None
            for k, shp in enumerate(text_shapes):
                if k in used:
                    continue
                if st._role_of(shp, k, layout_name) == role:      # noqa: SLF001
                    pick = k
                    break
            if pick is None:
                pick = next((k for k in range(len(text_shapes)) if k not in used), None)
            if pick is None:                                     # 槽不够：不硬塞，交给计划里的提示
                break
            used.add(pick)
            _write_text(text_shapes[pick], fill["text"], want)

    # 页数比会话多 → 删掉多余（默认策略，§10.2）
    deleted = int(the_plan.get("policy", {}).get("deleted") or 0)
    for _ in range(deleted):
        if len(prs.slides._sldIdLst) <= len(fills):                # noqa: SLF001
            break
        rId = prs.slides._sldIdLst[-1].rId                          # noqa: SLF001
        prs.part.drop_rel(rId)
        prs.slides._sldIdLst.remove(prs.slides._sldIdLst[-1])       # noqa: SLF001


def _write_text(shape, text: str, want: list[str]) -> None:
    """写进一个文本框：**保留原有格式**，多行按段落写；一个字都不改内容。"""
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
    """两道校验（§10.3）：内容零丢失（阻断）+ 容量自查（提示，不阻断）。"""
    from pptx import Presentation

    prs = Presentation(str(pptx))
    got: list[str] = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                got.append((shape.text_frame.text or "").strip())
    blob = "\n".join(got)
    missing = [t for t in want if t.strip() and t.strip() not in blob]
    over = [w for w in the_plan.get("warnings") or [] if "容量" in w]
    return [
        {"name": "内容零丢失", "ok": not missing, "blocking": True,
         "detail": f"拟写入 {len(want)} 串，缺 {len(missing)} 串" + (f"：{missing[:3]}" if missing else "")},
        {"name": "页数符合策略", "ok": len(prs.slides._sldIdLst) == len(the_plan["pages"]),   # noqa: SLF001
         "blocking": False,
         "detail": f"产物 {len(prs.slides._sldIdLst)} 页 / 计划 {len(the_plan['pages'])} 页"},
        {"name": "容量自查", "ok": not over, "blocking": False,
         "detail": f"{len(over)} 处超容量（只提示，不阻断）" if over else "没有超容量"},
    ]


if __name__ == "__main__":        # 冒烟：python -m edu_agent.slide_fill <模板id>
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    tid = sys.argv[1] if len(sys.argv) > 1 else (st.list_templates() or [{}])[0].get("id", "")
    p = plan(tid, slide_deck.SAMPLE_DECK, "3.3 幂函数")
    print(f"模板 {tid}：计划 {len(p['pages'])} 页 · 警告 {len(p['warnings'])} · 退化 {len(p['degraded'])}")
    for w in p["warnings"] + p["degraded"]:
        print("  ·", w)
