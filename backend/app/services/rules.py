from __future__ import annotations

import ast
from datetime import date
from typing import Any

from app.domain.schemas import ProductionOrder, RuleDecision, RuleSet


DEFAULT_RULE_SET = RuleSet(
    hard_constraints=[
        {
            "name": "same_material",
            "when": "order.material != main_order.material",
            "action": "reject",
            "reason": "材料不同，不能共版",
        },
        {
            "name": "same_thickness",
            "when": "order.thickness != main_order.thickness",
            "action": "reject",
            "reason": "纸张厚度/克重不同，不能共版",
        },
    ],
    soft_scores=[
        {"name": "repeat_order", "expression": "order.is_repeat_order ? 1 : 0", "weight": 0.30},
        {"name": "same_category", "expression": "order.category == main_order.category ? 1 : 0", "weight": 0.20},
        {"name": "quote_amount", "expression": "normalize(order.quote_amount)", "weight": 0.15},
        {"name": "contacted", "expression": "order.contacted ? 1 : 0", "weight": 0.10},
        {"name": "due_date", "expression": "due_date_score(order.due_date)", "weight": 0.10},
        {"name": "geometry_fit", "expression": "geometry_fit_score(order)", "weight": 0.10},
        {"name": "quantity", "expression": "normalize(order.quantity)", "weight": 0.05},
    ],
)


class RuleExpressionError(ValueError):
    pass


ALLOWED_ORDER_FIELDS = set(ProductionOrder.model_fields)
ALLOWED_AST_NODES = (
    ast.Expression,
    ast.IfExp,
    ast.BoolOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Attribute,
    ast.Constant,
    ast.And,
    ast.Or,
    ast.Not,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.Is,
    ast.IsNot,
    ast.BinOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.USub,
    ast.UAdd,
)


def normalize(value: float, cap: float = 10000) -> float:
    if value <= 0:
        return 0.0
    return min(1.0, value / cap)


def due_date_score(value: date | None) -> float:
    if value is None:
        return 0.3
    days = (value - date.today()).days
    if days < 0:
        return 0.2
    if days <= 3:
        return 1.0
    if days <= 14:
        return 0.75
    if days <= 30:
        return 0.5
    return 0.25


def geometry_fit_score(_: ProductionOrder) -> float:
    # Geometry fit is recomputed during nesting. Before polygon analysis, keep it neutral.
    return 0.5


ALLOWED_FUNCTIONS = {
    "normalize": normalize,
    "due_date_score": due_date_score,
    "geometry_fit_score": geometry_fit_score,
}


def evaluate_expression(expression: str, main_order: ProductionOrder, order: ProductionOrder) -> Any:
    translated = translate_expression(expression)
    try:
        tree = ast.parse(translated, mode="eval")
    except SyntaxError as exc:
        raise RuleExpressionError("invalid syntax") from exc
    validate_expression_tree(tree)
    context = {
        "main_order": main_order,
        "order": order,
        **ALLOWED_FUNCTIONS,
    }
    return eval(compile(tree, "<rule-expression>", "eval"), {"__builtins__": {}}, context)


def translate_expression(expression: str) -> str:
    value = expression.strip()
    question_index = value.find("?")
    if question_index < 0:
        return value
    colon_index = value.find(":", question_index + 1)
    if colon_index < 0:
        raise RuleExpressionError("ternary expression is missing ':'")
    condition = value[:question_index].strip()
    true_value = value[question_index + 1 : colon_index].strip()
    false_value = value[colon_index + 1 :].strip()
    if not condition or not true_value or not false_value:
        raise RuleExpressionError("ternary expression is incomplete")
    return f"({true_value}) if ({condition}) else ({false_value})"


def validate_expression_tree(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_AST_NODES):
            raise RuleExpressionError(f"unsupported expression node: {type(node).__name__}")
        if isinstance(node, ast.Name):
            if node.id not in {"order", "main_order", *ALLOWED_FUNCTIONS}:
                raise RuleExpressionError(f"unknown name: {node.id}")
        elif isinstance(node, ast.Attribute):
            if not isinstance(node.value, ast.Name) or node.value.id not in {"order", "main_order"}:
                raise RuleExpressionError("only order fields can be read")
            if node.attr not in ALLOWED_ORDER_FIELDS:
                raise RuleExpressionError(f"unknown order field: {node.attr}")
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_FUNCTIONS:
                raise RuleExpressionError("only whitelisted functions can be called")
            if node.keywords:
                raise RuleExpressionError("keyword arguments are not supported")


def score_value(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise RuleExpressionError("soft score expression did not return a number") from exc
    return round(max(0.0, min(1.0, numeric)), 4)


def evaluate_order(main_order: ProductionOrder, order: ProductionOrder, ruleset: RuleSet = DEFAULT_RULE_SET) -> RuleDecision:
    reasons: list[str] = []
    evaluation_errors: list[str] = []
    for rule in ruleset.hard_constraints:
        try:
            should_reject = bool(evaluate_expression(rule.when, main_order, order))
        except RuleExpressionError as exc:
            evaluation_errors.append(f"{rule.name}: {exc}")
            return RuleDecision(
                accepted=False,
                reasons=[f"规则表达式无效：{rule.name}"],
                priority_score=0,
                score_breakdown={},
                evaluation_errors=evaluation_errors,
            )
        if should_reject and rule.action == "reject":
            reasons.append(rule.reason)
    if reasons:
        return RuleDecision(
            accepted=False,
            reasons=reasons,
            priority_score=0,
            score_breakdown={},
            evaluation_errors=evaluation_errors,
        )

    breakdown: dict[str, float] = {}
    score = 0.0
    for rule in ruleset.soft_scores:
        try:
            value = score_value(evaluate_expression(rule.expression, main_order, order))
        except RuleExpressionError as exc:
            evaluation_errors.append(f"{rule.name}: {exc}")
            value = 0.0
        breakdown[rule.name] = value
        score += value * rule.weight
    return RuleDecision(
        accepted=True,
        reasons=[],
        priority_score=round(score, 4),
        score_breakdown=breakdown,
        evaluation_errors=evaluation_errors,
    )


def filter_candidates(main_order: ProductionOrder, orders: list[ProductionOrder]) -> list[ProductionOrder]:
    accepted: list[ProductionOrder] = []
    for order in orders:
        decision = evaluate_order(main_order, order)
        if decision.accepted:
            accepted.append(order.model_copy(update={"priority_score": decision.priority_score}))
    return sorted(accepted, key=lambda item: item.priority_score, reverse=True)
