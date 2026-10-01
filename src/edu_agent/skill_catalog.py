# -*- coding: utf-8 -*-
"""DSH 式技能：**发现 + 按需加载**（写一个 SKILL.md 就等于创建一个技能）。

## 与模块3（skill_market）的分工 —— 两条车道，别混

| 车道 | 谁创建 | 生效时机 | 审核 |
|---|---|---|---|
| **快车道（本模块）** | 人 / agent 直接写 `SKILL.md` | 下一次调用即被发现（每次现读盘） | 无（与 DSH 一致） |
| **慢车道（skill_market）** | 从跑通的会话自动挖掘 → 草稿 → L1/L2/L3 → 人工审核 → 发布 | 只有 `published` 才进运行时目录 | 有，四道闸门 |

两条车道的**落地点是同一个运行时目录**：慢车道发布时把技能拷进 `<data_dir>/skills`，
所以本模块同样能发现它 —— 不需要两套发现逻辑。

**草稿永远不会被这里发现**：草稿住在 `skill_market/drafts`，不在任何扫描根里。

## 与 DSH 的逐条对照

| DSH（`dsh-skill-filesystem` + `dsh-tool-skill`） | 这里 |
|---|---|
| 5 个根 + rank（100/200/300/400/500） | `ROOTS` 表，rank 小的赢 |
| `<name>/SKILL.md` 或平铺 `<name>.md`；**不递归** `**/SKILL.md` | 同 |
| frontmatter 必填 `name`/`description`；可选 `whenToUse`/`metadata`/`disable-model-invocation`/`user-invocable` | 同（布尔严格解析，非法即**丢弃整个技能并告警**） |
| 会话目录（名字+限长描述）+ `skill` 工具按需加载正文 | `catalog_block()` + `load()`；因为 edu-agent 没有工具调用循环，改成**相关性命中 / 显式指令**时把正文注入提示词 |
| 监视根目录 → 免重启热重载 | 每次调用都现读盘（等价效果，且更简单） |
| 真 YAML frontmatter（`description: >` 折叠块合法） | 同（有 PyYAML 用真 YAML，没有就退回逐行） |
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from edu_agent.config import load_settings

DESC_MAX = 500          # 目录里渲染的描述上限（对齐 DSH 的 catalogDescriptionMaxLength 默认值）
BODY_INJECT_MAX = 2600  # 注入正文的长度上限（别把提示词撑爆）
MAX_INJECT = 2          # 最多同时注入几个技能的正文

_TRUE = {"true", "yes", "on", "1"}
_FALSE = {"false", "no", "off", "0"}
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


# ---------------------------------------------------------------- 根目录与优先级
def _project_root() -> Path:
    """项目根 = 含 .git 的最近祖先；没有就用 cwd（与 DSH 同口径）。"""
    here = Path(__file__).resolve()
    for p in [here] + list(here.parents):
        if (p / ".git").exists():
            return p
    return Path.cwd()


def roots() -> list[tuple[int, str, Path]]:
    """(rank, 来源名, 路径)。rank 小的赢（同名技能以先出现的为准）。"""
    out: list[tuple[int, str, Path]] = [
        (100, "project-skills", _project_root() / "skills"),          # 随仓库走的技能
        (200, "runtime-skills", Path(load_settings().data_dir) / "skills"),  # 运行时（含模块3 发布的）
    ]
    env = (os.getenv("EDU_SKILLS_DIR") or "").strip()
    if env:
        out.append((300, "env-skills", Path(env)))
    home = Path(os.getenv("EDU_AGENT_HOME") or (Path.home() / ".edu-agent"))
    out.append((400, "user-skills", home / "skills"))
    # 去重（同一路径只留 rank 最小的一次）
    seen: set[str] = set()
    uniq: list[tuple[int, str, Path]] = []
    for rank, name, p in sorted(out, key=lambda x: x[0]):
        key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append((rank, name, p))
    return uniq


# ---------------------------------------------------------------- frontmatter
def _yaml():
    try:
        import yaml

        return yaml
    except Exception:  # noqa: BLE001
        return None


def parse_front_matter(text: str) -> dict:
    """解析 YAML frontmatter。有 PyYAML 就真解析（`description: >` 折叠块合法），否则逐行。"""
    raw = str(text or "")
    if not raw.startswith("---"):
        return {}
    end = raw.find("\n---", 3)
    if end < 0:
        return {}
    block = raw[3:end]
    y = _yaml()
    if y is not None:
        try:
            data = y.safe_load(block)
            if isinstance(data, dict):
                return {str(k).lower(): v for k, v in data.items()}
        except Exception:  # noqa: BLE001
            pass
    meta: dict[str, str] = {}
    for line in block.splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        meta[k.strip().lower()] = v.strip().strip('"').strip("'")
    return meta


def _strict_bool(value, field: str, name: str) -> tuple[bool | None, str]:
    """按 DSH 口径严格解析布尔：只认 true/false/yes/no/on/off/1/0（不分大小写）。

    非法值 → 返回 (None, 告警)：**整个技能被丢弃**，而不是静默允许某个接口。
    """
    if value is None:
        return None, ""
    if isinstance(value, bool):
        return value, ""
    s = str(value).strip().lower()
    if s in _TRUE:
        return True, ""
    if s in _FALSE:
        return False, ""
    return None, "技能 %s 的 %s 不是合法布尔值（%r）：整个技能被丢弃" % (name, field, value)


def split_body(text: str) -> str:
    raw = str(text or "")
    if not raw.startswith("---"):
        return raw
    end = raw.find("\n---", 3)
    return raw[end + 4:].lstrip("\n") if end > 0 else raw


# ---------------------------------------------------------------- 发现
def _read_entry(path: Path, name_hint: str, rank: int, source: str) -> dict | None:
    """读一个 SKILL.md → 目录条目；格式不合法返回 None（并记告警）。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"name": name_hint, "_error": "读不到文件：%s" % e, "path": str(path),
                "rank": rank, "source": source}
    meta = parse_front_matter(text)
    name = str(meta.get("name") or "").strip()
    title = str(meta.get("title") or "").strip()
    desc = str(meta.get("description") or "").strip()
    errors: list[str] = []
    if not name or not _NAME_RE.match(name):
        # 向后兼容：老技能（含 skills_local 早期版本）把**中文名**写在 name: 里，
        # 而 DSH 口径要求 name 是 kebab-case 的 id。此时用目录名/文件名当 id，中文名降级为 title。
        if _NAME_RE.match(str(name_hint or "")):
            title = title or name
            name = name_hint
        else:
            errors.append("frontmatter 的 name 缺失或不是 kebab-case（%r）" % name)
    if not desc:
        errors.append("frontmatter 缺少必填字段 description")
    disable_model, warn1 = _strict_bool(meta.get("disable-model-invocation"), "disable-model-invocation", name)
    user_inv, warn2 = _strict_bool(meta.get("user-invocable"), "user-invocable", name)
    if warn1:
        errors.append(warn1)
    if warn2:
        errors.append(warn2)
    if errors:
        return {"name": name or name_hint, "_error": "；".join(errors), "path": str(path),
                "rank": rank, "source": source}
    body = split_body(text)
    meta_block = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
    return {
        "name": name,
        "title": title or name,
        "description": desc[:DESC_MAX],
        "when_to_use": str(meta.get("whentouse") or meta.get("when_to_use") or "").strip(),
        "metadata": meta_block,
        "disable_model_invocation": bool(disable_model) if disable_model is not None else False,
        "user_invocable": bool(user_inv) if user_inv is not None else True,
        "rank": rank,
        "source": source,
        "path": str(path),
        "dir": str(path.parent),
        "bytes": len(text.encode("utf-8")),
        "body_chars": len(body),
        "_error": "",
    }


