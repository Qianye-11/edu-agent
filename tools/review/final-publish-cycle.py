# -*- coding: utf-8 -*-
"""收尾：把 textbook-mindmap 再走一轮 Studio，顺带把行尾规范化成 LF。

为什么要再走一轮：上一轮（1.0.2）是在行尾修复之前装的，插件被写成了 CRLF。
这一轮之后：所有文件统一 LF、与仓库里的规范副本一致、批准哈希与安装内容对得上。
"""
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

B = "http://127.0.0.1:5174"
SKILL = "textbook-mindmap"
PLUGIN = "TextbookMindMapSkill.py"
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "edu-agent" / "src"))
from edu_agent.skill_market import paths  # noqa: E402


def call(method, path, body=None, timeout=180):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(B + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return {"ok": False, "_http": e.code, "_body": e.read().decode("utf-8", "replace")[:200]}


def info(tag, p):
    if not p.exists():
        print("    %-34s （不存在）" % tag)
        return
    b = p.read_bytes()
    print("    %-34s %5d B  CRLF=%d  sha256:%s"
          % (tag, len(b), b.count(b"\r\n"), hashlib.sha256(b).hexdigest()[:16]))


print("【改之前】运行时目录")
rt = paths.runtime_plugin_path(SKILL)
info("SKILL.md", rt / "SKILL.md")
info(PLUGIN, rt / PLUGIN)

print()
print("【第 1 步】读已发布原文 → 改一句描述")
d = call("GET", "/api/skill-market/draft?id=%s&area=published" % SKILL)
text = d.get("text") or ""
print("    读到 %d 字符" % len(text))
old = "description: 读取本地教材库指定课本的某一章，生成该章节的 Markdown 思维导图。"
new = "description: 读取本地教材库指定课本的某一章，生成该章节的 Markdown 思维导图（按小节层级 + 页码定位）。"
if new in text:
    print("    描述已经是新版，跳过（保证可重复执行）")
    edited = text
elif old in text:
    edited = text.replace(old, new)
    print("    描述已更新")
else:
    print("    ⚠️ 没找到待替换的描述行，原样保存")
    edited = text

print()
print("【第 2 步】保存草稿（派生新草稿；这一步会顺带规范行尾）")
s = call("PATCH", "/api/skill-studio/drafts/" + SKILL, {"actor": "teacher", "text": edited})
print("    ok=%s state=%s" % (s.get("ok"), s.get("state")))
info("草稿里的插件（应为 LF）", paths.market_root() / "drafts" / SKILL / PLUGIN)

print()
print("【第 3 步】提交审查（L1/L2/L3）")
sub = call("POST", "/api/skill-studio/drafts/%s/submit-review" % SKILL, {"actor": "teacher"})
print("    ok=%s gates=%s" % (sub.get("ok"), json.dumps(sub.get("gates"), ensure_ascii=False)))
if not sub.get("ok"):
    print("    失败:", sub.get("error"))
    raise SystemExit(1)
l3 = sub.get("L3") or {}
print("    L3 真跑: executed=%s ok=%s %sms  出站拦截=%s"
      % (l3.get("executed"), l3.get("ok"), l3.get("ms"), l3.get("outbound_attempts")))

print()
print("【第 4 步】批准（审核人 ≠ 创建者）")
a = call("POST", "/api/skill-studio/reviews/%s/approve" % SKILL,
         {"reviewer": "reviewer-G", "note": "仅改描述；L1/L2/L3 全过"})
print("    ok=%s 批准哈希=%s" % (a.get("ok"), (a.get("approval") or {}).get("content_hash")))

print()
print("【第 5 步】安装（覆盖旧版，五道闸门）")
i = call("POST", "/api/skill-studio/drafts/%s/install" % SKILL,
         {"actor": "teacher", "confirm": True, "allow_overwrite": True})
print("    ok=%s 闸门=%s" % (i.get("ok"), json.dumps(i.get("checks"), ensure_ascii=False)))
print("    新版=%s 被替代=%s" % (i.get("version"), i.get("superseded_version")))
print("    旧版备份=%s" % (i.get("backup") or "—"))

print()
print("【改之后】运行时目录")
info("SKILL.md", rt / "SKILL.md")
info(PLUGIN, rt / PLUGIN)

print()
print("【验收】")
st = call("GET", "/api/skill-studio/status/" + SKILL)
ap = st.get("approval") or {}
print("    状态=%s 版本=%s 可加载=%s" % (st.get("state"), (st.get("status") or {}).get("version"),
                                       (st.get("status") or {}).get("loadable")))
print("    批准有效=%s（哈希一致）审核人=%s" % (ap.get("hash_matches"), ap.get("reviewer")))
vs = call("GET", "/api/skill-studio/versions/" + SKILL)
print("    版本历史 %s 个（被替代 %s）：%s"
      % (vs.get("count"), vs.get("superseded_count"),
         ", ".join("%s%s" % (v["version"], "" if v["active"] else "(superseded)")
                   for v in vs.get("versions") or [])))
init = call("GET", "/api/skill/session/final-check/init")
print("    会话加载: %s" % init.get("loaded"))
inv = call("POST", "/api/skill-market/kernel/invoke",
           {"session_id": "final-check", "skill": SKILL, "function": "generate_mindmap",
            "args": {"book_id": "math_junior_1", "chapter_no": 2}})
v = inv.get("value")
v = getattr(v, "value", v)
print("    调用 ok=%s，出图：" % inv.get("ok"))
for line in str((v or {}).get("markdown") or "").splitlines()[:5]:
    print("      │ " + line)
