# -*- coding: utf-8 -*-
"""edu-math MCP server：课本教练的计算验证工具（本地 stdio，sympy 内核）。

路线 B「能力挂载」的第一块：一个真正独立的 MCP server 进程，
对教育agent 主进程暴露 16 个白名单数学工具：
  代数变形   derivative / simplify / factor / expand / solve_equation
  函数性质   monotonic_intervals / domain / roots / evaluate
  微积分     integral / limit
  数论       gcd_lcm / prime_factors
  三角对数   trig_value / log_value
  通用计算   calculator

输入兼容高中写法：x^2-4x+3（^ 幂、省略乘号）都会被解析。
工具只返回文本；解析失败返回 ERR: ... 字符串，不抛异常（client 侧另有兜底）。

启动（stdio，由 client 拉起，也可手动测）：
  .venv\\Scripts\\python.exe mcp_servers/math_server.py
"""
from __future__ import annotations

import re

import sympy as sp
from mcp.server.fastmcp import FastMCP
from sympy.calculus.util import continuous_domain
from sympy.core.relational import Equality, Relational
from sympy.parsing.sympy_parser import (
    convert_equals_signs,
    convert_xor,
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)

mcp = FastMCP("edu-math")

# 教学友好解析：^ -> **、省略乘号(4x -> 4*x)、= -> Eq
# sympy 1.14 起 = 号转换函数名为 convert_equals_signs（旧 convert_equality_inequalities 已移除）
_TRANS = standard_transformations + (
    convert_xor,
    implicit_multiplication_application,
    convert_equals_signs,
)


def _parse(expr: str, var: str = "x"):
    """解析用户表达式，返回 (expr, Symbol)。失败抛异常由调用方捕获。"""
    v = sp.Symbol(var)
    return parse_expr(expr, transformations=_TRANS, local_dict={var: v}), v


def _safe(fn):
    """把异常转成 'ERR: ...' 字符串，保证工具永远可返回。
    functools.wraps 必须保留：FastMCP 靠 inspect.signature 生成参数 schema，
    没有它包装器会暴露成 (*a, **kw) 两个必填字段。"""
    import functools

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return str(fn(*a, **kw))
        except Exception as e:
            return "ERR: %s" % e
    return wrapper


def _fmt_set(sol) -> str:
    """把 sympy 解集格式化成教学可读串：(-∞, 1) ∪ (3, +∞)、{1, 3}、无解。"""
    if sol is sp.S.EmptySet:
        return "无解"
    if sol is sp.S.Reals:
        return "全体实数"
    if isinstance(sol, sp.Interval):
        l, r = sol.left, sol.right
        ls = "-∞" if l is sp.S.NegativeInfinity else str(l)
        rs = "+∞" if r is sp.S.Infinity else str(r)
        a = "(" if sol.left_open else "["
        b = ")" if sol.right_open else "]"
        return "%s%s, %s%s" % (a, ls, rs, b)
    if isinstance(sol, sp.Union):
        return " ∪ ".join(_fmt_set(a) for a in sol.args)
    if isinstance(sol, sp.FiniteSet):
        return "{" + ", ".join(str(v) for v in sol.args) + "}"
    return str(sol)


@mcp.tool()
@_safe
def derivative(expr: str, var: str = "x") -> str:
    """对函数表达式求导。例：derivative('x^2-4x+3') -> 2*x - 4"""
    f, x = _parse(expr, var)
    return sp.diff(f, x)


@mcp.tool()
@_safe
def monotonic_intervals(expr: str, var: str = "x") -> str:
    """求函数单调区间：解 f'(x)>0（增）与 f'(x)<0（减）。例：monotonic_intervals('x^2-4x+3')"""
    f, x = _parse(expr, var)
    d = sp.diff(f, x)
    inc = sp.solve_univariate_inequality(d > 0, x, relational=False)
    dec = sp.solve_univariate_inequality(d < 0, x, relational=False)
    return "增区间: %s; 减区间: %s" % (_fmt_set(inc), _fmt_set(dec))


@mcp.tool()
@_safe
def solve_equation(expr: str, var: str = "x") -> str:
    """解方程或不等式（支持 = > < >= <=）。例：solve_equation('x^2-4x+3>0') 或 solve_equation('x^2-4x+3=0')"""
    e, x = _parse(expr, var)
    if isinstance(e, Relational):
        if isinstance(e, Equality):
            return "解集: %s" % _fmt_set(sp.solveset(e, x, domain=sp.S.Reals))
        return "解集: %s" % _fmt_set(
            sp.solve_univariate_inequality(e, x, relational=False))
    # 裸表达式按 =0 处理
    return "解集: %s" % _fmt_set(sp.solveset(sp.Eq(e, 0), x, domain=sp.S.Reals))


@mcp.tool()
@_safe
def evaluate(expr: str, var: str = "x", value: str = "0") -> str:
    """代入求值。例：evaluate('x^2-4x+3', 'x', '2') -> -1"""
    f, x = _parse(expr, var)
    return f.subs(x, sp.sympify(value))


@mcp.tool()
@_safe
def simplify(expr: str) -> str:
    """化简表达式。例：simplify('(x+1)^2-(x-1)^2') -> 4*x"""
    e, _ = _parse(expr)
    return sp.simplify(e)


