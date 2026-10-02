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
import os
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
    if s.wps_mcp_entry:
        return Path(s.wps_mcp_entry).parent.parent
    if s.wps_mcp_dir:
        return Path(s.wps_mcp_dir)
    return _runtime_wps_root() or _default_wps_root()


def wps_entry() -> Path:
    """wps-office MCP 服务入口（dist/index.js）。"""
    s = load_settings()
    if s.wps_mcp_entry:
        return Path(s.wps_mcp_entry)
    return wps_root() / "wps-office-mcp" / "dist" / "index.js"


def wps_skills_dir() -> Path:
    """第三方 Skills 目录（随 wps-skills 一起克隆，同样不入库）。"""
    return wps_root() / "skills"


def local_skills_dir() -> Path:
    """**本项目自带的** Skills 目录（<repo>/skills，随仓库版本管理）。

    与第三方 wps-skills 的区别：这里的技能是我们自己写的、跟着 edu-agent 仓库走；
    第三方技能仍在仓库外的 wps-skills 克隆里（不入库）。
    """
    return ROOT / "skills"


def skill_dir(cid: str) -> Path:
    """技能目录解析：**本项目优先**，其次第三方 wps-skills。"""
    local = local_skills_dir() / cid
    if (local / "SKILL.md").exists():
        return local
    return wps_skills_dir() / cid


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
    mcp: str = ""             # 绑定的 MCP 服务 id；留空 = 技能自带实现，不需要 MCP
    default_on: bool = True
    vendor: str = ""          # 详情页那行来源小字（留空则用 WPS 的默认文案）
    trial: str = ""           # 非空 = 详情页提供「试用」面板：ask（提问即答）/ workbench（工作台预览）
    entry: str = "scripts/ask.py"   # 入口脚本（相对技能目录）；平台按它调用技能
    builtin: bool = False     # True = 内置能力（没有 skills/<id>/SKILL.md 实体，UI 显示「内置」而非「文件缺失」）
    probe: Callable[[], tuple[bool, str]] | None = None   # 可选：返回 (running, detail)


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
    # 2026-10-02：教材库 MCP 服务。把平台**已经连通**的教材读取（textbook_lib，
    # 与 /api/textbook* 同一份实现）包成 MCP 工具，技能（如思维导图）只调工具、
    # 不自己读文件/解析格式。只读：不写文件、不改向量库。
    McpSpec(
        id="textbook",
        name="edu-textbook 教材服务",
        desc="读本地教材库：章节目录、某一章的小节清单、章节思维导图 Markdown（只读）",
        kind="python",
        resolve=lambda: ROOT / "mcp_servers" / "textbook_server.py",
        probe="stdio",
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
    # 本项目自带技能（<repo>/skills/ollama-qa）：内部封装本机 Ollama，不需要 MCP、不需要 API Key
    SkillSpec(id="ollama-qa", name="本机 Ollama 问答", icon="Q",
              desc="提问即答：把问题交给本机 Ollama 模型回答（免 API Key、可离线）",
              category="通用能力", mcp="", trial="ask",
              vendor="本工作区技能 · 内部封装本机 Ollama · MIT"),
    # 2026-09-12：会话内文档预览（纯前端内存渲染，不落盘、不碰 WPS）
    # 关闭它只会让会话页不渲染右侧预览面板，**完全不影响导出链路**。
    SkillSpec(id="doc-session-preview", name="会话文档预览", icon="D",
              desc="AI 生成文档后，在会话页右侧唤起多格式预览面板（内存渲染，不落盘、不调用 WPS）",
              category="文档", builtin=True),
    SkillSpec(id="vision-math", name="图片与公式分析", icon="V",
              desc="识别题目截图、函数图象、几何图和图片公式，并附本地符号校验结果",
              category="教学", builtin=True, probe=lambda: _vision_probe()),
    # 2026-09-26：课件生成 —— 第三方引擎 ppt-master（MIT，装在仓库外 D:\hermes\ppt-master）。
    # 只借它的「编译器层」（质量门 + SVG→原生 DrawingML），页面由 scripts/wsl_ppt_bridge.py
    # 用固定版式确定性渲染；本机 SAC 拦 lxml，故实际渲染在 WSL 里执行（与 wsl_docx 后路同因）。
    SkillSpec(id="ppt-master", name="课件生成（原生 PPTX）", icon="P",
              desc="教材内容 → 原生可编辑 PPTX：单个形状可选中拖动、中文字体正确、不依赖 Office",
              category="文档", builtin=True, probe=lambda: _ppt_engine_probe()),
    # ---- 以下两个技能来自 feat/ppt-view 分支（合并时两边各自新增 → 都保留） ----
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


# ---------- 技能动态发现（Skill 市场无需改代码即可接入新技能） ----------
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

        name(技能 id) / title(显示名) / description / category / icon /
        entry(入口脚本，默认 scripts/ask.py) / trial(试用面板类型) / vendor

    （第三方 wps-skills 仍在仓库外，由 `SKILL_SPECS` 静态注册，不走这里。）
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
            vendor=(meta.get("vendor") or "本工作区技能 · 随仓库版本管理").strip(),
            trial=(meta.get("trial") or "").strip().lower(),
            entry=(meta.get("entry") or "scripts/ask.py").strip(),
        ))
    return tuple(found)


