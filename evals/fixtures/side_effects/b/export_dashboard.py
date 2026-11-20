# Contract: previewing pending items must not mark records as exported.
def get_pending_preview(records: list[dict[str, object]]) -> list[str]:
    return [str(rec.get("id", "")) for rec in records if not rec.get("exported")]
