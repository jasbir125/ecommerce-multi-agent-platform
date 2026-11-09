"""Product Agent: finds an in-stock replacement that costs no more than the original.

It retrieves and recommends only. Prices, stock and categories come from the catalog tools; the model's choice is
accepted only if it is one of the verified candidates, otherwise the lowest-priced valid candidate is used.
"""
from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.agents.base import (MODEL_ERRORS, TOOL_ERRORS, AgentInputError, AgentRun, AgentUpdate, describe, run_react,
                             tool_results)
from app.models.money import CENT, DEFAULT_CURRENCY, is_within_budget, money_as_number, same_currency, to_money
from app.models.order import OrderSnapshot
from app.models.product import ProductCandidate, ProductRecommendation

log = logging.getLogger("agent.product")

# ---------------------------------------------------------------------------------------------- constants
AGENT_NAME = "product"
MAX_CANDIDATES = 10
NO_REPLACEMENT_REASON = "No suitable in-stock replacement was found."
RULE_PICK_REASON = "Selected from verified catalog results (lowest price at or below the original)."
SYSTEM_PROMPT = ("You are the Product Agent. Find an in-stock replacement for the customer's item that costs the same or less. "
                 "Use search_products with the item's category or keywords and a max_price.")


# -------------------------------------------------------------------------------------------------- models
class ProductChoice(BaseModel):
    """The model's answer. Untrusted: the product_id must be one of the verified candidates."""
    product_id: str | None
    reason: str


class ProductRequest(BaseModel):
    """Validated input: what the customer bought and the most a replacement may cost per unit."""
    original_product_id: str | None
    original_product_name: str | None
    currency: str
    unit_budget: Decimal


class ProductResult(AgentUpdate):
    product_recommendation: ProductRecommendation | None = None


class ProductContext(BaseModel):
    """What the model is told and what the rules need: the request plus the original item's category."""
    request: ProductRequest
    category: str = ""
    notes: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------------------------------------- run()
async def run(state, deps, config) -> dict[str, Any]:
    timer = AgentRun(AGENT_NAME, state)
    try:
        request = validate_input(state)
    except AgentInputError as e:
        return timer.finish(handle_error(e.code)).to_update()
    ctx = deps.run(state)
    context = await prepare_context(ctx, request)
    choice = await invoke_tools_or_agent(deps, ctx, context, config, timer)
    raw_results = await normalize_tool_results(ctx, context, timer)
    candidates = apply_business_rules(raw_results, request, context.notes)
    picked, reason = select_decision(choice, candidates, context.notes)
    recommendation = build_recommendation(request, picked, reason, candidates)
    result = build_result(recommendation, context.notes)
    return timer.finish(result).to_update()


# ---------------------------------------------------------------------------------------- validate_input()
def validate_input(state) -> ProductRequest:
    if state.order is None:
        raise AgentInputError("PRODUCT_SKIPPED_MISSING_ORDER")
    order: OrderSnapshot = state.order
    return ProductRequest(original_product_id=order.product_id, original_product_name=order.product_name,
                          currency=order.currency or DEFAULT_CURRENCY, unit_budget=calculate_replacement_budget(order))


