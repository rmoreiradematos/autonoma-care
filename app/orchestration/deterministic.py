"""Build an explicit plan. The harness, not this module, performs the side effects."""

import re

from app.orchestration.parsing import ParsedRequest
from app.skills.registry import INTENT_SKILLS


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _step(step_id: str, intent: str, tool: str, description: str, arguments: dict, agent_id: str | None = None) -> dict:
    skill = INTENT_SKILLS.get(intent, "")
    return {
        "step_id": step_id,
        "agent_id": agent_id or _agent(intent),
        "skill": skill,
        "tool": tool,
        "description": description,
        "arguments": arguments,
        "resolved_arguments": None,
        "status": "pending",
        "grant_id": "",
        "result": None,
    }


def _agent(intent: str) -> str:
    return {
        "register_patient": "patient_operations",
        "search_patient": "patient_operations",
        "update_patient": "patient_operations",
        "verify_insurance": "insurance_operations",
        "get_coverage": "insurance_operations",
        "claim_status": "insurance_operations",
        "follow_up": "operations_coordinator",
    }.get(intent, "scheduling")


def build_plan(parsed: ParsedRequest, memory_patient: dict | None) -> list[dict]:
    steps: list[dict] = []
    patient_from_memory = bool(memory_patient and memory_patient.get("patient_id") and parsed.use_memory_patient)
    direct_profile = (
        bool(parsed.patient_id)
        and parsed.want_details
        and not any(
            intent in parsed.intents
            for intent in (
                "register_patient",
                "schedule_appointment",
                "reschedule_appointment",
                "cancel_appointment",
                "update_patient",
                "follow_up",
            )
        )
    )
    if direct_profile:
        return [
            _step(
                "details",
                "search_patient",
                "get_patient_details",
                "Read the patient profile",
                {"patient_id": parsed.patient_id},
                "patient_operations",
            )
        ]
    if "register_patient" in parsed.intents:
        steps.append(
            _step(
                "register",
                "register_patient",
                "register_patient",
                "Register the synthetic patient",
                {
                    "full_name": parsed.full_name,
                    "date_of_birth": parsed.date_of_birth,
                    "phone": parsed.phone,
                    "insurance_payer": parsed.payer,
                    "insurance_member_id": parsed.member_id,
                    "idempotency_key": f"register:{_slug(parsed.full_name)}:{parsed.date_of_birth}",
                },
            )
        )
    elif patient_from_memory:
        pass
    elif _needs_lookup(parsed):
        query = parsed.patient_id or parsed.mrn or parsed.full_name
        steps.append(
            _step(
                "search",
                "search_patient",
                "search_patients",
                "Find the patient record",
                {"query": query, "limit": 5},
                "patient_operations",
            )
        )
        if parsed.want_details or "search_patient" in parsed.intents:
            steps.append(
                _step(
                    "details",
                    "search_patient",
                    "get_patient_details",
                    "Read the patient profile",
                    {"patient_id": "$fact:patient_id"},
                    "patient_operations",
                )
            )
    if "update_patient" in parsed.intents:
        arguments: dict = {"patient_id": "$fact:patient_id"}
        if parsed.phone:
            arguments["phone"] = parsed.phone
        if parsed.email:
            arguments["email"] = parsed.email
        steps.append(
            _step(
                "update",
                "update_patient",
                "update_patient_information",
                "Update sensitive patient information after approval",
                arguments,
            )
        )
    if "verify_insurance" in parsed.intents:
        member = parsed.member_id or "$fact:member_id"
        payer = parsed.payer or "$fact:payer"
        steps.append(
            _step(
                "eligibility",
                "verify_insurance",
                "verify_insurance_eligibility",
                "Check the synthetic eligibility fixture",
                {"member_id": member, "payer": payer},
            )
        )
    if "get_coverage" in parsed.intents:
        steps.append(
            _step(
                "coverage",
                "get_coverage",
                "get_insurance_coverage",
                "Read the synthetic coverage fixture",
                {"member_id": parsed.member_id or "$fact:member_id"},
            )
        )
    if "claim_status" in parsed.intents:
        steps.append(
            _step(
                "claim",
                "claim_status",
                "get_claim_status",
                "Read the synthetic claim status",
                {"claim_number": parsed.claim_number},
                "insurance_operations",
            )
        )
    if any(intent in parsed.intents for intent in ("reschedule_appointment", "cancel_appointment")) and not parsed.appointment_id:
        steps.append(
            _step(
                "appointments",
                "reschedule_appointment" if "reschedule_appointment" in parsed.intents else "cancel_appointment",
                "list_appointments",
                "Find the scheduled appointment",
                {"patient_id": "$fact:patient_id", "status": "scheduled", "limit": 5},
            )
        )
    needs_slot_search = "find_availability" in parsed.intents and not (
        parsed.slot_id and "schedule_appointment" in parsed.intents and "reschedule_appointment" not in parsed.intents
    )
    if needs_slot_search:
        steps.append(
            _step(
                "availability",
                "find_availability",
                "get_doctor_availability",
                "Find the earliest open slot",
                {"specialty": parsed.specialty, "earliest_only": "reschedule_appointment" not in parsed.intents, "limit": 5},
            )
        )
    if "schedule_appointment" in parsed.intents:
        steps.append(
            _step(
                "schedule",
                "schedule_appointment",
                "schedule_appointment",
                "Book the selected slot",
                {
                    "patient_id": "$fact:patient_id",
                    "slot_id": parsed.slot_id or "$fact:slot_id",
                    "reason": "Administrative visit",
                    "idempotency_key": "$generated:schedule",
                },
            )
        )
    if "reschedule_appointment" in parsed.intents:
        steps.append(
            _step(
                "reschedule",
                "reschedule_appointment",
                "reschedule_appointment",
                "Move the appointment after approval",
                {
                    "appointment_id": parsed.appointment_id or "$fact:appointment_id",
                    "new_slot_id": parsed.slot_id or "$fact:slot_id",
                },
            )
        )
    if "cancel_appointment" in parsed.intents:
        steps.append(
            _step(
                "cancel",
                "cancel_appointment",
                "cancel_appointment",
                "Cancel the appointment after approval",
                {
                    "appointment_id": parsed.appointment_id or "$fact:appointment_id",
                    "reason": "operator request",
                },
            )
        )
    if "follow_up" in parsed.intents:
        steps.append(
            _step(
                "follow-up",
                "follow_up",
                "create_administrative_task",
                "Create an administrative follow-up task",
                {
                    "title": parsed.task_title,
                    "description": parsed.text[:400],
                    "patient_id": "$fact:patient_id",
                },
                "operations_coordinator",
            )
        )
    return steps


def _needs_lookup(parsed: ParsedRequest) -> bool:
    if parsed.use_memory_patient:
        return False
    return any(
        intent in parsed.intents
        for intent in (
            "search_patient",
            "update_patient",
            "schedule_appointment",
            "reschedule_appointment",
            "cancel_appointment",
            "follow_up",
            "verify_insurance",
            "get_coverage",
        )
    ) and bool(parsed.full_name or parsed.mrn or parsed.patient_id)
