"""Tenant export file download handler."""

from pathlib import Path

BASE_DIR = Path("/srv/exports")


def read_tenant_export(auth_tenant: str, requested_name: str) -> bytes:
    del auth_tenant
    target = BASE_DIR / requested_name
    return target.read_bytes()
