# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow 输入表达式解析：${context.x}、${run.id}、${steps.name.result.y}。"""

from __future__ import annotations

import ast
import re
from typing import Any

_EXPR_FULL = re.compile(r"^\$\{([^}]+)\}$")
_EXPR_PARTIAL = re.compile(r"\$\{([^}]+)\}")
# 兼容 WORKFLOW 通知模板中误写为 {context.x} 的形式
_EXPR_BRACE = re.compile(r"\{([a-zA-Z_][\w.]*)\}")


def _lookup(path: str, env: dict[str, Any]) -> Any:
    parts = path.strip().split(".")
    if not parts:
        return None
    cur: Any = env
    for part in parts:
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (TypeError, ValueError, IndexError):
                return None
        else:
            return None
    return cur


def resolve_value(expr: Any, env: dict[str, Any]) -> Any:
    if not isinstance(expr, str):
        return expr
    text = expr.strip()
    full = _EXPR_FULL.match(text)
    if full:
        return _lookup(full.group(1), env)

    def repl(match: re.Match) -> str:
        val = _lookup(match.group(1), env)
        return "" if val is None else str(val)

    if "${" in text:
        text = _EXPR_PARTIAL.sub(repl, text)
    if _EXPR_BRACE.search(text):
        text = _EXPR_BRACE.sub(repl, text)
    return text


def resolve_inputs(mapping: dict[str, Any], env: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, expr in (mapping or {}).items():
        val = resolve_value(expr, env)
        if val is not None:
            out[key] = val
    return out


def build_step_env(
    *,
    context: dict[str, Any],
    run_id: str,
    ticket_id: str | None,
    step_records: list[Any],
    current_step_index: int,
) -> dict[str, Any]:
    steps_env: dict[str, Any] = {}
    for rec in step_records[:current_step_index]:
        steps_env[rec.step_name] = {
            "result": rec.result or {},
            "artifacts": rec.output_artifacts or {},
        }
    return {
        "context": context or {},
        "run": {"id": run_id, "ticket_id": ticket_id},
        "steps": steps_env,
    }


_WHEN_COMPARISON = re.compile(
    r"(==|!=|>=|<=|\bin\b|\bnot in\b|>|<|\band\b|\bor\b|\bnot\b)",
    re.IGNORECASE,
)

_WHEN_ALLOWED_AST = (
    ast.Expression,
    ast.BoolOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Constant,
    ast.And,
    ast.Or,
    ast.Not,
    ast.Eq,
    ast.NotEq,
    ast.In,
    ast.NotIn,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
)


def _eval_when_string(text: str) -> bool:
    """在变量替换后，安全求值简单比较表达式（如 low == 'high'）。"""
    tree = ast.parse(text.strip(), mode="eval")
    for node in ast.walk(tree):
        if isinstance(node, _WHEN_ALLOWED_AST):
            continue
        raise ValueError(f"when 表达式不支持: {type(node).__name__}")
    return bool(eval(compile(tree, "<when>", "eval"), {"__builtins__": {}}))


def step_is_enabled(step_when: str | None, env: dict[str, Any]) -> bool:
    """步骤 when 表达式为真时启用；未配置 when 则始终启用。"""
    if not step_when:
        return True
    resolved = resolve_value(step_when, env)
    if isinstance(resolved, str):
        stripped = resolved.strip()
        if not stripped:
            return False
        if _WHEN_COMPARISON.search(stripped):
            try:
                return _eval_when_string(stripped)
            except Exception:
                return False
        if stripped.lower() in ("false", "0", "no", "none", "null"):
            return False
        return True
    return bool(resolved)
