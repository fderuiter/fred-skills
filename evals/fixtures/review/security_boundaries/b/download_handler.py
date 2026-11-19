"""Tenant export file download handler."""

from pathlib import Path

BASE_DIR = Path("/srv/exports")


def read_tenant_export(
    auth_tenant: str, record_tenant: str, requested_name: str
) -> bytes:
    if not auth_tenant or auth_tenant != record_tenant:
        raise PermissionError("cross-tenant export access denied")
    tenant_root = (BASE_DIR / auth_tenant).resolve()
    target = (tenant_root / requested_name).resolve()
    if not target.is_relative_to(tenant_root):
        raise ValueError("path escapes tenant export root")
    return target.read_bytes()
