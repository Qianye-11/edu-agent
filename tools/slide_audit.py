# -*- coding: utf-8 -*-
"""版式审计 CLI：先打印基线，`--gate` 时有不合格项就非零退出。

引擎在 `src/edu_agent/slide_audit.py`（模型内审计，不需要浏览器，理由见 `docs/22` §7.3）。

用法（仓库根）：
    .venv\\Scripts\\python.exe tools\\slide_audit.py                    # 三套主题的示例 deck，打印基线
    .venv\\Scripts\\python.exe tools\\slide_audit.py --theme hand-drawn
    .venv\\Scripts\\python.exe tools\\slide_audit.py --gate             # 验收模式：有 error 就退出 1
    .venv\\Scripts\\python.exe tools\\slide_audit.py --selftest         # 负样本自检（证明它抓得住坏页）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from edu_agent import slide_audit, slide_deck, ppt_tokens as tk  # noqa: E402

OUT_DIR = _ROOT / "data" / "tmp" / "slide_audit"


def _fmt_baseline(rep: dict) -> str:
    rows = ["  指标                        基线值        目标        ",
            "  ------------------------    ----------   ----------"]
    for tid, b in rep.items():
        rows.append(f"  [{tid}] {b['name']}")
        rows.append(f"    内容纵向占比（最小）      {b['active_ratio_min']:.0%}"
                    f"          ≥{slide_audit.LIMITS['active_ratio_min']:.0%}")
        rows.append(f"    底部空白（最大）          {b['bottom_gap_max']:.0f}px"
                    f"        ≤{slide_audit.LIMITS['bottom_gap_max']:.0f}px")
        rows.append(f"    安全边距（最小）          {b['margin_min']:.0f}px        ≥tokens.margin")
        rows.append(f"    最大空白矩形（记录用）    {b['empty_rect_max']:.0%}")
        rows.append(f"    文字碰撞                  {b['collisions']} 处       0 处")
        rows.append(f"    主色面积（最大）          {b['accent_ratio_max']:.1%}"
                    f"        <{slide_audit.LIMITS['accent_ratio_max']:.0%}")
        rows.append(f"    左右失衡（最大）          {b['imbalance_max']:.0%}"
                    f"         ≤{slide_audit.LIMITS['imbalance_max']:.0%}（安静侧豁免）")
        rows.append(f"    标题下间距（最小）        {b['title_gap_min']:.0f}px"
                    f"        ≥{slide_audit.LIMITS['title_gap_min']:.0f}px")
        rows.append(f"    用到的字号                {b['font_sizes']}")
        rows.append(f"    结论                      error {b['errors']} / warn {b['warnings']}")
    return "\n".join(rows)


def selftest() -> int:
    """负样本自检：故意造三种坏页，审计必须抓得住。"""
    good = slide_deck.build_deck("tech-editorial", slide_deck.SAMPLE_DECK, title="3.3 幂函数")
    pages, layouts = good["pages"], good["layouts"]

    print("负样本自检（每个坏页都必须被抓到）")
    cases: list[tuple[str, list[str], list[str], str]] = [
        ("封面标题过长 → 越界", [pages[0].replace("3.3 幂函数", "3.3 幂函数与它的十七个常见变形以及全部图象性质汇总")] + pages[1:],
         layouts, "overflow"),
        ("版式不在白名单", pages, ["cover", "not-a-layout", layouts[2]], "whitelist"),
        ("字号跑到阶梯外", [pages[0], pages[1].replace('font-size="24.0"', 'font-size="13.0"'), pages[2]],
         layouts, "font-scale"),
        ("内容挤到边距外", [pages[0].replace('x="72.0"', 'x="20.0"'), pages[1], pages[2]], layouts, "margin"),
    ]
    ok = True
    for name, mutated, lays, want in cases:
        rep = slide_audit.audit_pages("tech-editorial", mutated, lays)
        codes = {f["code"] for f in rep["errors"]}
        hit = want in codes
        ok = ok and hit
        print(f"  {'✅' if hit else '❌'} {name} → 期望 {want}，实际 {sorted(codes) or '（没抓到）'}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="版式审计（模型内）")
    ap.add_argument("--theme", action="append", default=None, help="主题 id，可重复；默认全部")
    ap.add_argument("--gate", action="store_true", help="验收模式：有 error 就退出 1")
    ap.add_argument("--json", action="store_true", help="把原始数值写到 data/tmp/slide_audit/")
    ap.add_argument("--selftest", action="store_true", help="只跑负样本自检")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    themes = args.theme or tk.available()
    if not themes:
        print("没有可用主题（content/ppt_templates/_builtin/*.json 是空的？）")
        return 2

    print("幻灯片版式审计（模型内；定义见 docs/22 §7.1 / §7.1b）")
    base = slide_audit.baseline(themes)
    print("\n[基线]\n" + _fmt_baseline(base))

    total_err = total_warn = 0
    for tid in themes:
        rep = slide_audit.audit_deck(tid)
        total_err += rep["stats"]["errors"]
        total_warn += rep["stats"]["warnings"]
        if args.json:
            p = slide_audit.write_report(rep, OUT_DIR / f"{tid}.json")
            print(f"\n  原始数值：{p}")
        for row in rep["pages"]:
            head = f"  第 {row['page']} 页 [{row['layout']}] 盒子 {row['metrics']['boxes']}"
            print(f"\n{head}")
            for f in row["findings"]:
                mark = {"error": "❌", "warn": "⚠️", "info": "ℹ️"}[f["level"]]
                print(f"    {mark} [{f['code']}] {f['detail']}")
                if f["level"] != "info":
                    print(f"        修法：{f['fix']}")

    print(f"\n结果：error {total_err} · warn {total_warn}")
    if total_err:
        print("（有不合格项：按每条下面的「修法」处理；**不要用删内容来解决**）")
    return 1 if (args.gate and total_err) else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
