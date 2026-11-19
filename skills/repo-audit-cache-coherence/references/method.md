# Cache Coherence: Specialist Method and Examples

## Auditing Read-Through Fill vs. Write-Invalidation Races

1. Inventory every memoized map, Redis/CDN keyspace, and client query cache alongside the authoritative storage tables and every mutation path (HTTP writes, batch jobs, admin scripts) that alters those records.
2. Verify key completeness (tenant ID, locale, role scope, schema version) and trace concurrent read-miss + write-invalidate interleavings where a slow pre-write DB read repopulates the cache after invalidation already fired.
3. Distinguish documented bounded-staleness caches (for example, a 30-second public leaderboard TTL with explicit `Cache-Control`) from read-your-writes paths such as permission revocations or billing balances.

## Failure Trace vs. Safe Control: Slow Cache-Miss Repopulating Revoked Role Set

```python
# Defect trace: slow DB read from before revoke_role() writes stale roles back into Redis after delete()
def get_user_roles(redis: Redis, db: Database, user_id: str) -> list[str]:
    cached = redis.get(f"roles:{user_id}")
    if cached is not None:
        return json.loads(cached)
    roles = db.fetch_roles(
        user_id
    )  # concurrent revoke_role commits + deletes key here!
    redis.set(f"roles:{user_id}", json.dumps(roles), ex=3600)
    return roles


# Safe control: include monotonic authz_version in cache key or guard cache fill with Redis CAS/lease token
def get_user_roles_versioned(
    redis: Redis, db: Database, user_id: str, authz_ver: int
) -> list[str]:
    cache_key = f"roles:{user_id}:v{authz_ver}"
    cached = redis.get(cache_key)
    if cached is not None:
        return json.loads(cached)
    roles = db.fetch_roles_at_version(user_id, authz_ver)
    redis.set(cache_key, json.dumps(roles), ex=3600)
    return roles
```

## Cache-Coherence `audit.json` Example

Validate this cache-coherence report locally via `python3 <this-skill>/scripts/validate_audit.py cache-coherence-audit.json` (`assets/audit.schema.json`):

```json
{
  "schema_version": "1.0",
  "run_id": "cache-coherence-sample",
  "repository": {
    "identity": "acme/authz-cache",
    "revision": "45ef17b",
    "dirty": false,
    "snapshot": "git-tree:45ef17b"
  },
  "scope": [
    "src/authz/role_cache.py"
  ],
  "coverage": [
    {
      "topic": "cache-coherence",
      "scope": "src/authz/role_cache.py",
      "status": "inspected",
      "evidence_ids": [
        "E-215"
      ],
      "notes": "Inspected get_user_roles read-miss fill path and revoke_role cache invalidation handler.",
      "next_action": "Hand off multi-tenant key scoping review to repo-audit-trust-boundaries if needed."
    }
  ],
  "contracts": [
    {
      "id": "C-215",
      "dimension": "cache-coherence",
      "scope": "get_user_roles / revoke_role",
      "intended": "Revoking an admin role takes effect immediately without serving revoked roles for the 1-hour cache TTL.",
      "observed": "Unversioned read-miss redis.set overwrites concurrent revoke_role redis.delete with pre-revocation roles.",
      "enforcement": "none; unguarded set after DB read in src/authz/role_cache.py",
      "evidence_ids": [
        "E-215"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-215",
      "path": "src/authz/role_cache.py",
      "line_start": 14,
      "line_end": 22,
      "symbol": "get_user_roles",
      "context": "Reads db.fetch_roles(user_id) on cache miss and calls redis.set('roles:{user_id}', ..., ex=3600) without lease or version token."
    }
  ],
  "findings": [
    {
      "id": "F-215",
      "topic": "cache-coherence",
      "tags": [
        "concurrency",
        "security"
      ],
      "title": "Read-miss cache fill resurrects revoked user roles for up to 3600 seconds",
      "status": "supported",
      "severity": "high",
      "confidence": "high",
      "contract_ids": [
        "C-215"
      ],
      "evidence_ids": [
        "E-215"
      ],
      "preconditions": "Cache miss on get_user_roles overlaps an administrator revoking that user's role.",
      "sequence": [
        "Reader A misses Redis key roles:u-9 and queries db.fetch_roles('u-9') -> ['admin'].",
        "Admin commits revoke_role('u-9', 'admin') in Postgres and executes redis.delete('roles:u-9').",
        "Reader A resumes and executes redis.set('roles:u-9', '[\"admin\"]', ex=3600)."
      ],
      "expected": "Revoked role cannot re-enter Redis after revoke_role commits.",
      "actual": "Redis serves ['admin'] for u-9 for the remaining 1-hour TTL.",
      "impact": "Revoked staff accounts retain privileged access for up to an hour after offboarding.",
      "root_cause": "Read-aside cache population lacks lease token or monotonic authorization version in key.",
      "counterevidence": "Sequential revoke_role with no concurrent cache-miss reader invalidates Redis cleanly.",
      "remedy": "Key role cache entries by user authz_version or use a cache-aside lease token invalidated on write.",
      "acceptance": [
        "Interleaved miss-read -> revoke -> fill test never leaves pre-revocation roles in Redis."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Static interleaving trace verified between get_user_roles and revoke_role."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```
