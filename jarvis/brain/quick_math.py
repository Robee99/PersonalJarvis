"""Plain arithmetic answered exactly, without a model.

Live 2026-10-25 Gemma 4 E4B answered "What is 17 percent of 2340?" with
"three hundred ninety-five point eight"; the answer is 397.8. A 4B model does
arithmetic by guessing digits, so a question that is only numbers and
operators is worked out here and the reply states the result.

Only whole questions match: "what is 12 times 3", "17% of 2340", "square
root of 81", "what's 2.5 plus 4 divided by 2". Anything with other words
("what is 3 times the price of milk") goes to the brain.
"""

from __future__ import annotations

import ast
import math
import operator
import re

_ASK = re.compile(
    r"^\s*(?:(?:hey\s+)?jarvis[,\s]+)?(?:please\s+)?"
    r"(?:what(?:'s|\s+is)|calculate|compute|work\s+out|how\s+much\s+is)?\s*"
    r"(?P<expr>.+?)\s*[?.!]*\s*$",
    re.IGNORECASE,
)
#: Spoken operators, longest first, to Python's.
_WORDS: tuple[tuple[str, str], ...] = (
    (r"\bmultiplied\s+by\b", "*"),
    (r"\bdivided\s+by\b", "/"),
    (r"\bto\s+the\s+power\s+of\b", "**"),
    (r"\btimes\b", "*"),
    (r"\bplus\b", "+"),
    (r"\bminus\b", "-"),
    (r"\bover\b", "/"),
    (r"\bx\b", "*"),
    (r"×", "*"),
    (r"÷", "/"),
)
_PERCENT_OF = re.compile(
    r"^(?P<p>\d+(?:\.\d+)?)\s*(?:%|percent|per\s+cent)\s+of\s+(?P<n>\d+(?:\.\d+)?)$",
    re.IGNORECASE,
)
_ROOT = re.compile(r"^(?:the\s+)?square\s+root\s+of\s+(?P<n>\d+(?:\.\d+)?)$", re.IGNORECASE)
#: What may remain once the words are operators: numbers, operators, brackets.
_ONLY_MATH = re.compile(r"^[\d\s.+\-*/()]+$")
_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}
#: Exponents above this are not a spoken question; refuse them, never hang.
_MAX_EXPONENT = 100


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_EXPONENT:
            raise ValueError("exponent too large")
        return _OPS[type(node.op)](left, right)
    raise ValueError("not arithmetic")


def _number(value: float) -> str:
    if value == int(value) and abs(value) < 1e15:
        return f"{int(value):,}"
    return f"{value:,.6f}".rstrip("0").rstrip(".")


def solve(text: str) -> str | None:
    """The reply for a plain arithmetic question, else ``None``."""
    m = _ASK.match(text or "")
    if not m:
        return None
    expr = m.group("expr").strip().replace(",", "")
    percent = _PERCENT_OF.match(expr)
    if percent:
        p, n = float(percent.group("p")), float(percent.group("n"))
        return f"{_number(p)} percent of {_number(n)} is {_number(p * n / 100)}."
    root = _ROOT.match(expr)
    if root:
        n = float(root.group("n"))
        return f"The square root of {_number(n)} is {_number(math.sqrt(n))}."
    spoken = expr.lower()
    for pattern, symbol in _WORDS:
        spoken = re.sub(pattern, f" {symbol} ", spoken)
    if not _ONLY_MATH.match(spoken) or not re.search(r"\d\s*[-+*/]", spoken):
        return None
    try:
        value = _eval(ast.parse(spoken, mode="eval"))
    except ZeroDivisionError:  # the honest answer to x / 0 is said, not logged
        return "That divides by zero, so it has no answer."
    except (SyntaxError, ValueError, OverflowError):  # not plain arithmetic: the brain answers
        return None
    said = " ".join(spoken.split())
    for symbol, word in (
        ("**", "to the power of"),
        ("*", "times"),
        ("/", "divided by"),
        ("+", "plus"),
        ("-", "minus"),
    ):
        said = said.replace(f" {symbol} ", f" {word} ")
    # Read aloud as well as on screen, so the reply uses words, not symbols.
    return f"{said} is {_number(value)}."
