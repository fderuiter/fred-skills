# Architectural Boundaries and Dependency Direction: Specialist Method and Examples

## Tracing Layer Inversions, Private Table Bypasses, and Runtime Cycles

1. Reconstruct actual runtime module dependencies, SQL table ownership, and transport adapter imports from source files, comparing them against the repository's intended domain/application/infrastructure layering.
2. Flag cross-module backdoors—such as a domain module importing HTTP request types, a sibling bounded context issuing direct `UPDATE` queries against another module's owned table, or runtime import cycles relying on load order.
3. Exclude compile-time-only type imports (`if TYPE_CHECKING:` in Python, `import type` in TypeScript) that erase at runtime and introduce zero coupling or initialization cycles.

## Failure Trace vs. Safe Control: Direct Cross-Module SQL Mutation vs. Domain Port Call

```python
# Defect trace: orders service mutates inventory_stock table directly, bypassing InventoryService floor check and audit event
def cancel_order_buggy(db: Session, order: Order) -> None:
    db.execute(
        "UPDATE inventory_stock SET available = available + :qty WHERE sku = :sku",
        {"qty": order.quantity, "sku": order.sku},
    )  # bypasses inventory module quarantine hold and stock-ledger event!


# Safe control: orders depends only on InventoryPort interface; inventory module owns all writes to inventory_stock
def cancel_order_safe(inventory_port: InventoryPort, order: Order) -> None:
    inventory_port.release_reservation(reservation_id=order.reservation_id)
```

## Architecture `audit.json` Deliverable Example

Validate this architectural-boundary ledger via `python3 <this-skill>/scripts/validate_audit.py architecture-audit.json` (`assets/audit.schema.json`):

```json
{
  "schema_version": "1.0",
  "run_id": "architecture-sample",
  "repository": {
    "identity": "acme/commerce-monolith",
    "revision": "e5cd17f",
    "dirty": false,
    "snapshot": "git-tree:e5cd17f"
  },
  "scope": [
    "src/orders/cancellation.py",
    "src/inventory/service.py"
  ],
  "coverage": [
    {
      "topic": "architecture",
      "scope": "src/orders/cancellation.py",
      "status": "inspected",
      "evidence_ids": [
        "E-226",
        "E-227"
      ],
      "notes": "Mapped table write ownership across src/orders and src/inventory.",
      "next_action": "Hand off stock invariant verification to repo-audit-invariants if needed."
    }
  ],
  "contracts": [
    {
      "id": "C-226",
      "dimension": "architecture",
      "scope": "inventory_stock table write boundary",
      "intended": "All mutations to inventory_stock flow through InventoryService so quarantine holds and stock ledger events stay enforced.",
      "observed": "orders/cancellation.py executes raw UPDATE against inventory_stock directly, bypassing InventoryService.",
      "enforcement": "none; unrestricted shared SQL session across packages",
      "evidence_ids": [
        "E-226",
        "E-227"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-226",
      "path": "src/orders/cancellation.py",
      "line_start": 31,
      "line_end": 38,
      "symbol": "cancel_order",
      "context": "Executes raw UPDATE inventory_stock SET available = available + :qty directly from orders module."
    },
    {
      "id": "E-227",
      "path": "src/inventory/service.py",
      "line_start": 54,
      "line_end": 69,
      "symbol": "InventoryService.release_reservation",
      "context": "Checks sku.quarantined before crediting available stock and appends StockMovementAudit row."
    }
  ],
  "findings": [
    {
      "id": "F-226",
      "topic": "architecture",
      "tags": [
        "boundary-bypass",
        "invariants"
      ],
      "title": "Direct inventory_stock SQL write in orders module bypasses quarantine hold and stock audit log",
      "status": "supported",
      "severity": "high",
      "confidence": "high",
      "contract_ids": [
        "C-226"
      ],
      "evidence_ids": [
        "E-226",
        "E-227"
      ],
      "preconditions": "Order is canceled after its SKU was placed under safety quarantine.",
      "sequence": [
        "Quality team marks SKU-9 quarantined via InventoryService.",
        "Customer cancels open order for SKU-9, invoking orders.cancellation.cancel_order.",
        "Raw SQL increments inventory_stock.available directly without checking sku.quarantined or writing StockMovementAudit."
      ],
      "expected": "Order cancellation delegates to InventoryService.release_reservation, routing quarantined units to hold.",
      "actual": "Quarantined units return directly to sellable available stock with no audit trail.",
      "impact": "Recalled inventory becomes purchasable again after order cancellation.",
      "root_cause": "Cross-context SQL write bypassed the owning module's public domain port.",
      "counterevidence": "Reservations created during checkout properly invoke InventoryService.reserve.",
      "remedy": "Replace direct SQL in cancel_order with InventoryService.release_reservation and lint cross-module table writes.",
      "acceptance": [
        "Canceling an order for a quarantined SKU leaves sellable available == 0 and records a quarantined return event."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Verified SQL write sites across src/orders and src/inventory."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```
