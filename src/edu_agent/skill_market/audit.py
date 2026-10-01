# -*- coding: utf-8 -*-
"""SKILL-04 · 沙箱与审计：**只追加、不可改删、全程脱敏**的审计日志。

三条硬规矩（写进代码，不靠自觉）：

1. **只追加**：本模块只暴露 `append()` / `query()`，没有 update/delete。文件以
   `open(path, "a")` 追加，且每条记录带 `prev_hash` + `hash` 的**哈希链** ——
   事后有人手改一行，`verify_chain()` 立刻能指出断在哪条。
2. **脱敏**：写盘前所有字段过 `redact()`。学生姓名/学号/手机号/邮箱/身份证被替换成
   `sha256:xxxx(len=n)`；`answer/answers/raw_answer/transcript` 这类键**只留摘要与长度**，
   原文一律不落盘（这是"原始学生作答明文不能传入 Skill"的兜底闸门）。
3. **可追溯**：每条记录带 版本、操作者、输入摘要、执行结果、闸门结论 —— 正好是
   SKILL-04 要求的那四样，外加状态流转的 from/to。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from edu_agent.skill_market import paths

# 键名命中即"敏感"：值只留摘要
_PII_KEY_RE = re.compile(
    r"(name|student|pupil|phone|mobile|tel|email|mail|id_?card|address|"
    r"answer|answers|answer_text|raw|transcript|homework|score|grade|"
    r"password|secret|token|api_?key|credential)", re.I)
# 值里的隐私模式（即使键名无害也要擦）
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")
_IDCARD_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
# 自由文本里的姓名字段（必须带冒号，避免把"学生生成"这类正常词误擦）
_NAME_FIELD_RE = re.compile(r"(?:学生姓名|学生名|姓名|学生)\s*[:：]\s*[\u4e00-\u9fa5]{2,4}")
_STU_FIELD_RE = re.compile(r"(?:学号|考号)\s*[:：]\s*[A-Za-z0-9]{4,20}")
# 摘要展示上限
_SUMMARY_MAX = 120
_VALUE_MAX = 200

_lock = threading.Lock()


def digest(value: Any) -> str:
    """稳定摘要：sha256 前 12 位 + 原长度（可对账，不可还原）。"""
    raw = value if isinstance(value, (bytes, bytearray)) else str(value).encode("utf-8")
    return "sha256:%s(len=%d)" % (hashlib.sha256(raw).hexdigest()[:12], len(raw))


def redact(value: Any, *, key: str = "") -> Any:
    """递归脱敏。命中敏感键 → 只留摘要；字符串 → 擦隐私模式 + 截断。"""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            ks = str(k)
            if _PII_KEY_RE.search(ks):
                # 敏感键：不留原文，只留摘要与长度
                out[ks] = {"redacted": True, "digest": digest(v),
                           "chars": len(str(v))}
            else:
                out[ks] = redact(v, key=ks)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(v, key=key) for v in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    s = str(value)
    if _PII_KEY_RE.search(key):
        return {"redacted": True, "digest": digest(s), "chars": len(s)}
    s = _PHONE_RE.sub(lambda m: "1**********", s)
    s = _EMAIL_RE.sub(lambda m: "***@***", s)
    s = _IDCARD_RE.sub(lambda m: "******************", s)
    s = _NAME_FIELD_RE.sub("姓名:***", s)
    s = _STU_FIELD_RE.sub("学号:***", s)
    if len(s) > _VALUE_MAX:
        s = s[:_VALUE_MAX] + "…" + digest(s)
    return s


def summarize(text: Any) -> dict[str, Any]:
    """输入摘要：长度 + 摘要 + 脱敏后的短预览（给审计"输入摘要"字段用）。"""
    s = str(text or "")
    preview = redact(s)
    if isinstance(preview, str) and len(preview) > _SUMMARY_MAX:
        preview = preview[:_SUMMARY_MAX] + "…"
    return {"chars": len(s), "digest": digest(s), "preview": preview}


# ---------------------------------------------------------------- 记录写入
def _month_file(ts: float | None = None) -> Path:
    t = time.localtime(ts or time.time())
    return paths.audit_dir() / time.strftime("%Y-%m.jsonl", t)


def _canonical(record: dict) -> str:
    body = {k: v for k, v in record.items() if k != "hash"}
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _last_hash(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            last = ""
            for line in fh:
                if line.strip():
                    last = line
            if not last:
                return ""
            return str(json.loads(last).get("hash") or "")
    except Exception:  # noqa: BLE001  日志坏了也不能让业务流程炸
        return ""


def _next_seq(path: Path) -> int:
    if not path.exists():
        return 1
    n = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.strip():
                    n += 1
    except Exception:  # noqa: BLE001
        return 1
    return n + 1


def chain_append(path: Path, record: dict) -> dict:
    """把一条记录追加到任意 JSONL 并接上哈希链（seq/prev_hash/hash 由本函数补）。

    公共件：审计日志（`audit/`）与运行痕迹（`runs/`）共用同一套"只追加 + 可校验"的写法，
    免得两处各写一遍、日后各错一半。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {k: v for k, v in dict(record).items() if k != "hash"}
    rec["seq"] = _next_seq(path)
    rec["prev_hash"] = _last_hash(path)
    rec["hash"] = hashlib.sha256(
        (rec["prev_hash"] + _canonical(rec)).encode("utf-8")).hexdigest()
    line = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()
        try:
            os.fsync(fh.fileno())      # 审计不能只停在页缓存里
        except OSError:
            pass
    return rec


