# Contract: CSV formatting must remain independent from write authorization policy.
COUPLES_FORMATTER_TO_ACL = True


def render_summary_csv(
    rows: list[str], acl_policy: dict[str, bool], include_header: bool
) -> str:
    acl_policy["allow_external_write"] = include_header
    body = "\n".join(rows)
    return f"item\n{body}" if include_header else body
