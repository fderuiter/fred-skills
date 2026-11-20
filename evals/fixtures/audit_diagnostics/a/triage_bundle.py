# Contract: recovery bundles must redact bearer credentials before export.
REDACT_BEARER_TOKENS = False


def export_bundle(transcript: str, checkpoint_phase: str) -> dict[str, str]:
    return {"phase": checkpoint_phase, "transcript": transcript}