def verify_chain_in(path: Path) -> list[dict]:
    """校验单个 JSONL 的哈希链，返回断链/被改的记录清单（空 = 完好）。"""
    broken: list[dict] = []
    path = Path(path)
    if not path.exists():
        return broken
    prev = ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            broken.append({"file": path.name, "seq": None, "why": "不是合法 JSON（被改过？）"})
            continue
        if rec.get("prev_hash", "") != prev:
            broken.append({"file": path.name, "seq": rec.get("seq"), "why": "prev_hash 断链"})
        want = hashlib.sha256(
            (rec.get("prev_hash", "") + _canonical(rec)).encode("utf-8")).hexdigest()
        if rec.get("hash") != want:
            broken.append({"file": path.name, "seq": rec.get("seq"),
                           "why": "hash 不匹配（记录被修改）"})
        prev = rec.get("hash", "")
    return broken


def append(event: str, *, skill: str = "", version: str = "", actor: str = "",
           session_id: str = "", status_from: str = "", status_to: str = "",
           input_text: str = "", result: dict | None = None,
           gates: dict | None = None, extra: dict | None = None) -> dict:
    """追加一条审计记录（返回落盘的那条，含 seq/hash）。**没有对应的删除接口。**"""
    paths.ensure_dirs()
    ts = time.time()
    path = _month_file(ts)
    with _lock:
        rec: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ts)),
            "event": str(event),
            "skill": str(skill),
            "version": str(version),
            "actor": redact(str(actor)),
            "session_id": str(session_id),
            "status": {"from": str(status_from), "to": str(status_to)},
            "input": summarize(input_text) if input_text else None,
            "result": redact(result or {}),
            "gates": redact(gates or {}),
        }
        if extra:
            rec["extra"] = redact(extra)
        return chain_append(path, rec)


# ---------------------------------------------------------------- 读取 / 校验
def query(skill: str = "", event: str = "", limit: int = 50) -> list[dict]:
    """按技能/事件读审计（只读；市场详情页与对账脚本用）。"""
    out: list[dict] = []
    if not paths.audit_dir().exists():
        return out
    for path in sorted(paths.audit_dir().glob("*.jsonl"), reverse=True):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if skill and rec.get("skill") != skill:
                continue
            if event and rec.get("event") != event:
                continue
            out.append(rec)
            if len(out) >= max(1, int(limit)):
                return out
    return out


def verify_chain() -> dict:
    """校验哈希链（防篡改）。返回 {ok, files, records, broken:[...]}。"""
    broken: list[dict] = []
    files = 0
    records = 0
    if not paths.audit_dir().exists():
        return {"ok": True, "files": 0, "records": 0, "broken": []}
    for path in sorted(paths.audit_dir().glob("*.jsonl")):
        files += 1
        records += sum(1 for line in path.read_text(encoding="utf-8", errors="replace")
                       .splitlines() if line.strip())
        broken.extend(verify_chain_in(path))
    return {"ok": not broken, "files": files, "records": records, "broken": broken}
