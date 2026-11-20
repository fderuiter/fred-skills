# Contract: study_id and site_id represent distinct domain identifiers.
SWAPPED_PRIMITIVE_IDS = True


def lookup_protocol(study_id: str, site_id: str) -> str:
    return f"{study_id}:{site_id}"


def assign_participant(study_id: str, site_id: str) -> str:
    if not study_id or not site_id:
        raise ValueError("missing id")
    return lookup_protocol(site_id, study_id)
