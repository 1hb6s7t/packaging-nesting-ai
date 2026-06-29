from uuid import uuid4

from fastapi.testclient import TestClient

from app.domain.schemas import HardConstraint, ProductionOrder, RuleSet, SoftScoreRule
from app.main import app
from app.services.rules import DEFAULT_RULE_SET, evaluate_order
from auth_helpers import auth_headers


client = TestClient(app)


def _order(order_id: str, material: str = "white_card", thickness: str = "350gsm") -> ProductionOrder:
    return ProductionOrder(
        order_id=order_id,
        product_name=f"Product {order_id}",
        category="box",
        is_repeat_order=True,
        quote_amount=5000,
        contacted=True,
        quantity=1000,
        material=material,
        thickness=thickness,
    )


def test_hard_constraint_rejects_material_mismatch() -> None:
    decision = evaluate_order(_order("main"), _order("other", material="kraft"))
    assert not decision.accepted
    assert "材料不同" in decision.reasons[0]


def test_priority_score_is_deterministic() -> None:
    decision = evaluate_order(_order("main"), _order("other"))
    assert decision.accepted
    assert decision.priority_score > 0.5
    assert decision.score_breakdown["repeat_order"] == 1


def test_custom_hard_constraint_expression_rejects_order() -> None:
    ruleset = RuleSet(
        hard_constraints=[
            HardConstraint(
                name="quote_floor",
                when="order.quote_amount < main_order.quote_amount",
                action="reject",
                reason="报价低于主订单",
            )
        ],
        soft_scores=[SoftScoreRule(name="contacted", expression="order.contacted ? 1 : 0", weight=1)],
    )
    candidate = _order("other", material="white_card").model_copy(update={"quote_amount": 1000})
    decision = evaluate_order(_order("main"), candidate, ruleset)
    assert not decision.accepted
    assert decision.reasons == ["报价低于主订单"]


def test_custom_soft_score_expression_controls_score() -> None:
    ruleset = RuleSet(
        soft_scores=[
            SoftScoreRule(name="contacted_only", expression="order.contacted ? 1 : 0", weight=1),
            SoftScoreRule(name="quantity_ratio", expression="normalize(order.quantity)", weight=0.5),
        ]
    )
    decision = evaluate_order(_order("main"), _order("other"), ruleset)
    assert decision.accepted
    assert decision.score_breakdown["contacted_only"] == 1
    assert decision.score_breakdown["quantity_ratio"] == 0.1
    assert decision.priority_score == 1.05


def test_unsafe_rule_expression_is_rejected() -> None:
    ruleset = RuleSet(
        hard_constraints=[
            HardConstraint(
                name="unsafe",
                when='__import__("os").system("dir")',
                action="reject",
                reason="unsafe",
            )
        ]
    )
    decision = evaluate_order(_order("main"), _order("other"), ruleset)
    assert not decision.accepted
    assert decision.reasons == ["规则表达式无效：unsafe"]
    assert decision.evaluation_errors


def test_rule_set_api_versioning_activation_and_execution_logs() -> None:
    headers = auth_headers(client)
    active_response = client.get("/api/rules/sets/active", headers=headers)
    assert active_response.status_code == 200
    active_definition = DEFAULT_RULE_SET.model_dump(mode="json")
    suffix = uuid4().hex[:8]

    active_definition["ruleset_id"] = f"api_rule_test_{suffix}"
    active_definition["soft_scores"][0]["weight"] = 0.35
    create_response = client.post(
        "/api/rules/sets",
        headers=headers,
        json={
            "name": "API Rule Test",
            "version": suffix,
            "is_active": False,
            "definition": active_definition,
        },
    )
    assert create_response.status_code == 200
    created = create_response.json()
    assert created["is_active"] is False
    assert created["definition"]["ruleset_id"] == f"api_rule_test_{suffix}"

    activate_response = client.post(f"/api/rules/sets/{created['id']}/activate", headers=headers)
    assert activate_response.status_code == 200
    assert activate_response.json()["is_active"] is True
    assert client.get("/api/rules/sets/active", headers=headers).json()["id"] == created["id"]

    main_id = f"RULE-MAIN-{suffix}"
    candidate_id = f"RULE-CAND-{suffix}"
    rejected_id = f"RULE-REJECT-{suffix}"
    import_response = client.post(
        "/api/orders/import",
        headers=headers,
        json={
            "orders": [
                _order(main_id).model_dump(mode="json"),
                _order(candidate_id).model_dump(mode="json"),
                _order(rejected_id, material="kraft").model_dump(mode="json"),
            ]
        },
    )
    assert import_response.status_code == 200

    score_response = client.post(f"/api/orders/{candidate_id}/score?main_order_id={main_id}", headers=headers)
    assert score_response.status_code == 200
    score = score_response.json()
    assert score["accepted"] is True
    assert score["priority_score"] > 0.5

    filter_response = client.post(f"/api/orders/filter-candidates?main_order_id={main_id}", headers=headers)
    assert filter_response.status_code == 200
    filtered_ids = {row["order_id"] for row in filter_response.json()}
    assert candidate_id in filtered_ids
    assert rejected_id not in filtered_ids

    logs_response = client.get(f"/api/rules/execution-logs?order_id={candidate_id}&limit=10", headers=headers)
    assert logs_response.status_code == 200
    logs = logs_response.json()
    assert any(log["rule_set_id"] == created["id"] for log in logs)
    assert any(log["result"]["decision"]["accepted"] is True for log in logs)
