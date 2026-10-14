"""能力注册表：MCP 服务与 Skill 的统一开关 + 运行状态（2026-09-10 WPS 集成）。

设计（对齐项目既有兜底哲学，见 mcp_tools.py / retrieve.py）：
- 能力定义（spec）写在代码里；运行态开关落 data/capabilities.json，**默认全开**
- 状态探测全部吞异常：探测失败只影响「状态」显示，绝不阻断业务链路
- 探测结果带 TTL 缓存：右栏面板轮询不会反复起子进程

用法：
    from edu_agent import capabilities
    capabilities.snapshot()                      # -> {"mcp": [...], "skills": [...]}
    capabilities.is_enabled("mcp", "wps-office") # -> True / False
    capabilities.set_enabled("mcp", "wps-office", False)
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from edu_agent.config import load_settings

ROOT = Path(__file__).resolve().parents[2]

_MCP_CACHE_TTL = 45.0     # 快探测（桥接 ping / 进程 / 产物）缓存
_TOOLS_CACHE_TTL = 300.0  # MCP 工具清单缓存（要起子进程，慢）


# ---------- 第三方 WPS MCP（wps-skills）安装位置解析 ----------
# 交接约定（docs/09-WPS接入交接要求.md）：第三方源码/node_modules **不入库**。
# 2026-09-25 起**优先装在项目内 `.deps/`**（老师 C 盘紧张，把第三方与缓存都收进项目盘）；
# 解析优先级：
#   1) 环境变量 WPS_MCP_ENTRY（直接指向 dist/index.js）
#   2) 环境变量 WPS_MCP_DIR（wps-skills 克隆目录）
#   3) data/wps_mcp.json（安装脚本写的运行时配置）
#   4) 项目内 <repo>/.deps/wps-skills（推荐）
#   5) 旧位置 %LOCALAPPDATA%\edu-agent\deps\wps-skills（兼容老安装）
def _project_deps_root(name: str) -> Path:
    """项目内 `.deps/<name>`（仓库根 = 本文件的上两级）。"""
    return Path(__file__).resolve().parents[2] / ".deps" / name


def _default_wps_root() -> Path:
    import os

    local = _project_deps_root("wps-skills")
    if local.exists():
        return local
    base = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(base) / "edu-agent" / "deps" / "wps-skills"


def _runtime_wps_root() -> Path | None:
    """读安装脚本写的 data/wps_mcp.json。"""
    import json

    try:
        p = load_settings().data_dir / "wps_mcp.json"
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        d = str(data.get("dir") or "").strip()
        return Path(d) if d else None
    except Exception:
        return None


def wps_root() -> Path:
    """wps-skills 的克隆目录（仓库外）。"""
    s = load_settings()
    # apple-ui 版 Settings 可能缺 wps_mcp_entry/wps_mcp_dir 字段，用 getattr 兼容
    if getattr(s, "wps_mcp_entry", None):
        return Path(s.wps_mcp_entry).parent.parent
    if getattr(s, "wps_mcp_dir", None):
        return Path(s.wps_mcp_dir)
    return _runtime_wps_root() or _default_wps_root()


def wps_entry() -> Path:
    """wps-office MCP 服务入口（dist/index.js）。"""
    s = load_settings()
    if getattr(s, "wps_mcp_entry", None):
        return Path(s.wps_mcp_entry)
    return wps_root() / "wps-office-mcp" / "dist" / "index.js"


def wps_skills_dir() -> Path:
    """第三方 Skills 目录（随 wps-skills 一起克隆，同样不入库）。"""
    return wps_root() / "skills"


def _deps_mcp_root() -> Path:
    """官方 MCP servers 克隆目录（仓库外）。"""
    import os
    base = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(base) / "edu-agent" / "deps" / "mcp-servers"


def _deps_root() -> Path:
    """第三方依赖基目录（仓库外 %LOCALAPPDATA%/edu-agent/deps）。"""
    import os
    base = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(base) / "edu-agent" / "deps"



@dataclass(frozen=True)
class McpSpec:
    id: str
    name: str
    desc: str
    kind: str                 # python | node
    resolve: Callable[[], Path]   # 入口文件解析器（惰性：第三方可换位置/未安装）
    probe: str = "stdio"      # stdio | wps-bridge
    default_on: bool = True
    note: str = ""
    requires: tuple[str, ...] = ()   # 该服务额外需要的 python 模块（客户端 mcp 包另行统一检查）
    extra_args: tuple[str, ...] = ()  # 追加到入口文件后的启动参数（如 filesystem 的 -d 允许目录）

    def entry(self) -> Path:
        try:
            return self.resolve()
        except Exception:
            return Path("")


@dataclass(frozen=True)
class SkillSpec:
    id: str
    name: str
    desc: str
    category: str
    icon: str
    mcp: str = ""             # 绑定的 MCP 服务 id
    default_on: bool = True
    builtin: bool = False     # True = 内置能力（没有 skills/<id>/SKILL.md 实体，UI 显示「内置」而非「文件缺失」）


MCP_SPECS: tuple[McpSpec, ...] = (
    McpSpec(
        id="math",
        name="edu-math 计算服务",
        desc="符号计算 / 求导 / 单调区间 / 解方程（teaching 计算校验）",
        kind="python",
        resolve=lambda: ROOT / "mcp_servers" / "math_server.py",
        probe="stdio",
        requires=("sympy",),
    ),
    McpSpec(
        id="wps-office",
        name="WPS Office 服务",
        desc="经 PowerShell COM 操控 WPS 文字 / 表格 / 演示（教案导出 Word 等）",
        kind="node",
        resolve=wps_entry,
        probe="wps-bridge",
        note="第三方（wps-skills）装在仓库外，由 deploy/setup_wps_mcp.cmd 安装；调用时会自动拉起 WPS",
    ),
    McpSpec(
        id="filesystem",
        name="文件系统服务",
        desc="安全访问指定目录：列目录 / 读文件 / 搜索（白名单收口，默认仅项目目录）",
        kind="node",
        resolve=lambda: _deps_mcp_root() / "src" / "filesystem" / "dist" / "index.js",
        probe="stdio",
        extra_args=(str(ROOT),),
        note="第三方（官方 MCP servers/filesystem）装在仓库外；默认仅允许访问项目目录 D:\\edu-agent，可在 capabilities.py 调整",
    ),
    McpSpec(
        id="fetch",
        name="网页抓取服务",
        desc="抓取网页并转为 Markdown（fetch / fetch_multi，供 LLM 阅读）",
        kind="python",
        resolve=lambda: ROOT / "mcp_servers" / "fetch_launcher.py",
        probe="stdio",
        note="第三方（官方 MCP servers/fetch）；网络只读抓取，白名单收口",
    ),
    McpSpec(
        id="time",
        name="时间与时区服务",
        desc="当前时间 / 时区转换（get_current_time / convert_time）",
        kind="python",
        resolve=lambda: ROOT / "mcp_servers" / "time_launcher.py",
        probe="stdio",
        note="第三方（官方 MCP servers/time）；白名单收口",
    ),
    McpSpec(
        id="git",
        name="Git 仓库服务",
        desc="读取/查询本地 git 仓库（status/log/branch/diff 等，白名单收口，无写操作）",
        kind="python",
        resolve=lambda: ROOT / "mcp_servers" / "git_launcher.py",
        probe="stdio",
        extra_args=("--repository", str(ROOT)),
        note="第三方（官方 MCP servers/git）；默认操作项目仓库 D:\\edu-agent，仅查询收口",
    ),
    McpSpec(
        id="memory",
        name="记忆服务",
        desc="知识图谱记忆：读取/搜索节点（read_graph / search_nodes / open_nodes 等）",
        kind="node",
        resolve=lambda: _deps_mcp_root() / "src" / "memory" / "dist" / "index.js",
        probe="stdio",
        note="第三方（官方 MCP servers/memory）；白名单收口为读取",
    ),
    McpSpec(
        id="sqlite",
        name="SQLite 数据库服务",
        desc="本地 SQLite 只读查询（list_tables / get_schema / query）",
        kind="python",
        resolve=lambda: ROOT / "mcp_servers" / "sqlite_mcp.py",
        probe="stdio",
        note="自写只读 SQLite 服务；数据文件 data/sqlite/edu.db",
    ),
    McpSpec(
        id="playwright",
        name="浏览器自动化（Playwright）",
        desc="无头浏览器导航/读取页面（browser_navigate / browser_snapshot 等，白名单收口）",
        kind="node",
        resolve=lambda: _deps_root() / "playwright-mcp" / "node_modules" / "@playwright" / "mcp" / "cli.js",
        probe="stdio",
        extra_args=("--headless", "--browser", "chromium"),
        note="第三方 @playwright/mcp；浏览器控制风险高，白名单只放读取/导航类",
    ),

    # 教材库 MCP 服务（移植自 fork 线，2026-10-02）：把平台**已经连通**的教材读取
    # （textbook_lib，与 /api/textbook* 同一份实现）包成 MCP 工具，技能（如章节思维导图）
    # 只调工具、不自己读文件/解析格式。**只读**：不写文件、不改向量库。
    McpSpec(
        id="textbook",
        name="edu-textbook 教材服务",
        desc="读本地教材库：章节目录、某一章的小节清单、章节思维导图 Markdown（只读）",
        kind="python",
        resolve=lambda: ROOT / "mcp_servers" / "textbook_server.py",
        probe="stdio",
    ),
)

SKILL_SPECS: tuple[SkillSpec, ...] = (
    SkillSpec(id="wps-word", name="WPS 文字", icon="W",
              desc="通过自然语言操控 Word 文档：排版、格式、内容编辑",
              category="WPS Office", mcp="wps-office"),
    SkillSpec(id="wps-excel", name="WPS 表格", icon="X",
              desc="通过自然语言操控 Excel：公式编写、数据清洗、图表创建",
              category="WPS Office", mcp="wps-office"),
    SkillSpec(id="wps-ppt", name="WPS 演示", icon="P",
              desc="通过自然语言操控 PPT：排版美化、内容生成、动画设置",
              category="WPS Office", mcp="wps-office"),
    SkillSpec(id="wps-office", name="WPS 跨应用助手", icon="O",
              desc="统一管理 Excel / Word / PPT，处理跨应用操作与通用功能",
              category="WPS Office", mcp="wps-office"),
    # 2026-09-12：会话内文档预览（纯前端内存渲染，不落盘、不碰 WPS）
    # 关闭它只会让会话页不渲染右侧预览面板，**完全不影响导出链路**。
    SkillSpec(id="doc-session-preview", name="会话文档预览", icon="D",
              desc="AI 生成文档后，在会话页右侧唤起多格式预览面板（内存渲染，不落盘、不调用 WPS）",
              category="文档", builtin=True),
    # 2026-09-23：PPT 优化（读已有 .pptx → 体检 → 保守原地统一 → 内容零丢失校验）
    # **默认关闭**：它会在任务目录写中间文件、并改写老师的稿，属于"要显式开"的能力。
    SkillSpec(id="ppt-polish", name="PPT 优化", icon="P",
              desc="在右侧面板里读入已有 .pptx：体检（页数/字体/字号/密度）、统一字体与字号层级、清理动画，"
                   "并在导出后逐页校验内容没被改动",
              category="文档", builtin=True, default_on=False),
    # 2026-09-27（老师：「启用联网功能，因为计划与 agent 相连」）：联网取图。
    # 它做的是"把一个公网图片 URL 取回 data/uploads/ 并挂到某一页"——同事的教案 agent 给 URL，
    # 我们取图/内联/导出带图；**不需要任何图源 key**。搜索类（openverse/pexels…）缺 key 时如实报。
    # 默认开：老师明确要求启用；关掉它 = 图标/取图入口整体不可用（预览与导出不受影响）。
    SkillSpec(id="web-image", name="联网取图", icon="N",
              desc="允许把公网图片 URL 取回本地（data/uploads/）并挂到幻灯片某一页；"
                   "同事的 agent 可以直接给图 URL。只认 http/https 公网地址，拒内网，单张 ≤10MB",
              category="素材", builtin=True, default_on=True),
)

_KIND_SPECS: dict[str, dict[str, McpSpec | SkillSpec]] = {
    "mcp": {s.id: s for s in MCP_SPECS},
    "skill": {s.id: s for s in SKILL_SPECS},
}

_CACHE: dict[str, tuple[float, dict]] = {}


# ---------- 开关持久化 ----------
def mcp_spec(cid: str) -> McpSpec | None:
    """按 id 取 MCP 服务定义（mcp_tools 启动子进程时用）。"""
    return _KIND_SPECS["mcp"].get(cid)


def skill_spec(cid: str) -> SkillSpec | None:
    return _KIND_SPECS["skill"].get(cid)


def state_path() -> Path:
    return load_settings().data_dir / "capabilities.json"


def _load_state() -> dict:
    p = state_path()
    if not p.exists():
        return {"version": 1, "mcp": {}, "skill": {}}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return {"version": 1,
                "mcp": dict(raw.get("mcp") or {}),
                "skill": dict(raw.get("skill") or {})}
    except Exception:
        return {"version": 1, "mcp": {}, "skill": {}}


def _save_state(st: dict) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def _default_on(kind: str, cid: str) -> bool:
    spec = _KIND_SPECS.get(kind, {}).get(cid)
    return bool(spec.default_on) if spec else False


def is_enabled(kind: str, cid: str) -> bool:
    """能力是否开启（未显式设置过 -> 取 spec 默认，即默认全开）。"""
    st = _load_state()
    bucket = st.get(kind) or {}
    if cid in bucket:
        return bool(bucket[cid])
    return _default_on(kind, cid)


def set_enabled(kind: str, cid: str, enabled: bool) -> dict:
    if cid not in _KIND_SPECS.get(kind, {}):
        return {"ok": False, "error": f"未知能力：{kind}/{cid}"}
    st = _load_state()
    st.setdefault(kind, {})[cid] = bool(enabled)
    _save_state(st)
    _CACHE.pop(kind + ":" + cid, None)   # 开关变化 -> 状态缓存失效
    return {"ok": True, "kind": kind, "id": cid, "enabled": bool(enabled)}


# ---------- 依赖探测 ----------
def _node_exe() -> str | None:
    return shutil.which("node")


def _powershell_exe() -> str | None:
    return shutil.which("powershell") or shutil.which("pwsh")


def _bridge_ping(timeout: float = 20.0) -> tuple[bool, str]:
    """跑 wps-com.ps1 的 ping（纯 no-op，不碰 COM、不会拉起 WPS）。"""
    ps = _powershell_exe()
    script = wps_root() / "wps-office-mcp" / "scripts" / "wps-com.ps1"
    if not ps or not script.exists():
        return False, "桥接脚本缺失"
    try:
        r = subprocess.run(
            [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
             "-Action", "ping", "-Params", "{}"],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        if r.returncode == 0 and '"success": true' in (r.stdout or "").replace('"success":true', '"success": true'):
            return True, "COM 桥接就绪"
        return False, (r.stdout or r.stderr or "无输出").strip()[:120]
    except subprocess.TimeoutExpired:
        return False, f"桥接探测超时（{timeout:.0f}s）"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _wps_running() -> list[str]:
    """tasklist 一次拿到 WPS 三件套运行状态（wps/et/wpp）。"""
    try:
        r = subprocess.run(["tasklist", "/NH", "/FO", "CSV"],
                           capture_output=True, text=True, timeout=15,
                           encoding="utf-8", errors="replace")
        out = (r.stdout or "").lower()
        return [n for n in ("wps.exe", "et.exe", "wpp.exe") if '"' + n + '"' in out]
    except Exception:
        return []


def _mods_ok(mods: tuple[str, ...]) -> tuple[bool, str]:
    """检查一批 python 模块是否可导入（失败返回第一个缺的名字）。"""
    import importlib.util

    for m in mods:
        try:
            if importlib.util.find_spec(m) is None:
                return False, m
        except Exception:
            return False, m
    return True, ""


def _mcp_tools(spec: McpSpec) -> list[str]:
    """真做一次 MCP 握手拿工具清单（慢，长缓存；失败返回 []）。"""
    key = "tools:" + spec.id
    hit = _CACHE.get(key)
    now = time.time()
    if hit and now - hit[0] < _TOOLS_CACHE_TTL:
        return hit[1].get("tools", [])
    try:
        from edu_agent import mcp_tools
        tools = mcp_tools.list_tools(spec.id, timeout=25.0)
    except Exception:
        tools = []
    _CACHE[key] = (now, {"tools": tools})
    return tools


def _probe_mcp(spec: McpSpec, deep: bool = False) -> dict:
    """探测单个 MCP 服务：产物/运行时/桥接/进程；deep=True 时附带工具清单。"""
    key = "mcp:" + spec.id
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _MCP_CACHE_TTL and not deep:
        base = dict(hit[1])
    else:
        base = {"running": False, "detail": "", "tools": None}
        ok_client, miss = _mods_ok(("mcp",) + tuple(spec.requires))
        if not ok_client:
            base["detail"] = f"缺依赖 {miss}（.venv 里 pip install {miss}）"
        elif not spec.entry().exists():
            where = ("deploy\\setup_wps_mcp.cmd" if spec.id == "wps-office"
                     else str(spec.entry()))
            base["detail"] = "未安装（先跑 " + where + "）"
        elif spec.kind == "node" and not _node_exe():
            base["detail"] = "未检测到 Node.js"
        elif spec.probe == "wps-bridge":
            ok, msg = _bridge_ping()
            apps = _wps_running()
            base["running"] = ok
            base["wps_apps"] = apps
            if ok:
                base["detail"] = ("WPS 运行中（" + "/".join(a.replace('.exe', '') for a in apps) + "）"
                                  if apps else "桥接就绪 · WPS 未启动（导出时自动拉起）")
            else:
                base["detail"] = msg
        else:
            base["running"] = True
            base["detail"] = "入口就绪"
        _CACHE[key] = (now, dict(base))
    out = dict(base)
    out["tools"] = len(_mcp_tools(spec)) if deep else None
    return out


def _skill_files(spec: SkillSpec) -> dict:
    """读 skills/<id>/SKILL.md 的 front-matter（name/description）。"""
    p = wps_skills_dir() / spec.id / "SKILL.md"
    info = {"path": str(p), "exists": p.exists(), "size": 0}
    if p.exists():
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
            info["size"] = len(txt)
            for line in txt.splitlines()[:12]:
                if line.startswith("name:"):
                    info["name"] = line.split(":", 1)[1].strip()
                elif line.startswith("description:"):
                    info["desc"] = line.split(":", 1)[1].strip()
        except Exception:
            pass
    return info


# ---------- 对外快照 ----------
def snapshot(deep: bool = False) -> dict:
    """全部能力 + 开关 + 状态（右栏面板 / MCP 服务视图 / Skill 市场共用）。"""
    mcp_rows = []
    for spec in MCP_SPECS:
        en = is_enabled("mcp", spec.id)
        st = _probe_mcp(spec, deep=deep) if en else {"running": False, "detail": "已关闭", "tools": None}
        mcp_rows.append({
            "id": spec.id, "name": spec.name, "desc": spec.desc, "note": spec.note,
            "kind": spec.kind, "probe": spec.probe,
            "enabled": en, "running": bool(st.get("running")), "detail": st.get("detail", ""),
            "tools": st.get("tools"), "wps_apps": st.get("wps_apps", []),
            "entry": str(spec.entry()), "entry_exists": spec.entry().exists(),
        })
    skill_rows = []
    for spec in SKILL_SPECS:
        en = is_enabled("skill", spec.id)
        files = _skill_files(spec)
        bound = spec.mcp
        bound_en = is_enabled("mcp", bound) if bound else True
        skill_rows.append({
            "id": spec.id, "name": spec.name, "desc": spec.desc or files.get("desc", ""),
            "category": spec.category, "icon": spec.icon, "mcp": bound,
            "enabled": en, "active": en and bound_en,
            "installed": files["exists"], "size": files["size"], "path": files["path"],
            "builtin": spec.builtin,
        })
    # 2026-09-19：再加一路「自建技能」——agent 自己写出来的 SKILL.md（经验→技能回路），
    # 与第三方 wps-skills 那批分开列，前端单独展示；读不到就当空表，绝不影响快照。
    local_rows: list[dict] = []
    try:
        from edu_agent import skills_local

        local_rows = skills_local.list_skills()
    except Exception:  # noqa: BLE001
        local_rows = []
    return {"mcp": mcp_rows, "skills": skill_rows, "skills_local": local_rows,
            "wps_export_ready": wps_available()[0],
            "session_doc_preview_ready": session_preview_ready(),
            "ppt_polish_ready": ppt_polish_ready(),
            "state_file": str(state_path())}


def session_preview_ready() -> bool:
    """会话内文档预览是否启用（只影响会话页右侧预览面板的渲染，不影响任何导出）。"""
    return is_enabled("skill", "doc-session-preview")


def ppt_polish_enabled() -> bool:
    """PPT 优化开关（默认关）。开不开都不影响 WPS 导出与教案生成。"""
    return is_enabled("skill", "ppt-polish")


def ppt_polish_ready() -> bool:
    """PPT 优化是否可用 = 开关开着 **且** ppt-master 装好（脚本齐 + 依赖齐）。

    任何一项不满足，面板上的「优化」按钮就该灰显并说明原因——比"点了报错"友好。
    """
    if not ppt_polish_enabled():
        return False
    try:
        from edu_agent import ppt_polish  # 延迟导入：没装也不该拖慢能力快照
        return bool(ppt_polish.status().get("intake_ready"))
    except Exception:  # noqa: BLE001 自检失败=不可用，不抛
        return False


def wps_available() -> tuple[bool, str]:
    """WPS 导出可用性：开关开着 + 入口构建好 + 桥接通。"""
    if not is_enabled("mcp", "wps-office"):
        return False, "WPS Office 服务已在「MCP 服务」中关闭"
    spec = _KIND_SPECS["mcp"]["wps-office"]
    st = _probe_mcp(spec)
    if not st.get("running"):
        return False, str(st.get("detail") or "WPS 桥接不可用")
    return True, "就绪"


# ---------- 技能动态发现（Skill 生态 / skill_market 需要；纯新增，不改动上面任何逻辑） ----------
# 用途：`skill_market/capability_registry.py` 用 `all_skill_specs()` 校验 `skill:<id>`
# 能力 id 是否真实存在；`_parse_front_matter` 是 `discover_skills()` 的内部依赖。
# 移植说明：本仓库的 `SkillSpec` 没有 vendor / trial / entry 字段（那三个是"本工作区技能"
# 试用面板用的），所以这里只填本仓库确实存在的字段 —— 技能生态只需要
# id / name / desc / category / icon 这几个来判断能力是否存在。
def local_skills_dir() -> Path:
    """本仓库自带的 Skills 目录（`<repo>/skills`，随仓库版本管理）。"""
    return ROOT / "skills"


def _parse_front_matter(path: Path) -> dict:
    """读 SKILL.md 的 front-matter（只支持单行 `key: value`，零依赖、够用）。

    注意：**不要**写 YAML 折叠块（`description: >`）—— 按行读会拿到字面量 `>`。
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    meta: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip().strip('"').strip("'")
        if key and value:
            meta[key] = value
    return meta