def discover(include_errors: bool = False) -> dict:
    """扫所有根，返回 {entries, errors, roots}。

    规则（对齐 DSH）：只看根目录**顶层**的 `<name>/SKILL.md` 与 `<name>.md`，
    **刻意不递归** `**/SKILL.md`；同名以 rank 小者为准。
    """
    entries: dict[str, dict] = {}
    errors: list[dict] = []
    scanned: list[dict] = []
    for rank, source, root in roots():
        scanned.append({"rank": rank, "source": source, "path": str(root),
                        "exists": root.is_dir()})
        if not root.is_dir():
            continue
        try:
            children = sorted(root.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_dir():
                md = child / "SKILL.md"
                if not md.exists():
                    continue
                got = _read_entry(md, child.name, rank, source)
            elif child.suffix.lower() == ".md":
                got = _read_entry(child, child.stem, rank, source)
            else:
                continue
            if got is None:
                continue
            if got.get("_error"):
                errors.append(got)
                continue
            if got["name"] not in entries:          # rank 小的先到，先到先得
                entries[got["name"]] = got
            else:
                errors.append({**got, "_error": "同名技能已由 rank %s 提供，本条被忽略"
                                                 % entries[got["name"]]["rank"]})
    out = sorted(entries.values(), key=lambda e: (e["rank"], e["name"]))
    return {"ok": True, "entries": out, "errors": errors, "roots": scanned,
            "count": len(out)}


def catalog(model_invocable_only: bool = True) -> list[dict]:
    """面向模型的目录（`disable-model-invocation: true` 的技能被排除）。"""
    got = discover()
    rows = got["entries"]
    if model_invocable_only:
        rows = [r for r in rows if not r["disable_model_invocation"]]
    return rows


def find(name: str) -> dict | None:
    """按名查条目（含被排除的？不含：查不到就返回 None）。"""
    want = str(name or "").strip().lower()
    for r in discover()["entries"]:
        if r["name"].lower() == want:
            return r
    return None


def load(name: str) -> dict:
    """按名加载**正文**（每次现读盘 → 编辑正文无需重启/缓存失效）。"""
    entry = find(name)
    if entry is None:
        known = "、".join(r["name"] for r in discover()["entries"]) or "（无）"
        return {"ok": False, "error": "未知技能：%s（现有：%s）" % (name, known)}
    if entry["disable_model_invocation"]:
        return {"ok": False, "error": "技能 %s 被标记 disable-model-invocation，不能由模型加载" % name}
    try:
        text = Path(entry["path"]).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"ok": False, "error": "读不到技能正文：%s" % e}
    body = split_body(text)
    return {"ok": True, "name": entry["name"], "path": entry["path"],
            "description": entry["description"], "text": body,
            "chars": len(body), "truncated": False,
            "source": entry["source"], "rank": entry["rank"]}


