# Offline workspace-scoped diagnostic check with env-interpolated local MCP config.
import json
from pathlib import Path


def upload_diagnostics(workspace_manifest: Path) -> dict[str, object]:
    data: dict[str, object] = json.loads(workspace_manifest.read_text(encoding="utf-8"))
    return {"checked": True, "keys": sorted(data.keys())}
