"""Product Agent: find an in-stock replacement, never the original, never above the original unit price."""
import json
from decimal import Decimal

import pytest

from app.agents import product_agent
from app.agents.product_agent import ProductChoice
from app.guardrails.tool_guardrails import ToolCallError
from app.llm import BudgetExceeded
from tests.agent_fakes import FakeGateway, make_deps, make_state, order, patch_react


def product(pid, price, qty=5, name=None, currency=None):
    row = {"productId": pid, "name": name or pid, "price": price, "availableQty": qty}
    if currency:
        row["currency"] = currency
    return row


def search_call(*products):
    return {"tool": "search_products", "ok": True, "result": json.dumps({"products": list(products)})}


def gateway(*products, category="headphones"):
    return FakeGateway({"check_inventory": {"category": category}, "search_products": {"products": list(products)}})


async def run(monkeypatch, gw, state=None, structured=None, calls=None, error=None):
    patch_react(monkeypatch, product_agent, structured=structured, tool_calls=calls, error=error)
    deps, _ = make_deps(gw)
    return await product_agent.run(state or make_state(order=order()), deps, {})


# ------------------------------------------------------------------------------------------------ happy path
async def test_model_choice_is_used_when_it_is_a_valid_candidate(monkeypatch):
    rows = [product("PRD-103", 79.99), product("PRD-104", 49.99)]
    out = await run(monkeypatch, gateway(*rows), structured=ProductChoice(product_id="PRD-103", reason="better reviews"),
                    calls=[search_call(*rows)])
    rec = out["product_recommendation"]
    assert rec.product_id == "PRD-103" and rec.price == Decimal("79.99") and rec.reason == "better reviews"
    assert out["errors"] == [] and out["evidence"] == ["replacement candidate PRD-103 at 79.99"]


async def test_response_keeps_the_workflow_contract_keys(monkeypatch):
    out = await run(monkeypatch, gateway(), structured=None)
    assert {"product_recommendation", "errors", "evidence"} <= set(out)


# ---------------------------------------------------------------------------------------- missing state
async def test_missing_order_skips_the_agent_and_writes_no_recommendation(monkeypatch):
    out = await run(monkeypatch, gateway(), state=make_state())
    assert out["errors"] == ["PRODUCT_SKIPPED_MISSING_ORDER"] and "product_recommendation" not in out


# ----------------------------------------------------------------------------- invalid model output / fallback
async def test_model_pick_outside_the_verified_candidates_is_replaced_by_the_lowest_priced_valid_one(monkeypatch):
    rows = [product("PRD-103", 79.99), product("PRD-104", 49.99), product("PRD-105", 99.00)]
    out = await run(monkeypatch, gateway(*rows), structured=ProductChoice(product_id="PRD-HALLUCINATED", reason="trust me"),
                    calls=[search_call(*rows)])
    assert out["product_recommendation"].product_id == "PRD-104"
    assert "PRODUCT_PICK_REPLACED_BY_RULE" in out["errors"]


async def test_model_pick_that_costs_more_than_the_original_is_not_accepted(monkeypatch):
    rows = [product("PRD-200", 300.0), product("PRD-103", 79.99)]
    out = await run(monkeypatch, gateway(*rows), structured=ProductChoice(product_id="PRD-200", reason="premium"), calls=[search_call(*rows)])
    assert out["product_recommendation"].product_id == "PRD-103"


async def test_non_model_object_as_structured_answer_is_ignored(monkeypatch):
    rows = [product("PRD-103", 79.99)]
    out = await run(monkeypatch, gateway(*rows), structured={"product_id": "PRD-103"}, calls=[search_call(*rows)])
    assert out["product_recommendation"].product_id == "PRD-103" and "PRODUCT_PICK_REPLACED_BY_RULE" in out["errors"]


async def test_fallback_search_runs_when_the_model_found_nothing(monkeypatch):
    out = await run(monkeypatch, gateway(product("PRD-103", 79.99)), structured=None)
    assert out["product_recommendation"].product_id == "PRD-103" and "PRODUCT_SEARCH_DIRECT" in out["errors"]


# ---------------------------------------------------------------------------------------------- tool failure
async def test_failed_fallback_search_returns_no_recommendation_with_a_code(monkeypatch):
    gw = FakeGateway({"check_inventory": {"category": "headphones"}, "search_products": ToolCallError("UPSTREAM_503: down")})
    out = await run(monkeypatch, gw)
    assert out["product_recommendation"].product_id is None
    assert any(e.startswith("PRODUCT_SEARCH_FAILED") for e in out["errors"])
    assert out["evidence"] == ["no replacement available"]


async def test_unknown_category_is_recorded_and_no_fallback_search_is_attempted(monkeypatch):
    gw = FakeGateway({"check_inventory": ToolCallError("PRODUCT_NOT_FOUND: x")})
    out = await run(monkeypatch, gw)
    assert any(e.startswith("PRODUCT_CATEGORY_UNKNOWN") for e in out["errors"]) and "search_products" not in gw.names()


