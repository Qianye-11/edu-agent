"""版式数值（tokens）：唯一权威 + 单位换算。

规格与出处见 `docs/22-预览版式与导出规格.md` §3：
- 数值只许写在 `content/ppt_templates/_builtin/<主题>.json`，代码与文档都读它；
- 画布是固定 1280×720 设计坐标（面板内等比缩放），因此 **画布 px 是唯一口径**：
  `1px = 0.75pt = 9525 EMU`（1280px ↔ 13.333in，见 docs/22 §3.3）；
- 字体只用本机已装字体（WPS 不读 pptx 内嵌的裸 TTF，见 docs/22 §1 D8）。

本模块只做三件事：读 tokens、校验 tokens、换算单位；**不含任何版式逻辑**（在 ppt_svg.py）。
"""

from __future__ import annotations

import json
from pathlib import Path

# ---------------------------------------------------------------- 常量与换算

PX_PER_INCH = 96.0
EMU_PER_PX = 9525          # 12192000 EMU / 1280 px
PT_PER_PX = 0.75           # 96 dpi 下 1px = 0.75pt
CANVAS_W = 1280
CANVAS_H = 720

#: 允许出现在 tokens 里的字号键（每套主题都必须给全）
TYPE_KEYS = ("cover", "title", "label", "body", "caption")
#: 6 个版式 id（docs/22 §5 与 docs/19 §8.4）
LAYOUTS = ("cover", "section", "bullets", "two-col", "media", "end")


def px_to_pt(px: float) -> float:
    """画布 px → pt（导出层用）。"""
    return round(px * PT_PER_PX, 4)


def px_to_emu(px: float) -> int:
    """画布 px → EMU（pptx 原生坐标）。"""
    return int(round(px * EMU_PER_PX))


def blend(fg: str, bg: str, alpha: float) -> str:
    """把 fg 按 alpha 叠在 bg 上，返回显式十六进制色。

    用于"上游没给次文色"的主题：与其写 `fill-opacity`（转 pptx 时容易被丢），
    不如直接算成显式颜色 —— 上游也要求 "Use explicit fills and strokes"。
    """
    f = _rgb(fg)
    b = _rgb(bg)
    mix = tuple(round(f[i] * alpha + b[i] * (1 - alpha)) for i in range(3))
    return "#%02X%02X%02X" % mix


def rgb(color: str) -> tuple[int, int, int]:
    """`#RRGGBB` / `#RGB` → (r, g, b)。公开接口（审计要比颜色，别再调私有 `_rgb`）。"""
    c = str(color or "").strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if len(c) != 6:
        raise ValueError(f"颜色格式不对：{color}")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _rgb(color: str) -> tuple[int, int, int]:
    return rgb(color)


# ---------------------------------------------------------------- 读写 tokens

def repo_root() -> Path:
    """仓库根（src/edu_agent/ppt_tokens.py → 上溯三层）。"""
    return Path(__file__).resolve().parents[2]


def templates_root() -> Path:
    """模板库根目录：`content/ppt_templates/`（可用环境变量 `EDU_PPT_TEMPLATES` 覆盖，测试用）。

    注意：这个环境变量指的是**模板库根**，内置主题在它下面的 `_builtin/`（与 `docs/19` §9.1 一致）。
    """
    import os

    env = os.environ.get("EDU_PPT_TEMPLATES", "").strip()
    if env:
        return Path(env).expanduser()
    return repo_root() / "content" / "ppt_templates"


def builtin_dir() -> Path:
    """内置主题目录（`<模板库根>/_builtin`）。"""
    return templates_root() / "_builtin"


def available() -> list[str]:
    """列出内置主题 id（按文件名排序）。"""
    d = builtin_dir()
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.json"))


def load(theme_id: str) -> dict:
    """读一套主题 tokens；读不到直接抛错（宁可炸也不要静默用默认值）。"""
    path = builtin_dir() / f"{theme_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"没有这套主题：{path}（可选：{', '.join(available()) or '无'}）")
    data = json.loads(path.read_text(encoding="utf-8"))
    problems = validate(data)
    if problems:
        raise ValueError(f"{path.name} 不合法：" + "；".join(problems))
    return data


# ---------------------------------------------------------------- 校验

