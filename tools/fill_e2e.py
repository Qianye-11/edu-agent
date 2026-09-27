# -*- coding: utf-8 -*-
"""填充端到端（面板真实路径）：会话 → 教案正文 → 幻灯片 → 填充 → 预览。

跑的是老师点按钮那条 HTTP 路（**必须先启动服务** `src/edu_agent/web_server.py`），
不是单测。用途：单测全绿但"老师那套模板填出来不能看"时，用这条命令复现现场。

    .venv/Scripts/python.exe tools/fill_e2e.py [模板名]

检查项：5 条硬验收全过、预览页数 = 计划页数、哪些页内联了底图、每页的标题与正文条数。
退出码非 0 表示有硬验收没过（可直接进 CI 或当发布前的手检）。
"""
import sys
import time

import httpx

sys.stdout.reconfigure(encoding="utf-8")      # 控制台是 GBK 时 ✅/中文会 UnicodeEncodeError（踩过）

BASE = "http://127.0.0.1:5174"
TID = sys.argv[1] if len(sys.argv) > 1 else "红领巾小课堂：趣味学习乐园"
MD = """# 3.3 幂函数

人教A版 · 必修第一册 · 高一

### 情境导入（约 5 分钟）
- 正方形边长与面积的关系
- 正方体棱长与体积的关系

### 概念建构（约 12 分钟）
- 一般地，形如 y = x^α 的函数叫做幂函数，其中 x 是自变量，α 是常数。
- 注意：指数 α 为常数，底数 x 为自变量，前面的系数必须是 1。

### 图象与性质（约 10 分钟）
- 五个常见幂函数：y = x，y = x²，y = x³，y = x^(1/2)，y = x^(-1)
- 它们的定义域、值域与奇偶性各不相同，需要逐一分析。

### 例题精讲（约 8 分钟）
- 比较 1.5^0.3 与 1.7^0.3 的大小。
- 解：构造函数 y = x^0.3，它在 (0, +∞) 上单调递增，因为 1.5 < 1.7，所以 1.5^0.3 < 1.7^0.3。

### 课堂小结（约 3 分钟）
- 幂函数的定义与三个特征
- 利用单调性比较幂值大小
"""

c = httpx.Client(base_url=BASE, timeout=600.0)
sid = c.post("/api/session/new", json={}).json().get("id") or ""
prev = c.post("/api/session/doc-preview", json={"markdown": MD, "session_id": sid}).json()
slides = prev.get("slides") or []
print("会话 %s → 派生 %d 页：%s" % (sid[:8], len(slides), [s.get("name") for s in slides]))

t0 = time.time()
r = c.post("/api/slide/fill", json={"template_id": TID, "slides": slides,
                                    "title": "3.3 幂函数", "confirm": True}).json()
print("填充 ok=%s 用时 %.1fs 页数=%s" % (r.get("ok"), time.time() - t0, r.get("preview_pages")))
print("detail =", r.get("detail"))
for chk in r.get("checks") or []:
    print("   %s %s — %s" % ("OK " if chk["ok"] else "FAIL", chk["name"], chk["detail"]))
print("warnings =", r.get("warnings"))
print("plan 页数 =", len((r.get("plan") or {}).get("pages") or []))
for i, page in enumerate((r.get("plan") or {}).get("pages") or [], 1):
    texts = [f["text"] for f in page["fills"] if f["role"] == "title" and f["text"]]
    bodies = [f["text"] for f in page["fills"] if f["role"] == "body" and f["text"]]
    print("   第%2d页 母版p%-3s 标题=%s 正文%d条" % (i, page["template_index"] + 1,
                                                   (texts or ["（无）"])[0][:18], len(bodies)))
imgs = sum(1 for p in r.get("preview") or [] if "data:image" in p)
print("预览：%d 页，其中 %d 页内联了底图" % (len(r.get("preview") or []), imgs))
print("文件 =", r.get("filename"))
c.close()

bad = [chk["name"] for chk in (r.get("checks") or []) if not chk["ok"]]
if bad or not r.get("ok"):
    print("硬验收没过：%s" % "、".join(bad))
    raise SystemExit(1)
print("硬验收全过 ✅")
