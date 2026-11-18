# Naija Bites prepared-food inventory — development RFC

**Status:** experimental development slice. Do not merge/deploy until the new
customer checkout integration is complete and independently validated.

## Goals

- Only owner screens use grams, millilitres and piece counts. Customer
  storefront/chat/cart/review show meal names, "standard" and "extra", never
  mass/volume measurements.
- Prepared stock (cooked Jollof Rice, soup, chicken pieces) is tracked without
  raw ingredients or accounting.
- Owner can configure serving estimates, add/remove/set physical stock, and
  record reasons with audit trail; never allow negative stock.
- Conservative availability uses **target + tolerance**:
  5,000 g / (250 g + 10 g) = **19 standard portions**.
  A serving can be 240–260 g physically, so 19 is a safe planning maximum.
- An extra rice serving can have a separate internal target (e.g. 125 ± 10 g)
  and selling price (e.g. NGN 700). Chicken can be counted in pieces; natural
  weight differences require no individual weighing.

## Owner-only API (development)

- `GET /owner/prepared-stock` configured items, quantities and conservative capacities.
- `POST /owner/prepared-stock/config` establishes or edits target, tolerance,
  extra quantity, extra price, unit. It starts at zero stock.
- `POST /owner/prepared-stock/adjust` add, remove or physically reconcile
  stock. Every movement records actor, time, amount, before/after and reason.
- `GET /owner/prepared-stock/history` last 50 changes.

Existing owner authentication, session cookies and FastAPI role gates are reused.
No public stock units API is added. Stock is stored in integer g, mL or pieces;
no rounding floats or fractional piece balances.

## Important release gate: **not yet wired to checkout**

The existing `inventory.quantity` per menu product/variant remains the sole
source used by the customer storefront, cart and atomic checkout. **Prepared stock
entries currently do not reserve or deduct customer orders**. This intentional
separation avoids a dangerous partial migration of orders, payment verification
and cart logic. Every prepared-stock response says `checkout_enabled: false`.

Before production rollout, the next tested slice must:
1. Decide the exact mapping of existing menu variants and customized extras
   (currently `meal_addons`/`ingredients`) to prepared stock, without charging
   or deducting extras twice.
2. Build an owner UI that shows internal weights, tolerances, stock history, and
   the pending-to-active rollout state.
3. Atomically validate and deduct conservative prepared-stock usage inside
   `OrderService._commit_approved_checkout` with PostgreSQL row locks and
   all existing idempotency/payment protections.
4. Update cart availability and menu discovery to match that same source.
5. Verify rollback and concurrency (two customers racing for last stock),
   payment/retry behavior, old orders and legacy inventory compatibility.
6. Prove that no internal units leak into customer-visible menu, agent,
   cart, checkout review or order confirmation.

No multi-business features, ingredient purchasing, accounting, or production
schema changes are part of this slice.

## Example owner workflow

1. Create configuration: Jollof Rice, gram, target 250, tolerance 10,
   extra 125, extra tolerance 10, extra selling price NGN 700.
2. Add 5,000 g with reason `Cooked new batch`.
3. Dashboard displays `5 kg on hand`, `19 conservative standard servings`.
4. Remove 300 g spoiled rice with reason, or `set` 4,500 g after a physical check.
5. Review movement history to trace discrepancies.

**No physical weighing of every served portion is required.**
