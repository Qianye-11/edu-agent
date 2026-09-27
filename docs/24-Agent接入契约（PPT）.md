# 24 · Agent 接入契约（PPT 生成与填充）

> **这份文档给谁看**：要接进来的**外部 agent**（例如"另一个通识 agent 生成教案"那条链路）的开发者。
> **一句话**：你给我们一份**教案结构**，我们还你一份 **pptx**；要更好控制，就读我们的计划、改完再回传。
> **不做 skill、不设开关**：这是接口，不是能力开关——外部 agent 直接调 HTTP 即可（`docs/23` §6.5）。
> **同类先例**：`docs/12-教案PPT意图联动.md`（教案 Agent → 结构化 payload → 确定性填入），本文是它在
> "演示模板 + 幻灯片"这条线上的同构版本。

---

## 1. 两个方向、三个接口

| 方向 | 接口 | 用途 |
|---|---|---|
| **读**（可选） | `GET /api/slide/fill/plan?template_id=&session_id=&title=` | 拿"模板槽位清单 + 我们的内容 + 确定性草案"，据此决定每页每槽填什么 |
| **写**（控制粒度：逐槽） | `POST /api/slide/fill`（带 `mapping`） | 按你给的逐页逐槽填法执行；不给 `mapping` 就走我们的草案 |
| **写**（最省事：一步） | `POST /api/slide/deck` | 一份 `lesson` → 直接出 pptx（有 `template_id` 走模板填充，没有就按主题生成） |

`schema` 版本：**`edu-agent.fill-plan.v1`**（每次响应都带；字段只增不改，破坏性变更升版本号并在 §6 记一笔）。

---

## 2. 输入形状（`lesson`）

```jsonc
{
  "lesson": {
    "title": "3.3 幂函数",
    "slides": [
      {"layout": "cover", "name": "3.3 幂函数", "cover_title": "3.3 幂函数",
       "subtitle": "人教A版 · 高一", "meta": "1 课时"},
      {"name": "情境导入", "minutes": "5", "bullets": ["投影五个实例", "追问：这些函数有什么共同点"]},
      {"name": "概念建构", "minutes": "12",
       "blocks": [{"label": "定义", "text": "形如 y=x^α 的函数，α 为常数"}]}
    ]
  },
  "template_id": "红领巾小课堂：趣味学习乐园",   // 可选：给了就按这份 .pptx 模板填
  "theme_id": "tech-editorial",                  // 可选：不给模板时用哪套内置主题
  "outDir": "D:\\导出目录",                        // 可选：默认 data/ppt_out
  "confirm": true                                 // 落盘类操作要显式确认（权限闸门）
}
```

- `slides` 里 `bullets`（纯字符串数组）与 `blocks`（带 `label`）二选一即可；两者都给时以 `blocks` 为准；
- `layout` 可选：`cover` / `section` / `bullets` / `two-col` / `media` / `end`；不给就按位置推断（首封面、尾小结）；
- 单条文字上限 400 字（超了会被裁到 400，**不会**静默丢整条）。

---

## 3. 读端响应（`GET /api/slide/fill/plan`）

```jsonc
{
  "ok": true, "schema": "edu-agent.fill-plan.v1",
  "template": { "id": "…", "pages": 12,
    "slots": [ { "index": 0, "layout": "", "slots": [
        {"role": "title", "capacity": 16, "sample": "快乐成长"},
        {"role": "body",  "capacity": 42, "sample": ""},
        {"role": "decoration", "capacity": 0, "sample": "PART 01"} ] } ] },
  "content": { "title": "3.3 幂函数", "slides": [ … ] },
  "draft": { "pages": [ { "template_index": 0, "copied": false,
      "fills": [ {"role": "title", "text": "3.3 幂函数", "capacity": 16, "shape_index": 2, "sample": ""},
                 {"role": "body",  "text": "",           "clear": true} ],
      "kept": ["快乐成长"], "replaced": [ … ] } ],
    "warnings": [], "adjustments": [], "policy": {"deleted": 8} },
  "notes": [ … ]
}
```

`role` 只有三种：**`title` / `body` / `decoration`**（`decoration` 是模板的设计标签，**我们一个字都不会动**）。

---

## 4. 写端：回传 `mapping`

`POST /api/slide/fill` 的 body 在 §2 的基础上多一个 `mapping`（**只需要给你想改的页**）：

```jsonc
{ "template_id": "…", "confirm": true,
  "lesson": { … },                      // 或 "session_id": "…"（用会话里那份）
  "mapping": [
    { "template_index": 1,
      "fills": [ {"shape_index": 5, "text": "投影五个实例"},   // ← 推荐：精确指槽
                 {"role": "title",   "text": "情境导入"} ] }   // ← 老写法：只改该角色第一个槽
  ] }
```

规则：

1. 只认草案里**已有的** `template_index`；`shape_index` 或 `role` 都认，其余忽略（不报错，也不乱写）；
2. **`shape_index` 是精确指槽**（草案里每个 `fills[]` 都带它）：只改那一个框。
   一页有多个同角色槽时**必须**用它 —— 按 `role` 只会改**第一个**（2026-09-27 收紧语义：
   从前"给 role 就把该角色所有槽写成同一句"，卡片式模板一页 4 个正文框会被一次写满，属于 bug）；
3. 文字超容量的会被**适度压缩**（去括号补充 → 从末尾减分句 → 最后才硬截），并在 `adjustments` 里留痕
   （`by` 字段值由 `agent-mapping` 改为 `mapping`：同一套执行代码现在也给老师的页内编辑用）；
4. 没给到的页保持草案原样；
5. 返回值里的 `checks`（内容零丢失 / 页数符合策略 / 容量自查）就是这次的验收结论。

---

## 5. 响应与错误码

成功：`{ok: true, schema, filename, outDir, file, pages, checks[], adjustments[], download_url}`

| 情况 | HTTP | body |
|---|---|---|
| 没给 `confirm`（落盘要确认） | 200 | `{ok:false, gate:"permission", hint:"…"}` |
| `lesson.slides` 为空 | 400 | `{ok:false, schema, error:"lesson.slides 为空：至少要有一个环节"}` |
| `template_id` 不存在/读不出槽位 | 200 | 走"只用配色渲染"或明确 `error` |
| ppt-master 未就绪 | 200 | `{ok:false, gate:"install", error:"ppt-master 未就绪：…"}` |
| 内部异常 | 200 | `{ok:false, error:"…"}`（**不会** 500 把栈丢给你） |

**幂等与落盘**：产物目录默认 `data/ppt_out/`；同名（秒级时间戳）会自动加 `-2`、`-3`；中间件在
`data/tmp/ppt_jobs/`（24h 自清）；**`content/plans/` 一个字不写**。

---

## 6. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| `edu-agent.fill-plan.v1` | 2026-09-27 | 首版：`GET /api/slide/fill/plan`、`POST /api/slide/fill`（`mapping`）、`POST /api/slide/deck` |
| `edu-agent.fill-plan.v1` | 2026-09-27 | `mapping` 细化：新增 **`shape_index` 精确指槽**；只给 `role` 时语义收紧为"该角色第一个槽"；`adjustments[].by` 由 `agent-mapping` 改为 `mapping`。⚠️ 若你在用只给 `role` 的写法**且依赖"一次改满同角色所有槽"**，请改成逐槽给 `shape_index`（那种行为会串格，是 bug 不是特性） |
| — | 2026-09-27 | 新增编辑器用的读端：`POST /api/slide/render` 的 `annotate: true` → 响应多一个 `boxes`（每页 `[{slot,kind,x,y,w,h,size,align}]`，1280×720 设计像素）。**导出路径不传它**，所以导出的 SVG 不变 |

---

## 7. 最小示例（Python）

```python
import httpx

BASE = "http://127.0.0.1:5174"
lesson = {"title": "3.3 幂函数",
          "slides": [{"layout": "cover", "name": "3.3 幂函数", "cover_title": "3.3 幂函数"},
                     {"name": "情境导入", "minutes": "5", "bullets": ["投影五个实例"]}]}

# ① 一步出稿（最省事）
r = httpx.post(f"{BASE}/api/slide/deck",
               json={"lesson": lesson, "template_id": "红领巾小课堂：趣味学习乐园", "confirm": True},
               timeout=300).json()
print(r["ok"], r["filename"], r["download_url"])

# ② 想自己控制：先读计划，改完回传
plan = httpx.get(f"{BASE}/api/slide/fill/plan",
                 params={"template_id": "红领巾小课堂：趣味学习乐园"}).json()
plan["draft"]["pages"][0]["fills"][0]["text"] = "3.3 幂函数（我的标题）"
r2 = httpx.post(f"{BASE}/api/slide/fill",
                json={"lesson": lesson, "template_id": "红领巾小课堂：趣味学习乐园",
                      "mapping": plan["draft"]["pages"], "confirm": True}, timeout=300).json()
print(r2["ok"], r2["checks"])
```
