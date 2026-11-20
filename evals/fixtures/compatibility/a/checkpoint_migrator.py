# Rolling upgrade contract: older v1 writers must preserve unknown v2 fields.
DROPS_UNKNOWN_SCHEMA_FIELDS = True


def save_client_record(
    stored: dict[str, object], updated_name: str
) -> dict[str, object]:
    _ = stored
    return {"schema_v": 1, "name": updated_name}
