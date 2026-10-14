# -*- coding: utf-8 -*-
"""Skill Studio 编辑器「非破坏性往返」验证。

复刻页面里的 patchFrontMatter()（只替换表单管的键所在的行，其余原样保留），
然后走真实的 PATCH 保存接口，最后检查：
  * 未被表单管理的 front-matter 字段（status / fixture / source / session_id…）**没有丢**；
  * inline tests 块**还在**（丢了的话结构校验会失败、fixture 也会丢）；
  * 结构校验仍然通过；
  * 正文除被改的那一处外逐行不变。
"""
import json
import re
import urllib.error
import urllib.request

B = "http://127.0.0.1:5174"
SKILL = "textbook-mindmap"


def call(method, path, body=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(B + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, {"_http": e.code, "body": e.read().decode("utf-8", "replace")[:200]}


def split_doc(text):
    t = str(text or "")
    if not t.startswith("---"):
        return "", t
    end = t.find("\n---", 3)
    if end < 0:
        return "", t
    return t[:end + 4], t[end + 4:].lstrip("\n")


def patch_front_matter(fm, edits):
    """与页面 patchFrontMatter() 同一算法。"""
    lines = str(fm or "").split("\n")
    for k, val in edits.items():
        out, done, i = [], False, 0
        while i < len(lines):
            ln = lines[i]
            m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", ln)
            if m and m.group(1) == k:
                out.append("%s: %s" % (k, val))
                done = True
                j = i + 1
                while j < len(lines) and re.match(r"^\s+\S", lines[j]):
                    j += 1
                i = j
                continue
            out.append(ln)
            i += 1
        lines = out
        if not done:
            close = len(lines) - 1
            while close > 0 and lines[close].strip() == "":
                close -= 1
            lines.insert(close, "%s: %s" % (k, val))
    return "\n".join(lines)


# ── 读原文 ────────────────────────────────────────────────
code, d = call("GET", "/api/skill-market/draft?id=%s&area=draft" % SKILL)
if not d.get("ok"):
    print("  ❌ 读不到草稿：", d)
    raise SystemExit(1)
original = d["text"]
fm, body = split_doc(original)
print("  原文 %d 字符；front-matter %d 字符，正文 %d 字符" % (len(original), len(fm), len(body)))

def fm_keys(text):
    keys = {}
    for ln in split_doc(text)[0].split("\n"):
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:", ln)
        if m:
            keys[m.group(1)] = True
    return sorted(keys)

before_keys = fm_keys(original)
has_inline_tests = bool(re.search(r"(?m)^tests:\s*$", split_doc(original)[0]))
print("  front-matter 键 %d 个：%s" % (len(before_keys), ", ".join(before_keys)))
print("  含 inline tests 块:", has_inline_tests)

# ── 页面同款编辑：只改 title ────────────────────────────────
new_title = "教材章节思维导图（Studio 往返测试）"
doc = patch_front_matter(fm, {"title": new_title}) + "\n\n" + body
code, saved = call("PATCH", "/api/skill-studio/drafts/" + SKILL, {"actor": "teacher", "text": doc})
print("  保存 → HTTP %s ok=%s state=%s" % (code, saved.get("ok"), saved.get("state")))
if not saved.get("ok"):
    print("  ❌ 保存失败：", json.dumps(saved, ensure_ascii=False)[:300])
    raise SystemExit(1)

# ── 复核 ──────────────────────────────────────────────────
code, after = call("GET", "/api/skill-market/draft?id=%s&area=draft" % SKILL)
aft = after["text"]
after_keys = fm_keys(aft)
lost = [k for k in before_keys if k not in after_keys]
gained = [k for k in after_keys if k not in before_keys]
print("  往返后 front-matter 键 %d 个；丢失 %s；新增 %s" % (len(after_keys), lost or "无", gained or "无"))
print("  inline tests 块仍在:", bool(re.search(r"(?m)^tests:\s*$", split_doc(aft)[0])))
print("  title 已改:", new_title in aft)

code, val = call("POST", "/api/skill-studio/drafts/%s/validate" % SKILL, {})
print("  结构校验 ok=%s problems=%s" % (val.get("ok"), val.get("problems") or "无"))

body_after = split_doc(aft)[1]
same_body = (body_after.strip() == body.strip())
print("  正文逐字未变:", same_body)

ok = (not lost) and (not gained) and val.get("ok") and same_body \
     and bool(re.search(r"(?m)^tests:\s*$", split_doc(aft)[0]))
print()
print("结论：%s" % ("✅ 非破坏性往返通过（表单改动不丢其它字段）" if ok else "❌ 有数据丢失或校验失败"))
raise SystemExit(0 if ok else 1)
