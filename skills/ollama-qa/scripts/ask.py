#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本机 Ollama 问答技能的执行体（课本教练 / skills/ollama-qa）。

调用方（平台的 capabilities.ask_ollama_qa）只会执行：

    python ask.py "<问题>" --json

所以本文件把「怎么调 Ollama」完整封在自己内部：**零依赖**（只用标准库）、
模型自动挑选、流式输出、超时、错误翻译成中文下一步。换任何 3.8+ 解释器都能跑。

本文件也能脱离平台单独运行（排查问题用）：

    python ask.py "什么是导数"          一问一答（流式打印）
    python ask.py --interactive        交互模式（q 退出，/reset 清上下文，/model X 换模型）
    python ask.py --selftest           自检：连通性 + 模型清单 + 一次真实问答
    python ask.py --list-models        看本机装了哪些模型

环境变量（都可选）：OLLAMA_HOST / OLLAMA_QA_MODEL / OLLAMA_QA_SYSTEM / OLLAMA_QA_TIMEOUT
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ------------------------------------------------------------------ 基础配置


def _repo_root() -> Path:
    """本文件在 <repo>/skills/ollama-qa/scripts/ask.py -> 上溯 3 层到仓库根。"""
    try:
        return Path(__file__).resolve().parents[3]
    except IndexError:                                        # pragma: no cover
        return Path.cwd()


def _start_hint() -> str:
    """给「Ollama 没启动」准备一条尽量可执行的提示（工作区里那个启动脚本）。"""
    repo = _repo_root()
    for cand in (repo.parent / ".tools" / "start-ollama.cmd",
                 repo / ".tools" / "start-ollama.cmd",
                 repo / "deploy" / "start_ollama.cmd"):
        if cand.exists():
            return str(cand)
    return "Ollama 启动脚本（本机 .tools\\start-ollama.cmd）"


REPO_ROOT = _repo_root()
START_HINT = _start_hint()

_host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").strip().rstrip("/")
if _host and not _host.startswith("http"):
    _host = "http://" + _host
DEFAULT_HOST = _host or "http://127.0.0.1:11434"

PREFERRED_MODELS = [
    m for m in [
        os.environ.get("OLLAMA_QA_MODEL", "").strip(),
        "qwen2.5:3b",
        "qwen2.5:7b",
        "qwen2.5:1.5b",
        "qwen3:4b",
        "deepseek-r1:1.5b",
        "llama3.1:8b",
    ] if m
]
# 向量/精排模型不能对话，自动挑选时跳过
EMBED_HINTS = ("bge", "embed", "nomic", "gte", "e5-", "rerank", "minilm")

DEFAULT_SYSTEM = os.environ.get(
    "OLLAMA_QA_SYSTEM",
    "你是一个准确、简洁的中文助手。直接回答问题；不确定的内容明确说明，不要编造。",
)
DEFAULT_TIMEOUT = float(os.environ.get("OLLAMA_QA_TIMEOUT", "300"))
HISTORY_MAX_TURNS = 10


def _make_console_safe() -> None:
    """Windows 控制台默认 GBK，直接打印某些字符会 UnicodeEncodeError 崩掉。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:                                     # pragma: no cover
            pass


def _force_utf8_stdout() -> None:
    """机器可读输出（--json）时强制 stdout 为 UTF-8。

    Windows 上**管道**里的 Python stdout 默认是 GBK(cp936)，调用方（如课本教练网关）
    按 UTF-8 解码就会把中文读成乱码。所以 JSON 模式必须显式声明 UTF-8；
    交互模式不强制 —— 那时要保持控制台自己的编码，中文才能正常显示。
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                                         # pragma: no cover
        pass


class OllamaError(RuntimeError):
    """带中文下一步提示的失败。"""


# ------------------------------------------------------------------ HTTP 封装


def _request(path: str, payload: dict | None = None, timeout: float = DEFAULT_TIMEOUT):
    url = DEFAULT_HOST + path
    if payload is None:
        req = urllib.request.Request(url, method="GET")
    else:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:                                     # pragma: no cover
            pass
        raise OllamaError("Ollama 返回 HTTP %s：%s" % (exc.code, detail)) from exc
    except urllib.error.URLError as exc:
        raise OllamaError(
            "连不上 Ollama（%s）：%s\n  解决：先启动 Ollama —— %s"
            % (DEFAULT_HOST, exc.reason, START_HINT)
        ) from exc
    except TimeoutError as exc:
        raise OllamaError("连接 Ollama 超时（%s）；可用 --timeout 调大超时。" % DEFAULT_HOST) from exc


