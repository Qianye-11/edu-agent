# -*- coding: utf-8 -*-
"""个性化工作台的**数据层**：学段 -> 工作台模块配置。

这一层只放数据与纯函数（不依赖 Semantic Kernel），方便单独测试与扩展：
想让「初中/高中」各显示什么模块，改这里就够了。

结构（前端直接按这个渲染）：
    {
      "stage": "初中",                  # 规范化后的学段
      "display_name": "初中",
      "grade_hint": "七 ~ 九年级",
      "theme": {...},                  # 配色/圆角/字号，供前端套主题
      "greeting": "...",               # 进工作台时的一句话
      "sidebar": [{"id","title","icon"}],
      "modules": [{"id","title","icon","desc","size","tags","actions":[{"label","kind"}]}],
      "quick_actions": [{"id","label","icon","kind"}],
      "notes": "..."                   # 给前端/教研看的备注
    }
"""
from __future__ import annotations

import re
from datetime import datetime

# ---------------------------------------------------------------- 学段定义

# 别名 -> 规范学段（用户可能输入「初一」「七年级」「初中数学」等）
_STAGE_ALIASES: dict[str, str] = {
    "初中": "初中", "初级中学": "初中", "中学初级": "初中", "junior": "初中", "middle": "初中",
    "初一": "初中", "初二": "初中", "初三": "初中",
    "七年级": "初中", "八年级": "初中", "九年级": "初中", "7年级": "初中",
    "8年级": "初中", "9年级": "初中", "初中数学": "初中",
    "高中": "高中", "高级中学": "高中", "senior": "高中", "high": "高中",
    "高一": "高中", "高二": "高中", "高三": "高中",
    "高一上": "高中", "高一下": "高中", "高二上": "高中", "高二下": "高中",
    "十年级": "高中", "十一年级": "高中", "十二年级": "高中", "高中数学": "高中",
}

STAGE_ORDER: tuple[str, ...] = ("初中", "高中")

# ---------------------------------------------------------------- 配置表

