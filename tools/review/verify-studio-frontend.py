# -*- coding: utf-8 -*-
"""Skill Studio 前端数据层自检：把页面里会发的请求照原样打一遍。

页面用的是纯 fetch，没法直接单测；这里等价地复刻它的调用序列，
任何一个 404/字段缺失都会在这里先暴露，而不是等用户点开页面才发现。
"""
import json
import sys
import urllib.error
import urllib.request

B = "http://127.0.0.1:5174"


def call(method, path, body=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(B + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, {"_http_error": e.read().decode("utf-8", "replace")[:200]}
    except Exception as e:  # noqa: BLE001
        return 0, {"_error": "%s: %s" % (type(e).__name__, e)}


fails = []


def check(label, path, method="GET", body=None, keys=("ok",)):
    code, d = call(method, path, body)
    ok = code == 200 and isinstance(d, dict) and all(k in d for k in keys)
    mark = "OK  " if ok else "FAIL"
    print("  [%s] %-6s %-58s HTTP %s%s" % (
        mark, method, path[:58], code,
        "" if ok else "  → " + json.dumps(d, ensure_ascii=False)[:120]))
    if not ok:
        fails.append(label)
    return d


print("=== 顶部状态 ===")
ov = check("overview", "/api/skill-studio/overview", keys=("ok", "config", "candidates", "outbox"))
print("      config.enabled=%s execute_plugin=%s" % (
    (ov.get("config") or {}).get("enabled"), (ov.get("config") or {}).get("execute_plugin")))

print("=== 候选箱 ===")
cand = check("candidates", "/api/skill-studio/candidates?limit=100", keys=("ok", "candidates"))
rows = cand.get("candidates") or []
print("      候选 %d 个" % len(rows))
if rows:
    first = rows[0]
    for k in ("candidate_id", "signature", "title", "runs", "sessions", "score", "criteria",
              "blocking", "evidence", "cooldown_until", "sensitive_title"):
        if k not in first:
            print("      ❌ 候选行缺字段 %s（页面会渲染成 undefined）" % k)
            fails.append("candidate field " + k)
    check("candidate detail", "/api/skill-studio/candidates/" + urllib.parse.quote(first["candidate_id"]),
          keys=("ok", "candidate"))

print("=== 审查页 ===")
idx = check("market index", "/api/skill-market/index", keys=("ok", "skills"))
skills = idx.get("skills") or []
print("      市场技能 %d 个：%s" % (len(skills), ", ".join(s["name"] + "/" + s["status"] for s in skills[:6])))
target = None
for s in skills:
    if s.get("status") in ("submitted", "approved", "published"):
        target = s["name"]
        break
if target:
    check("status", "/api/skill-studio/status/" + target, keys=("ok", "state", "studio_state", "status"))
    rv = check("reviews", "/api/skill-studio/reviews/" + target,
               keys=("ok", "approval", "content_hash", "resources"))
    check("validate", "/api/skill-studio/drafts/%s/validate" % target, method="POST", body={},
          keys=("ok",))
    check("policy", "/api/skill-studio/export-policy/" + target, keys=("ok", "allowed", "risk"))
    print("      approval.approved=%s hash_matches=%s resources=%d" % (
        (rv.get("approval") or {}).get("approved"), (rv.get("approval") or {}).get("hash_matches"),
        len(rv.get("resources") or [])))
else:
    print("      （市场里没有可审技能，跳过审查页检查）")

print("=== 编辑页 ===")
if target:
    det = check("detail(id=…)", "/api/skill-market/detail?id=" + target,
                keys=("ok", "manifest", "body"))
    m = det.get("manifest") or {}
    print("      manifest 键 %d 个；body %d 字符" % (len(m), len(str(det.get("body") or ""))))
    for k in ("name", "title", "description", "version", "allowed_capabilities", "data_scope"):
        if k not in m:
            print("      ⚠️ manifest 缺 %s（页面表单该字段会空）" % k)

print("=== 已安装库 ===")
if target:
    vs = check("versions", "/api/skill-studio/versions/" + target,
               keys=("ok", "current_version", "versions"))
    print("      版本 %s 个，被替代 %s 个" % (vs.get("count"), vs.get("superseded_count")))
check("outbox", "/api/skill-studio/sync/outbox", keys=("ok", "count", "items"))

print("=== 审计 ===")
check("audit", "/api/skill-studio/audit?limit=100", keys=("ok", "records"))
check("verify-chain", "/api/skill-create/verify-chain")
check("safe-import", "/api/skill-studio/safe-import",
      keys=("ok", "import_roots", "allowed_suffixes", "skill_id_regex"))

print("=== 页面本身 ===")
with urllib.request.urlopen(B + "/static/skill-studio.html", timeout=20) as r:
    html = r.read().decode("utf-8")
need = ("候选箱", "审查", "编辑", "已安装库", "审计", "generate-draft", "submit-review",
        "approve", "reject", "install", "enqueue", "skill-market/draft",
        "patchFrontMatter", "editPreview", "export-policy", "safe-import")
missing = [x for x in need if x not in html]
print("  [%s] 页面 %d 字节；缺少关键片段: %s" % ("OK  " if not missing else "FAIL", len(html), missing or "无"))
if missing:
    fails.append("page fragments")

with urllib.request.urlopen(B + "/", timeout=20) as r:
    home = r.read().decode("utf-8")
print("  [%s] 主界面侧栏含 Skill Studio 入口" % ("OK  " if "skill-studio.html" in home else "FAIL"))
if "skill-studio.html" not in home:
    fails.append("sidebar entry")

print()
print("结论：%s" % ("全部通过 ✅" if not fails else "有 %d 项失败：%s" % (len(fails), fails)))
sys.exit(1 if fails else 0)