# ---------- 后续新增：代数变形 / 函数性质 / 微积分 / 数论 / 三角对数 ----------

@mcp.tool()
@_safe
def factor(expr: str) -> str:
    """因式分解。例：factor('x^2-4x+3') -> (x - 3)*(x - 1)"""
    e, _ = _parse(expr)
    return sp.factor(e)


@mcp.tool()
@_safe
def expand(expr: str) -> str:
    """展开多项式/乘积。例：expand('(x+1)^3') -> x**3 + 3*x**2 + 3*x + 1"""
    e, _ = _parse(expr)
    return sp.expand(e)


@mcp.tool()
@_safe
def domain(expr: str, var: str = "x") -> str:
    """求函数定义域。例：domain('sqrt(x-1)') -> [1, +∞)；domain('1/x') -> (-∞, 0) ∪ (0, +∞)"""
    f, x = _parse(expr, var)
    return _fmt_set(continuous_domain(f, x, sp.S.Reals))


@mcp.tool()
@_safe
def roots(expr: str, var: str = "x") -> str:
    """求方程 f(x)=0 的根 / 函数零点（实数域）。例：roots('x^2-4x+3') -> 零点: {1, 3}"""
    f, x = _parse(expr, var)
    return "零点: %s" % _fmt_set(sp.solveset(sp.Eq(f, 0), x, domain=sp.S.Reals))


@mcp.tool()
@_safe
def integral(expr: str, var: str = "x", a: str = "", b: str = "") -> str:
    """求积分。不填 a、b 为不定积分；填 a、b 为定积分 ∫_a^b f dx。
    例：integral('2*x') -> x**2；integral('x^2','x','0','1') -> 1/3"""
    f, x = _parse(expr, var)
    if a != "" and b != "":
        return sp.integrate(f, (x, sp.sympify(a), sp.sympify(b)))
    return sp.integrate(f, x)


@mcp.tool()
@_safe
def limit(expr: str, var: str = "x", approach: str = "0", direction: str = "") -> str:
    """求极限。例：limit('sin(x)/x','x','0') -> 1。direction 填 '+'（右）/'-'（左）做单侧极限。"""
    f, x = _parse(expr, var)
    x0 = sp.sympify(approach)
    if direction == "+":
        return sp.limit(f, x, x0, dir="+")
    if direction == "-":
        return sp.limit(f, x, x0, dir="-")
    return sp.limit(f, x, x0)


@mcp.tool()
@_safe
def gcd_lcm(a: str, b: str) -> str:
    """求两整数的最大公约数 gcd 与最小公倍数 lcm。例：gcd_lcm('12','18') -> 最大公约数: 6; 最小公倍数: 36"""
    na = sp.sympify(a)
    nb = sp.sympify(b)
    return "最大公约数: %s; 最小公倍数: %s" % (sp.gcd(na, nb), sp.lcm(na, nb))


@mcp.tool()
@_safe
def prime_factors(n: str) -> str:
    """质因数分解，返回 {质数: 次数}。例：prime_factors('60') -> {2: 2, 3: 1, 5: 1}"""
    return str(sp.factorint(sp.sympify(n)))


_TRIG_FUNCS = frozenset({"sin", "cos", "tan", "cot", "sec", "csc"})


@mcp.tool()
@_safe
def trig_value(func: str, angle: str) -> str:
    """求三角函数值。func: sin/cos/tan/cot/sec/csc；angle 用弧度（可用 pi/6、pi/4、pi/3 等特殊角）。
    例：trig_value('sin','pi/6') -> 1/2"""
    if func not in _TRIG_FUNCS:
        return "ERR: 不支持的函数 %s（可选 %s）" % (func, "/".join(sorted(_TRIG_FUNCS)))
    a = sp.sympify(angle)
    return str(getattr(sp, func)(a))


@mcp.tool()
@_safe
def log_value(base: str, x: str) -> str:
    """求对数 log base (x)。例：log_value('2','8') -> 3；log_value('10','1000') -> 3"""
    return str(sp.log(sp.sympify(x), sp.sympify(base)))


@mcp.tool()
@_safe
def calculator(expr: str) -> str:
    """通用计算器：求数值表达式的结果（四则/幂/根号/三角/对数/π/e/阶乘/绝对值）。
    精确结果优先；结果含无理数（如 sqrt(2)、π）时附 10 位小数近似；含未赋值变量则报错。
    例：calculator('1+2*3') -> 7；calculator('3/4+1/6') -> 11/12；
        calculator('sqrt(2)') -> sqrt(2) ≈ 1.414213562；calculator('sin(pi/6)') -> 1/2"""
    e, _ = _parse(expr)
    r = sp.simplify(e)
    if not r.is_number:
        return "ERR: 请输入数值表达式（当前含未赋值变量: %s）" % ", ".join(
            sorted(str(s) for s in r.free_symbols))
    if r.is_Rational or r.is_Integer:
        return str(r)
    try:
        approx = sp.N(r, 10)
    except Exception:
        approx = None
    if approx is not None and approx != r:
        return "%s ≈ %s" % (r, approx)
    return str(r)


if __name__ == "__main__":
    # stdio transport 是默认；显式写明便于阅读
    mcp.run(transport="stdio")