_CONFIGS: dict[str, dict] = {
    "初中": {
        "display_name": "初中",
        "grade_hint": "七 ~ 九年级",
        "theme": {
            "tone": "活泼明亮", "primary": "#3b82f6", "accent": "#f59e0b",
            "radius": "16px", "font_scale": 1.05, "density": "comfortable",
        },
        "greeting": "同学你好呀！今天想先练几道题，还是把不会的题理一理？",
        "sidebar": [
            {"id": "home", "title": "今日首页", "icon": "H"},
            {"id": "practice", "title": "每日一练", "icon": "P"},
            {"id": "mistakes", "title": "我的错题本", "icon": "M"},
            {"id": "play", "title": "趣味数学", "icon": "G"},
        ],
        "modules": [
            {"id": "daily-practice", "title": "每日一练", "icon": "P", "size": "large",
             "desc": "按当天进度自动出 5 道基础题，做完立刻看讲解，不用自己找题。",
             "tags": ["基础巩固", "10 分钟"],
             "actions": [{"label": "开始练习", "kind": "practice"},
                         {"label": "换一组", "kind": "refresh"}]},
            {"id": "mistake-book", "title": "图形化错题本", "icon": "M", "size": "large",
             "desc": "错题自动按知识点分类，用颜色标出「会了 / 还差点 / 不会」，一眼看出该补哪里。",
             "tags": ["错题归因", "可视化"],
             "actions": [{"label": "查看错题", "kind": "open"},
                         {"label": "一键重做", "kind": "practice"}]},
            {"id": "concept-cards", "title": "知识点卡片", "icon": "C", "size": "normal",
             "desc": "定义、公式、常见误区做成一页一卡，课间两分钟就能翻一遍。",
             "tags": ["随堂记忆"],
             "actions": [{"label": "翻卡片", "kind": "open"}]},
            {"id": "fun-math", "title": "趣味数学闯关", "icon": "G", "size": "normal",
             "desc": "把计算和推理包进小游戏，闯关式加练计算速度与数感。",
             "tags": ["计算速度", "兴趣"],
             "actions": [{"label": "开始闯关", "kind": "play"}]},
            {"id": "geometry-lab", "title": "几何直观实验室", "icon": "L", "size": "normal",
             "desc": "拖动图形看变化：全等、相似、辅助线怎么加，动手比背结论更管用。",
             "tags": ["几何直观"],
             "actions": [{"label": "动手试试", "kind": "open"}]},
            {"id": "habit-checkin", "title": "学习习惯打卡", "icon": "K", "size": "normal",
             "desc": "每天打卡练题、订正、提问，坚持一周就能看到自己的节奏。",
             "tags": ["习惯养成"],
             "actions": [{"label": "今日打卡", "kind": "checkin"}]},
        ],
        "quick_actions": [
            {"id": "ask", "label": "问一道题", "icon": "?", "kind": "ask"},
            {"id": "practice", "label": "来 5 道题", "icon": "P", "kind": "practice"},
            {"id": "card", "label": "背 3 张卡片", "icon": "C", "kind": "open"},
        ],
        "notes": "初中端强调「基础巩固 + 兴趣 + 习惯」：题量小、反馈快、视觉元素多，避免长文阅读。",
    },
    "高中": {
        "display_name": "高中",
        "grade_hint": "高一 ~ 高三",
        "theme": {
            "tone": "沉稳克制", "primary": "#0f766e", "accent": "#6366f1",
            "radius": "10px", "font_scale": 0.98, "density": "compact",
        },
        "greeting": "欢迎回来。今天想推进哪个专题，还是先把上次的错题收个尾？",
        "sidebar": [
            {"id": "home", "title": "学习总览", "icon": "H"},
            {"id": "topics", "title": "专题突破", "icon": "T"},
            {"id": "papers", "title": "限时套卷", "icon": "S"},
            {"id": "analysis", "title": "错题归因", "icon": "A"},
        ],
        "modules": [
            {"id": "topic-breakthrough", "title": "专题突破", "icon": "T", "size": "large",
             "desc": "按高考高频专题（导数、数列、解析几何、概率统计）逐块清障，每块配「必会题型清单」。",
             "tags": ["高考高频", "体系化"],
             "actions": [{"label": "选专题", "kind": "open"},
                         {"label": "看题型清单", "kind": "open"}]},
            {"id": "hard-problems", "title": "压轴题精练", "icon": "X", "size": "large",
             "desc": "按得分点拆解压轴题：第一问保分、第二问抢分、第三问尽力，配套逐步提示。",
             "tags": ["得分策略", "思维链"],
             "actions": [{"label": "开始拆解", "kind": "practice"},
                         {"label": "看提示", "kind": "hint"}]},
            {"id": "timed-paper", "title": "限时套卷", "icon": "S", "size": "normal",
             "desc": "按高考时长与卷面结构计时作答，交卷后自动给出时间分配与失分分布。",
             "tags": ["考场节奏"],
             "actions": [{"label": "开始计时", "kind": "practice"}]},
            {"id": "error-analysis", "title": "错题归因分析", "icon": "A", "size": "normal",
             "desc": "把错因分成「概念不清 / 方法缺失 / 计算失误 / 审题偏差」四类，看主要短板在哪。",
             "tags": ["归因", "提分优先"],
             "actions": [{"label": "看分析", "kind": "open"}]},
            {"id": "knowledge-graph", "title": "知识网络图", "icon": "N", "size": "normal",
             "desc": "把一章的概念、公式、常见变形连成网，标出你自己的薄弱节点。",
             "tags": ["结构化"],
             "actions": [{"label": "打开网络图", "kind": "open"}]},
            {"id": "exam-countdown", "title": "高考倒计时与目标", "icon": "D", "size": "normal",
             "desc": "剩余天数、目标分数、当前差距，按周给出可执行的推进建议。",
             "tags": ["目标管理"],
             "actions": [{"label": "设定目标", "kind": "open"}]},
        ],
        "quick_actions": [
            {"id": "ask", "label": "问一道题", "icon": "?", "kind": "ask"},
            {"id": "topic", "label": "推进专题", "icon": "T", "kind": "open"},
            {"id": "timed", "label": "来一套限时", "icon": "S", "kind": "practice"},
        ],
        "notes": "高中端强调「专题化 + 得分效率 + 自我诊断」：信息密度高、少装饰，突出短板与提分路径。",
    },
}


# ---------------------------------------------------------------- 纯函数接口


def normalize_stage(text: str | None) -> str:
    """把用户输入的学段说法规范成「初中」或「高中」；认不出来时默认「高中」。"""
    t = (text or "").strip()
    if not t:
        return "高中"
    if t in _STAGE_ALIASES:
        return _STAGE_ALIASES[t]
    low = t.lower().replace(" ", "")
    for alias, stage in _STAGE_ALIASES.items():
        if alias in low or alias in t:
            return stage
    # 兜底：出现「初」判初中，出现「高」判高中
    if "初" in t:
        return "初中"
    if "高" in t:
        return "高中"
    return "高中"


