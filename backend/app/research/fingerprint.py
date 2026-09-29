"""Behaviour fingerprinting shared by the frozen-definition phases.

Phase 41 introduced this: a definition is frozen by hashing the *behaviour* of
the functions that decide an answer, not the constants they read and not the
text of the file. Hashing constants alone misses a change inside a function
body — the mistake it was written to catch — and hashing file text moves on a
reflowed comment and cries wolf until nobody reads it.

It lives here rather than in one phase because a second phase now freezes a
definition of its own, and two copies of a canonical serialisation are two
serialisations: they agree until one is fixed, and then two frozen definitions
disagree about identical code for a reason that has nothing to do with either.

The tree is serialised here rather than by :func:`ast.dump` or
:func:`ast.unparse`, both of whose output drifts between interpreter versions
— 3.13 adds fields 3.10 has never heard of and parenthesises a tuple target
differently — which would make the same code hash differently on two boxes and
turn every cross-machine comparison into a false alarm.
"""
from __future__ import annotations

import ast
import hashlib
import inspect
from collections.abc import Callable

# Syntax-tree fields that exist on some interpreters and not others. They carry
# no behaviour for this code — generic type parameters and `# type:` comments —
# so including them would only make the hash depend on the interpreter.
VERSION_SPECIFIC: frozenset[str] = frozenset({"type_params", "type_ignores"})


def behaviour(fn: Callable) -> str:
    """The function's syntax tree, docstring removed, as a stable string.

    Parsing rather than reading means a comment, a blank line or a reflowed
    line cannot move the fingerprint. Dropping the docstring means the prose
    explaining a decision can be improved without invalidating the sessions
    measured under it — which matters, because prose is what gets corrected
    after a reader misunderstands a number. The function's own name is
    normalised away for the same reason: which callables are covered is
    recorded beside the hash, and a rename is a rename, not a new measurement.
    """
    tree = ast.parse(inspect.getsource(fn).lstrip())
    top = tree.body[0]
    if isinstance(top, ast.FunctionDef | ast.AsyncFunctionDef):
        top.name = "_"
    for node in ast.walk(tree):
        if not isinstance(
            node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
            | ast.Module,
        ):
            continue
        body = node.body
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    return canonical(tree)


def canonical(node: object) -> str:
    """A syntax tree as text, spelled the same way on every interpreter."""
    if isinstance(node, ast.AST):
        parts = [type(node).__name__]
        parts += [
            f"{field}={canonical(getattr(node, field, None))}"
            for field in node._fields if field not in VERSION_SPECIFIC
        ]
        return "(" + ",".join(parts) + ")"
    if isinstance(node, list):
        return "[" + ",".join(canonical(item) for item in node) + "]"
    return repr(node)


def digest(parts: list[str]) -> str:
    """A short stable hash over an ordered list of strings."""
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode())
        h.update(b"\x00")
    return h.hexdigest()[:16]


def components(groups: dict[str, tuple[Callable, ...]]) -> dict:
    """Per-group behaviour hashes plus the callables each group covers.

    Each group is hashed separately as well as together, so a changed
    fingerprint says *which* part of the definition moved rather than only that
    something did.
    """
    parts: dict[str, str] = {}
    members: dict[str, list[str]] = {}
    for name, fns in groups.items():
        members[name] = sorted(f"{fn.__module__}.{fn.__qualname__}"
                               for fn in fns)
        parts[name] = digest([
            behaviour(fn)
            for fn in sorted(fns, key=lambda f: (f.__module__, f.__qualname__))
        ])
    return {"components": parts, "covers": members}
