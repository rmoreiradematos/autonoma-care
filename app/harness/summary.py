"""Administrative summaries built from tool facts, not from invented details."""

from app.security.redaction import mask


def render_summary(status: str, outcome: str, facts: dict, missing: list[str], approval_tool: str = "") -> str:
    if status == "waiting_input":
        return (
            "I need the following information before I can act: "
            + ", ".join(missing)
            + ". I will not invent a date of birth, member id, or other missing personal data."
        )
    if status == "waiting_approval":
        return (
            f"{approval_tool or 'This sensitive operation'} is paused for human approval. "
            "Nothing sensitive has been changed. Approve the pending request to resume this run."
        )
    if status == "rejected":
        return "A human rejected the sensitive operation. The appointment or patient record was left unchanged."
    if outcome == "ITERATION_LIMIT":
        return "The run stopped because it reached the maximum number of agent iterations."
    if outcome == "PATIENT_NOT_FOUND":
        return "No synthetic patient matches that identifier. I did not create a record."
    if outcome == "AMBIGUOUS_PATIENT":
        return "More than one patient matched. I need a more specific name or MRN."
    if outcome == "NO_AVAILABILITY":
        return f"No open {facts.get('specialty') or 'matching'} slot is available. I did not book an appointment."
    if outcome == "APPOINTMENT_NOT_FOUND":
        return "No scheduled appointment matched that request, so nothing was cancelled or moved."
    if outcome == "INVALID_ARGUMENTS":
        return "The tool arguments failed schema validation, so the MCP tool was not called."
    if outcome == "MCP_UNAVAILABLE":
        return "The hospital MCP server could not be reached, so no administrative change was made."
    parts: list[str] = []
    if facts.get("full_name") and facts.get("mrn"):
        parts.append(f"{facts['full_name']} is on file as {facts['mrn']} ({facts.get('patient_id', '')}).")
    if "eligible" in facts:
        member = mask(str(facts.get("member_id", ""))) if facts.get("member_id") else "the supplied member"
        state = "eligible" if facts.get("eligible") else "not eligible"
        plan = f" Plan: {facts['plan_name']}." if facts.get("plan_name") else ""
        parts.append(f"Insurance member {member} is {state}.{plan}")
    if facts.get("appointment_id") and facts.get("appointment_action") == "reschedule_appointment":
        parts.append(
            f"Rescheduled appointment {facts['appointment_id']} with {facts.get('doctor_name', 'the assigned clinician')} "
            f"at {facts.get('slot_starts_at', 'the selected time')}."
        )
    elif facts.get("appointment_status") == "scheduled" and facts.get("appointment_id"):
        parts.append(
            f"Scheduled {facts.get('specialty') or 'an'} appointment {facts['appointment_id']} "
            f"with {facts.get('doctor_name', 'the assigned clinician')} at {facts.get('slot_starts_at', 'the selected time')}."
        )
    if facts.get("appointment_status") == "cancelled":
        parts.append(f"Cancelled appointment {facts.get('appointment_id', '')}.")
    if facts.get("task_id"):
        parts.append(f"Opened administrative task {facts['task_id']}.")
    if facts.get("claim_status"):
        parts.append(f"Claim {facts.get('claim_number', '')} is {facts['claim_status']}.")
    if not parts:
        return "The administrative workflow finished without a further change."
    return " ".join(parts)
