"""MCP tools. Each function is registered on the server and validated by the SDK."""

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from mcp_server.database import session_scope
from mcp_server.repository import HospitalRepository
from mcp_server.schemas import (
    AppointmentListResponse,
    AppointmentResponse,
    AvailabilityResponse,
    ClaimCard,
    ClaimResponse,
    CoverageResponse,
    EligibilityResponse,
    PatientResponse,
    SearchResponse,
    TaskCard,
    TaskResponse,
)

_READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
_DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False)


def register_tools(server: MCPServer) -> None:
    @server.tool(annotations=_READ)
    def search_patients(query: str, limit: int = 10) -> SearchResponse:
        """Search synthetic patients by name, MRN, patient id, or phone.

        Returns zero or more administrative profiles. An empty list is a valid result.
        """
        with session_scope() as session:
            patients = HospitalRepository(session).search_patients(query, limit)
        return SearchResponse(ok=True, code="OK", message=f"Found {len(patients)} patient(s).", patients=patients)

    @server.tool(annotations=_READ)
    def get_patient_details(patient_id: str) -> PatientResponse:
        """Retrieve one synthetic patient by patient id or MRN.

        Does not create a record when the identifier is unknown.
        """
        with session_scope() as session:
            patient = HospitalRepository(session).get_patient(patient_id)
            if patient is None:
                return PatientResponse(ok=False, code="PATIENT_NOT_FOUND", message="No patient matches that identifier.")
            from mcp_server.repository import patient_card

            return PatientResponse(ok=True, code="OK", message="Patient found.", patient=patient_card(patient))

    @server.tool(annotations=_WRITE)
    def register_patient(
        full_name: str,
        date_of_birth: str,
        phone: str = "",
        email: str = "",
        address: str = "",
        insurance_payer: str = "",
        insurance_member_id: str = "",
        idempotency_key: str = "",
    ) -> PatientResponse:
        """Register a synthetic patient.

        Required: full_name and date_of_birth (YYYY-MM-DD). Refuses to guess missing identity fields.
        Replays the same idempotency key or the same name plus date of birth without creating a duplicate.
        """
        with session_scope() as session:
            code, message, card = HospitalRepository(session).register_patient(
                full_name=full_name,
                date_of_birth=date_of_birth,
                phone=phone,
                email=email,
                address=address,
                insurance_payer=insurance_payer,
                insurance_member_id=insurance_member_id,
                idempotency_key=idempotency_key or None,
            )
        ok = code in {"CREATED", "IDEMPOTENT_REPLAY", "PATIENT_EXISTS"} and bool(card.patient_id)
        return PatientResponse(ok=ok, code=code, message=message, patient=card if card.patient_id else None)

    @server.tool(annotations=_DESTRUCTIVE)
    def update_patient_information(
        patient_id: str,
        grant_id: str = "",
        phone: str = "",
        email: str = "",
        address: str = "",
        insurance_payer: str = "",
        insurance_member_id: str = "",
        date_of_birth: str = "",
    ) -> PatientResponse:
        """Update sensitive patient fields. Requires an approval grant bound to these exact arguments."""
        changes = {
            key: value
            for key, value in {
                "phone": phone,
                "email": email,
                "address": address,
                "insurance_payer": insurance_payer,
                "insurance_member_id": insurance_member_id,
                "date_of_birth": date_of_birth,
            }.items()
            if value
        }
        arguments = {"patient_id": patient_id, "grant_id": grant_id, **changes}
        with session_scope() as session:
            code, message, card = HospitalRepository(session).update_patient(patient_id, changes, arguments)
        return PatientResponse(ok=code == "UPDATED", code=code, message=message, patient=card)

    @server.tool(annotations=_READ)
    def get_doctor_availability(
        specialty: str = "",
        doctor_id: str = "",
        earliest_only: bool = False,
        limit: int = 5,
    ) -> AvailabilityResponse:
        """List open future slots. Filter by specialty and/or doctor id.

        earliest_only returns the single soonest open slot for the filter.
        """
        with session_scope() as session:
            slots = HospitalRepository(session).availability(
                specialty=specialty or None,
                doctor_id=doctor_id or None,
                earliest_only=earliest_only,
                limit=limit,
            )
        if not slots:
            return AvailabilityResponse(ok=True, code="NO_AVAILABILITY", message="No open slots match that filter.")
        return AvailabilityResponse(ok=True, code="OK", message=f"Found {len(slots)} open slot(s).", slots=slots)

    @server.tool(annotations=_READ)
    def list_appointments(patient_id: str = "", status: str = "", limit: int = 20) -> AppointmentListResponse:
        """List synthetic appointments, optionally filtered by patient id or MRN and status."""
        with session_scope() as session:
            code, message, appointments = HospitalRepository(session).list_appointments(
                patient_id or None, status or None, limit
            )
        return AppointmentListResponse(ok=code == "OK", code=code, message=message, appointments=appointments)

    @server.tool(annotations=_WRITE)
    def schedule_appointment(
        patient_id: str,
        slot_id: str,
        reason: str = "Administrative visit",
        idempotency_key: str = "",
    ) -> AppointmentResponse:
        """Book an open slot for a synthetic patient.

        This tool does not require a human grant. It refuses booked, unknown, or past slots.
        """
        with session_scope() as session:
            code, message, card = HospitalRepository(session).schedule(
                patient_id=patient_id,
                slot_id=slot_id,
                reason=reason,
                idempotency_key=idempotency_key or None,
            )
        return AppointmentResponse(ok=code in {"SCHEDULED", "IDEMPOTENT_REPLAY"}, code=code, message=message, appointment=card)

    @server.tool(annotations=_DESTRUCTIVE)
    def reschedule_appointment(appointment_id: str, new_slot_id: str, grant_id: str = "") -> AppointmentResponse:
        """Move a scheduled appointment to a different open slot. Requires an approval grant."""
        arguments = {"appointment_id": appointment_id, "new_slot_id": new_slot_id, "grant_id": grant_id}
        with session_scope() as session:
            code, message, card = HospitalRepository(session).reschedule(appointment_id, new_slot_id, arguments)
        return AppointmentResponse(ok=code == "RESCHEDULED", code=code, message=message, appointment=card)

    @server.tool(annotations=_DESTRUCTIVE)
    def cancel_appointment(appointment_id: str, reason: str = "", grant_id: str = "") -> AppointmentResponse:
        """Cancel a scheduled appointment and release its slot. Requires an approval grant."""
        arguments = {"appointment_id": appointment_id, "reason": reason, "grant_id": grant_id}
        with session_scope() as session:
            code, message, card = HospitalRepository(session).cancel(appointment_id, reason, arguments)
        return AppointmentResponse(ok=code in {"CANCELLED", "ALREADY_CANCELLED"}, code=code, message=message, appointment=card)

    @server.tool(annotations=_READ)
    def verify_insurance_eligibility(member_id: str, payer: str) -> EligibilityResponse:
        """Look up a synthetic eligibility fixture by member id and payer.

        Does not create a policy. Unknown members return eligible=false and reason NO_POLICY.
        """
        with session_scope() as session:
            code, message, policy = HospitalRepository(session).eligibility(member_id, payer)
        if policy is None:
            eligible = False
            return EligibilityResponse(
                ok=code != "INVALID_ARGUMENT",
                code=code,
                message=message,
                eligible=eligible,
                payer=payer,
                member_id=member_id,
                reason=code,
            )
        eligible = policy.status == "eligible"
        return EligibilityResponse(
            ok=True,
            code="ELIGIBLE" if eligible else "INELIGIBLE",
            message="Synthetic eligibility found." if eligible else "Synthetic policy is not eligible.",
            eligible=eligible,
            payer=policy.payer,
            member_id=policy.member_id,
            plan_name=policy.plan_name,
            reason=policy.status,
        )

    @server.tool(annotations=_READ)
    def get_insurance_coverage(member_id: str) -> CoverageResponse:
        """Return the synthetic coverage fixture for a member id. This is not a benefit decision."""
        with session_scope() as session:
            policy = HospitalRepository(session).coverage(member_id)
        if policy is None:
            return CoverageResponse(ok=False, code="POLICY_NOT_FOUND", message="No synthetic policy matches that member id.", member_id=member_id)
        return CoverageResponse(
            ok=True,
            code="OK",
            message="Synthetic coverage fixture returned.",
            payer=policy.payer,
            member_id=policy.member_id,
            plan_name=policy.plan_name,
            status=policy.status,
            copay_cents=policy.copay_cents,
            deductible_cents=policy.deductible_cents,
            coverage_summary=policy.coverage_summary,
        )

    @server.tool(annotations=_READ)
    def get_claim_status(claim_number: str) -> ClaimResponse:
        """Return the synthetic status of one administrative claim."""
        with session_scope() as session:
            claim = HospitalRepository(session).claim(claim_number)
        if claim is None:
            return ClaimResponse(ok=False, code="CLAIM_NOT_FOUND", message="No synthetic claim matches that number.")
        return ClaimResponse(
            ok=True,
            code="OK",
            message="Claim status found.",
            claim=ClaimCard(
                claim_number=claim.claim_number,
                member_id=claim.member_id,
                status=claim.status,
                amount_cents=claim.amount_cents,
                service_date=claim.service_date,
                description=claim.description,
            ),
        )

    @server.tool(annotations=_WRITE)
    def create_administrative_task(title: str, description: str = "", patient_id: str = "") -> TaskResponse:
        """Create a follow-up task for hospital operations staff. This does not document a clinical plan."""
        try:
            with session_scope() as session:
                task = HospitalRepository(session).create_task(title, description, patient_id)
        except LookupError:
            return TaskResponse(ok=False, code="PATIENT_NOT_FOUND", message="No patient matches that identifier.")
        except ValueError:
            return TaskResponse(ok=False, code="INVALID_ARGUMENT", message="A task title is required.")
        return TaskResponse(ok=True, code="CREATED", message="Administrative task created.", task=task)

    @server.tool(annotations=_READ)
    def get_administrative_task_status(task_id: str) -> TaskResponse:
        """Read the status of one administrative task."""
        with session_scope() as session:
            task = HospitalRepository(session).task(task_id)
            if task is None:
                return TaskResponse(ok=False, code="TASK_NOT_FOUND", message="No administrative task matches that identifier.")
            card = TaskCard(
                task_id=task.task_id,
                title=task.title,
                description=task.description,
                status=task.status,
                patient_id=task.patient_id,
            )
        return TaskResponse(ok=True, code="OK", message="Task status found.", task=card)
