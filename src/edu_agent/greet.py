# -*- coding: utf-8 -*-
"""纯问候短路：把「你好」这类寒暄直接回一句，**不检索教材、不调模型**。

为什么要它（2026-09-27 实测反馈）：
    聊天链路（答疑 A）的设计是「依据教材作答」—— 它会先检索教材、把命中的片段塞进提示词，
    再要求模型按协议作答。用户只发一句「你好」时：
      * 检索照样会跑，拿回几段**与问候毫无关系**的教材片段（实测是「列车加速到 350km/h…
        S=350s…」那种课文整段）；
      * 本地小模型于是顺着这些片段编出一大段教材讲解（还会复述课文原文）。
    问候不是知识问题，走检索+生成既慢又答非所问，所以在这里直接短路。

判定规则（**严格**，宁可漏拦也不误伤）：
    去掉标点空白后不超过 12 个字符，且把「问候词 + 称呼 + 语气词」从串里**剥干净后必须什么都不剩**。
    也就是说只有「整条消息就是一句招呼」才拦：
        你好 / 你好呀！/ 您好 / hi / Hello / 嗨 / 早上好 / 在吗 / 有人吗 / 谢谢 / 多谢！
    带实际问题的消息一律放行（第一版用「包含」判断，把「你好，帮我讲讲导数」也拦了 —— 已修）：
        你好，帮我讲讲导数 / 你好呀，函数的定义域怎么求 / 在吗？生成一份《导数》教案

可用环境变量 `EDU_GREET_REPLY` 覆盖「你好」类问候的默认回复（留空则用内置文案）。
"""
from __future__ import annotations

import os
import re

DEFAULT_REPLY = "你好呀！有什么想聊或者要帮忙的吗？"
PRESENCE_REPLY = "在的～有什么想问的直接说就行。"
THANKS_REPLY = "不客气～有需要随时喊我。"

# 命中即按问候处理（按顺序匹配，先具体后笼统）
_GREET_TABLE: tuple[tuple[tuple[str, ...], str], ...] = (
    (("在吗", "在么", "有人吗", "你在吗", "在不在"), PRESENCE_REPLY),
    (("谢谢", "多谢", "感谢", "thanks", "thankyou", "3q"), THANKS_REPLY),
    (("你好", "您好", "哈喽", "哈罗", "嗨", "hi", "hello", "hey",
      "早上好", "中午好", "下午好", "晚上好", "早安", "goodmorning"), DEFAULT_REPLY),
)

# 可以跟着问候一起出现、剥掉后不影响判定的称呼
_DECOR = ("老师", "教练", "同学", "您", "你", "ai")

# 归一化时要丢掉的标点/空白
_PUNCT_RE = re.compile(r"[\s，。！？、,.!?~～…:：;；'\"“”‘’()（）\[\]【】]+")
# 句尾语气词：你好**呀** / 在吗**呢** —— 剥掉不算内容
_TAIL_RE = re.compile(r"(呀|啊|哇|哦|噢|啦|了|哈|呢|嘛|哟)+$")
_MAX_LEN = 12          # 归一化后超过这个长度就不再当问候（避免误伤真问题）


def _normalize(text: str) -> str:
    return _PUNCT_RE.sub("", (text or "").lower())


def _strip_affixes(s: str) -> str:
    """把所有问候词/称呼从串首尾反复剥掉，返回剩下的部分。"""
    tokens: list[str] = []
    for keys, _resp in _GREET_TABLE:
        tokens.extend(keys)
    tokens.extend(_DECOR)
    tokens = sorted(set(tokens), key=len, reverse=True)
    changed = True
    while changed and s:
        changed = False
        for t in tokens:
            if s.startswith(t):
                s = s[len(t):]
                changed = True
            elif s.endswith(t):
                s = s[: -len(t)]
                changed = True
    return s


def _override(resp: str) -> str:
    """只让环境变量覆盖「你好」类问候的文案（在吗/谢谢那两句保留内置，语义更贴）。"""
    if resp is DEFAULT_REPLY:
        custom = (os.getenv("EDU_GREET_REPLY") or "").strip()
        if custom:
            return custom
    return resp


def _match(text: str) -> str | None:
    """整条消息只是问候 -> 返回该回的话；否则 None。"""
    n = _normalize(text)
    if not n or len(n) > _MAX_LEN:
        return None
    rest = _strip_affixes(_TAIL_RE.sub("", n))
    rest = _TAIL_RE.sub("", rest)
    if rest:                       # 剥完还有内容 = 里面夹着真问题 -> 放行
        return None
    for keys, resp in _GREET_TABLE:
        for key in keys:
            if key in n:
                return _override(resp)
    return _override(DEFAULT_REPLY)


def is_greeting(text: str) -> bool:
    return _match(text) is not None


def reply(text: str) -> str | None:
    return _match(text)