# ---------------------------------------------------------------- 目录与按需注入
def catalog_block(limit: int = 12, desc_max: int = 200) -> str:
    """会话目录：每个技能给「名字 + 限长描述」。等价于 DSH 的持久目录消息。"""
    rows = catalog()[:max(1, int(limit))]
    if not rows:
        return ""
    items = "；".join("%s（%s）" % (r["name"], (r["description"] or "")[:desc_max]) for r in rows)
    return ("【我自己积累的技能（写 SKILL.md 即可新增，下次对话生效）】" + items +
            "。若当前问题和某个技能相关，就按该技能的做法执行；"
            "用户说「用技能 <名字>」时按那个技能做；说「存成技能：名字」时把这一轮的有效做法写进技能。")


_CLEAN_RE = re.compile(r"[^\w\u4e00-\u9fff]+")


def _bigrams(s: str) -> set[str]:
    """字符二元组。**先去掉标点与空白** —— 否则「帮我做个思维导图，梳理这一章」里的
    逗号会切出 `图，`/`，梳` 这种无意义二元组，把覆盖率稀释掉（实测：3/13=0.23 判不相关）。"""
    t = _CLEAN_RE.sub("", str(s or "")).lower()
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) > 1 else set()


def similarity(a: str, b: str) -> float:
    ba, bb = _bigrams(a), _bigrams(b)
    if not ba or not bb:
        return 0.0
    return len(ba & bb) / len(ba | bb)