def all_skill_specs() -> dict[str, SkillSpec]:
    """静态注册的技能 + 动态发现的技能（**同名以静态为准**，便于单独覆盖）。"""
    merged: dict[str, SkillSpec] = {s.id: s for s in SKILL_SPECS}
    for spec in discover_skills():
        merged.setdefault(spec.id, spec)
    return merged


_CACHE: dict[str, tuple[float, dict]] = {}


# ---------- 开关持久化 ----------
def mcp_spec(cid: str) -> McpSpec | None:
    """按 id 取 MCP 服务定义（mcp_tools 启动子进程时用）。"""
    return _KIND_SPECS["mcp"].get(cid)


def skill_spec(cid: str) -> SkillSpec | None:
    """按 id 取技能定义（含 skills/ 下动态发现的技能）。"""
    return all_skill_specs().get(cid)


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
    spec = skill_spec(cid) if kind == "skill" else _KIND_SPECS.get(kind, {}).get(cid)
    return bool(spec.default_on) if spec else False


def is_enabled(kind: str, cid: str) -> bool:
    """能力是否开启（未显式设置过 -> 取 spec 默认，即默认全开）。"""
    st = _load_state()
    bucket = st.get(kind) or {}
    if cid in bucket:
        return bool(bucket[cid])
    return _default_on(kind, cid)


def set_enabled(kind: str, cid: str, enabled: bool) -> dict:
    known = all_skill_specs() if kind == "skill" else _KIND_SPECS.get(kind, {})
    if cid not in known:
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


def _ppt_engine_probe() -> tuple[bool, str]:
    """课件生成引擎（ppt-master）探活。available() 自带 lru_cache，只在首次起一次 WSL。"""
    try:
        from edu_agent import ppt_master

        return ppt_master.available()
    except Exception as exc:  # noqa: BLE001  探测失败只影响状态显示，绝不阻断业务链路
        return False, f"{type(exc).__name__}: {exc}"


