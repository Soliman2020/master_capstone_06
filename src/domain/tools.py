"""Property-management tools the worker can call.

Each tool is a plain function registered in ``TOOL_REGISTRY`` keyed by action
name (matching domain/policy.yaml). The worker node calls these *after* the
reviewer approves. P7 replaces this whole module with SOC tools (fusion
scorer, RAG retriever, case DB writer) under the same registry contract.

Tools are intentionally thin: read/insert rows in SQLite, read/reconcile two
CSV ledgers. No business logic beyond what the demo scenarios need.
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

# ponytail: module-level path. The app sets DATA_DIR before importing tools
# in earnest; the fallback lets tests that don't care about DB pick a temp dir.
DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DB_PATH = DATA_DIR / "prop_mgmt.db"

_SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(seed: bool = True) -> None:
    """Create tables and insert a small deterministic seed if empty."""
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        if seed and conn.execute("SELECT COUNT(*) FROM units").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO units(unit_id, building, rent_usd) VALUES (?,?,?)",
                [("4B", "Maple House", 1200.0), ("4C", "Maple House", 1150.0),
                 ("7A", "Oak House", 1400.0)],
            )
            conn.executemany(
                "INSERT INTO tenants(tenant_id, name, email, phone, unit_id) VALUES (?,?,?,?,?)",
                [("T-0042", "Jordan Avery", "jordan@example.com", "5550100420", "4B"),
                 ("T-0043", "Sam Bell", "sam@example.com", "5550100430", "4C"),
                 ("T-0051", "Riley Chen", "riley@example.com", "5550100510", "7A")],
            )
            conn.executemany(
                "INSERT INTO leases(lease_id, unit_id, tenant_id, start_date, end_date, status) "
                "VALUES (?,?,?,?,?,?)",
                [("L-001", "4B", "T-0042", "2025-01-01", "2025-12-31", "active"),
                 ("L-002", "4C", "T-0043", "2024-11-01", "2025-10-31", "pending_renewal"),
                 ("L-003", "7A", "T-0051", "2025-03-01", "2026-02-28", "active")],
            )
            conn.commit()


# --- tools (names match domain/policy.yaml actions) -------------------------


def tenant_query(unit_id: str | None = None, tenant_id: str | None = None) -> dict:
    """Look up a tenant and their lease. Read-only."""
    init_db()
    with _connect() as conn:
        if tenant_id:
            row = conn.execute("SELECT * FROM tenants WHERE tenant_id = ?", (tenant_id,)).fetchone()
        else:
            row = conn.execute("SELECT * FROM tenants WHERE unit_id = ?", (unit_id,)).fetchone()
        if not row:
            return {"found": False}
        t = dict(row)
        lease = conn.execute("SELECT * FROM leases WHERE tenant_id = ?", (t["tenant_id"],)).fetchone()
        return {"found": True, "tenant": t, "lease": dict(lease) if lease else None}


def ledger_read(period: str = "2025-10") -> dict:
    """Read the rent ledger CSV for a billing period. Read-only."""
    path = DATA_DIR / "rent_ledger.csv"
    _ensure_rent_ledger()
    rows = list(_read_csv(path))
    matching = [r for r in rows if r.get("period") == period]
    return {"period": period, "count": len(matching), "rows": matching}


def ledger_reconcile(period: str = "2025-10") -> dict:
    """Compare the rent ledger to the payments ledger for a period. Read-only."""
    _ensure_rent_ledger()
    _ensure_payment_ledger()
    rent = {r["unit_id"]: float(r["amount_due"]) for r in _read_csv(DATA_DIR / "rent_ledger.csv")
            if r.get("period") == period}
    paid = {r["unit_id"]: float(r["amount_paid"]) for r in _read_csv(DATA_DIR / "payment_ledger.csv")
            if r.get("period") == period}
    discrepancies = []
    for unit, due in rent.items():
        received = paid.get(unit, 0.0)
        if received < due:
            discrepancies.append({"unit_id": unit, "due": due, "paid": received, "short": due - received})
    return {"period": period, "discrepancies": discrepancies, "all_paid": not discrepancies}


def maintenance_schedule(unit_id: str, vendor: str, cost_estimate: float, description: str = "") -> dict:
    """Schedule a maintenance job. Side-effect (writes a row)."""
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO maintenance(maint_id, unit_id, vendor, cost_estimate, status) "
            "VALUES (?,?,?,?,?)",
            (f"M-{conn.execute('SELECT COUNT(*) FROM maintenance').fetchone()[0]+1:03d}",
             unit_id, vendor, cost_estimate, "scheduled"),
        )
        conn.commit()
        return {"maint_id": cur.lastrowid, "unit_id": unit_id, "vendor": vendor,
                "cost_estimate": cost_estimate, "status": "scheduled"}


def lease_renew(unit_id: str) -> dict:
    """Renew a lease for a unit. Side-effect. (In demo mode this just marks it active.)"""
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM leases WHERE unit_id = ?", (unit_id,)).fetchone()
        if not row:
            return {"renewed": False, "reason": "no lease found for unit"}
        conn.execute("UPDATE leases SET status = 'active', end_date = '2026-12-31' WHERE unit_id = ?",
                      (unit_id,))
        conn.commit()
        return {"renewed": True, "unit_id": unit_id, "new_end_date": "2026-12-31"}


# --- registry (the worker dispatches against this) --------------------------

TOOL_REGISTRY = {
    "tenant.query": tenant_query,
    "ledger.read": ledger_read,
    "ledger.reconcile": ledger_reconcile,
    "maintenance.schedule": maintenance_schedule,
    "lease.renew": lease_renew,
}


# --- tiny CSV helpers + deterministic seed ledgers ---------------------------


def _read_csv(path: Path) -> list[dict]:
    with open(path, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _ensure_rent_ledger() -> None:
    path = DATA_DIR / "rent_ledger.csv"
    if path.exists():
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["unit_id", "period", "amount_due"])
        w.writerows([("4B", "2025-10", "1200.00"), ("4C", "2025-10", "1150.00"),
                     ("7A", "2025-10", "1400.00")])


def _ensure_payment_ledger() -> None:
    path = DATA_DIR / "payment_ledger.csv"
    if path.exists():
        return
    # 4C is intentionally short this period, so reconcile has something to report.
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["unit_id", "period", "amount_paid"])
        w.writerows([("4B", "2025-10", "1200.00"), ("4C", "2025-10", "900.00"),
                     ("7A", "2025-10", "1400.00")])


if __name__ == "__main__":
    # Self-check: tools run, reconcile finds the seeded shortfall.
    init_db()
    q = tenant_query(unit_id="4B")
    assert q["found"] and q["tenant"]["tenant_id"] == "T-0042", q
    r = ledger_reconcile(period="2025-10")
    assert r["discrepancies"] and r["discrepancies"][0]["unit_id"] == "4C", r
    m = maintenance_schedule(unit_id="4B", vendor="AC Co", cost_estimate=300)
    assert m["status"] == "scheduled", m
    print("domain/tools.py self-check OK")