# -------------------------------------------------------------------------------------------------- timeout
async def test_model_timeout_is_recorded_and_the_rules_still_choose(monkeypatch):
    out = await run(monkeypatch, gateway(product("PRD-103", 79.99)), error=TimeoutError("model took too long"))
    assert "PRODUCT_AGENT_ERROR: TimeoutError" in out["errors"]
    assert out["product_recommendation"].product_id == "PRD-103"


async def test_a_spent_token_budget_is_not_swallowed(monkeypatch):
    with pytest.raises(BudgetExceeded):
        await run(monkeypatch, gateway(), error=BudgetExceeded("budget"))


async def test_unexpected_bugs_are_not_swallowed(monkeypatch):
    with pytest.raises(KeyError):
        await run(monkeypatch, gateway(), error=KeyError("bug"))


# ----------------------------------------------------------------------------------- invalid prices and rules
async def test_original_out_of_stock_expensive_and_bad_price_items_are_excluded(monkeypatch):
    rows = [product("PRD-100", 10.0), product("PRD-201", 10.0, qty=0), product("PRD-202", 500.0), product("PRD-203", "abc"),
            product("PRD-204", -5), product("PRD-205", 0), {"name": "no id", "price": 5, "availableQty": 1}, product("PRD-103", 79.99)]
    out = await run(monkeypatch, gateway(*rows), structured=None, calls=[search_call(*rows)])
    rec = out["product_recommendation"]
    assert [c.product_id for c in rec.candidates] == ["PRD-103"]
    assert any(e.startswith("PRODUCT_CANDIDATE_MALFORMED") for e in out["errors"])


async def test_a_replacement_in_another_currency_is_never_compared_by_amount(monkeypatch):
    rows = [product("PRD-300", 1.00, currency="JPY"), product("PRD-103", 79.99, currency="USD")]
    out = await run(monkeypatch, gateway(*rows), calls=[search_call(*rows)])
    assert [c.product_id for c in out["product_recommendation"].candidates] == ["PRD-103"]


async def test_a_cheaper_replacement_must_cost_strictly_less_than_the_original(monkeypatch):
    rows = [product("PRD-EQ", 129.99), product("PRD-OVER", 130.00), product("PRD-LESS", 129.98)]
    out = await run(monkeypatch, gateway(*rows), calls=[search_call(*rows)])
    assert [c.product_id for c in out["product_recommendation"].candidates] == ["PRD-LESS"]


async def test_budget_is_the_unit_price_rounded_half_up_with_decimal(monkeypatch):
    state = make_state(order=order(amount="100.00", quantity=3))
    out = await run(monkeypatch, gateway(product("PRD-103", 33.32), product("PRD-104", 33.33)), state=state,
                    calls=[search_call(product("PRD-103", 33.32), product("PRD-104", 33.33))])
    assert out["product_recommendation"].max_price == Decimal("33.33")
    assert [c.product_id for c in out["product_recommendation"].candidates] == ["PRD-103"]


def test_budget_treats_a_missing_or_zero_quantity_as_one():
    assert product_agent.calculate_replacement_budget(order(amount=50, quantity=None)) == Decimal("50.00")
    assert product_agent.calculate_replacement_budget(order(amount=50, quantity=0)) == Decimal("50.00")


# ------------------------------------------------------------------------------ duplicate results, cap of ten
async def test_duplicate_products_are_listed_once(monkeypatch):
    rows = [product("PRD-103", 79.99), product("PRD-103", 79.99)]
    out = await run(monkeypatch, gateway(*rows), calls=[search_call(*rows), search_call(*rows)])
    assert [c.product_id for c in out["product_recommendation"].candidates] == ["PRD-103"]


async def test_at_most_ten_candidates_are_kept(monkeypatch):
    rows = [product(f"PRD-{n:03d}", 10 + n) for n in range(15)]
    out = await run(monkeypatch, gateway(*rows), calls=[search_call(*rows)])
    assert len(out["product_recommendation"].candidates) == product_agent.MAX_CANDIDATES


async def test_no_valid_replacement_returns_no_recommendation(monkeypatch):
    out = await run(monkeypatch, gateway(product("PRD-202", 500.0)), calls=[search_call(product("PRD-202", 500.0))])
    rec = out["product_recommendation"]
    assert rec.product_id is None and rec.reason == product_agent.NO_REPLACEMENT_REASON


def test_lowest_price_wins_and_the_id_breaks_ties():
    from app.models.product import ProductCandidate
    a = ProductCandidate(product_id="PRD-B", name="b", price=10, available_qty=1)
    b = ProductCandidate(product_id="PRD-A", name="a", price=10, available_qty=1)
    assert product_agent.select_replacement([a, b]).product_id == "PRD-A"