def _vision_probe() -> tuple[bool, str]:
    """视觉能力只检查配置，不在状态轮询中发起计费请求。"""
    try:
        from edu_agent import vision

        return vision.available()
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def _skill_files(spec: SkillSpec) -> dict:
    """读技能目录里 SKILL.md 的 front-matter（name/description/size）。

    实现走 `_parse_front_matter`：**按行**读取，所以 SKILL.md 的 description 必须写成单行，
    写成 YAML 折叠块（`description: >`）会被读成字面量 `>`。
    """
    p = skill_dir(spec.id) / "SKILL.md"
    info = {"path": str(p), "exists": p.exists(), "size": 0}
    if p.exists():
        try:
            info["size"] = len(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            pass
        meta = _parse_front_matter(p)
        for key, out_key in (("name", "name"), ("description", "desc"), ("title", "title")):
            if meta.get(key):
                info[out_key] = meta[key]
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
    for spec in all_skill_specs().values():
        en = is_enabled("skill", spec.id)
        files = _skill_files(spec)
        bound = spec.mcp
        bound_en = is_enabled("mcp", bound) if bound else True
        running, detail = None, ""
        if spec.probe is not None:                 # 只有声明了 probe 的技能才做真探活
            try:
                running, detail = spec.probe()
            except Exception as exc:  # noqa: BLE001
                running, detail = False, f"{type(exc).__name__}: {exc}"
        skill_rows.append({
            "id": spec.id, "name": spec.name, "desc": spec.desc or files.get("desc", ""),
            "category": spec.category, "icon": spec.icon, "mcp": bound,
            "enabled": en, "active": en and bound_en,
            "installed": files["exists"], "size": files["size"], "path": files["path"],
            "vendor": spec.vendor, "trial": spec.trial,
            "builtin": spec.builtin,
            "running": running, "detail": detail,
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


# ---------- Skill 执行：本机 Ollama 问答（skills/ollama-qa） ----------
# 设计取舍：**平台不实现技能的业务逻辑，只负责转发**。执行全部封在技能自己的脚本里，
# 所以技能能独立演进、也能脱离平台单独跑（skills/ollama-qa/scripts/ask.cmd）。
# 解释器用 sys.executable —— 平台跑在 edu-agent 的 venv 里，天然就是正确的那个。
_SKILL_TIMEOUT = 600.0        # 纯 CPU 推理慢，给足时间


def run_skill_script(cid: str, args: list[str],
                     timeout: float = _SKILL_TIMEOUT) -> dict:
    """执行技能的入口脚本（由 SKILL.md 的 `entry` 指定，默认 scripts/ask.py）。

    返回 {ok, stdout, stderr, code}（异常一律吞成 ok=False）。
    """
    spec = skill_spec(cid)
    rel = (spec.entry if spec else "") or "scripts/ask.py"
    d = skill_dir(cid)
    script = d / rel
    if not script.exists():
        return {"ok": False, "error": "技能脚本缺失：" + str(script)}
    # PYTHONIOENCODING=utf-8：Windows 上管道里的 stdout 默认是 GBK，
    # 不强制的话技能输出的中文会被按 UTF-8 解码成乱码（实测踩过）。
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            [sys.executable, str(script)] + list(args),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, cwd=str(d), env=env,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "技能执行超时（%.0f 秒）。纯 CPU 上大模型慢，换更小的模型或重试。" % timeout}
    except Exception as e:                                    # noqa: BLE001
        return {"ok": False, "error": type(e).__name__ + ": " + str(e)}
    return {"ok": True, "stdout": proc.stdout or "", "stderr": proc.stderr or "",
            "code": proc.returncode}


def run_skill(cid: str, args: dict | None = None, timeout: float = _SKILL_TIMEOUT) -> dict:
    """**通用**技能调用：`{参数名: 值}` -> 技能入口脚本的命令行参数 -> 技能输出的结构化 JSON。

    约定（写在技能 SKILL.md 里）：入口脚本支持 `--json`，把结果打成一行 JSON 到 stdout。
    参数转换：`{"stage": "初中"}` -> `--stage 初中`；值为 True -> 只加那个开关；空值跳过。
    这样**任何放在 skills/<id>/ 下的技能都能被调起来**，不必为每个技能单独写接口。
    """
    spec = skill_spec(cid)
    if spec is None:
        known = "、".join(sorted(all_skill_specs())) or "（无）"
        return {"ok": False, "error": "未知技能：%s（现有：%s）" % (cid, known)}
    if not is_enabled("skill", cid):
        return {"ok": False, "gate": "capability",
                "error": "技能「%s」已在「MCP 服务」中被关闭" % spec.name}

    cli: list[str] = []
    for key, value in (args or {}).items():
        if value is None or value is False or value == "":
            continue
        flag = "--" + str(key).strip().lstrip("-").replace("_", "-")
        if value is True:
            cli.append(flag)
        else:
            cli.extend([flag, str(value)])
    cli.append("--json")

    t0 = time.time()
    res = run_skill_script(cid, cli, timeout=timeout)
    if not res.get("ok"):
        return {"ok": False, "skill": cid, "error": str(res.get("error") or "技能执行失败")}

    raw = (res.get("stdout") or "").strip()
    payload = raw
    if payload and not payload.lstrip().startswith("{"):
        # 兜底：脚本若在前面写了日志，取最后一行 JSON
        for line in reversed(raw.splitlines()):
            if line.strip().startswith("{"):
                payload = line.strip()
                break
    try:
        data = json.loads(payload)
    except Exception:                                          # noqa: BLE001
        return {"ok": False, "skill": cid, "error": "技能没有输出 JSON",
                "raw": raw[-400:], "stderr": (res.get("stderr") or "")[-400:]}
    if not isinstance(data, dict):
        return {"ok": False, "skill": cid, "error": "技能输出的 JSON 不是对象"}
    data.setdefault("ok", True)
    data.setdefault("skill", cid)
    data["ms"] = int((time.time() - t0) * 1000)
    # skill-create 埋点（docs/21）：把"这一轮调了哪个技能"记进本轮缓冲，
    # 供后续生成运行痕迹（R2/L2 需要"实际调用集合"）。埋点失败不影响技能结果。
    try:
        from edu_agent.skill_market import health as _health
        from edu_agent.skill_market import runs as _runs

        _runs.record_skill_call(cid, ok=bool(data.get("ok")), ms=int(data["ms"]),
                                args=args or {})
        # L4 发布后回归：**已发布**的技能（装在运行时目录里）额外记一条 skill-run，
        # 这样成功率/延迟统计不依赖"是否在一轮对话里被调用"。草稿/市场技能不记。
        _health.record_run(cid, ok=bool(data.get("ok")), ms=int(data["ms"]),
                           error=str(data.get("error") or ""), args=args or {})
    except Exception:  # noqa: BLE001
        pass
    return data


def ask_ollama_qa(question: str) -> dict:
    """Skill「本机 Ollama 问答」入口：问题 -> {ok, answer, model, question, ms}。

    技能被关掉时返回 gate=capability（与 WPS 导出的闸门语义一致）。
    """
    question = (question or "").strip()
    if not question:
        return {"ok": False, "error": "问题不能为空"}
    if not is_enabled("skill", "ollama-qa"):
        return {"ok": False, "gate": "capability",
                "error": "技能「本机 Ollama 问答」已在「MCP 服务」中被关闭"}

    t0 = time.time()
    res = run_skill_script("ollama-qa", [question, "--json"])
    if not res.get("ok"):
        return {"ok": False, "error": str(res.get("error") or "技能执行失败")}

    raw = (res.get("stdout") or "").strip()
    try:
        data = json.loads(raw)
    except Exception:                                         # noqa: BLE001
        return {"ok": False, "error": "技能返回了非 JSON 输出",
                "raw": raw[-400:], "stderr": (res.get("stderr") or "")[-400:]}
    if not data.get("ok"):
        return {"ok": False, "error": str(data.get("error") or "技能执行失败"),
                "stderr": (res.get("stderr") or "")[-400:]}
    return {"ok": True,
            "answer": data.get("answer", ""),
            "model": data.get("model", ""),
            "question": data.get("question", question),
            "ms": int((time.time() - t0) * 1000)}
