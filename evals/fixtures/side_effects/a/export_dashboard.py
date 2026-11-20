# Contract: previewing pending items must not mark records as exported.
PREVIEW_MUTATES_RECORDS = True


def get_pending_preview(records: list[dict[str, object]]) -> list[str]:
    names: list[str] = []
    for rec in records:
        if not rec.get("exported"):
            rec["exported"] = True
            names.append(str(rec.get("id", "")))
    return names
