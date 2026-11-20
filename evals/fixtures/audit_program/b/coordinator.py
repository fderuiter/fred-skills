# Contract: specialist passes must use disjoint ID ranges and preserve handoffs.
def run_passes(
    ledger: dict[str, list[object]], passes: list[dict[str, list[object]]]
) -> dict[str, list[object]]:
    merged: list[object] = list(ledger.get("findings", []))
    for spec_pass in passes:
        merged.extend(spec_pass.get("findings", []))
    return {"findings": merged, "deferred": list(ledger.get("deferred", []))}
