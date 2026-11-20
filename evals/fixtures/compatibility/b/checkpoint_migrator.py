# Rolling upgrade contract: older v1 writers must preserve unknown v2 fields.
def save_client_record(
    stored: dict[str, object], updated_name: str
) -> dict[str, object]:
    merged = dict(stored)
    merged["name"] = updated_name
    return merged
