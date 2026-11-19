def export_rows(rows: list[dict[str, str]]) -> str:
    return "\n".join(",".join(row.values()) for row in rows)
