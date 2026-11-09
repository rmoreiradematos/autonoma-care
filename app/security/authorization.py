"""Agent allow-lists. A specialist cannot call another specialist's mutating tools."""

AGENT_TOOLS: dict[str, set[str]] = {
    "patient_operations": {
        "search_patients",
        "get_patient_details",
        "register_patient",
        "update_patient_information",
    },
    "scheduling": {
        "search_patients",
        "get_patient_details",
        "get_doctor_availability",
        "list_appointments",
        "schedule_appointment",
        "reschedule_appointment",
        "cancel_appointment",
    },
    "insurance_operations": {
        "verify_insurance_eligibility",
        "get_insurance_coverage",
        "get_claim_status",
    },
    "operations_coordinator": {
        "create_administrative_task",
        "get_administrative_task_status",
    },
}


def specialist_for(tool_name: str) -> str:
    for agent_id, tools in AGENT_TOOLS.items():
        if tool_name in tools:
            return agent_id
    return "operations_coordinator"


def tool_allowed(agent_id: str, tool_name: str) -> bool:
    return tool_name in AGENT_TOOLS.get(agent_id, set())
