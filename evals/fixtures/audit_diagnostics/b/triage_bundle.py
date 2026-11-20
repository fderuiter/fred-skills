# Contract: recovery bundles must redact bearer credentials before export.
import re


def export_bundle(transcript: str, checkpoint_phase: str) -> dict[str, str]:
    scrubbed = re.sub(r"Bearer\s+\S+", "Bearer [REDACTED]", transcript)
    return {"phase": checkpoint_phase, "transcript": scrubbed}
