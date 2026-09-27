# -*- coding: utf-8 -*-
"""PPT 全链路体检（HTTP 真跑一遍，跑出问题就报错退出）。

覆盖与 PPT 有关的**每一条接口**（docs/19 / docs/22 / docs/23 那套）：

  1. 文档预览派生幻灯片        POST /api/session/doc-preview
  2. 主题与模板清单            GET  /api/slide/themes · /api/slide/templates
  3. 渲染（内置主题/导入模板）  POST /api/slide/render
  4. 编辑存回会话              POST /api/slide/save（+ 预览优先用改过的那份）
  5. 新版式导出                POST /api/wps/export  kind=pptx-deck
  6. 模板导入（上传 .pptx）     POST /api/slide/template/import
  7. 按模板填充                POST /api/slide/fill
  8. PPT 优化那条线            GET/POST /api/ppt/status · intake · polish · download
  9. 错误路径                 坏主题/没模板/空内容/非 pptx 上传 —— 必须**明确报错**，不能 500
 10. 落盘红线                  产物在 data/ppt_out，中间件在 data/tmp，content/plans 一个字不写
 11. 产物可打开                python-pptx 能重新打开、页数对、文字在

用法（先「启动」）：
    .venv\\Scripts\\python.exe tools\\ppt_smoke.py            # 全部跑
    .venv\\Scripts\\python.exe tools\\ppt_smoke.py --quick    # 跳过 PPT 优化那两步（慢）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import zipfile
from pathlib import Path

import httpx

_ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:5174"
SAMPLE_PPTX = _ROOT / "content" / "plans" / "完整课时模板（默认）.pptx"
MD = ("# 3.3 幂函数\n\n人教A版 · 必修第一册 · 高一\n\n"
      "### 情境导入（约 5 分钟）\n- 投影五个实例\n- 追问：这些函数有什么共同点\n\n"
      "### 概念建构（约 12 分钟）\n- 形如 y=x^α 的函数\n- α 为常数\n\n"
      "### 小结（约 3 分钟）\n- 三条结论\n")

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, extra: str = "") -> bool:
    RESULTS.append((name, bool(ok), extra))
    print(f"  {'OK  ' if ok else 'FAIL'} {name}" + (f"  {extra}" if extra else ""))
    return bool(ok)


def _files(root: Path) -> set[str]:
    return {str(p) for p in root.rglob("*") if p.is_file()} if root.exists() else set()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="跳过 PPT 优化（intake/polish）两步")
    args = ap.parse_args()

    c = httpx.Client(base_url=BASE, timeout=300.0)
    plans = _ROOT / "content" / "plans"
    plans_before = _files(plans)

    print("[1] 文档预览 → 幻灯片")
    # 用**全新会话**：否则会命中"当前会话"里存过的编辑结果（正是我们要验的另一条路径，见 [4]）
    sid = c.post("/api/session/new", json={}).json().get("id") or ""
    r = c.post("/api/session/doc-preview", json={"markdown": MD, "session_id": sid}).json()
    check("doc-preview 正常", bool(r.get("ok")), r.get("error") or "")
    slides = r.get("slides") or []
    check("派生出幻灯片", len(slides) >= 3, f"{len(slides)} 页：{[s.get('name') for s in slides]}")
    check("新会话没被标成已编辑", not r.get("slides_edited"), str(r.get("slides_edited")))
    check("前端能拿到 enabled 标志", "enabled" in r)

    print("\n[2] 主题与模板清单")
    th = c.get("/api/slide/themes").json()
    check("主题清单 3 套", len(th.get("themes") or []) == 3, str([t["id"] for t in th.get("themes") or []]))
    check("默认主题是学术蓝", th.get("default") == "tech-editorial", str(th.get("default")))
    tp = c.get("/api/slide/templates").json()
    imported = tp.get("imported") or []
    check("模板清单含内置 3 套", len(tp.get("builtin") or []) == 3, f"导入 {len(imported)} 套")

    print("\n[3] 渲染")
    for tid in ("tech-editorial", "illustration", "hand-drawn"):
        rr = c.post("/api/slide/render", json={"theme_id": tid, "slides": slides, "title": "3.3 幂函数"}).json()
        pages = rr.get("pages") or []
        check(f"渲染 {tid}", bool(rr.get("ok")) and len(pages) == len(slides)
              and all("<svg" in p for p in pages), f"{len(pages)} 页")
    if imported:
        tpl_id = imported[0]["id"]
        rr = c.post("/api/slide/render", json={"theme_id": "", "template_id": tpl_id,
                                              "slides": slides, "title": "3.3 幂函数"}).json()
        check("用导入模板渲染（借配色）", bool(rr.get("ok")) and len(rr.get("pages") or []) == len(slides),
              f"{len(rr.get('pages') or [])} 页")

    print("\n[4] 编辑存回会话")
    edited = [dict(slides[0] or {}), dict(slides[1] or {})]
    edited[1]["name"] = "情境导入（体检改过）"
    sv = c.post("/api/slide/save", json={"session_id": sid, "markdown": MD, "title": "3.3 幂函数",
                                        "slides": edited}).json()
    check("save 成功", bool(sv.get("ok")), sv.get("error") or "")
    r2 = c.post("/api/session/doc-preview", json={"markdown": MD, "session_id": sid}).json()
    check("预览优先用改过的那份", (r2.get("slides") or [{}])[1].get("name") == "情境导入（体检改过）",
          str((r2.get("slides") or [{}])[1].get("name")))
    check("标了 slides_edited", bool(r2.get("slides_edited")))
    r3 = c.post("/api/slide/render", json={"theme_id": "tech-editorial", "session_id": sid}).json()
    check("渲染也用改过的那份", "体检改过" in "".join(r3.get("pages") or []))

    print("\n[5] 新版式导出（kind=pptx-deck）")
    out_dir = _ROOT / "data" / "tmp" / "smoke_out"
    ex = c.post("/api/wps/export", json={"kind": "pptx-deck", "theme_id": "tech-editorial",
                                        "slides": slides, "title": "3.3 幂函数",
                                        "outDir": str(out_dir), "confirm": True}).json()
    ok_ex = bool(ex.get("ok")) and Path(ex.get("file") or "").is_file()
    check("导出成功", ok_ex, (ex.get("error") or ex.get("filename") or "")[:80])
    if ok_ex:
        checks = {x["name"]: x["ok"] for x in (ex.get("verify") or {}).get("checks") or []}
        check("导出即验全过", all(checks.values()) and len(checks) >= 4, str(checks))
        check("产物能重新打开", _openable(Path(ex["file"])), "")
        check("产物在 ppt_out/smoke_out，不在 content/plans", "content\\plans" not in str(ex["file"]))

    print("\n[6] 模板导入（上传）")
    tpl_new = ""
    if SAMPLE_PPTX.is_file():
        with SAMPLE_PPTX.open("rb") as fh:
            up = c.post("/api/slide/template/import", params={"name": "体检模板"},
                        files={"file": (SAMPLE_PPTX.name, fh,
                                        "application/vnd.openxmlformats-officedocument.presentationml.presentation")})
        j = up.json()
        tpl_new = j.get("id") or ""
        check("导入成功", bool(j.get("ok")), (j.get("error") or j.get("id") or "")[:80])
        check("抽到页数与槽位", (j.get("pages") or 0) > 0 and bool(j.get("slots_ok")), f"{j.get('pages')} 页")
        check("退化原因如实返回", isinstance(j.get("degraded"), list), str(j.get("degraded"))[:60])
    else:
        check("样例 pptx 存在（跳过导入）", False, str(SAMPLE_PPTX))

    print("\n[7] 按模板填充")
    if tpl_new:
        fl = c.post("/api/slide/fill", json={"template_id": tpl_new, "slides": slides,
                                            "title": "3.3 幂函数", "outDir": str(out_dir),
                                            "confirm": True}).json()
        ok_fl = bool(fl.get("ok")) and Path(fl.get("file") or "").is_file()
        check("填充成功", ok_fl, (fl.get("error") or fl.get("filename") or "")[:80])
        if ok_fl:
            got = {x["name"]: x["ok"] for x in fl.get("checks") or []}
            check("内容零丢失通过", got.get("内容零丢失") is True, str(got))
            check("填充产物能重新打开", _openable(Path(fl["file"])), "")
            check("提示可读（不是 raw 异常）", all("Traceback" not in w for w in fl.get("warnings") or []))

    print("\n[8] PPT 优化那条线")
    st = c.get("/api/ppt/status").json()
    check("status 不设门禁且报安装", "ready" in st and bool(st.get("install")), str(st.get("install", {}).get("commit")))
    if not args.quick and SAMPLE_PPTX.is_file() and st.get("install", {}).get("intake_ready"):
        with SAMPLE_PPTX.open("rb") as fh:
            ik = c.post("/api/ppt/intake", files={"file": (SAMPLE_PPTX.name, fh, "application/vnd.ms-powerpoint")}).json()
        check("intake 出体检报告", bool(ik.get("ok")) and (ik.get("slides") or 0) > 0,
              f"{ik.get('slides')} 页 · {(ik.get('totals') or {}).get('chars')} 字")
        if ik.get("ok"):
            pl = c.post("/api/ppt/polish", json={"job_id": ik.get("job_id"), "actions": ["font", "strip-anim"]}).json()
            check("polish 出结果与内容校验", bool(pl.get("ok")) and bool((pl.get("verify") or {}).get("ok")),
                  str((pl.get("verify") or {}).get("detail"))[:60])
            if pl.get("ok"):
                # 产物先落在任务目录（受控中间目录），点「下载」才拷进 data/ppt_out —— 这是既定设计
                check("优化产物在任务目录里", "ppt_jobs" in str(pl.get("file")), str(pl.get("file")))
                dl = c.get("/api/ppt/download", params={"job_id": ik.get("job_id")})
                check("下载回传成功", dl.status_code == 200 and len(dl.content) > 10000,
                      f"{dl.status_code} · {len(dl.content) // 1024} KB")
                outs = sorted((_ROOT / "data" / "ppt_out").glob("优化_*.pptx"),
                              key=lambda p: p.stat().st_mtime, reverse=True)
                check("下载后 data/ppt_out 里有副本", bool(outs), str(outs[0].name if outs else "（无）"))
    else:
        print("  （--quick：跳过 intake/polish）")

    print("\n[9] 错误路径（要明确报错，不能 500）")
    bad = c.post("/api/slide/render", json={"theme_id": "不存在的主题", "slides": slides})
    check("坏主题 → 400 且带说明", bad.status_code == 400 and "主题" in bad.text, f"{bad.status_code} {bad.text[:60]}")
    m = c.post("/api/slide/fill", json={"template_id": "", "slides": slides, "confirm": True})
    check("没选模板 → 400", m.status_code == 400, f"{m.status_code}")
    fresh = c.post("/api/session/new", json={}).json().get("id") or ""
    m2 = c.post("/api/slide/fill", json={"template_id": tpl_new or "无", "session_id": fresh,
                                        "slides": [], "confirm": True})
    check("全新会话且没内容 → 400", m2.status_code == 400, f"{m2.status_code} {m2.text[:50]}")
    up = c.post("/api/slide/template/import", files={"file": ("x.txt", b"hello", "text/plain")})
    check("非 pptx 上传 → 400", up.status_code == 400, f"{up.status_code} {up.text[:50]}")
    bad_fill = c.post("/api/slide/fill", json={"template_id": tpl_new or "无", "slides": slides,
                                              "outDir": str(out_dir), "confirm": True})
    check("坏模板填充 → 明确错误（不是 500）", bad_fill.status_code in (200, 400) and "Traceback" not in bad_fill.text,
          f"{bad_fill.status_code}")

    print("\n[10] 落盘红线")
    plans_after = _files(plans)
    check("没碰 content/plans", plans_before == plans_after,
          f"新增 {sorted(plans_after - plans_before)[:2]} 少了 {sorted(plans_before - plans_after)[:2]}")

    print("\n[11] 边界（这些踩过，都是真 bug）")
    edge = _ROOT / "data" / "tmp" / "smoke_edge"
    edge.mkdir(parents=True, exist_ok=True)
    for f in edge.glob("*.pptx"):
        f.unlink()
    # ① 同一秒连导两次：不能互相覆盖
    d1 = c.post("/api/wps/export", json={"kind": "pptx-deck", "slides": slides, "title": "重复导出",
                                        "outDir": str(edge), "confirm": True}).json()
    d2 = c.post("/api/wps/export", json={"kind": "pptx-deck", "slides": slides, "title": "重复导出",
                                        "outDir": str(edge), "confirm": True}).json()
    check("连导两次不覆盖（两个文件）",
          bool(d1.get("ok") and d2.get("ok")) and len(list(edge.glob("*.pptx"))) == 2,
          f"{d1.get('filename')} / {d2.get('filename')}")
    # ② 相对路径 outDir：产物不能落进任务目录
    rel = c.post("/api/wps/export", json={"kind": "pptx-deck", "slides": slides, "title": "相对路径",
                                         "outDir": "data/tmp/smoke_edge/rel", "confirm": True}).json()
    check("相对路径 outDir 也落到项目里（不是任务目录）",
          bool(rel.get("ok")) and "ppt_jobs" not in str(rel.get("file") or ""), str(rel.get("file"))[:70])
    # ③ 超长文本：必须继续换行（曾经并成一行 → 转换器拒绝 → 静默回退旧版式）
    long_deck = [{"name": "长", "bullets": ["字" * 400]}]
    le = c.post("/api/wps/export", json={"kind": "pptx-deck", "slides": long_deck, "title": "长文本",
                                        "outDir": str(edge), "confirm": True}).json()
    check("超长文本能出原生版式（不回退）", bool(le.get("ok")) and not le.get("fallback"),
          f"ok={le.get('ok')} fallback={le.get('fallback')} {str(le.get('error') or '')[:40]}")
    check("超长文本导出过全部硬验收",
          all(x["ok"] for x in (le.get("verify") or {}).get("checks") or []) if le.get("ok") else False)
    # ④ 空内容导出：明确报错，且不写 content/plans
    empty = c.post("/api/wps/export", json={"kind": "pptx-deck", "slides": [], "title": "空", "confirm": True})
    check("空内容导出 → 400 且说明原因", empty.status_code == 400 and "没有可导出的内容" in empty.text,
          f"{empty.status_code}")
    # ⑤ 伪造 pptx（.txt 改名）：收下但退化，不炸
    fake = edge / "fake.pptx"
    fake.write_bytes(b"not a zip at all")
    with fake.open("rb") as fh:
        fj = c.post("/api/slide/template/import", params={"name": "伪造"},
                    files={"file": ("fake.pptx", fh, "application/vnd.ms-powerpoint")}).json()
    check("伪造 pptx：收下并退化（不 500）", bool(fj.get("ok")) and not fj.get("slots_ok"),
          str(fj.get("degraded"))[:60])
    # ⑥ 坏动作 id：明确报错
    pol = c.post("/api/ppt/polish", json={"job_id": "不存在的任务", "actions": ["font"]})
    check("坏 job/动作 → 明确错误（不是 500）", pol.status_code in (200, 404) and "Traceback" not in pol.text,
          f"{pol.status_code}")
    # ⑦ 不存在的模板 id 渲染：退回内置主题而不是报错
    tr = c.post("/api/slide/render", json={"theme_id": "tech-editorial", "template_id": "不存在",
                                          "slides": slides}).json()
    check("不存在的模板 → 退回内置主题继续渲染",
          bool(tr.get("ok")) and len(tr.get("pages") or []) == len(slides), tr.get("theme_id"))
    plans_after2 = _files(plans)
    check("边界用例也没碰 content/plans", plans_before == plans_after2)

    print("\n[12] 收尾：别把体检模板留在老师的模板库里")
    tpl_before = {t["id"] for t in (c.get("/api/slide/templates").json().get("imported") or [])}
    leftover = sorted({tpl_new, fj.get("id") or ""} - {""})
    for tid in leftover:
        c.post("/api/slide/template/delete", json={"template_id": tid})
    tpl_after = {t["id"] for t in (c.get("/api/slide/templates").json().get("imported") or [])}
    check("体检用过的模板已清掉（不污染老师的选择列表）",
          not (tpl_after & set(leftover)), f"清理 {len(leftover)} 套；剩余 {len(tpl_after)} 套")
    check("老师原有的模板一套没少", tpl_before - set(leftover) <= tpl_after,
          f"原有 {len(tpl_before - set(leftover))} 套")

    c.close()
    ok = sum(1 for _, o, _ in RESULTS if o)
    print(f"\n结果：{ok}/{len(RESULTS)} 项通过")
    for name, o, extra in RESULTS:
        if not o:
            print(f"  ! {name} {extra}")
    return 0 if ok == len(RESULTS) else 1


def _openable(path: Path) -> bool:
    """产物能不能被 python-pptx 重新打开（顺带看 zip 完整性）。"""
    try:
        from pptx import Presentation

        with zipfile.ZipFile(path) as z:
            if z.testzip() is not None:
                return False
        prs = Presentation(str(path))
        return len(prs.slides._sldIdLst) > 0        # noqa: SLF001
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    t0 = time.time()
    code = main()
    print(f"用时 {time.time() - t0:.1f}s")
    raise SystemExit(code)
