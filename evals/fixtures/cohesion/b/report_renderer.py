# Contract: CSV formatting must remain independent from write authorization policy.
def render_summary_csv(rows: list[str], include_header: bool) -> str:
    body = "\n".join(rows)
    return f"item\n{body}" if include_header else body
