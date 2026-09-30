"""知识库统一语义切块。

切块优先落在换行、中文句末和分号处；只有单个句子本身过长时才硬切。
返回块之间保留少量重叠，避免定义、条件或推导刚好落在边界两侧。
"""
from __future__ import annotations

import re


_BOUNDARY_RE = re.compile(r"(?<=[。！？!?；;])")


def _units(text: str, hard_limit: int) -> list[str]:
    """把正文拆成不超过 hard_limit 的语义单元。"""
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    for line in normalized.split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in _BOUNDARY_RE.split(line) if p.strip()]
        for part in parts or [line]:
            while len(part) > hard_limit:
                out.append(part[:hard_limit])
                part = part[hard_limit:]
            if part:
                out.append(part)
    return out


def semantic_chunks(text: str, target_chars: int = 700,
                    overlap_chars: int = 90) -> list[str]:
    """按语义边界生成细粒度块，块间保留尾部语义单元作为重叠。

    target_chars 是目标上限而非 token 数。中文教材中字符数比简单 token 估算更稳定，
    且不引入额外 tokenizer 依赖。返回值去空、顺序稳定、不会产生重复的空块。
    """
    target = max(120, int(target_chars))
    overlap = max(0, min(int(overlap_chars), target // 3))
    units = _units(text, target)
    if not units:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    def emit() -> None:
        nonlocal current, current_len
        body = "\n".join(current).strip()
        if body:
            chunks.append(body)

        tail: list[str] = []
        tail_len = 0
        if overlap:
            for unit in reversed(current):
                extra = len(unit) + (1 if tail else 0)
                if tail and tail_len + extra > overlap:
                    break
                if not tail and len(unit) > overlap:
                    unit = unit[-overlap:]
                    extra = len(unit)
                tail.insert(0, unit)
                tail_len += extra
        current = tail
        current_len = len("\n".join(current))

    for unit in units:
        extra = len(unit) + (1 if current else 0)
        if current and current_len + extra > target:
            emit()
            extra = len(unit) + (1 if current else 0)
            # 重叠尾巴不能挤掉新正文；必要时仅保留新单元。
            if current and current_len + extra > target:
                current = []
                current_len = 0
                extra = len(unit)
        current.append(unit)
        current_len += extra

    tail = "\n".join(current).strip()
    if tail:
        # 很短的纯重叠尾巴不单独成为一个块。
        if len(tail) > overlap or not chunks:
            chunks.append(tail)
    return chunks
