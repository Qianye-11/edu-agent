# -*- coding: utf-8 -*-
"""Skill Studio 请求/响应模型（doc 34 §5.1 的 `skill_studio_models.py`）。

为什么要这一层：`studio_api.py` 目前直接用 `dict` 收 body —— 客户端传错字段时
只是"静默按默认值走"，排查成本高（例如 `reviewer` 拼成 `review` 就变成匿名批准）。
这里用 Pydantic 把**入参形状**和**变更类响应信封**都定死：

* 请求模型：字段名/类型/长度/取值范围全部有约束，`extra="forbid"` ——
  多传一个没定义的字段直接报错，而不是被悄悄忽略；
* 响应模型：doc §5.5 规定的信封 `{ok, candidate_id, state, content_hash, updated_at}`
  加 `actor` / `input_text` / `error_code` 可选位（审计用）。

**没有引入任何新依赖**：FastAPI 自带 Pydantic v2。
**不改变现有接口的形状**：`studio_api` 仍然返回 dict；这一层只做"进来先校验"。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------- 公共约束
SKILL_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{1,47}$"
MAX_NOTE = 300
MAX_REASON = 500
MAX_BODY = 200_000
MAX_TEXT = 512 * 1024


class _Strict(BaseModel):
    """所有入参模型的基类：多传字段直接报错（不许静默吞掉拼错的字段名）。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------- 请求模型
class ActorBody(_Strict):
    """只带操作者的请求（提交/安装等）。"""

    actor: str = Field(default="E", max_length=64, description="操作者标识（写审计）")


class ApproveBody(_Strict):
    """审核通过（doc §5.5：批准要绑定内容哈希，所以必须有 reviewer）。"""

    reviewer: str = Field(min_length=1, max_length=64, description="审核人（不能是创建者本人）")
    note: str = Field(default="", max_length=MAX_NOTE)


class RejectBody(_Strict):
    """驳回（理由必填 —— 冷却期与后续候选提示都要用）。"""

    reviewer: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=MAX_REASON)


class GenerateDraftBody(_Strict):
    """候选 → 草稿。"""

    actor: str = Field(default="E", max_length=64)
    confirm: bool = False
    force: bool = False


class PatchDraftBody(_Strict):
    """编辑草稿：整篇 `text` 或分片 `body` + `files`。"""

    actor: str = Field(default="E", max_length=64)
    text: str = Field(default="", max_length=MAX_TEXT)
    body: str | None = Field(default=None, max_length=MAX_BODY)
    files: dict[str, str] = Field(default_factory=dict)
    allow_code: bool = True

    @field_validator("files")
    @classmethod
    def _files_not_huge(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > 32:
            raise ValueError("一次最多写 32 个资源文件")
        for name, content in v.items():
            if len(content) > MAX_BODY:
                raise ValueError("资源 %s 过大（>%d 字节）" % (name, MAX_BODY))
        return v


class InstallBody(_Strict):
    """安装（doc §5.4：同名覆盖必须显式允许）。"""

    actor: str = Field(default="E", max_length=64)
    confirm: bool = False
    allow_overwrite: bool = False


class ImportBody(_Strict):
    """导入手写 SKILL.md：给 `text` 或**导入根内**的 `path`（§5.5 白名单在服务端判）。"""

    text: str = Field(default="", max_length=MAX_TEXT)
    path: str = Field(default="", max_length=1024)
    actor: str = Field(default="E", max_length=64)
    confirm: bool = False
    description: str = Field(default="", max_length=MAX_NOTE)


class TeamImportBody(_Strict):
    """团队资产导入（只允许落到本地草稿）。"""

    skill_id: str = Field(default="", max_length=64)
    version: str = Field(default="", max_length=32)
    content_hash: str = Field(default="", max_length=128)
    files: dict[str, str] = Field(default_factory=dict)


class ModeBody(_Strict):
    """单用户/多教师模式开关。"""

    single_user_mode: bool


class McpUserServiceBody(_Strict):
    """MCP 用户服务接线配置（现阶段保持关闭）。"""

    enabled: bool | None = None
    transport: Literal["http", "mcp"] | None = None
    base_url: str | None = Field(default=None, max_length=512)
    mcp_server_id: str | None = Field(default=None, max_length=64)
    timeout: float | None = Field(default=None, gt=0, le=120)


# ---------------------------------------------------------------- 响应信封（doc §5.5）
class MutationEnvelope(BaseModel):
    """变更类接口的固定返回形状。

        {"ok": true, "candidate_id": "...", "state": "draft",
         "content_hash": "sha256:...", "updated_at": "..."}
    """

    ok: bool
    candidate_id: str = ""
    state: str = ""
    content_hash: str = ""
    updated_at: str = ""
    # 下面几个是审计/排查用的可选位（doc 没禁，且排障非常需要）
    skill: str = ""
    actor: str = ""
    error_code: str = ""
    error: str = ""
    gates: dict[str, Any] = Field(default_factory=dict)


class CandidateCard(BaseModel):
    """候选卡（doc §5.2：只展示脱敏证据摘要，不回显原始对话）。"""

    candidate_id: str
    signature: str = ""
    state: str = "candidate"
    studio_state: str = "detected"
    title: str = ""
    runs: int = 0
    sessions: int = 0
    score: float = 0.0
    criteria: dict[str, bool] = Field(default_factory=dict)
    blocking: list[str] = Field(default_factory=list)
    cooldown_until: float = 0.0
    sensitive_title: bool = False
    evidence: list[dict] = Field(default_factory=list)


class SafeImportPolicy(BaseModel):
    """§5.5 白名单快照。"""

    ok: bool = True
    import_roots: list[str] = Field(default_factory=list)
    allowed_suffixes: list[str] = Field(default_factory=list)
    denied_suffixes: list[str] = Field(default_factory=list)
    skill_id_regex: str = ""
    max_import_bytes: int = 0


def validate_or_error(model: type[BaseModel], body: Any) -> tuple[bool, dict, Any]:
    """把 dict 交给模型校验。

    返回 `(ok, error_dict, model_instance)`：
    * ok=True 时 `model_instance` 可用；
    * ok=False 时 `error_dict` 是给接口直接返回的对象（含逐字段原因），
      形状与其它接口一致（`{ok: False, gate: "request", error: ...}`）。
    """
    try:
        # ⚠️ Pydantic v2 的 BaseModel.__init__ **只接受关键字参数** ——
        # 写成 model(body) 会抛 "takes 1 positional argument but 2 were given"（实测踩过）
        payload = body if isinstance(body, dict) else {}
        return True, {}, model(**payload)
    except Exception as e:  # noqa: BLE001  pydantic.ValidationError 及任何解析失败
        details: list[str] = []
        errs = getattr(e, "errors", None)
        if callable(errs):
            for item in errs():
                loc = ".".join(str(x) for x in (item.get("loc") or ()))
                details.append("%s: %s" % (loc or "body", item.get("msg") or "非法值"))
        return False, {"ok": False, "gate": "request",
                       "error": "请求体不合法：" + ("；".join(details) or str(e))[:400],
                       "details": details}, None
