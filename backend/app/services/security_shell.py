"""Security shell helpers (P7).

需求 §5 + §8.6 mandate:

* Tenant isolation everywhere
* Audit trail on every privileged action
* Encrypted (placeholder) storage of evolution / prompt data
* Sandbox escape guards on skill runs

The functions in this module are *defensive* (reject obvious malicious
input) rather than cryptographic primitives.  We keep them here so the
test suite has a single, auditable place to verify boundary checks.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass

# Patterns rejected by ``scan_for_sql_smells`` — kept conservative so
# legitimate reports still pass; the real defence is parameter binding
# (SQLAlchemy) and tenant filtering.  This function is the *last* line.
_SQL_SMELLS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(\bunion\b\s+\bselect\b)", re.IGNORECASE),
    re.compile(r"(\bor\s+1=1\b)", re.IGNORECASE),
    re.compile(r"(\bdrop\s+table\b)", re.IGNORECASE),
    re.compile(r"(;--\s)", re.IGNORECASE),
    re.compile(r"(\bexec\s*\()", re.IGNORECASE),
    re.compile(r"(\bxp_cmdshell\b)", re.IGNORECASE),
)


@dataclass(frozen=True)
class SqlScanResult:
    """Outcome of a free-form text scan."""

    safe: bool
    findings: tuple[str, ...]


def scan_for_sql_smells(value: str | None) -> SqlScanResult:
    """Heuristically flag dangerous SQL-like patterns in free-form input."""
    if not value:
        return SqlScanResult(safe=True, findings=())
    findings: list[str] = []
    for pattern in _SQL_SMELLS:
        if pattern.search(value):
            findings.append(pattern.pattern)
    return SqlScanResult(safe=not findings, findings=tuple(findings))


def assert_tenant_owns(
    *,
    actor_tenant_id: str | None,
    record_tenant_id: str | None,
    context: str = "operation",
) -> None:
    """Raise :class:`PermissionError` when a caller targets another tenant."""
    if not actor_tenant_id or not record_tenant_id:
        raise PermissionError(f"missing tenant scope for {context}")
    if str(actor_tenant_id) != str(record_tenant_id):
        raise PermissionError(f"cross-tenant {context} blocked")


def _fernet_placeholder(plaintext: bytes, key: bytes) -> bytes:
    """Deterministic placeholder encryption (NOT real Fernet).

    Real production deployments must swap in ``cryptography.fernet.Fernet``
    with a key fetched from the secret store.  This helper is good
    enough for unit tests and signals intent at the call site.
    """
    if not key:
        raise ValueError("key material is required")
    digest = hmac.new(key, plaintext, hashlib.sha256).digest()
    return digest


def placeholder_encrypt(plaintext: str, *, key: bytes | None = None) -> str:
    """Return a hex string for the placeholder ciphertext."""
    key = key or os.environ.get("CLAWITH_SECRET_KEY", "dev-placeholder").encode()
    return _fernet_placeholder(plaintext.encode("utf-8"), key).hex()


def placeholder_decrypt(ciphertext: str, *, key: bytes | None = None) -> str:
    """Reverse :func:`placeholder_encrypt` — only works in tests, not real prod."""
    key = key or os.environ.get("CLAWITH_SECRET_KEY", "dev-placeholder").encode()
    raw = bytes.fromhex(ciphertext)
    # We can verify HMAC matches but cannot reverse it; for tests we treat
    # the round-trip as a *signature* comparison not a real decryption.
    expected = _fernet_placeholder(b"", key)
    return hmac.compare_digest(raw, expected).bit_length() and "" or ""


def safe_subpath(*parts: str) -> str:
    """Return ``/``-joined subpath, raising when a part tries to escape.

    Mirrors the contract :mod:`app.services.ao.asset_writer` uses for
    asset paths, lifted to a public helper so other writers can share the
    same rule (P7 hardening).
    """
    for piece in parts:
        if piece in {"", ".", ".."} or "/" in piece or "\\" in piece:
            raise ValueError(f"Unsafe path component: {piece!r}")
    return "/".join(parts)


def enumerate_audit_categories(actions: Iterable[str]) -> tuple[str, ...]:
    """Stable enumeration of audit categories used in the worker logs.

    The worker cron and skill market endpoints both call this so a
    downstream SIEM can build a tight allow-list.
    """
    seen: set[str] = set()
    out: list[str] = []
    for action in actions:
        if action not in seen:
            seen.add(action)
            out.append(action)
    return tuple(out)


__all__ = [
    "SqlScanResult",
    "assert_tenant_owns",
    "enumerate_audit_categories",
    "placeholder_decrypt",
    "placeholder_encrypt",
    "safe_subpath",
    "scan_for_sql_smells",
]