def calculate_replacement_budget(order: OrderSnapshot) -> Decimal:
    """The original unit price: order total divided by quantity (quantity missing or invalid counts as 1)."""
    quantity = order.quantity if order.quantity and order.quantity > 0 else 1
    return (order.total_amount / Decimal(quantity)).quantize(CENT, rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------------------- prepare_context()
async def prepare_context(ctx, request: ProductRequest) -> ProductContext:
    context = ProductContext(request=request)
    context.category = await get_original_product_category(ctx, request, context.notes)
    return context


async def get_original_product_category(ctx, request: ProductRequest, notes: list[str]) -> str:
    """The original item's category comes from the catalog, not from the model."""
    if not request.original_product_id:
        return ""
    try:
        inventory = await ctx.gateway.call("check_inventory", {"product_id": request.original_product_id})
    except TOOL_ERRORS as e:
        notes.append(f"PRODUCT_CATEGORY_UNKNOWN: {str(e)[:80]}")
        log.warning("AGENT_ERROR agent_name=%s tool_name=check_inventory error_code=PRODUCT_CATEGORY_UNKNOWN error=%s", AGENT_NAME, e)
        return ""
    return inventory.get("category") or ""


# ------------------------------------------------------------------------------- invoke_tools_or_agent()
async def invoke_tools_or_agent(deps, ctx, context: ProductContext, config, timer: AgentRun) -> ProductChoice | None:
    """Let the model search the catalog and pick a candidate. A model failure is recorded, not fatal."""
    try:
        _, choice = await run_react(deps, ctx, AGENT_NAME, SYSTEM_PROMPT, build_model_prompt(context), config,
                                    response_format=ProductChoice)
    except MODEL_ERRORS as e:
        context.notes.append(f"PRODUCT_AGENT_ERROR: {describe(e)}")
        timer.failed("PRODUCT_AGENT_ERROR", e)
        return None
    return request_model_product_choice(choice)


def build_model_prompt(context: ProductContext) -> str:
    request = context.request
    budget = money_as_number(request.unit_budget)
    return (f"The customer ordered '{request.original_product_name}' ({request.original_product_id}, category '{context.category}') "
            f"at {request.unit_budget} {request.currency}. Search with category='{context.category}' and max_price={budget}. "
            f"Choose one product_id from the search results, or null if none is suitable.")


def request_model_product_choice(structured: Any) -> ProductChoice | None:
    """The structured response is untrusted model output: anything that is not a ProductChoice is ignored."""
    return structured if isinstance(structured, ProductChoice) else None


# ------------------------------------------------------------------------------- normalize_tool_results()
async def normalize_tool_results(ctx, context: ProductContext, timer: AgentRun) -> list[dict[str, Any]]:
    """The catalog search results (the model's own searches, or a direct category search if they found nothing)."""
    results = tool_results(ctx, AGENT_NAME, "search_products")
    return await apply_fallback_search_if_needed(ctx, context, results, timer)


async def apply_fallback_search_if_needed(ctx, context: ProductContext, results: list[Any], timer: AgentRun) -> list[dict[str, Any]]:
    has_products = any(isinstance(r, dict) and r.get("products") for r in results)
    if has_products or not context.category:
        return [r for r in results if isinstance(r, dict)]
    args = {"category": context.category, "max_price": money_as_number(context.request.unit_budget)}
    try:
        direct = await ctx.gateway.call("search_products", args)
    except TOOL_ERRORS as e:
        context.notes.append(f"PRODUCT_SEARCH_FAILED: {str(e)[:80]}")
        timer.failed("PRODUCT_SEARCH_FAILED", e, tool="search_products")
        return [r for r in results if isinstance(r, dict)]
    context.notes.append("PRODUCT_SEARCH_DIRECT")
    return [r for r in [*results, direct] if isinstance(r, dict)]


# ------------------------------------------------------------------------------------ apply_business_rules()
def apply_business_rules(results: list[dict[str, Any]], request: ProductRequest, notes: list[str]) -> list[ProductCandidate]:
    """Keep only verified, in-stock, affordable, different products; at most MAX_CANDIDATES, one per product id."""
    valid: dict[str, ProductCandidate] = {}
    rejected = 0
    for raw in extract_raw_products(results):
        candidate = parse_candidate(raw)
        if candidate is None:
            rejected += 1
        elif is_valid_candidate(candidate, request):
            valid.setdefault(candidate.product_id, candidate)
    if rejected:
        notes.append(f"PRODUCT_CANDIDATE_MALFORMED: {rejected} skipped")
        log.warning("AGENT_ERROR agent_name=%s error_code=PRODUCT_CANDIDATE_MALFORMED skipped=%d", AGENT_NAME, rejected)
    return list(valid.values())[:MAX_CANDIDATES]


def extract_raw_products(results: list[dict[str, Any]]) -> list[Any]:
    products: list[Any] = []
    for result in results:
        products.extend(result.get("products") or [])
    return products


def parse_candidate(raw: Any) -> ProductCandidate | None:
    """A catalog row as a typed candidate, or None if a required field is missing or malformed."""
    if not isinstance(raw, dict):
        return None
    try:
        return ProductCandidate(product_id=raw["productId"], name=raw["name"], price=to_money(raw["price"]),
                                currency=raw.get("currency"), available_qty=int(raw.get("availableQty") or 0))
    except (KeyError, TypeError, ValueError):
        return None


def is_valid_candidate(candidate: ProductCandidate, request: ProductRequest) -> bool:
    return (is_in_stock(candidate) and is_different_product(candidate, request)
            and same_currency(candidate.currency, request.currency) and costs_no_more_than_original(candidate, request))


def is_in_stock(candidate: ProductCandidate) -> bool:
    return candidate.available_qty > 0


def is_different_product(candidate: ProductCandidate, request: ProductRequest) -> bool:
    return candidate.product_id != request.original_product_id


def costs_no_more_than_original(candidate: ProductCandidate, request: ProductRequest) -> bool:
    return candidate.price > 0 and is_within_budget(candidate.price, request.unit_budget)


# ----------------------------------------------------------------------------------------- select_decision()
def select_decision(choice: ProductChoice | None, candidates: list[ProductCandidate], notes: list[str]) -> tuple[ProductCandidate | None, str]:
    """The model's pick if it is a verified candidate; otherwise the lowest-priced candidate; otherwise nothing."""
    by_id = {c.product_id: c for c in candidates}
    if choice is not None and choice.product_id in by_id:
        return by_id[choice.product_id], choice.reason
    if not candidates:
        return None, NO_REPLACEMENT_REASON
    notes.append("PRODUCT_PICK_REPLACED_BY_RULE")
    return select_replacement(candidates), RULE_PICK_REASON


def select_replacement(candidates: list[ProductCandidate]) -> ProductCandidate:
    """Lowest price wins; the product id breaks ties so the result is deterministic."""
    return min(candidates, key=lambda c: (c.price, c.product_id))


# ----------------------------------------------------------------------------------------- build_evidence()
def build_evidence(recommendation: ProductRecommendation) -> list[str]:
    if recommendation.product_id:
        return [f"replacement candidate {recommendation.product_id} at {recommendation.price}"]
    return ["no replacement available"]


# ------------------------------------------------------------------------------------ build_recommendation()
def build_recommendation(request: ProductRequest, picked: ProductCandidate | None, reason: str,
                         candidates: list[ProductCandidate]) -> ProductRecommendation:
    return ProductRecommendation(
        product_id=picked.product_id if picked else None, name=picked.name if picked else None,
        price=picked.price if picked else None, max_price=request.unit_budget,
        reason=reason or NO_REPLACEMENT_REASON, candidates=candidates)


# --------------------------------------------------------------------------------------------- build_result()
def build_result(recommendation: ProductRecommendation, notes: list[str]) -> ProductResult:
    return ProductResult(product_recommendation=recommendation, errors=notes, evidence=build_evidence(recommendation))


# -------------------------------------------------------------------------------------------- handle_error()
def handle_error(code: str) -> ProductResult:
    """The agent could not run (missing input). No recommendation is written; the code explains why."""
    return ProductResult(errors=[code])
