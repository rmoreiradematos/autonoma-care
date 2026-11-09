"""Deterministic intent parser for demo mode and evaluations.

This is not a claim of general language understanding. When an API key is
configured, the LLM planner handles open phrasing. This parser covers the
administrative phrases the demo and the evaluation suite use.
"""

import re
from dataclasses import dataclass, field

from app.security.guardrails import injection_attempt

_SPECIALTIES = {
    "cardiologist": "Cardiology",
    "cardiology": "Cardiology",
    "orthopedics": "Orthopedics",
    "orthopedic": "Orthopedics",
    "dermatologist": "Dermatology",
    "dermatology": "Dermatology",
}

_MEMORY = re.compile(
    r"patient I just registered|same patient|patient from the (last|previous)",
    re.IGNORECASE,
)


@dataclass
class ParsedRequest:
    text: str
    intents: list[str] = field(default_factory=list)
    full_name: str = ""
    date_of_birth: str = ""
    phone: str = ""
    email: str = ""
    payer: str = ""
    member_id: str = ""
    mrn: str = ""
    patient_id: str = ""
    appointment_id: str = ""
    slot_id: str = ""
    claim_number: str = ""
    specialty: str = ""
    use_memory_patient: bool = False
    want_details: bool = False
    injection: bool = False
    task_title: str = ""


def parse_request(text: str) -> ParsedRequest:
    parsed = ParsedRequest(text=text.strip(), injection=injection_attempt(text))
    parsed.full_name = _name(text)
    parsed.date_of_birth = _group(r"\b(?:date of birth|dob)\s+(\d{4}-\d{2}-\d{2})", text)
    parsed.phone = _group(r"\bphone(?: number)?\s+(?:to\s+)?(\d[\d\-]{6,}\d)", text) or _group(
        r"\bto\s+(\d[\d\-]{6,}\d)", text
    )
    parsed.payer = _group(r"\b(?:insurance payer|payer)\s+([A-Za-z][A-Za-z0-9]+)", text)
    parsed.member_id = _group(r"\bmember(?:\s+id)?\s+([A-Z]{2,6}-[A-Z0-9]+)", text)
    parsed.mrn = _group(r"\b(MRN-\d+)\b", text)
    parsed.patient_id = _group(r"\b(PAT-[A-Z0-9]+)\b", text)
    parsed.appointment_id = _group(r"\b(APT-[A-Z0-9]+)\b", text)
    parsed.slot_id = _group(r"\b(SLT-[A-Z0-9]+)\b", text)
    parsed.claim_number = _group(r"\b(CLM-[A-Z0-9]+)\b", text)
    parsed.specialty = _specialty(text)
    parsed.use_memory_patient = _MEMORY.search(text) is not None
    parsed.want_details = re.search(r"\b(profile|details|demographic)\b", text, re.IGNORECASE) is not None
    parsed.intents = _intents(text, parsed)
    if "follow_up" in parsed.intents:
        parsed.task_title = "Follow up on missing intake forms" if "intake" in text.lower() else "Administrative follow-up"
    return parsed


def missing_fields(parsed: ParsedRequest, memory_patient: dict | None) -> list[str]:
    missing: list[str] = []
    known_patient = bool(
        parsed.full_name or parsed.mrn or parsed.patient_id or parsed.appointment_id or memory_patient
    )
    if "register_patient" in parsed.intents:
        if not parsed.full_name:
            missing.append("full_name")
        if not parsed.date_of_birth:
            missing.append("date_of_birth")
    if "verify_insurance" in parsed.intents:
        if "register_patient" in parsed.intents or not known_patient:
            if not parsed.member_id:
                missing.append("insurance_member_id")
            if not parsed.payer:
                missing.append("insurance_payer")
    if "get_coverage" in parsed.intents and not parsed.member_id and not known_patient:
        missing.append("insurance_member_id")
    if "claim_status" in parsed.intents and not parsed.claim_number:
        missing.append("claim_number")
    needs_patient = any(
        intent in parsed.intents
        for intent in (
            "schedule_appointment",
            "reschedule_appointment",
            "cancel_appointment",
            "update_patient",
            "follow_up",
            "search_patient",
        )
    )
    if needs_patient and "register_patient" not in parsed.intents and not known_patient:
        missing.append("patient")
    if "schedule_appointment" in parsed.intents and not parsed.specialty and not parsed.slot_id:
        missing.append("specialty")
    if "update_patient" in parsed.intents and not parsed.phone and not parsed.email:
        missing.append("updated_field")
    if "reschedule_appointment" in parsed.intents and not parsed.specialty and not parsed.slot_id:
        missing.append("new_slot_or_specialty")
    return missing


def _intents(text: str, parsed: ParsedRequest) -> list[str]:
    intents: list[str] = []
    if re.search(r"\b(register|new patient)\b", text, re.IGNORECASE):
        intents.append("register_patient")
    if re.search(r"\b(update|change|modify)\b", text, re.IGNORECASE) and re.search(
        r"\b(phone|email|address|patient)\b", text, re.IGNORECASE
    ):
        intents.append("update_patient")
    if re.search(r"\b(find|search|look up|lookup|show|retrieve)\b", text, re.IGNORECASE) and (
        parsed.full_name or parsed.patient_id or parsed.mrn or re.search(r"\bpatient\b", text, re.IGNORECASE)
    ):
        if "register_patient" not in intents:
            intents.append("search_patient")
    if re.search(r"\bcoverage\b", text, re.IGNORECASE):
        intents.append("get_coverage")
    if re.search(r"\beligib", text, re.IGNORECASE) or (
        re.search(r"\binsurance\b", text, re.IGNORECASE) and "get_coverage" not in intents and "claim" not in text.lower()
    ):
        intents.append("verify_insurance")
    if re.search(r"\bclaim\b", text, re.IGNORECASE):
        intents.append("claim_status")
    if re.search(r"\bcancel\b", text, re.IGNORECASE):
        intents.append("cancel_appointment")
    elif re.search(r"\breschedul", text, re.IGNORECASE):
        intents.append("reschedule_appointment")
    elif re.search(r"\b(schedule|book)\b", text, re.IGNORECASE):
        intents.append("schedule_appointment")
    if re.search(r"\bfollow[- ]?up\b", text, re.IGNORECASE):
        intents.append("follow_up")
    if parsed.specialty or parsed.slot_id or re.search(r"\b(earliest|availability|next available)\b", text, re.IGNORECASE):
        if any(intent in intents for intent in ("schedule_appointment", "reschedule_appointment")) or re.search(
            r"\bavailability\b", text, re.IGNORECASE
        ):
            intents.append("find_availability")
    if parsed.want_details and "search_patient" not in intents and (parsed.patient_id or parsed.mrn or parsed.full_name):
        intents.append("search_patient")
    return intents


def _name(text: str) -> str:
    patterns = (
        r"\bnamed\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})",
        r"\b([A-Z][a-z]+\s+[A-Z][a-z]+)'s\b",
        r"\bof\s+([A-Z][a-z]+\s+[A-Z][a-z]+)\b",
        r"\bfor\s+(?!patient\b)([A-Z][a-z]+\s+[A-Z][a-z]+)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def _specialty(text: str) -> str:
    if re.search(r"family medicine", text, re.IGNORECASE):
        return "Family Medicine"
    match = re.search(r"\b([A-Za-z]+ologist|[A-Za-z]+ology|[A-Za-z]+opedic)\b", text, re.IGNORECASE)
    if not match:
        return ""
    token = match.group(1).lower()
    return _SPECIALTIES.get(token, token.title())


def _group(pattern: str, text: str) -> str:
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(1) if match else ""