def coverage(query: str, blob: str) -> tuple[float, int]:
    """**问题覆盖率**：问题的二元组有多少被技能描述覆盖（命中数, 覆盖率）。

    为什么不用 Jaccard：问题很短、技能描述很长，Jaccard 会被长描述稀释 ——
    "帮我做个思维导图" vs "根据 book_id 与章节号读取本地教材库，生成该章节 Markdown 思维导图"
    明明相关，Jaccard 却只有 0.05（实测踩过）。覆盖率 3/7=0.43 才是对的信号。
    """
    q = _bigrams(query)
    if not q:
        return 0.0, 0
    hit = len(q & _bigrams(blob))
    return hit / len(q), hit


_EXPLICIT_RE = re.compile(
    r"(?:用|按|使用|调用|套用|执行)\s*技能\s*[:：]?\s*([a-z0-9][a-z0-9_-]{1,63})", re.I)


def explicit_intent(text: str) -> str:
    """识别「用技能 xxx」「按技能 xxx 来」→ 返回技能名（空串 = 没明说）。"""
    m = _EXPLICIT_RE.search(str(text or ""))
    return (m.group(1) or "").strip() if m else ""


def relevant(query: str, k: int = MAX_INJECT, threshold: float = 0.2,
             min_hits: int = 3) -> list[dict]:
    """按相关性挑技能：问题覆盖率 ≥ threshold 且命中二元组 ≥ min_hits。

    blob 用 **title + name + description + whenToUse**（title 是中文可读名，
    对 skills_local 写出来的技能特别有用）。
    """
    q = str(query or "")
    scored: list[tuple[float, int, dict]] = []
    for row in catalog():
        blob = " ".join([row.get("title") or "", row["name"],
                         row["description"], row.get("when_to_use") or ""])
        cov, hits = coverage(q, blob)
        if cov >= threshold and hits >= min_hits:
            scored.append((cov, hits, row))
    scored.sort(key=lambda x: (-x[0], -x[1], x[2]["name"]))
    return [r for _, _, r in scored[:max(0, int(k))]]


def prompt_block(query: str = "", *, k: int = MAX_INJECT, limit: int = 12,
                 max_chars: int = BODY_INJECT_MAX) -> str:
    """组装注入提示词的技能段：**目录** + （显式指定或相关性命中时）**正文**。

    这是 edu-agent 版的"按需加载"：DSH 靠 `skill` 工具让模型自己拉正文，
    这里没有工具循环，所以改成"相关性/显式指令命中就把正文喂进去"，效果等价。
    """
    parts: list[str] = []
    block = catalog_block(limit=limit)
    if block:
        parts.append(block)
    name = explicit_intent(query)
    if name:
        got = load(name)
        if got.get("ok"):
            parts.append("【用户点名使用技能 %s，请严格按它的做法执行】\n%s"
                         % (got["name"], got["text"][:max_chars]))
        else:
            parts.append("【用户点名使用技能 %s，但它不存在：%s】" % (name, got.get("error")))
        return "\n\n".join(parts)
    for row in relevant(query, k=k):
        got = load(row["name"])
        if got.get("ok"):
            parts.append("【相关技能 %s 的做法】\n%s" % (got["name"], got["text"][:max_chars]))
    return "\n\n".join(parts)


def stats() -> dict:
    """给界面/自检看的快照。"""
    got = discover()
    return {
        "ok": True,
        "count": got["count"],
        "roots": got["roots"],
        "errors": got["errors"],
        "entries": [{k: v for k, v in r.items() if k != "_error"} for r in got["entries"]],
        "model_invocable": len(catalog()),
    }


def slug(text: str, fallback: str = "skill") -> str:
    """名字 → 合法 kebab-case id（含中文则退化成 skill-<hash>）。"""
    raw = " ".join(str(text or "").split())
    if raw and re.fullmatch(r"[A-Za-z0-9 _.-]+", raw):
        s = re.sub(r"[^a-zA-Z0-9]+", "-", raw).strip("-").lower()[:48]
        if _NAME_RE.match(s):
            return s
    return "skill-" + hashlib.sha1((raw or fallback).encode("utf-8")).hexdigest()[:10]