def discover_skills() -> tuple[SkillSpec, ...]:
    """扫描本项目 `skills/*/SKILL.md`，**动态注册**技能。

    也就是说：往 `<repo>/skills/<id>/` 放一个带 front-matter 的 SKILL.md，
    Skill 市场里就会出现它 —— 不用改平台代码。识别字段：
    name(技能 id) / title(显示名) / description / category / icon。
    """
    root = local_skills_dir()
    if not root.is_dir():
        return ()
    found: list[SkillSpec] = []
    for child in sorted(root.iterdir()):
        skill_md = child / "SKILL.md"
        if not child.is_dir() or not skill_md.exists():
            continue
        meta = _parse_front_matter(skill_md)
        sid = (meta.get("name") or child.name).strip()
        if not sid:
            continue
        found.append(SkillSpec(
            id=sid,
            name=(meta.get("title") or meta.get("name") or sid).strip(),
            desc=(meta.get("description") or "").strip(),
            category=(meta.get("category") or "通用能力").strip(),
            icon=(meta.get("icon") or "S").strip()[:2],
            mcp="",
        ))
    return tuple(found)


def all_skill_specs() -> dict[str, SkillSpec]:
    """静态注册的技能 + 动态发现的技能（**同名以静态为准**，便于单独覆盖）。"""
    merged: dict[str, SkillSpec] = {s.id: s for s in SKILL_SPECS}
    for spec in discover_skills():
        merged.setdefault(spec.id, spec)
    return merged
