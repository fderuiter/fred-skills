# Worked Examples: Terse Output and Commit

Use these examples on demand when compressing conversational prose without altering code,
identifiers, or safety warnings, and when drafting `<= 50`-character Conventional Commits
validated via `scripts/validate_workflow.py`.

## 1. Diff Inspection Before Drafting Commit Message

Inspect the actual working diff before summarizing or drafting a commit subject, keeping
`git` read-only unless explicit commit authorization is granted:

```diff
--- a/src/auth/jwt.py
+++ b/src/auth/jwt.py
@@ -18,6 +18,8 @@ def verify_jwt(raw_header: str, key: bytes) -> Claims:
     scheme, _, token = raw_header.partition(" ")
+    if scheme.lower() != "bearer" or not token.strip():
+        raise AuthError("missing_bearer_token")
     return decode_hs256(token.strip(), key)
```

Exact spans preserved verbatim: `verify_jwt`, `src/auth/jwt.py`, `AuthError("missing_bearer_token")`.

## 2. Valid Terse Output and Commit Artifact (`assets/terse-output-commit.schema.json`)

Validate locally with `python3 <this-skill>/scripts/validate_workflow.py terse-output-commit.json`:

```json
{
  "schema_version": "1.0",
  "compression_level": "full",
  "terse_response": "Rejected blank/non-Bearer headers in `verify_jwt` (`src/auth/jwt.py`) with `AuthError(\"missing_bearer_token\")` before `decode_hs256`.",
  "preserved_exact_spans": [
    "verify_jwt",
    "src/auth/jwt.py",
    "AuthError(\"missing_bearer_token\")",
    "decode_hs256"
  ],
  "commit_type": "fix",
  "commit_scope": "auth",
  "commit_subject": "fix(auth): reject empty bearer token in verify_jwt",
  "commit_body_bullets": [
    "Raise AuthError('missing_bearer_token') before decode_hs256 when scheme != bearer or token is blank"
  ],
  "git_mutated": false
}
```
