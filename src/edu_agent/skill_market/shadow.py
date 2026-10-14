# -*- coding: utf-8 -*-
"""skill-create · M7 影子运行（L3）：发布前，用**真代码**跑一次，但不产生任何副作用。

L2 只做声明式回放（核对痕迹里的调用），拿不到"这份代码真的跑起来会怎样"。
L3 补上这一段：把草稿里人工写的 `plugin.py` 放进**子进程**跑一次，
并在子进程里把所有出站调用换成**记录桩**：

* 出站被拦：`contract.ModuleClient` 的传输被替换，调用只被记下来，**不发网络、不写别的模块**；
* 失控被拦：子进程 + 超时（默认 60 秒），卡死/崩溃都只影响这一次检验；
* 真实证据：拿到**实际调用集合**（比 L2 从痕迹推的更准）与**实际返回字段**。

⚠️ 说清楚边界：**L3 不是安全沙箱**（Python 里做不到真沙箱）。
它拦的是"出站调用"和"失控/超时"；文件写入、执行外部程序这类副作用，
由 **L1 静态扫描在更早一步**拦下（未声明就审核不通过）。所以三级检验是配合关系，不是替代关系。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from edu_agent.skill_market import audit, capability_registry as caps, manifest as mf
from edu_agent.skill_market import miner, paths, runs, store

SHADOW_DIR = "_shadow"

# 子进程里跑的引导脚本：注入记录桩 → 导入插件 → 调第一个 @kernel_function → 打印 JSON
_RUNNER = r'''
# -*- coding: utf-8 -*-
"""影子运行引导脚本（由 shadow.py 生成，不要手改）。"""
import inspect
import json
import sys
from pathlib import Path

# 关键：**不要写 .pyc**。否则 import 插件会在技能目录里留下 __pycache__，
# 文件集合一变，教师批准绑定的内容哈希就失效（实测踩过这个连环坑）。
sys.dont_write_bytecode = True

plugin_path, fixture_path, src_path = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, src_path)

calls = []


def _stub(url, payload, timeout):
    """记录桩：只记下"想调什么"，不发网络、不碰别的模块。"""
    calls.append({"url": str(url), "keys": sorted(str(k) for k in (payload or {}))})
    return {"ok": True, "shadow": True, "stub": True}


def main():
    from edu_agent.skill_market import contract

    contract._default_transport = _stub
    contract._default_get_transport = _stub
    _orig_init = contract.ModuleClient.__init__

    def _init(self, bases=None, *, transport=None, get_transport=None, timeout=20.0):
        # 读写两条出站路径都换成记录桩（GET 也要拦，否则只读接口会真发出去）
        _orig_init(self, bases, transport=_stub, get_transport=_stub, timeout=timeout)

    contract.ModuleClient.__init__ = _init

    import importlib.util
    spec = importlib.util.spec_from_file_location("shadow_plugin", plugin_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["shadow_plugin"] = mod
    spec.loader.exec_module(mod)

    fx = json.loads(Path(fixture_path).read_text(encoding="utf-8")) if Path(fixture_path).exists() else {}
    inp = dict(fx.get("input") or {})
    ctx = dict(inp.get("teaching_context") or {})

    fn = owner = None
    for name in dir(mod):
        obj = getattr(mod, name)
        if not isinstance(obj, type) or obj.__module__ != mod.__name__:
            continue
        try:
            inst = obj()
        except Exception:
            continue
        for m in dir(inst):
            f = getattr(inst, m)
            if getattr(f, "__kernel_function__", False):
                fn, owner = f, inst
                break
        if fn is not None:
            break
    if fn is None:
        print(json.dumps({"ok": False, "error": "插件里没有 @kernel_function 函数"}, ensure_ascii=False))
        return

    kwargs = {}
    for pname in inspect.signature(fn).parameters:
        if pname == "self":
            continue
        if pname in inp:
            kwargs[pname] = inp[pname]
        elif pname == "knowledge_id":
            kwargs[pname] = inp.get("knowledge_id")
        elif pname in ("stage", "subject", "chapter"):
            kwargs[pname] = ctx.get(pname, "")
    value = fn(**kwargs)
    print(json.dumps({"ok": True, "value": value, "calls": calls,
                      "kwargs": sorted(kwargs),
                      "plugin_class": type(owner).__name__}, ensure_ascii=False, default=str))


try:
    main()
except Exception as e:  # noqa: BLE001
    print(json.dumps({"ok": False, "error": "%s: %s" % (type(e).__name__, e),
                      "calls": calls}, ensure_ascii=False))
'''


def _repo_src() -> str:
    return str(Path(__file__).resolve().parents[2])


def _declared_tokens(man: mf.SkillManifest) -> set[str]:
    return set(man.allowed_capabilities)


def _pair_digest(*paths) -> str:
    """(插件 + fixture) 的内容摘要 —— L3 缓存键：**代码或用例一变就失效**。"""
    import hashlib

    h = hashlib.sha256()
    for p in paths:
        try:
            h.update(Path(p).read_bytes())
        except OSError:
            h.update(b"<missing>")
    return h.hexdigest()[:16]


def _cache_get(skill_id: str, key: str) -> dict | None:
    st = store.get_state(skill_id)
    hit = (st.get("l3_cache") or {}).get(key)
    return hit if isinstance(hit, dict) and hit.get("ok") else None


def _cache_put(skill_id: str, key: str, ok: bool, ms: int, extra: dict | None = None) -> None:
    """只缓存**成功**结果：失败必须重跑（可能是超时/环境抖动，不能一票否决到底）。"""
    if not ok:
        return
    try:
        st = store.get_state(skill_id)
        cache = dict(st.get("l3_cache") or {})
        cache[key] = {"ok": True, "ms": int(ms or 0), "at": time.time(),
                      **(extra or {})}
        if len(cache) > 4:                      # 只留最近 4 条
            cache = dict(sorted(cache.items(), key=lambda kv: kv[1].get("at", 0))[-4:])
        store.write_state(skill_id, l3_cache=cache)
    except Exception:  # noqa: BLE001
        pass


def _static_check(man: mf.SkillManifest, plugin: Path, fixture_path: Path,
                  cid: str, skill_id: str) -> dict:
    """静态核对（不执行代码）：AST 读插件返回键，比对 fixture 期望字段。

    这是 `SKILL_STUDIO_EXECUTE_PLUGIN=0` 时的 L3 形态 —— 比真跑弱，但比不查强：
    它能抓出"插件根本不返回期望字段"这类问题，只是抓不到运行期行为。
    """
    import json as _json

    keys = _plugin_return_keys_static(plugin)
    expected: list[str] = []
    try:
        fx = _json.loads(Path(fixture_path).read_text(encoding="utf-8"))
        expected = list(((fx.get("expected_output_shape") or {}).get("fields")) or [])
    except (OSError, ValueError):
        pass
    matched = sorted(set(expected) & set(keys))
    shape_match = (len(matched) / len(expected)) if expected else 0.0
    ok = bool(keys) and shape_match >= 1.0
    l3 = {"executed": False, "mode": "static", "ok": ok,
          "plugin_return_keys": sorted(keys), "expected_fields": expected,
          "matched": matched, "shape_match": round(shape_match, 4),
          "note": "SKILL_STUDIO_EXECUTE_PLUGIN=0：按 doc 34 §1.3 不执行脚本，只做静态核对"}
    ver = dict((store.get_state(skill_id).get("verification") or {}))
    ver.update({"l3": ok, "l3_at": time.time(), "l3_mode": "static"})
    store.write_state(skill_id, verification=ver)
    _audit(cid or skill_id, man.name, "static",
           {"ok": ok, "code": "shadow-static", "shape_match": round(shape_match, 4)})
    return {"ok": ok, "candidate_id": cid, "skill": man.name, "L3": l3,
            "message": "静态核对通过（未执行脚本）" if ok else "静态核对未通过：插件返回键与期望字段不一致"}


def _plugin_return_keys_static(plugin: Path) -> set[str]:
    """AST 读插件里所有 `return {...}` 的字符串键（不 import、不执行）。"""
    import ast

    try:
        tree = ast.parse(Path(plugin).read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, OSError):
        return set()
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            for k in node.value.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
    return keys


def shadow_run(target: str, *, actor: str = "E", timeout: int = 60,
               use_cache: bool = True) -> dict:
    """对一个草稿做影子运行（L3）。

    `target` 可以是**候选 id**（miner 挖出来的）或**技能 id**（人工导入的草稿）。
    没有 plugin.py 的纯提示词技能会 skip。

    `use_cache=True` 时，**同一份插件 + 同一份 fixture** 已通过过就不再重跑
    （提交审核时会调它，重复跑子进程太贵）；代码或用例一变，摘要就变，缓存自动失效。
    """
    from edu_agent.skill_market import verifier as _verifier

    tgt = _verifier.resolve_target(target)
    if not tgt:
        return {"ok": False, "error": "既不是候选也不是草稿：%s" % target}
    cid = tgt.get("candidate_id") or ""
    skill_id = tgt["skill_id"]
    d = Path(tgt["dir"])
    if not (d / "SKILL.md").exists():
        return {"ok": False, "gate": "draft", "candidate_id": cid, "skill_id": skill_id,
                "error": "还没有草稿：先调 /api/skill-create/draft 或 import"}
    man, _body = mf.from_document((d / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
    plugin = d / (man.plugin or "plugin.py")
    if not plugin.exists():
        return {"ok": False, "skipped": True, "candidate_id": cid, "skill": man.name,
                "reason": "该技能没有 plugin.py（纯提示词技能）：L3 不适用，按 L1+L2 判定即可",
                "L3": {"executed": False, "skipped": True}}
    fixture_probe = Path(paths.fixtures_dir()) / ("%s.json" % man.name)
    if not fixture_probe.exists():
        fixture_probe = d / (man.fixture or "fixture.json")
    cache_key = _pair_digest(plugin, fixture_probe)

    # doc 34 §1.3「第一版不自动执行 Skill 内脚本」：SKILL_STUDIO_EXECUTE_PLUGIN=0 时
    # 改成**静态核对**（AST 读返回键 vs fixture 期望字段 + 声明能力核对），不跑子进程。
    # 默认是 1（真跑，验证更强）；这是配置开关，不是"按模式跳过校验"。
    try:
        from edu_agent.skill_market import skill_studio

        execute = bool(skill_studio.config().get("execute_plugin", True))
    except Exception:  # noqa: BLE001
        execute = True
    if not execute:
        return _static_check(man, plugin, fixture_probe, cid, skill_id)

    if use_cache:
        hit = _cache_get(man.name, cache_key)
        if hit:
            return {"ok": True, "cached": True, "candidate_id": cid, "skill": man.name,
                    "L3": {"executed": True, "ok": True, "cached": True,
                           "ms": hit.get("ms"), "at": hit.get("at"),
                           "note": "同一份插件 + fixture 已通过过 L3，直接复用结论"},
                    "message": "影子运行通过（缓存命中：代码与用例都没变）"}

    work = Path(paths.market_root()) / SHADOW_DIR
    work.mkdir(parents=True, exist_ok=True)
    runner = work / "run_plugin.py"
    runner.write_text(_RUNNER, encoding="utf-8")
    fixture = Path(paths.fixtures_dir()) / ("%s.json" % man.name)
    if not fixture.exists():
        fixture = d / (man.fixture or "fixture.json")

    t0 = time.time()
    try:
        proc = subprocess.run(
            # 全部用**绝对路径**：cwd 会被设成草稿目录，相对路径会在子进程里解析错
            # （实测踩过：can't open file '...\drafts\<id>\.tmp-debug\...\run_plugin.py'）
            [sys.executable, str(runner.resolve()), str(plugin.resolve()),
             str(fixture.resolve()) if Path(fixture).exists() else "", _repo_src()],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, cwd=str(d.resolve()),
            env={"PYTHONIOENCODING": "utf-8", "EDU_SKILL_SHADOW": "1",
                 # 双保险：即使插件自己 import 别的模块，也不在技能目录里落 .pyc
                 "PYTHONDONTWRITEBYTECODE": "1",
                 "PATH": __import__("os").environ.get("PATH", ""),
                 "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")},
        )
    except subprocess.TimeoutExpired:
        _audit(cid, man.name, actor, {"ok": False, "code": "timeout", "timeout": timeout})
        return {"ok": False, "candidate_id": cid, "skill": man.name,
                "error": "影子运行超时（%d 秒）：插件可能在等外部输入或死循环" % timeout,
                "L3": {"executed": True, "ok": False, "timeout": True}}
    ms = int((time.time() - t0) * 1000)

    payload: dict = {}
    for line in reversed((proc.stdout or "").splitlines()):
        if line.strip().startswith("{"):
            try:
                payload = json.loads(line)
                break
            except ValueError:
                continue
    if not payload:
        _audit(cid, man.name, actor, {"ok": False, "code": "no-output", "rc": proc.returncode})
        return {"ok": False, "candidate_id": cid, "skill": man.name,
                "error": "影子运行没有输出可解析结果（退出码 %s）" % proc.returncode,
                "stderr": (proc.stderr or "")[-500:],
                "L3": {"executed": True, "ok": False, "ms": ms}}

    if not payload.get("ok"):
        _audit(cid, man.name, actor, {"ok": False, "code": "plugin-error",
                                      "error": str(payload.get("error"))[:120]})
        return {"ok": False, "candidate_id": cid, "skill": man.name,
                "error": "插件执行失败：%s" % payload.get("error"),
                "L3": {"executed": True, "ok": False, "ms": ms,
                       "calls": payload.get("calls") or []}}

    # ---- 判定 ----
    value = payload.get("value")
    actual_fields = sorted(value.keys()) if isinstance(value, dict) else []
    fixture_data = {}
    try:
        fixture_data = json.loads(Path(fixture).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    expected = list(((fixture_data.get("expected_output_shape") or {}).get("fields")) or [])
    matched = sorted(set(expected) & set(actual_fields))
    shape_match = (len(matched) / len(expected)) if expected else 0.0

    declared = _declared_tokens(man)
    attempted: list[str] = []
    for c in payload.get("calls") or []:
        attempted.append(str(c.get("url") or ""))
    # 出站目标必须落在**声明能力允许的范围**内（由 capability_registry 的令牌→目标映射决定，
    # 不再硬编码 module2/module4 —— 那样会把平台只读接口误判成未声明目标）。
    allowed_targets = caps.targets_for(declared)
    undeclared_hosts: list[str] = []
    for url in attempted:
        host = caps.classify_url(url)
        if host == "external" or (host not in allowed_targets):
            if host not in undeclared_hosts:
                undeclared_hosts.append(host)

    shape_min = float(runs.config().get("consistency_min") or 1.0)
    ok = (shape_match >= shape_min) and not undeclared_hosts
    l3 = {"executed": True, "ok": ok, "ms": ms, "exit_code": proc.returncode,
          "actual_fields": actual_fields, "expected_fields": expected,
          "matched": matched, "shape_match": round(shape_match, 4),
          "outbound_attempts": attempted, "allowed_targets": sorted(allowed_targets),
          "undeclared_targets": undeclared_hosts,
          "kwargs_used": payload.get("kwargs") or [],
          "plugin_class": payload.get("plugin_class") or "",
          "note": "出站调用已被记录桩拦截：未发网络、未写其它模块"}

    ver = dict((tgt.get("candidate") or {}).get("verification") or {}) if cid else \
        dict((store.get_state(skill_id).get("verification") or {}))
    ver.update({"l3": ok, "l3_at": time.time()})
    if cid:
        miner.set_state(cid, (tgt.get("candidate") or {}).get("state") or "drafted",
                        verification=ver)
    else:
        store.write_state(skill_id, verification=ver)
    _cache_put(man.name, cache_key, ok, ms,
               extra={"shape_match": round(shape_match, 4)})
    _audit(cid or skill_id, man.name, actor, {"ok": ok, "code": "shadow",
                                              "shape_match": round(shape_match, 4),
                                              "undeclared": undeclared_hosts})
    return {"ok": ok, "candidate_id": cid, "skill": man.name, "L3": l3,
            "message": "影子运行通过：真代码跑通、返回形状一致、出站调用都被拦下" if ok else
                       "影子运行未通过：见 L3 明细"}


def _audit(cid: str, skill: str, actor: str, result: dict) -> None:
    audit.append("skill-create.shadow" if result.get("ok") else "skill-create.shadow.failed",
                 skill=skill, actor=actor, input_text=cid, result=result,
                 gates={"L3": "pass" if result.get("ok") else "fail"})
