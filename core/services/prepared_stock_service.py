"""Owner-only prepared-food inventory, independent from legacy checkout stock.

Never trust frontend-supplied owner identities. Call only after require_owner.
Amounts are integer grams, millilitres, or whole pieces. Customer storefront
and checkout cannot read this table until a reviewed cutover is implemented.
"""
import os
from contextlib import closing

from data.database.models import get_connection

MAX_UNITS = 1_000_000_000
UNITS = frozenset(("g", "ml", "piece"))
ACTIONS = frozenset(("add", "remove", "set"))


def _checked_int(value, name, *, minimum=0, maximum=MAX_UNITS):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be a whole number from {minimum} to {maximum}.")
    return value


def _variant(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 40:
        raise ValueError("Invalid meal variant.")
    return value.strip()


def _present(row):
    result = dict(row)
    max_standard = result["serving_units"] + result["tolerance_units"]
    max_extra = result["extra_units"] + result["extra_tolerance_units"]
    result["safe_standard_servings"] = result["quantity_units"] // max_standard
    result["safe_extra_servings"] = result["quantity_units"] // max_extra
    result["checkout_enabled"] = False
    return result


def list_stocks():
    with closing(get_connection()) as conn:
        rows = conn.execute("""
            SELECT ps.product_id, p.name AS product_name, ps.variant, ps.unit,
                   ps.quantity_units, ps.serving_units, ps.tolerance_units,
                   ps.extra_units, ps.extra_tolerance_units, ps.extra_price,
                   p.available AS product_available
            FROM prepared_stock ps
            JOIN products p ON p.id = ps.product_id
            ORDER BY p.name, ps.variant
        """).fetchall()
    return [_present(row) for row in rows]


def recent_movements(limit=50):
    with closing(get_connection()) as conn:
        rows = conn.execute("""
            SELECT m.id, m.product_id, p.name AS product_name, m.variant,
                   m.action, m.change_units, m.before_units, m.after_units,
                   m.reason, m.created_at, c.name AS actor_name
            FROM prepared_stock_movements m
            JOIN products p ON p.id = m.product_id
            JOIN customers c ON c.id = m.actor_customer_id
            ORDER BY m.id DESC LIMIT ?
        """, (limit,)).fetchall()
    return [dict(row) for row in rows]


def configure(owner, config):
    product_id = _checked_int(config.get("product_id"), "Product ID", minimum=1)
    variant = _variant(config.get("variant", "standard"))
    unit = config.get("unit")
    if unit not in UNITS:
        raise ValueError("Unit must be g, ml, or piece.")
    serving = _checked_int(config.get("serving_units"), "Standard serving", minimum=1)
    tolerance = _checked_int(config.get("tolerance_units"), "Standard tolerance")
    extra = _checked_int(config.get("extra_units"), "Extra serving", minimum=1)
    extra_tolerance = _checked_int(config.get("extra_tolerance_units"), "Extra tolerance")
    price = _checked_int(config.get("extra_price"), "Extra serving price", maximum=100000000)
    if tolerance >= serving or extra_tolerance >= extra:
        raise ValueError("Serving tolerance must be less than its target.")
    if unit == "piece" and (serving != 1 or tolerance or extra != 1 or extra_tolerance):
        raise ValueError("Whole-piece stock uses one piece per serving with zero tolerance.")

    with closing(get_connection()) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            inventory = conn.execute("""
                SELECT 1 FROM inventory WHERE product_id = ? AND variant = ?
            """, (product_id, variant)).fetchone()
            if inventory is None:
                raise LookupError("Select an existing menu item and variant.")
            lock = " FOR UPDATE" if os.getenv("DATABASE_URL", "").strip() else ""
            existing = conn.execute("""
                SELECT quantity_units, unit FROM prepared_stock
                WHERE product_id = ? AND variant = ?
            """ + lock, (product_id, variant)).fetchone()
            if existing:
                if existing["unit"] != unit and existing["quantity_units"]:
                    raise ValueError("Set stock to zero before changing its unit.")
                conn.execute("""
                    UPDATE prepared_stock SET unit = ?, serving_units = ?,
                        tolerance_units = ?, extra_units = ?,
                        extra_tolerance_units = ?, extra_price = ?
                    WHERE product_id = ? AND variant = ?
                """, (unit, serving, tolerance, extra, extra_tolerance, price,
                      product_id, variant))
            else:
                conn.execute("""
                    INSERT INTO prepared_stock
                    (product_id, variant, unit, serving_units, tolerance_units,
                     extra_units, extra_tolerance_units, extra_price)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (product_id, variant, unit, serving, tolerance, extra,
                      extra_tolerance, price))
            conn.execute("""
                INSERT INTO owner_audit_log(actor, action, target_customer_id)
                VALUES (?, ?, ?)
            """, (owner["email"], f"prepared_stock:config:{product_id}:{variant}", owner["id"]))
            row = conn.execute("""
                SELECT product_id, variant, unit, quantity_units,
                       serving_units, tolerance_units, extra_units,
                       extra_tolerance_units, extra_price
                FROM prepared_stock WHERE product_id = ? AND variant = ?
            """, (product_id, variant)).fetchone()
            conn.commit()
            return _present(row)
        except Exception:
            conn.rollback()
            raise


def adjust(owner, adjustment):
    product_id = _checked_int(adjustment.get("product_id"), "Product ID", minimum=1)
    variant = _variant(adjustment.get("variant", "standard"))
    action = adjustment.get("action")
    if action not in ACTIONS:
        raise ValueError("Action must be add, remove or set.")
    amount = _checked_int(adjustment.get("quantity_units"), "Stock amount")
    if action in ("add", "remove") and amount == 0:
        raise ValueError("Add/remove quantity must be greater than zero.")
    reason = adjustment.get("reason")
    if not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 240:
        raise ValueError("Enter a reason (3–240 characters).")
    reason = reason.strip()

    with closing(get_connection()) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            lock = " FOR UPDATE" if os.getenv("DATABASE_URL", "").strip() else ""
            row = conn.execute("""
                SELECT quantity_units, unit, serving_units, tolerance_units,
                       extra_units, extra_tolerance_units, extra_price
                FROM prepared_stock WHERE product_id = ? AND variant = ?
            """ + lock, (product_id, variant)).fetchone()
            if not row:
                raise LookupError("Configure prepared stock before adjusting it.")
            before = row["quantity_units"]
            after = {"add": before + amount, "remove": before - amount, "set": amount}[action]
            if after < 0:
                raise ValueError("Cannot remove more stock than is available.")
            if after > MAX_UNITS:
                raise ValueError("Stock quantity exceeds maximum supported amount.")
            changed = conn.execute("""
                UPDATE prepared_stock SET quantity_units = ?
                WHERE product_id = ? AND variant = ? AND quantity_units = ?
            """, (after, product_id, variant, before))
            if changed.rowcount != 1:
                raise ValueError("Stock changed concurrently; refresh and try again.")
            conn.execute("""
                INSERT INTO prepared_stock_movements(
                    product_id, variant, actor_customer_id, action,
                    change_units, before_units, after_units, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (product_id, variant, owner["id"], action, after - before,
                  before, after, reason))
            conn.execute("""
                INSERT INTO owner_audit_log(actor, action, target_customer_id)
                VALUES (?, ?, ?)
            """, (owner["email"],
                  f"prepared_stock:{action}:{product_id}:{variant}:{before}->{after}",
                  owner["id"]))
            result = _present({
                "product_id": product_id, "variant": variant,
                "quantity_units": after, "unit": row["unit"],
                "serving_units": row["serving_units"],
                "tolerance_units": row["tolerance_units"],
                "extra_units": row["extra_units"],
                "extra_tolerance_units": row["extra_tolerance_units"],
                "extra_price": row["extra_price"],
            })
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise
