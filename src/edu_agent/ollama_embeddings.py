"""Ollama 本地 bge-m3 Embedding 封装（requests 直调 /api/embed）。

实测（2026-09-06）：POST {model, input:[...]} 返回
  {"embeddings": [[1024 floats], ...], ...}  —— embeddings 直接是向量列表。
免去远程 embedding 的 key/网络依赖。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from urllib.parse import urlparse

import requests

BGE_M3_DIM = 1024
_START_ATTEMPTED = False


def _start_local_ollama(base_url: str) -> bool:
    """本机 Ollama 未运行时尝试拉起一次，避免 Agent B 因 embedding 服务缺席而整体失败。

    只处理 localhost，远程 Ollama 的连接与权限由部署方负责；同时每个 Python
    进程只尝试一次，避免服务异常时反复派生子进程。成功标准是 /api/tags 可访问。
    """
    global _START_ATTEMPTED
    host = (urlparse(base_url).hostname or "").lower()
    if _START_ATTEMPTED or host not in {"localhost", "127.0.0.1", "::1"}:
        return False
    _START_ATTEMPTED = True
    executable = shutil.which("ollama")
    if not executable:
        return False
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        subprocess.Popen([executable, "serve"], **kwargs)
    except OSError:
        return False
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            if requests.get(f"{base_url}/api/tags", timeout=1).ok:
                return True
        except requests.RequestException:
            time.sleep(0.25)
    return False


class OllamaBgeM3Embeddings:
    """LangChain Embeddings 兼容（embed_query/embed_documents）。

    keep_alive: 每次请求携带的 ollama 驻留时长。默认 -1 = 模型加载后常驻内存
    （解决闲置 5 分钟被卸载、首问冷加载 2-4s 的问题）；ollama 服务重启后失效，
    由下一次请求重新钉住。
    """

    def __init__(self, model: str = "bge-m3", base_url: str = "http://localhost:11434",
                 keep_alive: int | str = -1):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.keep_alive = keep_alive

    def embed_documents(self, texts: list[str], batch_size: int = 8) -> list[list[float]]:
        if not texts:
            return []
        # 分批：一次喂过多长文本 Ollama 会 400
        out: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            request = lambda: requests.post(
                f"{self.base_url}/api/embed",
                json={"model": self.model, "input": list(batch),
                      "keep_alive": self.keep_alive},
                # (连接超时, 读取超时)：连接层 5s 快速失败，
                # 避免 Ollama 未启动/被防火墙黑洞时一次提问挂满 300s 才报错
                timeout=(5, 300),
            )
            try:
                r = request()
            except requests.ConnectionError as error:
                if not _start_local_ollama(self.base_url):
                    raise RuntimeError(
                        "本机 Ollama 未运行，且自动启动失败。请启动 Ollama 后重试。"
                    ) from error
                r = request()
            if r.status_code != 200:
                raise RuntimeError(f"Ollama /api/embed {r.status_code}: {r.text[:300]}")
            embs: list[list[float]] = r.json().get("embeddings") or []
            if len(embs) != len(batch):
                raise RuntimeError(f"Ollama 返回条数异常: {len(embs)} != 输入 {len(batch)}")
            for e in embs:
                if len(e) != BGE_M3_DIM:
                    raise RuntimeError(f"bge-m3 维度异常: {len(e)} != {BGE_M3_DIM}")
            out.extend(embs)
        return out

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]
