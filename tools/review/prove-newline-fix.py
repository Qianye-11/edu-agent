# -*- coding: utf-8 -*-
"""证明"编辑描述不再改动插件字节"（行尾修复的回归验证）。

修之前：派生草稿走文本读写，Windows 上 write_text 把 \n 全转成 \r\n →
老师只改了描述，插件字节却变了、内容哈希跟着变、批准莫名失效。
修之后：技能包文件统一按 LF 写（newline="\\n"），只有真正改过的文件才变字节。
"""
import hashlib
import os
import shutil
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2] / "edu-agent"
sys.path.insert(0, str(ROOT / "src"))

BASE = ROOT / ".tmp-newline-proof"
shutil.rmtree(BASE, ignore_errors=True)
(BASE / "rt").mkdir(parents=True)
os.environ.update({
    "EDU_SKILL_MARKET_DIR": str(BASE / "market"),
    "EDU_SKILL_RUNTIME_DIR": str(BASE / "rt"),
    "EDU_DATA_DIR": str(BASE / "data"),
    "EDU_SKILL_REGISTRY_DB": str(BASE / "reg.db"),
    "EDU_AGENT_HOME": str(BASE / "home"),
})
os.environ.pop("EDU_SINGLE_USER_MODE", None)

from edu_agent.skill_market import lifecycle, manifest as mf, packager, skill_studio, store  # noqa: E402

SRC = ROOT / "skill-src" / "textbook-mindmap-skill"
SKILL = "textbook-mindmap"
PLUGIN = "TextbookMindMapSkill.py"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def crlf(p: Path) -> tuple[int, int]:
    b = p.read_bytes()
    return len(b), b.count(b"\r\n")


print("=== ① 用规范副本（LF）导入 → 提交 → 批准 → 发布 ===")
with mock.patch("edu_agent.permissions.policy", return_value="allow"):
    packager.import_draft((SRC / "SKILL.md").read_text(encoding="utf-8"),
                          actor="teacher", confirm=True)
    man, body = mf.from_document((SRC / "SKILL.md").read_text(encoding="utf-8"))
    store.write_draft(man, body,
                      files={PLUGIN: (SRC / PLUGIN).read_text(encoding="utf-8")},
                      allow_code=True)

# ⚠️ 必须在 submit 之前量：submit 会把整个草稿目录搬进 submitted 区
draft_plugin = store.area_dir("draft", SKILL) / PLUGIN
size, n_crlf = crlf(draft_plugin)
print("  草稿里的插件: %d B, CRLF 行数 %d  ← 应为 0（全 LF）" % (size, n_crlf))
base_sha = sha(draft_plugin)
print("  插件 sha256:%s" % base_sha)

with mock.patch("edu_agent.permissions.policy", return_value="allow"):
    lifecycle.submit(SKILL, "teacher")
    skill_studio.approve(SKILL, "reviewer-G", note="v1")
    lifecycle.publish(SKILL, "teacher", confirm=True)
print("  发布后状态:", store.effective_status(SKILL))

print()
print("=== ② 派生新草稿（模拟点【编辑】→ 保存）→ 再看插件字节 ===")
with mock.patch("edu_agent.permissions.policy", return_value="allow"):
    r = lifecycle.new_draft(SKILL, "teacher", note="改描述")
print("  new_draft ok=%s 状态=%s" % (r.get("ok"), store.effective_status(SKILL)))
after = store.area_dir("draft", SKILL) / PLUGIN
size2, n_crlf2 = crlf(after)
print("  派生后插件: %d B, CRLF 行数 %d" % (size2, n_crlf2))
print("  sha 是否不变: %s（%s → %s）" % (sha(after) == base_sha, base_sha, sha(after)))

print()
print("=== ③ 真的只改描述，然后看哪些文件变了 ===")
raw = (store.area_dir("draft", SKILL) / "SKILL.md").read_text(encoding="utf-8")
before_files = {p.name: sha(p) for p in sorted(store.area_dir("draft", SKILL).rglob("*")) if p.is_file()}
with mock.patch("edu_agent.permissions.policy", return_value="allow"):
    ed = skill_studio.ensure_editable(SKILL, "teacher")
man2, body2 = mf.from_document(raw)
new_doc = mf.SkillManifest(**{**man2.to_dict(),
                              "description": "读取本地教材库指定课本的某一章，生成章节思维导图（描述已更新）。"})
store.write_draft(new_doc, body2, files={}, allow_code=True)
after_files = {p.name: sha(p) for p in sorted(store.area_dir("draft", SKILL).rglob("*")) if p.is_file()}
for name in sorted(set(before_files) | set(after_files)):
    b, a = before_files.get(name), after_files.get(name)
    print("  %-28s %s" % (name, "未变" if a == b else "**变了** %s → %s" % (b, a)))

print()
print("结论：%s" % ("✅ 只改描述时，插件字节保持不变（行尾修复有效）"
                   if after_files.get(PLUGIN) == base_sha else "❌ 插件字节仍被动过"))
shutil.rmtree(BASE, ignore_errors=True)
