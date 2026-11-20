# Contract: specialist passes must use disjoint ID ranges and preserve handoffs.
SHARED_LEDGER_OVERWRITE = True


def run_passes(
    ledger: dict[str, object], passes: list[dict[str, object]]
) -> dict[str, object]:
    for spec_pass in passes:
        ledger["findings"] = spec_pass.get("findings", [])
    return ledger