def list_stages() -> list[dict]:
    """支持的学段清单（供前端渲染「学段切换」，或给模型做函数选择参考）。"""
    out = []
    for stage in STAGE_ORDER:
        cfg = _CONFIGS[stage]
        out.append({
            "stage": stage,
            "display_name": cfg["display_name"],
            "grade_hint": cfg["grade_hint"],
            "module_count": len(cfg["modules"]),
            "tone": cfg["theme"]["tone"],
            "aliases": sorted(a for a, s in _STAGE_ALIASES.items() if s == stage and a != stage),
        })
    return out


def get_config(stage: str | None, *, for_frontend: bool = True) -> dict:
    """按学段返回完整工作台配置（结构化，直接可渲染）。"""
    name = normalize_stage(stage)
    cfg = _CONFIGS[name]
    data = {
        "stage": name,
        "requested_stage": (stage or "").strip() or None,
        "display_name": cfg["display_name"],
        "grade_hint": cfg["grade_hint"],
        "theme": dict(cfg["theme"]),
        "greeting": cfg["greeting"],
        "sidebar": [dict(x) for x in cfg["sidebar"]],
        "modules": [dict(m) for m in cfg["modules"]],
        "quick_actions": [dict(x) for x in cfg["quick_actions"]],
        "module_count": len(cfg["modules"]),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    if for_frontend:
        data["notes"] = cfg["notes"]
        # 给前端一个「布局建议」：大模块两列，普通模块三列（前端可忽略）
        data["layout"] = {
            "columns": 3,
            "featured": [m["id"] for m in cfg["modules"] if m.get("size") == "large"],
        }
    return data


def get_module(stage: str | None, module_id: str) -> dict | None:
    """取某个学段下单个模块的详情（模块名/id 都认）。"""
    name = normalize_stage(stage)
    key = (module_id or "").strip()
    for m in _CONFIGS[name]["modules"]:
        if key in (m["id"], m["title"]) or key.lower() == m["id"].lower():
            return {"stage": name, "module": dict(m)}
    return None


def stage_summary(stage: str | None) -> str:
    """一句话概述该学段工作台的重点（给模型/前端做副标题）。"""
    cfg = get_config(stage, for_frontend=False)
    titles = "、".join(m["title"] for m in cfg["modules"][:3])
    return "%s（%s）工作台共 %d 个模块，重点是%s；界面风格%s。" % (
        cfg["display_name"], cfg["grade_hint"], cfg["module_count"], titles, cfg["theme"]["tone"])


def diff_stages() -> dict:
    """两个学段的差异摘要（前端可用来解释「为什么界面不一样」）。"""
    a, b = get_config("初中", for_frontend=False), get_config("高中", for_frontend=False)
    a_ids = {m["id"] for m in a["modules"]}
    b_ids = {m["id"] for m in b["modules"]}
    return {
        "初中": {"modules": sorted(a_ids), "theme": a["theme"]["tone"]},
        "高中": {"modules": sorted(b_ids), "theme": b["theme"]["tone"]},
        "only_junior": sorted(a_ids - b_ids),
        "only_senior": sorted(b_ids - a_ids),
    }


_ID_RE = re.compile(r"^[a-z0-9\-]+$")


def validate() -> list[str]:
    """自检数据表：id 合法、必填字段齐全、两学段模块不重复。"""
    errs: list[str] = []
    for stage, cfg in _CONFIGS.items():
        for key in ("display_name", "grade_hint", "theme", "greeting", "sidebar", "modules"):
            if key not in cfg:
                errs.append("%s 缺少字段 %s" % (stage, key))
        seen: set[str] = set()
        for m in cfg.get("modules", []):
            for key in ("id", "title", "icon", "desc", "actions"):
                if key not in m:
                    errs.append("%s/%s 缺少字段 %s" % (stage, m.get("id"), key))
            if not _ID_RE.match(str(m.get("id", ""))):
                errs.append("%s 的模块 id 不合法：%r（只允许小写字母/数字/连字符）" % (stage, m.get("id")))
            if m.get("id") in seen:
                errs.append("%s 模块 id 重复：%s" % (stage, m.get("id")))
            seen.add(m.get("id"))
    return errs
