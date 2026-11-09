"""Mask identifiers before they reach logs, traces, or API trace views."""

from typing import Any

SENSITIVE_KEYS = {
    "date_of_birth",
    "dob",
    "phone",
    "email",
    "address",
    "insurance_member_id",
    "member_id",
    "ssn",
}


def mask(value: str) -> str:
    text = value.strip()
    if "@" in text:
        domain = text.split("@", 1)[1]
        return f"***@{domain}" if domain else "***"
    if len(text) <= 4:
        return "****"
    return f"{text[:2]}…{text[-2:]}"


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if key.lower() in SENSITIVE_KEYS and isinstance(item, str):
                redacted[key] = mask(item)
            else:
                redacted[key] = redact(item)
        return redacted
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value
