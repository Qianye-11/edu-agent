# -*- coding: utf-8 -*-
"""带旁白的完整审核演示：textbook-mindmap 走一遍 Skill Studio 审核流程。

用途：给"想学会怎么审核"的人看每一步在做什么、后台发生了什么、该看哪些栏位。
对应关系：每一步都标注界面上点哪里（Skill Studio 的哪个页签/按钮）。
"""
import json
import sys
import urllib.error
import urllib.request

B = "http://127.0.0.1:5174"
SKILL = "textbook-mindmap"


def call(method, path, body=None, timeout=180):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(B + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, {"ok": False, "_http": e.code, "_body": e.read().decode("utf-8", "replace")[:300]}


def head(n, title, where):
    print()
    print("═" * 78)
    print("  第 %s 步 · %s" % (n, title))
    print("  界面上：%s" % where)
    print("═" * 78)


def kv(k, v):
    print("    %-18s %s" % (k, v))


def say(t):
    print("    · %s" % t)


# ════════════════════════════════════════════════════════════
head(1, "先看它现在是什么状态（审核前必做）", "Skill Studio → 已安装库 → 点「详情」")
_, st = call("GET", "/api/skill-studio/status/" + SKILL)
s, a = st.get("status") or {}, st.get("approval") or {}
kv("市场状态", "%s（Studio 显示 %s）" % (st.get("state"), st.get("studio_state")))
kv("版本", s.get("version"))
kv("可加载", s.get("loadable"))
kv("批准记录", "%s（审核人 %s）" % (a.get("approved"), a.get("reviewer")))
kv("哈希一致", a.get("hash_matches"))
say("「哈希一致」这一栏最关键：False 说明批准之后内容被改过，批准已失效、不能安装。")
kv("批准哈希", a.get("content_hash"))
kv("当前哈希", a.get("current_hash"))

# ════════════════════════════════════════════════════════════
head(2, "看这一版都用到了什么（资源清单）", "「审查」页 → 资源清单表")
_, rv = call("GET", "/api/skill-studio/reviews/" + SKILL)
for r in rv.get("resources") or []:
    kv(r["name"], "%s B   sha256:%s…" % (r["bytes"], r["sha256"]))
say("清单里每个文件都参与内容哈希：改任何一个字节，批准就失效（这是设计，不是 bug）。")

# ════════════════════════════════════════════════════════════
head(3, "看隐私与出口策略（能不能分享出去）", "「审查」页 → 检查隐私与导出策略")
_, pol = call("GET", "/api/skill-studio/export-policy/" + SKILL)
kv("可否进团队库", pol.get("allowed"))
kv("风险等级", "%s %s" % (pol.get("risk"), "；".join(pol.get("risk_reasons") or [])))
for r in pol.get("reasons") or []:
    say("拒绝理由：" + r)
say("只有「已批准 ∧ 哈希一致 ∧ 无敏感内容」才允许同步到团队库；这里显示的是真实判据。")

# ════════════════════════════════════════════════════════════
head(4, "结构校验：先看有没有硬伤", "「编辑」页 → 点【实时校验】")
_, val = call("POST", "/api/skill-studio/drafts/%s/validate" % SKILL, {})
kv("是否通过", val.get("ok"))
for p in val.get("problems") or []:
    kv("问题", p)
say("校验查三类：manifest 必填字段、doc 34 要求的段位（只对 Agent 生成草稿强制）、包内文件安全。")

# ════════════════════════════════════════════════════════════
head(5, "改一处内容（演示「改了就要重新批准」）", "「编辑」页 → 改进正文 → 点【仅保存草稿】")
_, d0 = call("GET", "/api/skill-market/draft?id=%s&area=published" % SKILL)
text = d0.get("text") or ""
if not text:
    _, d0 = call("GET", "/api/skill-market/draft?id=%s&area=draft" % SKILL)
    text = d0.get("text") or ""
kv("读到原文", "%d 字符" % len(text))
marker = "边界"
addition = "- 若 book_id 匹配不到课本，会回退到默认课本（当前不会额外提示，调用方请注意）。\n"
if addition.strip() in text:
    say("这条补充之前已经写过了，跳过修改（保证演示可重复）")
    edited = text
else:
    lines = text.rstrip("\n").split("\n")
    lines.append(addition.rstrip("\n"))
    edited = "\n".join(lines) + "\n"
    say("在正文末尾补了一行边界说明（真实场景：审查时发现的坑，回写进技能）")
_, saved = call("PATCH", "/api/skill-studio/drafts/" + SKILL, {"actor": "teacher", "text": edited})
kv("保存结果", "ok=%s state=%s" % (saved.get("ok"), saved.get("state")))
say("注意：保存会把技能变回 **draft**（派生新草稿）；但线上已装的那一版**照常服务** —— "
    "这是刚才修掉的一个真 bug（早先会让技能从所有会话里消失）。")
_, st2 = call("GET", "/api/skill-studio/status/" + SKILL)
kv("保存后状态", st2.get("state"))
kv("仍可加载", (st2.get("status") or {}).get("loadable"))
_, a2 = call("GET", "/api/skill-studio/reviews/" + SKILL)
kv("哈希一致", (a2.get("approval") or {}).get("hash_matches"))
say("看这里：批准哈希与当前哈希已经不一致 → 旧批准自动失效，必须重新批准。")

# ════════════════════════════════════════════════════════════
head(6, "提交审查：三重校验（L1→L2→L3）", "「审查」页 → 点【提交审查】")
_, sub = call("POST", "/api/skill-studio/drafts/%s/submit-review" % SKILL, {"actor": "teacher"})
kv("结果", "ok=%s state=%s" % (sub.get("ok"), sub.get("state")))
kv("三道闸门", json.dumps(sub.get("gates"), ensure_ascii=False))
l3 = sub.get("L3") or {}
kv("L1 静态扫描", "%s（能力白名单/网络/文件/执行/越界）"
   % ((sub.get("scan") or {}).get("verdict")))
kv("L2 manifest", "%s（声明式回放：实际调用 ⊆ 声明能力）"
   % json.dumps(sub.get("L2"), ensure_ascii=False)[:90])
kv("L3 影子运行", "executed=%s ok=%s 用时=%sms" % (l3.get("executed"), l3.get("ok"), l3.get("ms")))
kv("L3 返回形状", "%s ↔ 期望 %s（一致率 %s）"
   % (l3.get("actual_fields"), l3.get("expected_fields"), l3.get("shape_match")))
kv("L3 出站拦截", l3.get("outbound_attempts"))
say("L3 是真的在子进程里跑插件：出站调用被记录桩拦下，所以「不联网也能验收」。")
if not sub.get("ok"):
    kv("失败原因", sub.get("error"))
    sys.exit(1)

# ════════════════════════════════════════════════════════════
head(7, "批准：绑定内容哈希", "「审查」页 → 点【批准（绑定内容哈希）】")
_, ap = call("POST", "/api/skill-studio/reviews/%s/approve" % SKILL,
             {"reviewer": "reviewer-G", "note": "本次改动仅补一行边界说明，L1/L2/L3 全过"})
kv("结果", "ok=%s state=%s" % (ap.get("ok"), ap.get("state")))
kv("批准哈希", (ap.get("approval") or {}).get("content_hash"))
say("批准记录落在 reviews/<id>.json，绑的是「此刻的内容哈希」。之后任何改动都会让它失效。")
say("审核人不能是创建者本人（职责分离），所以填 reviewer-G 而不是 owner。")

# ════════════════════════════════════════════════════════════
head(8, "安装：五条闸门 + 复制落盘", "「审查」页 → 点【批准并安装到本地】")
_, ins = call("POST", "/api/skill-studio/drafts/%s/install" % SKILL,
              {"actor": "teacher", "confirm": True, "allow_overwrite": True})
kv("结果", "ok=%s studio=%s" % (ins.get("ok"), ins.get("studio_state")))
kv("闸门逐条", json.dumps(ins.get("checks"), ensure_ascii=False))
kv("安装到", ins.get("installed_to"))
kv("新版 / 被替代", "%s / %s" % (ins.get("version"), ins.get("superseded_version")))
kv("旧版备份", ins.get("backup") or "（首次安装，无需备份）")
say("安装只做两件事：完整复制到全局 data/skills/<id>/、把状态改成 published。"
    "它不碰内核、不碰会话绑定。")
_, vs = call("GET", "/api/skill-studio/versions/" + SKILL)
kv("版本历史", "%s 个（被替代 %s）" % (vs.get("count"), vs.get("superseded_count")))
for v in vs.get("versions") or []:
    kv("  " + v["version"], "激活" if v["active"] else "被 %s 替代（保留但不激活）" % v["superseded_by"])

# ════════════════════════════════════════════════════════════
head(9, "验收：它真的生效了吗", "主界面聊天 / 或用接口调一次")
_, init = call("GET", "/api/skill/session/review-demo/init")
kv("会话自动加载", init.get("loaded"))
_, inv = call("POST", "/api/skill-market/kernel/invoke",
              {"session_id": "review-demo", "skill": SKILL,
               "function": "generate_mindmap",
               "args": {"book_id": "math_junior_1", "chapter_no": 2}})
kv("调用 ok", inv.get("ok"))
v = inv.get("value")
v = getattr(v, "value", v)
for line in str((v or {}).get("markdown") or "").splitlines():
    print("      │ " + line)

# ════════════════════════════════════════════════════════════
head(10, "回看审计：谁在什么时候做了什么", "Skill Studio → 审计 页签")
_, au = call("GET", "/api/skill-studio/audit?skill=%s&limit=20" % SKILL)
for r in reversed(au.get("records") or []):
    res = r.get("result") or {}
    print("    %-22s %-12s %s" % (r.get("event"), r.get("actor"), res.get("code") or ""))
_, chain = call("GET", "/api/skill-create/verify-chain")
kv("哈希链校验", "ok=%s（%s 条记录）" % (chain.get("ok"), chain.get("records")))
say("审计只追加、且带哈希链：改一条历史记录就能被 verify-chain 查出来。")

print()
print("═" * 78)
print("  走完。这个技能现在是：", end=" ")
_, fin = call("GET", "/api/skill-studio/status/" + SKILL)
print("%s v%s，可加载=%s" % (fin.get("state"), (fin.get("status") or {}).get("version"),
                              (fin.get("status") or {}).get("loadable")))
print("═" * 78)
