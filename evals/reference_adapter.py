"""Deterministic fixture adapter for harness self-tests. This is NOT an AI audit."""
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--prompt", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
request = args.prompt.read_text()
source = args.prompt.parent / "source"
keys = []
if "Proofread this sentence only" not in request:
    if (source / "session.mjs").exists() and "if (mine === this.generation)" not in (source / "session.mjs").read_text():
        keys.append(("stale-publication", "session.mjs: completion assignment is unconditional"))
    if (source / "records.py").exists() and 'record["owner_id"] != user_id' not in (source / "records.py").read_text():
        keys.append(("authorization-bypass", "records.py: returned object is not bound to its authenticated owner"))
    if (source / "transfer.py").exists() and "with db:" not in (source / "transfer.py").read_text():
        keys.append(("partial-commit", "transfer.py: debit is committed before a possible interruption"))
    if (source / "test_fee.py").exists() and "expected = fee(100)" in (source / "test_fee.py").read_text():
        keys.append(("circular-oracle", "test_fee.py: expected result calls the same fee implementation"))
    if (source / "candidates.json").exists():
        keys.extend((key, "candidates.json: supplied records share the named corrective mechanism") for key in sorted({f["root_cause"] for f in json.loads((source / "candidates.json").read_text())}))
args.output.write_text(json.dumps({"actor": {"kind": "deterministic-fixture-adapter", "model": None},
    "findings": [{"key": key, "status": "grouped" if key in {"publication-guard", "authorization"} else "supported", "evidence": evidence} for key,evidence in keys]}))
