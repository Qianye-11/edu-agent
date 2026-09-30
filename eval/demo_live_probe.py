# -*- coding: utf-8 -*-
"""演示前实探：对**正在运行**的 Web UI 跑一遍演示题，打印真实耗时 / 首字延迟 / 引用页码。

为什么需要这么做：
    docs/27 里 3~7s 的耗时来自 eval/demo_canned_answers.md 的**预跑存档**。
    现场机器的负载、是否开精排（RERANK_ON）、以及**同一会话里的前几轮问答**，
    都会改变真实延迟与检索结果。上台前用真机实探一遍，才知道哪几题能点、哪几题要撤。

用法：
    .venv\Scripts\python.exe eval\demo_live_probe.py                 # 默认探 5181 的 5 道核心题
    .venv\Scripts\python.exe eval\demo_live_probe.py --all           # 加旁栏 / 坐标题
    .venv\Scripts\python.exe eval\demo_live_probe.py --port 5174
    .venv\Scripts\python.exe eval\demo_live_probe.py --only 5        # 只探第 5 题
    .venv\Scripts\python.exe eval\demo_live_probe.py --degrade       # 挑一道**真能降级**的越界题

退出码：0 = 全部在阈内可演；1 = 有题超时或没达预期（现场别点，改用 eval/demo_canned_answers.md）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")   # Windows 控制台默认 gbk，emoji 会炸

# (问句, 期望坐标, 期望页码, 是否必演)
CORE = [
    ("基本不等式是什么？什么时候取等号？", "第二章 2.2", 44, True),
    ("并集、交集、补集分别是怎样定义的？", "第一章 1.3", 10, True),
    ("什么是充分条件、必要条件和充要条件？", "第一章 1.4", 17, True),
    ("怎么判断一个函数在某区间上的单调性？", "第三章 3.2", 76, True),
    ("计算不定积分 ∫x^2 dx", "（越界：期望降级、0 引用）", None, True),
]
EXTRA = [
    ("对数是谁发明的，教材阅读材料讲了什么？", "第四章·文献阅读与写作", None, False),
    ("请解释教材4.2节的主要内容。", "第四章 4.2", None, False),
    ("第二章复习参考题在哪里？", "第二章·复习参考题2", None, False),
]
# 越界降级是演示的「王牌反差」，但必须真能降级才算数 —— 这几道候选逐个试。
DEGRADE = [
    "计算不定积分 ∫x^2 dx",
    "求解微分方程 y' = y",
    "求矩阵 A 的特征值",
    "什么是拉格朗日中值定理？",
    "用洛必达法则求极限",
    "解释一下傅里叶变换",
]
SLOW_LINE = 25.0        # 超过它现场别点（docs/27 §5 风险 3）
HARD_LINE = 60.0        # 超过它直接判定禁用
# 越界题的「干净降级」判据 = 明确声明未收录 + 明确标注通用推理（非教材依据）。
# 注意：**允许带引用**——实测它会把检索到的沾边片段当「反面证据」列出来，
#      并明说「这些和问题无关，不能拿来替代」，这比单纯 0 引用更强。
DEGRADE_HINT = ("未收录", "不硬编")
NOT_BOOK_HINT = ("非教材依据", "通用推理")


def ask(port: int, text: str, session_id: str | None, timeout: float) -> dict:
    """打一发 /api/chat（SSE），返回耗时 / 首字延迟 / done 事件。"""
    t0 = time.time()
    first = None
    done: dict | None = None
    err: str | None = None
    body = {"text": text, "preset": "A", "corpus": "textbook"}
    if session_id:
        body["session_id"] = session_id
    with requests.post(f"http://127.0.0.1:{port}/api/chat", json=body,
                       stream=True, timeout=timeout) as resp:
        resp.raise_for_status()
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data: "):
                continue
            try:
                ev = json.loads(raw[6:])
            except json.JSONDecodeError:
                continue
            if ev.get("t") == "d" and first is None:
                first = time.time() - t0
            elif ev.get("t") == "done":
                done = ev
            elif ev.get("t") == "error":
                err = ev.get("text")
    return {"total": time.time() - t0, "first": first, "done": done, "error": err}


def new_session(port: int) -> str | None:
    """建一个专用会话 —— 别把实探记录灌进用户当前会话（用完记得删）。"""
    try:
        r = requests.post(f"http://127.0.0.1:{port}/api/session/new", timeout=10)
        if r.ok:
            return (r.json() or {}).get("id")
    except requests.RequestException:
        pass
    return None


def del_session(port: int, sid: str) -> None:
    try:
        requests.post(f"http://127.0.0.1:{port}/api/session/del", json={"id": sid}, timeout=8)
    except requests.RequestException:
        pass


def verdict(expect: str, page: int | None, cites: list, ans: str) -> tuple[bool, str]:
    """返回 (是否达标, 说明)。越界题看是否真的降级；其余题看有没有引用/命中页码。"""
    if expect.startswith("（越界"):
        declared = any(h in ans for h in DEGRADE_HINT)
        labeled = any(h in ans for h in NOT_BOOK_HINT)
        clean = declared and labeled
        if clean:
            note = "干净降级（声明未收录 + 标注通用推理，另带 %d 条反面引用）" % len(cites)
        elif declared:
            note = "只说未收录，没标注「通用推理」"
        else:
            note = "未见降级声明（疑似硬答）"
        return clean, note
    if page is not None:
        hit = [c for c in cites if c.get("page") == page]
        return bool(hit), ("命中 p%d" % page) if hit else ("未命中 p%d" % page)
    return bool(cites), ("引用 %d 条" % len(cites)) if cites else "无引用"


def run_one(port: int, sid: str | None, q: str, expect: str, page: int | None,
            timeout: float) -> dict:
    try:
        r = ask(port, q, sid, timeout)
    except Exception as e:                                        # noqa: BLE001
        return {"q": q, "error": f"{type(e).__name__}: {e}", "ok": False}
    done = r["done"] or {}
    cites = done.get("citations") or []
    ans = done.get("text") or ""
    good, note = verdict(expect, page, cites, ans)
    loc = ""
    if cites:
        c0 = cites[0]
        loc = " ".join(str(x) for x in (c0.get("chapter"), c0.get("section"),
                                        c0.get("heading")) if x)
        loc += f" p{c0.get('page')}"
    return {"q": q, "seconds": round(r["total"], 1),
            "first_token": round(r["first"] or 0, 1),
            "citations": len(cites), "top": loc, "expect": expect,
            "expect_page": page, "ok": good, "note": note,
            "answer_head": ans[:240].replace("\n", " "), "error": r["error"]}


def mark_of(row: dict) -> str:
    if row.get("error"):
        return "❌"
    s = row.get("seconds", 0)
    if s > HARD_LINE:
        return "⛔"
    if s > SLOW_LINE:
        return "🐢"
    return "✅" if row.get("ok") else "⚠️"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5181)
    ap.add_argument("--all", action="store_true", help="连旁栏 / 坐标题一起探")
    ap.add_argument("--only", default="", help="只探指定序号，逗号分隔，如 5 或 1,2,5")
    ap.add_argument("--degrade", action="store_true", help="只在越界候选里挑真能降级的题")
    ap.add_argument("--timeout", type=float, default=180.0, help="单题超时（秒）")
    args = ap.parse_args()

    try:
        requests.get(f"http://127.0.0.1:{args.port}/", timeout=8)
    except requests.RequestException as e:
        print(f"[实探] 连不上 http://127.0.0.1:{args.port} —— {e}")
        print("       先起服务：deploy\\start_web_5181.cmd（或 启动课本教练.bat，默认 5174）")
        return 2

    sid = new_session(args.port)
    print(f"[实探] 目标 http://127.0.0.1:{args.port}  会话={sid or '(复用当前会话)'}\n")

    if args.degrade:
        items = [(q, "（越界：期望降级、0 引用）", None, False) for q in DEGRADE]
    else:
        items = CORE + (EXTRA if args.all else [])
        if args.only:
            want = {int(x) for x in args.only.replace("，", ",").split(",") if x.strip()}
            items = [it for i, it in enumerate(items, 1) if i in want]

    rows = []
    for i, (q, expect, page, must) in enumerate(items, 1):
        row = run_one(args.port, sid, q, expect, page, args.timeout)
        row["must_show"] = must
        rows.append(row)
        print(f"{i:>2} {mark_of(row)} {row.get('seconds', 0):5.1f}s "
              f"首字{row.get('first_token', 0):4.1f}s 引用{row.get('citations', 0)} | "
              f"{q[:22]:<24} → {row.get('top') or '(无引用)'}"
              + (f"  [{row['note']}]" if row.get("note") else ""))

    bad = [x for x in rows if x.get("error") or x.get("seconds", 0) > SLOW_LINE or not x.get("ok")]
    print("\n" + "=" * 72)
    print(f"实探完成：{len(rows)} 题，达标 {sum(1 for x in rows if x.get('ok'))} 题，"
          f"需处置 {len(bad)} 题")
    for x in bad:
        print(f"  ⛔ {x['q'][:26]}  {x.get('seconds')}s  {x.get('note') or x.get('error')}")
        print(f"     → 现场别点；兜底用 eval/demo_canned_answers.md"
              f"{'（或改用 --degrade 挑一道能降级的越界题）' if x.get('expect', '').startswith('（越界') else ''}")
    out = Path(__file__).with_name("demo_live_probe.json")
    out.write_text(json.dumps({"at": datetime.now().isoformat(timespec="seconds"),
                               "port": args.port, "session": sid, "rows": rows},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n存档：{out.relative_to(Path(__file__).resolve().parents[1])}（会话 {sid} 请用完删）")
    if sid:
        del_session(args.port, sid)
        print(f"已清理实探会话 {sid}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
