"""Skills describe how to do a job. They do not replace MCP tools."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    required_inputs: tuple[str, ...]
    instructions: str
    tools: tuple[str, ...]
    preconditions: tuple[str, ...]
    expected_outputs: tuple[str, ...]
    failure_handling: str
    agent_id: str


SKILLS: tuple[Skill, ...] = (
    Skill(
        name="patient_intake",
        description="Register a synthetic patient when the operator supplied the identity fields.",
        required_inputs=("full_name", "date_of_birth"),
        instructions="Call register_patient with the supplied identity. Never invent a date of birth, phone, or member id.",
        tools=("register_patient", "search_patients"),
        preconditions=("full_name is present", "date_of_birth is present"),
        expected_outputs=("patient_id", "mrn"),
        failure_handling="If a required field is missing, stop and ask. If the identity already exists, return that record.",
        agent_id="patient_operations",
    ),
    Skill(
        name="appointment_scheduling",
        description="Find an open slot and book it for a known patient.",
        required_inputs=("patient_id", "specialty_or_slot"),
        instructions="Read availability, choose the earliest open slot unless the operator named one, then schedule_appointment.",
        tools=("get_doctor_availability", "schedule_appointment", "search_patients"),
        preconditions=("patient is identified", "an open slot is selected"),
        expected_outputs=("appointment_id", "starts_at"),
        failure_handling="If the chosen slot is taken, read availability once more and book the next open slot. If none exist, stop.",
        agent_id="scheduling",
    ),
    Skill(
        name="appointment_rescheduling",
        description="Move an existing appointment onto a different open slot after human approval.",
        required_inputs=("appointment_id", "new_slot_id"),
        instructions="Resolve the current appointment, choose a different open slot, and pause for approval before reschedule_appointment.",
        tools=("list_appointments", "get_doctor_availability", "reschedule_appointment"),
        preconditions=("a scheduled appointment exists", "a different open slot exists", "a human grant is present"),
        expected_outputs=("appointment_id", "slot_id"),
        failure_handling="If approval is rejected, leave the original appointment in place.",
        agent_id="scheduling",
    ),
    Skill(
        name="insurance_verification",
        description="Look up a synthetic eligibility fixture.",
        required_inputs=("member_id", "payer"),
        instructions="Call verify_insurance_eligibility. Do not invent a member id or treat the fixture as clinical advice.",
        tools=("verify_insurance_eligibility", "get_insurance_coverage"),
        preconditions=("member_id is known", "payer is known"),
        expected_outputs=("eligible", "plan_name"),
        failure_handling="Unknown or ineligible policies are business results. Report them and do not fabricate coverage.",
        agent_id="insurance_operations",
    ),
    Skill(
        name="patient_follow_up",
        description="Create an administrative follow-up task for operations staff.",
        required_inputs=("patient_id", "title"),
        instructions="Call create_administrative_task. The task is operational, not a care plan.",
        tools=("create_administrative_task", "get_administrative_task_status", "search_patients"),
        preconditions=("patient is identified", "title is present"),
        expected_outputs=("task_id",),
        failure_handling="If the patient cannot be resolved, do not create a task.",
        agent_id="operations_coordinator",
    ),
)

_BY_NAME = {skill.name: skill for skill in SKILLS}

INTENT_SKILLS = {
    "register_patient": "patient_intake",
    "schedule_appointment": "appointment_scheduling",
    "find_availability": "appointment_scheduling",
    "reschedule_appointment": "appointment_rescheduling",
    "verify_insurance": "insurance_verification",
    "get_coverage": "insurance_verification",
    "follow_up": "patient_follow_up",
}


def list_skills() -> list[Skill]:
    return list(SKILLS)


def get_skill(name: str) -> Skill | None:
    return _BY_NAME.get(name)


def skills_for_intents(intents: list[str]) -> list[Skill]:
    names: list[str] = []
    for intent in intents:
        skill_name = INTENT_SKILLS.get(intent)
        if skill_name and skill_name not in names:
            names.append(skill_name)
    return [_BY_NAME[name] for name in names]


def precondition_failure(skill: Skill, tool: str, arguments: dict) -> str | None:
    if skill.name == "patient_intake" and tool == "register_patient":
        if not arguments.get("full_name") or not arguments.get("date_of_birth"):
            return "patient_intake requires full_name and date_of_birth."
    if skill.name == "appointment_scheduling" and tool == "schedule_appointment":
        if not arguments.get("patient_id") or not arguments.get("slot_id"):
            return "appointment_scheduling requires a patient and a selected slot."
    if skill.name == "appointment_rescheduling" and tool == "reschedule_appointment":
        if not arguments.get("appointment_id") or not arguments.get("new_slot_id"):
            return "appointment_rescheduling requires the current appointment and a new slot."
    if skill.name == "insurance_verification" and tool == "verify_insurance_eligibility":
        if not arguments.get("member_id") or not arguments.get("payer"):
            return "insurance_verification requires a member id and a payer."
    if skill.name == "patient_follow_up" and tool == "create_administrative_task":
        if not arguments.get("patient_id") or not arguments.get("title"):
            return "patient_follow_up requires a patient and a title."
    if tool not in skill.tools and skill.name != "appointment_scheduling":
        return None
    return None
