# -*- coding: utf-8 -*-
"""全量闸门（R24）：一条命令跑完所有验收，最后给一张矩阵。

为什么要有它：到 R24 为止，验收已经分成七摊（单测 / 面板检查 / 版式审计 / PPT 体检 /
导出验收（含 WPS 真机）/ 产物交付体检 / 导出任务目录校验）。
老师与后来的人不该去记"先跑哪个、看哪个数字"，所以这里**一条命令**跑完并只回答一个问题：
**这一版能不能交**（硬项全绿 = 能）。

用法：
    .venv\\Scripts\\python.exe tools\\full_gate.py            # 全量（约 6~8 分钟）
    .venv\\Scripts\\python.exe tools\\full_gate.py --quick    # 跳过单测与导出验收（约 4 分钟）
    .venv\\Scripts\\python.exe tools\\full_gate.py --only=audit,smoke

判据（与各脚本自己的判据一致，不另立一套）：
  · 单测        → `OK`（skipped 可以有，failure/error 不行）
  · 面板检查    → 0 失败
  · 版式审计    → error 0
  · PPT 体检    → 49/49
  · 导出验收    → 全项通过（含 WPS 真机）
  · 交付体检    → 产物体检无 error（ppt-master 的 pptx_delivery_check.py，只读）
  · 任务目录校验 → **error 0**（batch_validate 的 warning 是"缺 README/设计规范"这类
                  PPT Master 作者工作流的文档约定，我们的流水线本来就不产出它们，不算缺陷）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
NODE = "node"
PM_SCRIPTS = ROOT / ".deps" / "ppt-master" / "skills" / "ppt-master" / "scripts"
RESULTS: list[tuple[str, bool, str]] = []


def run(cmd: list[str], timeout: int = 3600) -> tuple[int, str]:
    """跑一条命令，返回 (exit, 输出尾巴)。输出太长只留尾巴（矩阵里够用）。

    环境里强制 `PYTHONIOENCODING=utf-8`：子脚本有的没自己 reconfigure，GBK 控制台下吐出来的
    中文会被按 UTF-8 解成乱码（矩阵里就成了问号脸 —— 踩过）。
    """
    t0 = time.time()
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"{type(exc).__name__}: {exc}"
    tail = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    print(f"      （{time.time() - t0:.0f}s）")
    return proc.returncode, tail


def line(name: str, ok: bool, detail: str) -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}　{detail}")


# ---------------------------------------------------------------- 各摊验收
def jscheck() -> None:
    print("\n[1] 前端内联脚本语法（tools/jscheck.py）")
    code, out = run([PY, "tools/jscheck.py"])
    m = re.search(r"OK.*?(\d+)\s*段.*?(\d+)\s*行", out) or re.search(r"OK\s+共\s*(\d+)\s*段，(\d+)\s*行", out)
    ok = code == 0 and "OK" in out
    line("内联脚本语法", ok, f"{m.group(1)} 段 / {m.group(2)} 行" if m else out.strip()[-60:])


def audit() -> None:
    print("\n[2] 版式审计（tools/slide_audit.py --gate）")
    code, out = run([PY, "tools/slide_audit.py", "--gate"])
    m = re.search(r"error\s*(\d+)\s*·\s*warn\s*(\d+)", out)
    ok = code == 0 and bool(m) and m.group(1) == "0"
    line("版式审计 error=0", ok, f"error {m.group(1)} / warn {m.group(2)}" if m else out.strip()[-70:])


def smoke() -> None:
    print("\n[3] PPT 体检（tools/ppt_smoke.py）")
    code, out = run([PY, "tools/ppt_smoke.py"])
    m = re.search(r"(\d+)\s*/\s*(\d+)\s*项通过", out)
    ok = code == 0 and bool(m) and m.group(1) == m.group(2)
    line("PPT 体检全过", ok, f"{m.group(1)}/{m.group(2)}" if m else out.strip()[-70:])


def panel() -> None:
    print("\n[4] 面板检查（node tools/uicheck/panel_check.js）")
    code, out = run([NODE, "tools/uicheck/panel_check.js"])
    m = re.search(r"结果：(\d+)\s*通过\s*/\s*(\d+)\s*失败", out)
    ok = code == 0 and bool(m) and m.group(2) == "0"
    line("面板检查 0 失败", ok, f"{m.group(1)} 通过 / {m.group(2)} 失败" if m else out.strip()[-70:])


def unit() -> None:
    print("\n[5] 单元测试（unittest discover）")
    code, out = run([PY, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"], timeout=1800)
    m = re.search(r"Ran\s+(\d+)\s+tests?.*?\n(OK|FAILED)", out, re.S)
    skip = re.search(r"skipped=(\d+)", out)
    ok = code == 0 and bool(m) and m.group(2) == "OK"
    detail = (f"{m.group(1)} 例 OK" if m else "没解析出结果") + (f" · 跳过 {skip.group(1)}" if skip else "")
    line("单元测试全绿", ok, detail)


def registry() -> None:
    """R27a：设计规格注册表 —— 编译产物是否与来源 skill 一致。

    `.deps` 不在（换机器没装）时**跳过不阻断**：注册表本身是入库的、自包含的，
    没有 skill 也能跑（`docs/29` §1.2 红线）；有 skill 时才做"产物 vs 来源"的一致性比对。
    """
    print("\n[5b] 设计规格注册表（tools/compile_design_registry.py --check）")
    skill = ROOT / ".deps" / "ppt-master" / "skills" / "ppt-master" / "SKILL.md"
    if not skill.is_file():
        line("设计规格注册表", True, "ppt-master 不在（.deps），跳过比对 —— 注册表是自包含的，运行时不受影响")
        return
    code, out = run([PY, "tools/compile_design_registry.py", "--check"], timeout=600)
    diff = [ln for ln in out.splitlines() if ln.startswith("DIFF")]
    ok = code == 0 and not diff
    n = len([ln for ln in out.splitlines() if ln.startswith("OK")])
    line("设计规格注册表与来源一致", ok,
         f"{n} 份一致" if ok else f"有 {len(diff)} 份需要重新编译：{' '.join(diff)[:60]}")


def acceptance(with_wps: bool) -> None:
    print(f"\n[6] 导出验收（tools/ppt_acceptance.py{' --with-wps' if with_wps else ''}）")
    cmd = [PY, "tools/ppt_acceptance.py"] + (["--with-wps"] if with_wps else [])
    code, out = run(cmd, timeout=1800)
    m = re.search(r"结果：(\d+)\s*/\s*(\d+)\s*项通过", out)
    ok = code == 0 and bool(m) and m.group(1) == m.group(2)
    line("导出验收全过", ok, f"{m.group(1)}/{m.group(2)}" + ("（含 WPS 真机）" if with_wps else "")
         if m else out.strip()[-70:])


def delivery_check() -> None:
    """产物交付体检：拿**最新**那份 pptx 跑 ppt-master 的只读体检。"""
    print("\n[7] 产物交付体检（ppt-master pptx_delivery_check.py，只读）")
    tool = PM_SCRIPTS / "pptx_delivery_check.py"
    out_root = ROOT / "data" / "ppt_out"
    if not tool.is_file():
        line("交付体检", True, "ppt-master 不在，跳过（不阻断）")
        return
    pptx = sorted(out_root.glob("*.pptx"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not pptx:
        line("交付体检", True, "还没有 pptx 产物，跳过")
        return
    # 两条产品线各挑最新的一份：新版式导出（教案_*）与 PPT 优化（优化_*）
    picks: list[Path] = []
    for prefix in ("教案_", "优化_"):
        hit = next((p for p in pptx if p.name.startswith(prefix)), None)
        if hit is not None:
            picks.append(hit)
    picks = picks or pptx[:1]
    bad: list[str] = []
    notes: list[str] = []
    for p in picks:
        code, out = run([PY, str(tool), str(p)], timeout=300)
        data: dict = {}
        try:
            data = json.loads(out[out.find("{"):out.rfind("}") + 1] or "{}")
        except Exception:                                    # noqa: BLE001
            data = {}
        summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
        errors = summary.get("errors") if isinstance(summary.get("errors"), list) else []
        adv = summary.get("advisories") if isinstance(summary.get("advisories"), list) else []
        status = str(data.get("status") or summary.get("status")
                     or ("passed" if code == 0 else "unknown"))
        # `passed-with-advisories` = **过了，只是有话要说**（与 svg_quality_checker 的
        # "passed-with-warnings 仍 exit 1" 同一族）—— 别把它当失败，否则闸门天天红。
        good = (status.startswith("passed") or status in ("ok", "pass")) and not errors
        notes.append(f"{p.name} · {status}" + (f" · {len(adv)} 条建议" if adv else "")
                     + (f" · {len(errors)} 条 error" if errors else ""))
        if not good:
            bad.append(p.name)
    line("产物交付体检", not bad, "；".join(notes))


def batch_validate() -> None:
    """导出任务目录校验：**只看 error**（warning 是 PPT Master 作者工作流的文档约定）。"""
    print("\n[8] 导出任务目录校验（ppt-master batch_validate.py）")
    tool = PM_SCRIPTS / "batch_validate.py"
    jobs = ROOT / "data" / "tmp" / "ppt_jobs"
    if not tool.is_file():
        line("任务目录校验", True, "ppt-master 不在，跳过（不阻断）")
        return
    if not jobs.is_dir():
        line("任务目录校验", True, "还没有导出任务目录，跳过")
        return
    code, out = run([PY, str(tool), str(jobs)], timeout=1800)
    m = re.search(r"Total projects:\s*(\d+)", out)
    e = re.search(r"\[ERROR\]\s*With errors:\s*(\d+)", out)
    w = re.search(r"\[WARN\]\s*With warnings:\s*(\d+)", out)
    total = m.group(1) if m else "?"
    errs = int(e.group(1)) if e else -1
    warns = w.group(1) if w else "?"
    ok = errs == 0
    line("任务目录校验 error=0", ok,
         f"{total} 个任务目录 · error {errs if errs >= 0 else '?'} · warning {warns}"
         + ("（warning 是缺 README/设计规范这类作者工作流约定，不算缺陷）" if warns != "0" else ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="跳过单测与导出验收（约 4 分钟）")
    ap.add_argument("--no-wps", action="store_true", help="导出验收不打开 WPS 真机")
    ap.add_argument("--only", default="",
                    help="只跑这几摊，逗号分隔：js,audit,smoke,panel,unit,registry,accept,delivery,batch")
    args = ap.parse_args()
    want = {s.strip() for s in args.only.split(",") if s.strip()}

    def on(name: str) -> bool:
        return (not want) or (name in want)

    print("=" * 78)
    print("全量闸门（tools/full_gate.py）—— 硬项全绿才算能交")
    print("=" * 78)
    if on("js"):
        jscheck()
    if on("audit"):
        audit()
    if on("smoke"):
        smoke()
    if on("panel"):
        panel()
    if on("unit") and not args.quick:
        unit()
    if on("registry"):
        registry()
    if on("accept") and not args.quick:
        acceptance(with_wps=not args.no_wps)
    if on("delivery"):
        delivery_check()
    if on("batch"):
        batch_validate()

    print("\n" + "=" * 78)
    print("矩阵")
    print("=" * 78)
    for name, ok, detail in RESULTS:
        print(f"  {'✅' if ok else '❌'} {name:<18} {detail}")
    bad = [n for n, ok, _ in RESULTS if not ok]
    print()
    if bad:
        print(f"结果：{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过 —— 没过的：{('、'.join(bad))}")
        return 1
    print(f"结果：{len(RESULTS)}/{len(RESULTS)} 全过 —— 这一版可以交")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")       # 控制台默认 GBK，打印 ✅ 会当场炸
    raise SystemExit(main())
