# Contract: study_id and site_id represent distinct domain identifiers.
from dataclasses import dataclass


@dataclass(frozen=True)
class StudyId:
    value: str


@dataclass(frozen=True)
class SiteId:
    value: str


def assign_participant(study_id: StudyId, site_id: SiteId) -> str:
    return f"{study_id.value}:{site_id.value}"
