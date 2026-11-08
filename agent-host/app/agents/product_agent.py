"""Product Agent: finds an in-stock replacement that costs no more than the original."""
from pydantic import BaseModel

from app.agents.base import run_react, tool_results
from app.guardrails.tool_guardrails import ToolCallError
from app.models.product import ProductCandidate, ProductRecommendation

SYSTEM = ("You are the Product Agent. Find an in-stock replacement for the customer's item that costs the same or less. "
          "Use search_products with the item's category or keywords and a max_price.")


class ProductChoice(BaseModel):
    product_id: str | None
    reason: str


async def run(state, deps, config) -> dict:
    ctx = deps.run(state)
    if state.order is None:
        return {"errors": ["PRODUCT_SKIPPED_MISSING_ORDER"]}
    qty = state.order.quantity or 1
    max_price = round(state.order.total_amount / qty, 2)
    choice, notes = None, []
    category = ""
    if state.order.product_id:
        try:    # the original item's category comes from the catalog, not from the model
            category = (await ctx.gateway.call("check_inventory", {"product_id": state.order.product_id})).get("category") or ""
        except ToolCallError as e:
            notes.append(f"PRODUCT_CATEGORY_UNKNOWN: {str(e)[:80]}")
    try:
        _, choice = await run_react(
            deps, ctx, "product", SYSTEM,
            f"The customer ordered '{state.order.product_name}' ({state.order.product_id}, category '{category}') "
            f"at {max_price} {state.order.currency}. Search with category='{category}' and max_price={max_price}. "
            f"Choose one product_id from the search results, or null if none is suitable.",
            config, response_format=ProductChoice)
    except Exception as e:  # noqa: BLE001
        notes.append(f"PRODUCT_AGENT_ERROR: {e}")
    candidates: dict[str, ProductCandidate] = {}
    results = tool_results(ctx, "product", "search_products")
    if not any(r.get("products") for r in results) and category:
        # The model's own searches found nothing: run the category search ourselves.
        try:
            results.append(await ctx.gateway.call("search_products", {"category": category, "max_price": max_price}))
            notes.append("PRODUCT_SEARCH_DIRECT")
        except ToolCallError as e:
            notes.append(f"PRODUCT_SEARCH_FAILED: {str(e)[:80]}")
    for res in results:
        for p in res.get("products", []):
            if p.get("availableQty", 0) > 0 and p["productId"] != state.order.product_id:
                candidates[p["productId"]] = ProductCandidate(product_id=p["productId"], name=p["name"],
                                                              price=float(p["price"]), available_qty=int(p["availableQty"]))
    valid = {k: c for k, c in candidates.items() if c.price <= max_price + 1e-9}
    picked = valid.get(choice.product_id) if choice and choice.product_id else None
    reason = choice.reason if picked and choice else ""
    if picked is None and valid:
        # The model's pick was missing or not in the catalog results: choose the closest-priced valid option.
        picked = max(valid.values(), key=lambda c: c.price)
        reason = "Selected from verified catalog results (closest price at or below the original)."
        notes.append("PRODUCT_PICK_REPLACED_BY_RULE")
    rec = ProductRecommendation(product_id=picked.product_id if picked else None, name=picked.name if picked else None,
                                price=picked.price if picked else None, max_price=max_price,
                                reason=reason or "No suitable in-stock replacement was found.", candidates=list(valid.values()))
    return {"product_recommendation": rec, "errors": notes,
            "evidence": [f"replacement candidate {rec.product_id} at {rec.price}" if rec.product_id else "no replacement available"]}