def list_models() -> list[str]:
    with _request("/api/tags", None, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    names = []
    for item in data.get("models", []) or []:
        name = item.get("name") or item.get("model") or ""
        if name:
            names.append(name)
    return names


def pick_model(requested: str | None = None, verbose: bool = False) -> str:
    """显式指定优先；否则按候选顺序挑；最后退到「任意非向量模型」。"""
    available = list_models()
    if requested:
        if requested in available:
            return requested
        for name in available:
            if name.split(":")[0] == requested.split(":")[0]:
                return name
        raise OllamaError(
            "模型 %r 不在本机（现有：%s）\n  解决：用 --model 指定已有模型，或先 ollama pull %s"
            % (requested, ", ".join(available) or "无", requested)
        )
    for candidate in PREFERRED_MODELS:
        for name in available:
            if name == candidate or name.split(":")[0] == candidate.split(":")[0]:
                if verbose:
                    print("[ollama-qa] 自动选用模型：%s" % name, file=sys.stderr)
                return name
    chat_models = [n for n in available if not any(h in n.lower() for h in EMBED_HINTS)]
    if chat_models:
        if verbose:
            print("[ollama-qa] 候选都没命中，改用：%s" % chat_models[0], file=sys.stderr)
        return chat_models[0]
    raise OllamaError(
        "本机没有可用于对话的模型（现有：%s）\n  解决：ollama pull qwen2.5:3b"
        % (", ".join(available) or "无")
    )


# ------------------------------------------------------------------ 对话


def chat_stream(model: str, messages: list[dict], temperature: float = 0.6,
                timeout: float = DEFAULT_TIMEOUT):
    payload = {"model": model, "messages": messages, "stream": True,
               "options": {"temperature": temperature}}
    with _request("/api/chat", payload, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("error"):
                raise OllamaError("Ollama 报错：%s" % obj["error"])
            piece = (obj.get("message") or {}).get("content") or ""
            if piece:
                yield piece
            if obj.get("done"):
                break


def chat_once(model: str, question: str, system: str = DEFAULT_SYSTEM,
              history: list[dict] | None = None, temperature: float = 0.6,
              timeout: float = DEFAULT_TIMEOUT, stream_out: bool = True) -> str:
    messages = [{"role": "system", "content": system}] if system else []
    messages += list(history or [])
    messages.append({"role": "user", "content": question})

    buf: list[str] = []
    t0 = time.time()
    try:
        for piece in chat_stream(model, messages, temperature, timeout):
            buf.append(piece)
            if stream_out:
                sys.stdout.write(piece)
                sys.stdout.flush()
    except OllamaError:
        raise
    except TimeoutError as exc:
        raise OllamaError(
            "推理超时（已等 %.0f 秒）。纯 CPU 上大模型很慢，可调大 --timeout 或换更小的模型。"
            % (time.time() - t0)
        ) from exc
    if stream_out:
        sys.stdout.write("\n")
    return "".join(buf).strip()


# ------------------------------------------------------------------ 历史


def load_history(path: str | None) -> list[dict]:
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except Exception:                                         # noqa: BLE001
        return []


def save_history(path: str | None, messages: list[dict]) -> None:
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(messages[-(HISTORY_MAX_TURNS * 2):], fh, ensure_ascii=False, indent=2)
    except Exception as exc:                                  # noqa: BLE001
        print("[ollama-qa] 历史写入失败：%s" % exc, file=sys.stderr)


# ------------------------------------------------------------------ 模式


def mode_list_models() -> int:
    print("本机 Ollama 模型（%s）：" % DEFAULT_HOST)
    for name in list_models():
        mark = "  <- 向量模型，不能对话" if any(h in name.lower() for h in EMBED_HINTS) else ""
        print("  - %s%s" % (name, mark))
    try:
        print("自动选用：%s" % pick_model(None))
    except OllamaError as exc:
        print("自动选用失败：%s" % exc)
    return 0


def mode_interactive(model: str | None, system: str, temperature: float,
                     timeout: float, history_path: str | None) -> int:
    resolved = pick_model(model, verbose=True)
    history = load_history(history_path)
    print("=" * 62)
    print("本机 Ollama 问答 · 模型 %s · %s" % (resolved, DEFAULT_HOST))
    print("直接输入问题回车；/reset 清空上下文，/model <名字> 换模型，/models 看模型，q 退出")
    print("=" * 62)
    while True:
        try:
            question = input("\n你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question:
            continue
        if question.lower() in ("q", "quit", "exit", "/exit"):
            break
        if question == "/reset":
            history = []
            save_history(history_path, history)
            print("[ollama-qa] 上下文已清空")
            continue
        if question == "/models":
            mode_list_models()
            continue
        if question.startswith("/model "):
            try:
                resolved = pick_model(question.split(None, 1)[1].strip(), verbose=True)
                print("[ollama-qa] 已切换到 %s" % resolved)
            except OllamaError as exc:
                print("[ollama-qa] %s" % exc)
            continue
        print("AI  > ", end="", flush=True)
        try:
            answer = chat_once(resolved, question, system, history, temperature,
                               timeout, stream_out=True)
        except OllamaError as exc:
            print("[ollama-qa] %s" % exc)
            continue
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer})
        save_history(history_path, history)
    save_history(history_path, history)
    return 0


def mode_selftest(model: str | None) -> int:
    print("=== ollama-qa 自检 ===")
    print("1) 解释器     : %s" % sys.version.split()[0])
    print("   仓库根     : %s  %s" % (REPO_ROOT, "OK" if REPO_ROOT.is_dir() else "!! 路径不对"))
    print("2) Ollama 地址: %s" % DEFAULT_HOST)
    try:
        with _request("/api/version", None, timeout=10) as resp:
            ver = json.loads(resp.read().decode("utf-8", "replace")).get("version", "?")
        print("3) Ollama 版本: %s  [OK] 已启动" % ver)
    except OllamaError as exc:
        print("3) 连接失败 [!!]\n   %s" % exc)
        return 1
    try:
        names = list_models()
        print("4) 已装模型   : %s" % (", ".join(names) or "无"))
        chosen = pick_model(model, verbose=True)
        print("5) 选用模型   : %s  [OK]" % chosen)
    except OllamaError as exc:
        print("4) 模型检查失败 [!!]\n   %s" % exc)
        return 1
    print("6) 真实问答   : 发送「1+1=? 只回答数字」…")
    try:
        answer = chat_once(chosen, "1+1=? 只回答数字", DEFAULT_SYSTEM, None, 0.0,
                           min(DEFAULT_TIMEOUT, 180), stream_out=False)
    except OllamaError as exc:
        print("   失败 [!!] %s" % exc)
        return 1
    if not answer:
        print("   返回空内容 [!!]")
        return 1
    print("   回答：%s" % answer[:120].replace("\n", " "))
    print("=== 自检通过 [OK] ===")
    return 0


# ------------------------------------------------------------------ 入口


def main(argv: list[str] | None = None) -> int:
    _make_console_safe()
    parser = argparse.ArgumentParser(
        prog="ask.py",
        description="把问题交给本机 Ollama 回答（零依赖，内部封装 Ollama 调用）",
    )
    parser.add_argument("question", nargs="*", help="要问的问题；不给则从标准输入读")
    parser.add_argument("--model", help="指定模型名（默认自动挑）")
    parser.add_argument("--system", default=DEFAULT_SYSTEM, help="系统提示词")
    parser.add_argument("--temperature", type=float, default=0.6)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--history", help="多轮上下文文件（JSON）")
    parser.add_argument("--interactive", "-i", action="store_true", help="交互模式")
    parser.add_argument("--list-models", action="store_true", help="列出本机模型")
    parser.add_argument("--selftest", action="store_true", help="自检")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = parser.parse_args(argv)

    if args.json:
        _force_utf8_stdout()          # 机器可读：必须 UTF-8（见函数注释）

    if args.list_models:
        return mode_list_models()
    if args.selftest:
        return mode_selftest(args.model)
    if args.interactive:
        return mode_interactive(args.model, args.system, args.temperature,
                                args.timeout, args.history)

    question = " ".join(args.question).strip()
    if not question:
        if sys.stdin.isatty():
            parser.print_help()
            return 2
        question = sys.stdin.read().strip()
    if not question:
        print("没有收到问题。用法：python ask.py \"你的问题\"", file=sys.stderr)
        if args.json:
            print(json.dumps({"ok": False, "error": "问题为空"}, ensure_ascii=False))
        return 2

    try:
        model = pick_model(args.model)
        stream_out = not args.json
        if stream_out:
            print("[模型 %s]" % model)
        history = load_history(args.history)
        answer = chat_once(model, question, args.system, history,
                           args.temperature, args.timeout, stream_out=stream_out)
    except OllamaError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print("[ollama-qa] %s" % exc, file=sys.stderr)
        return 1

    if args.history:
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer})
        save_history(args.history, history)

    if args.json:
        print(json.dumps({"ok": True, "model": model, "question": question, "answer": answer},
                         ensure_ascii=False))
    elif not answer:
        print("[ollama-qa] 模型返回了空内容，可重试或换模型。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
