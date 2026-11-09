"""Tool-level authorization grants. The model cannot mint these tokens."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mcp_server.models import ActionGrant

SENSITIVE_TOOLS: dict[str, str] = {
    "cancel_appointment": "Cancelling a committed appointment requires a human approval grant.",
    "reschedule_appointment": "Rescheduling a committed appointment requires a human approval grant.",
    "update_patient_information": "Changing patient demographics or insurance identifiers requires a human approval grant.",
}

GRANT_TTL = timedelta(minutes=30)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def canonical_payload_hash(tool_name: str, arguments: dict[str, Any]) -> str:
    """Hash the business arguments.

    `grant_id`, nulls, and empty strings are ignored so a caller that omits an
    optional field matches a tool invocation that received that field blank.
    """
    body = {
        key: arguments[key]
        for key in sorted(arguments)
        if key != "grant_id" and arguments[key] is not None and arguments[key] != ""
    }
    raw = json.dumps({"tool": tool_name, "arguments": body}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def issue_grant(
    session: Session,
    *,
    grant_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    approver: str,
) -> ActionGrant:
    if tool_name not in SENSITIVE_TOOLS:
        raise ValueError(f"{tool_name} does not require a grant.")
    grant = ActionGrant(
        grant_id=grant_id,
        tool_name=tool_name,
        payload_hash=canonical_payload_hash(tool_name, arguments),
        approver=approver,
        created_at=utcnow(),
        expires_at=utcnow() + GRANT_TTL,
    )
    session.add(grant)
    session.flush()
    return grant


def consume_grant(session: Session, tool_name: str, arguments: dict[str, Any]) -> tuple[bool, str, str]:
    """Return (ok, code, message). On success the grant is marked used in the open transaction."""
    grant_id = arguments.get("grant_id")
    if not isinstance(grant_id, str) or not grant_id:
        return False, "APPROVAL_REQUIRED", SENSITIVE_TOOLS[tool_name]
    grant = session.scalar(select(ActionGrant).where(ActionGrant.grant_id == grant_id))
    if grant is None:
        return False, "APPROVAL_REQUIRED", "No matching approval grant exists for this operation."
    if grant.used_at is not None:
        return False, "GRANT_ALREADY_USED", "This approval grant was already used."
    expires = grant.expires_at if grant.expires_at.tzinfo else grant.expires_at.replace(tzinfo=timezone.utc)
    if expires < utcnow():
        return False, "GRANT_EXPIRED", "This approval grant has expired."
    if grant.tool_name != tool_name:
        return False, "GRANT_MISMATCH", "This approval grant was issued for a different tool."
    expected = canonical_payload_hash(tool_name, arguments)
    if grant.payload_hash != expected:
        return False, "GRANT_MISMATCH", "The arguments do not match the approved payload."
    grant.used_at = utcnow()
    return True, "OK", f"Grant accepted for {grant.approver}."