def validate(t: dict) -> list[str]:
    """校验一套 tokens，返回问题清单（空列表 = 通过）。"""
    bad: list[str] = []

    for key in ("id", "name", "canvas", "palette", "type", "ranges", "fonts", "geometry", "density"):
        if key not in t:
            bad.append(f"缺字段 {key}")
    if bad:
        return bad

    canvas = t["canvas"]
    if (canvas.get("w"), canvas.get("h")) != (CANVAS_W, CANVAS_H):
        bad.append(f"画布必须是 {CANVAS_W}×{CANVAS_H}")
    margin = canvas.get("margin")
    if not isinstance(margin, (int, float)) or not (48 <= margin <= 120):
        bad.append(f"安全边距不合理：{margin}")
    cw = canvas.get("content_w") or []
    if len(cw) != 2 or not (0 < cw[0] <= cw[1] <= CANVAS_W - 2 * (margin or 0) + 1):
        bad.append(f"内容宽不合理：{cw}（右边距为负？）")

    # 字号必须落在自己声明的区间里（区间本身照抄上游 style-system）
    rng = t["ranges"]
    for k in TYPE_KEYS:
        size = t["type"].get(k)
        r = rng.get(k)
        if not isinstance(size, (int, float)):
            bad.append(f"字号 {k} 缺失")
        elif not (isinstance(r, list) and len(r) == 2 and r[0] <= size <= r[1]):
            bad.append(f"字号 {k}={size} 不在声明区间 {r}（上游 style-system 的区间）")

    for k, w in (t.get("weights") or {}).items():
        if not (100 <= int(w) <= 900):
            bad.append(f"字重 {k}={w} 不合理")

    palette = t["palette"]
    for k in ("text", "accent", "sub", "line", "soft"):
        if not palette.get(k):
            bad.append(f"配色缺 {k}")
        else:
            try:
                _rgb(palette[k])
            except ValueError as exc:
                bad.append(str(exc))
    canvas_bg = canvas.get("bg") or ""
    try:
        _rgb(canvas_bg)
    except ValueError as exc:
        bad.append(f"canvas.bg {exc}")

    marker = t.get("marker")
    if marker and not palette.get(marker):
        bad.append(f"marker 指向的配色不存在：{marker}")

    geo = t["geometry"]
    if geo.get("shadow") or geo.get("glow") or geo.get("gradient"):
        bad.append("上游禁用阴影/发光/渐变（style-system 的 Forbidden Treatments）")
    lw = geo.get("line_w") or []
    if not (len(lw) == 2 and 0 < lw[0] <= lw[1] <= 4):
        bad.append(f"线宽不合理：{lw}")
    rad = geo.get("radius") or []
    if not (len(rad) == 2 and rad[0] >= 0 and rad[1] <= 8):
        bad.append(f"圆角不合理：{rad}")

    fonts = t["fonts"]
    stack = fonts.get("stack") or []
    if not stack:
        bad.append("字体栈为空")
    for fam in stack:
        if fam.lower() in ("sans-serif", "serif", "monospace", "system-ui"):
            continue
        if not font_installed(fam):
            bad.append(f"字体《{fam}》本机没装（WPS 不读内嵌字体，只能用系统已装的）")

    dens = t["density"]
    if not (1 <= int(dens.get("blocks_max", 0)) <= 8):
        bad.append(f"每页模块上限不合理：{dens.get('blocks_max')}")
    if not (4 <= int(dens.get("chars_per_block_max", 0)) <= 60):
        bad.append(f"每模块字数上限不合理：{dens.get('chars_per_block_max')}")

    # 声明为"派生"的颜色必须真的等于混合结果（防止手改数字后忘了同步）
    for key, spec in (t.get("derived") or {}).items():
        want = blend(palette[spec["from"]], canvas["bg"], float(spec["alpha"]))
        if (palette.get(key) or "").upper() != want:
            bad.append(f"派生色 {key}={palette.get(key)} 与混合结果 {want} 不一致")
    return bad


# ---------------------------------------------------------------- 本机字体

_FONT_CACHE: set[str] | None = None


def font_installed(family: str) -> bool:
    """本机是否装了某个字体族（Windows：读注册表登记的字体名）。

    非 Windows 或读不到注册表时**返回 True**（不误报），并列出的检查交给真机验收。
    """
    global _FONT_CACHE
    if _FONT_CACHE is None:
        _FONT_CACHE = _installed_fonts()
    if not _FONT_CACHE:
        return True
    needle = family.lower().replace(" ", "")
    # 主题 JSON 常使用中文显示名，而 Windows 字体注册表通常只登记英文族名或文件前缀。
    # 不处理这一层会把已安装的微软雅黑 / 宋体误判为缺失，进而让预览根本无法启动。
    aliases = {
        "微软雅黑": ("microsoftyahei", "msyh"),
        "宋体": ("simsun",),
        "黑体": ("simhei",),
        "楷体": ("kaiti", "simkai"),
        "仿宋": ("fangsong", "simfang"),
    }
    needles = (needle,) + aliases.get(needle, ())
    names = tuple(name.lower().replace(" ", "") for name in _FONT_CACHE)
    return any(part in name for part in needles for name in names)


def _installed_fonts() -> set[str]:
    """读注册表里的字体名（形如 `Microsoft YaHei & Microsoft YaHei UI (TrueType)`）。"""
    import sys

    if sys.platform != "win32":
        return set()
    try:
        import winreg  # noqa: PLC0415  （只在 Windows 上导入）
    except ImportError:
        return set()

    names: set[str] = set()
    for root, sub in (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
    ):
        try:
            with winreg.OpenKey(root, sub) as key:
                total = winreg.QueryInfoKey(key)[1]
                for i in range(total):
                    names.add(winreg.EnumValue(key, i)[0])
        except OSError:
            continue
    return names
