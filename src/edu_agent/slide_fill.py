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
    return out


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
        if text and cap > 0 and len(text) > cap:
            where = f"第 {tpl_page.get('index', 0) + 1} 页" + ("标题" if role == "title" else "正文")
            how = "建议缩短标题" if role == "title" else "建议拆页或精简"
            out["warnings"].append(f"{where}超出模板框（{slot.get('sample') or '空框'}：容量 {cap} 字 < 填入 {len(text)} 字）→ {how}")
        fills.append({"role": role, "capacity": cap, "text": text, "clear": bool(clear),
                      "shape_index": slot.get("shape_index"),
                      "sample": slot.get("sample") or "", "geo": slot.get("geo") or {}})

    # ① 标题：第一个标题槽；副标题/元信息并进标题（模板没有更多槽时，别硬造）
    title_text = title or sub or meta
    if title_slots:
        emit(title_slots[0], title_text)
        for extra in title_slots[1:]:
            emit(extra, "", clear=True)
    elif body_slots:                       # 没有标题槽：正文第一条当标题（并把它从正文里去掉）
        first = body_texts.pop(0) if body_texts else (title or sub or meta)
        emit(body_slots[0], first)
        body_slots = body_slots[1:]

    # ② 正文：每个正文槽装一条（按阅读顺序）；多出来的条目并进最后一个槽，槽不够就提示
    for k, slot in enumerate(body_slots):
        if k < len(body_texts):
            emit(slot, body_texts[k])
        else:
            emit(slot, "", clear=True)     # 模板示例文字属于"内容"，没填就清掉，别和我们的混在一起
    if len(body_texts) > len(body_slots):
        rest = body_texts[len(body_slots):]
        if body_slots:
            last = fills[-1] if fills else None
            if last is not None and last.get("role") == "body":
                last["text"] = "\n".join([last["text"]] + rest) if last["text"] else "\n".join(rest)
                out["warnings"].append(f"第 {tpl_page.get('index', 0) + 1} 页模板只有 {len(body_slots)} 个正文框，"
                                       f"多出的 {len(rest)} 条并进了最后一个框（建议拆页）")
        else:
            out["warnings"].append(f"第 {tpl_page.get('index', 0) + 1} 页模板没有可用正文框 → {len(body_texts)} 条没填进去"
                                   f"（内容没删，请手动确认）")

    if minutes:
        out.setdefault("notes", []).append(f"第 {tpl_page.get('index', 0) + 1} 页时长 {minutes} 分钟"
                                           f"（模板没有时间槽，写进备注即可）")
    return {"template_index": tpl_page.get("index", 0), "layout": tpl_page.get("layout") or "",
            "copied": copied, "fills": fills,
            "kept": [s.get("sample") for s in deco if s.get("sample")],
            "replaced": [{"sample": f.get("sample"), "text": f["text"]}
                         for f in fills if f["text"] and f.get("sample")]}


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
            img = (f.parent / rel).resolve()
            try:
                if not img.is_file() or img.stat().st_size > 2_000_000:
                    return m.group(0)
                mime = mimetypes.guess_type(img.name)[0] or "image/png"
                b64 = base64.b64encode(img.read_bytes()).decode("ascii")
                return f'{m.group(1)}="data:{mime};base64,{b64}"'
            except OSError:
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
    preview = preview_pages(target, job) if with_preview else []
    return {"ok": ok, "file": str(target), "filename": target.name, "outDir": str(target_dir),
            "template_id": template_id, "job": str(job), "plan": the_plan,
            "checks": checks, "preview": preview, "preview_pages": len(preview),
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

    # 1) 先写"用模板现有页"的那些（按 template_index 落位）
    for page in fills:
        if page.get("copied"):
            continue
        ti = page.get("template_index")
        if ti is None or ti >= len(slides):
            continue
        _fill_slide(slides[ti], page, want)

    # 2) 再补"会话比模板多"的页：从模板自己的内容页（donor）复制，只复制文本类形状
    copied_pages = [p for p in fills if p.get("copied")]
    donor_idx = next((p.get("template_index") for p in copied_pages), None)
    donor = slides[donor_idx] if donor_idx is not None and donor_idx < len(slides) else \
        (slides[-1] if slides else None)
    copy_elem: dict[int, object] = {}
    for k, page in enumerate(copied_pages):
        if donor is None:
            break
        new = prs.slides.add_slide(donor.slide_layout)
        for shp in list(new.shapes):                                    # 清掉版式带来的空占位符
            shp._element.getparent().remove(shp._element)               # noqa: SLF001
        for shp in donor.shapes:
            if getattr(shp, "has_text_frame", False):
                new.shapes._spTree.append(_copy.deepcopy(shp._element))  # noqa: SLF001
        _fill_slide(new, page, want)
        idx = n0 + k
        if idx < len(sld_id_lst):
            copy_elem[id(page)] = sld_id_lst[idx]

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


def _fill_slide(slide, page: dict, want: list[str]) -> None:
    """把一页的填充内容写进这张幻灯片：**按 `shape_index` 精确落位**（槽位分类时记下的位置）。

    为什么要按 index 而不是"再猜一次角色"：填充与导入读的是同一个文件，形状顺序一致；
    按 index 落位能保证"计划里说要填那个框"和"实际填的就是那个框"，也避免二次猜测带来的漂移。
    `sample` 用来核对没拿错框（模板被改过时退化为按角色找）。
    """
    text_shapes = [s for s in slide.shapes if getattr(s, "has_text_frame", False)]
    for fill in page["fills"]:
        idx = fill.get("shape_index")
        shape = None
        if isinstance(idx, int) and 0 <= idx < len(list(slide.shapes)):
            cand = list(slide.shapes)[idx]
            if getattr(cand, "has_text_frame", False):
                sample = (cand.text_frame.text or "").strip()[:40]
                if not fill.get("sample") or sample == fill["sample"] or not sample:
                    shape = cand
        if shape is None:                       # 退路：按角色找第一个没用过的
            used = getattr(_fill_slide, "_used", None)
            if used is None or used[0] is not slide:
                used = (slide, set())
                _fill_slide._used = used          # type: ignore[attr-defined]
            for k, shp in enumerate(text_shapes):
                if k not in used[1]:
                    shape, _ = shp, used[1].add(k)
                    break
        if shape is None:
            continue
        text = fill.get("text") or ""
        if not text and not fill.get("clear"):
            continue                            # 没内容且不需要清空 → 不动模板的框
        _write_text(shape, text, want)


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
