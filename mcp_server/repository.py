"""Database access layer for hospital tools. Tools do not embed SQL."""

import re
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from mcp_server.models import Appointment, Claim, Doctor, InsurancePolicy, Patient, Slot, AdminTask
from mcp_server.schemas import (
    AppointmentCard,
    ClaimCard,
    DoctorCard,
    PatientCard,
    SlotCard,
    TaskCard,
)
from mcp_server.security import consume_grant

_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z' .-]{1,80}$")
_DOB_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MEMBER_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{2,30}$")
_PHONE_RE = re.compile(r"^[0-9+\-().\s]{7,20}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _aware(value).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def patient_card(patient: Patient) -> PatientCard:
    return PatientCard(
        patient_id=patient.patient_id,
        mrn=patient.mrn,
        full_name=patient.full_name,
        date_of_birth=patient.date_of_birth,
        phone=patient.phone,
        email=patient.email,
        address=patient.address,
        insurance_payer=patient.insurance_payer,
        insurance_member_id=patient.insurance_member_id,
    )


def _appointment_card(session: Session, appointment: Appointment) -> AppointmentCard:
    doctor = session.get(Doctor, appointment.doctor_id)
    slot = session.get(Slot, appointment.slot_id)
    return AppointmentCard(
        appointment_id=appointment.appointment_id,
        patient_id=appointment.patient_id,
        doctor_id=appointment.doctor_id,
        doctor_name=doctor.full_name if doctor else "",
        specialty=doctor.specialty if doctor else "",
        slot_id=appointment.slot_id,
        starts_at=_iso(slot.starts_at) if slot else "",
        ends_at=_iso(slot.ends_at) if slot else "",
        status=appointment.status,
        reason=appointment.reason,
    )


class HospitalRepository:
    def __init__(self, session: Session):
        self.session = session

    def search_patients(self, query: str, limit: int = 10) -> list[PatientCard]:
        needle = query.strip()
        if not needle:
            return []
        limit = max(1, min(limit, 25))
        like = f"%{needle}%"
        rows = self.session.scalars(
            select(Patient)
            .where(
                or_(
                    Patient.full_name.ilike(like),
                    Patient.mrn.ilike(like),
                    Patient.patient_id == needle,
                    Patient.phone == needle,
                )
            )
            .order_by(Patient.full_name)
            .limit(limit)
        ).all()
        return [patient_card(row) for row in rows]

    def get_patient(self, patient_id: str) -> Patient | None:
        token = patient_id.strip()
        patient = self.session.get(Patient, token)
        if patient is not None:
            return patient
        return self.session.scalar(select(Patient).where(Patient.mrn == token))

    def register_patient(
        self,
        *,
        full_name: str,
        date_of_birth: str,
        phone: str = "",
        email: str = "",
        address: str = "",
        insurance_payer: str = "",
        insurance_member_id: str = "",
        idempotency_key: str | None = None,
    ) -> tuple[str, str, PatientCard]:
        name = " ".join(full_name.split())
        if not _NAME_RE.match(name) or len(name.split()) < 2:
            return "INVALID_ARGUMENT", "full_name must contain at least two name parts.", PatientCard(
                patient_id="", mrn="", full_name="", date_of_birth=""
            )
        if not _DOB_RE.match(date_of_birth):
            return "INVALID_ARGUMENT", "date_of_birth must be YYYY-MM-DD.", _empty_patient()
        try:
            born = date.fromisoformat(date_of_birth)
        except ValueError:
            return "INVALID_ARGUMENT", "date_of_birth is not a real calendar date.", _empty_patient()
        today = _utcnow().date()
        if born > today or born.year < today.year - 120:
            return "INVALID_ARGUMENT", "date_of_birth is outside the accepted range.", _empty_patient()
        if phone and not _PHONE_RE.match(phone):
            return "INVALID_ARGUMENT", "phone is not a plausible phone number.", _empty_patient()
        if email and not _EMAIL_RE.match(email):
            return "INVALID_ARGUMENT", "email is not a plausible email address.", _empty_patient()
        member = insurance_member_id.strip().upper()
        payer = insurance_payer.strip()
        if member and not _MEMBER_RE.match(member):
            return "INVALID_ARGUMENT", "insurance_member_id has an invalid format.", _empty_patient()

        if idempotency_key:
            existing_key = self.session.scalar(select(Patient).where(Patient.idempotency_key == idempotency_key))
            if existing_key is not None:
                if existing_key.full_name.lower() != name.lower() or existing_key.date_of_birth != date_of_birth:
                    return "IDEMPOTENCY_CONFLICT", "Idempotency key belongs to a different patient.", _empty_patient()
                return "IDEMPOTENT_REPLAY", "Returned the patient already created for this key.", patient_card(existing_key)

        existing = self.session.scalar(
            select(Patient).where(func.lower(Patient.full_name) == name.lower(), Patient.date_of_birth == date_of_birth)
        )
        if existing is not None:
            return "PATIENT_EXISTS", "A patient with this name and date of birth is already registered.", patient_card(existing)

        count = self.session.scalar(select(func.count()).select_from(Patient)) or 0
        patient = Patient(
            patient_id=_new_id("PAT"),
            mrn=f"MRN-{1000 + int(count) + 1}",
            full_name=name,
            date_of_birth=date_of_birth,
            phone=phone.strip(),
            email=email.strip(),
            address=address.strip(),
            insurance_payer=payer,
            insurance_member_id=member,
            idempotency_key=idempotency_key,
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        self.session.add(patient)
        self.session.flush()
        return "CREATED", "Patient registered.", patient_card(patient)

    def update_patient(self, patient_id: str, changes: dict[str, str], grant_arguments: dict) -> tuple[str, str, PatientCard | None]:
        allowed = {"phone", "email", "address", "insurance_payer", "insurance_member_id", "date_of_birth"}
        unknown = set(changes) - allowed
        if unknown:
            return "INVALID_ARGUMENT", f"Unsupported fields: {', '.join(sorted(unknown))}.", None
        if not changes:
            return "INVALID_ARGUMENT", "Provide at least one field to update.", None
        for key, value in changes.items():
            if key == "phone" and not _PHONE_RE.match(value):
                return "INVALID_ARGUMENT", "phone is not a plausible phone number.", None
            if key == "email" and not _EMAIL_RE.match(value):
                return "INVALID_ARGUMENT", "email is not a plausible email address.", None
            if key == "date_of_birth" and not _DOB_RE.match(value):
                return "INVALID_ARGUMENT", "date_of_birth must be YYYY-MM-DD.", None
            if key == "insurance_member_id" and not _MEMBER_RE.match(value.upper()):
                return "INVALID_ARGUMENT", "insurance_member_id has an invalid format.", None
        patient = self.get_patient(patient_id)
        if patient is None:
            return "PATIENT_NOT_FOUND", "No patient matches that identifier.", None
        ok, code, message = consume_grant(self.session, "update_patient_information", grant_arguments)
        if not ok:
            return code, message, None
        for key, value in changes.items():
            stored = value.strip().upper() if key == "insurance_member_id" else value.strip()
            setattr(patient, key, stored)
        patient.updated_at = _utcnow()
        self.session.flush()
        return "UPDATED", "Patient information updated.", patient_card(patient)

    def availability(
        self,
        *,
        specialty: str | None,
        doctor_id: str | None,
        earliest_only: bool,
        limit: int,
    ) -> list[SlotCard]:
        limit = 1 if earliest_only else max(1, min(limit, 20))
        statement = (
            select(Slot, Doctor)
            .join(Doctor, Doctor.doctor_id == Slot.doctor_id)
            .where(Slot.status == "open", Slot.starts_at > _utcnow())
            .order_by(Slot.starts_at, Doctor.full_name)
            .limit(limit)
        )
        if specialty:
            statement = statement.where(func.lower(Doctor.specialty) == specialty.strip().lower())
        if doctor_id:
            statement = statement.where(Doctor.doctor_id == doctor_id)
        rows = self.session.execute(statement).all()
        return [
            SlotCard(
                slot_id=slot.slot_id,
                doctor_id=doctor.doctor_id,
                doctor_name=doctor.full_name,
                specialty=doctor.specialty,
                starts_at=_iso(slot.starts_at),
                ends_at=_iso(slot.ends_at),
                status=slot.status,
            )
            for slot, doctor in rows
        ]

    def list_appointments(self, patient_id: str | None, status: str | None, limit: int) -> tuple[str, str, list[AppointmentCard]]:
        limit = max(1, min(limit, 50))
        statement = select(Appointment).order_by(Appointment.created_at.desc()).limit(limit)
        if patient_id:
            patient = self.get_patient(patient_id)
            if patient is None:
                return "PATIENT_NOT_FOUND", "No patient matches that identifier.", []
            statement = statement.where(Appointment.patient_id == patient.patient_id)
        if status:
            statement = statement.where(Appointment.status == status)
        rows = self.session.scalars(statement).all()
        return "OK", "Appointments listed.", [_appointment_card(self.session, row) for row in rows]

    def schedule(
        self,
        *,
        patient_id: str,
        slot_id: str,
        reason: str,
        idempotency_key: str | None,
    ) -> tuple[str, str, AppointmentCard | None]:
        if idempotency_key:
            existing = self.session.scalar(select(Appointment).where(Appointment.idempotency_key == idempotency_key))
            if existing is not None:
                return "IDEMPOTENT_REPLAY", "Returned the appointment already created for this key.", _appointment_card(self.session, existing)
        patient = self.get_patient(patient_id)
        if patient is None:
            return "PATIENT_NOT_FOUND", "No patient matches that identifier.", None
        slot = self.session.get(Slot, slot_id)
        if slot is None:
            return "SLOT_NOT_FOUND", "No slot matches that identifier.", None
        if slot.status != "open" or _aware(slot.starts_at) <= _utcnow():
            return "SLOT_UNAVAILABLE", "That slot is not open.", None
        conflict = self.session.scalar(
            select(Appointment).where(Appointment.slot_id == slot.slot_id, Appointment.status == "scheduled")
        )
        if conflict is not None:
            return "SLOT_UNAVAILABLE", "That slot is already booked.", None
        appointment = Appointment(
            appointment_id=_new_id("APT"),
            patient_id=patient.patient_id,
            doctor_id=slot.doctor_id,
            slot_id=slot.slot_id,
            status="scheduled",
            reason=(reason or "Administrative visit").strip()[:240],
            idempotency_key=idempotency_key,
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        slot.status = "booked"
        self.session.add(appointment)
        self.session.flush()
        return "SCHEDULED", "Appointment scheduled.", _appointment_card(self.session, appointment)

    def reschedule(self, appointment_id: str, new_slot_id: str, grant_arguments: dict) -> tuple[str, str, AppointmentCard | None]:
        appointment = self.session.get(Appointment, appointment_id)
        if appointment is None or appointment.status != "scheduled":
            return "APPOINTMENT_NOT_FOUND", "No scheduled appointment matches that identifier.", None
        if appointment.slot_id == new_slot_id:
            return "INVALID_ARGUMENT", "The new slot is the same as the current slot.", None
        slot = self.session.get(Slot, new_slot_id)
        if slot is None or slot.status != "open" or _aware(slot.starts_at) <= _utcnow():
            return "SLOT_UNAVAILABLE", "The requested slot is not open.", None
        ok, code, message = consume_grant(self.session, "reschedule_appointment", grant_arguments)
        if not ok:
            return code, message, None
        current = self.session.get(Slot, appointment.slot_id)
        if current is not None:
            current.status = "open"
        slot.status = "booked"
        appointment.slot_id = slot.slot_id
        appointment.doctor_id = slot.doctor_id
        appointment.updated_at = _utcnow()
        self.session.flush()
        return "RESCHEDULED", "Appointment rescheduled.", _appointment_card(self.session, appointment)

    def cancel(self, appointment_id: str, reason: str, grant_arguments: dict) -> tuple[str, str, AppointmentCard | None]:
        appointment = self.session.get(Appointment, appointment_id)
        if appointment is None:
            return "APPOINTMENT_NOT_FOUND", "No appointment matches that identifier.", None
        if appointment.status == "cancelled":
            return "ALREADY_CANCELLED", "The appointment was already cancelled.", _appointment_card(self.session, appointment)
        if appointment.status != "scheduled":
            return "APPOINTMENT_NOT_FOUND", "Only a scheduled appointment can be cancelled.", None
        ok, code, message = consume_grant(self.session, "cancel_appointment", grant_arguments)
        if not ok:
            return code, message, None
        slot = self.session.get(Slot, appointment.slot_id)
        if slot is not None and slot.status == "booked":
            slot.status = "open"
        appointment.status = "cancelled"
        if reason:
            appointment.reason = f"Cancelled: {reason.strip()[:200]}"
        appointment.updated_at = _utcnow()
        self.session.flush()
        return "CANCELLED", "Appointment cancelled.", _appointment_card(self.session, appointment)

    def eligibility(self, member_id: str, payer: str) -> tuple[str, str, InsurancePolicy | None]:
        member = member_id.strip().upper()
        payer_name = payer.strip()
        if not _MEMBER_RE.match(member):
            return "INVALID_ARGUMENT", "member_id has an invalid format.", None
        if not payer_name:
            return "INVALID_ARGUMENT", "payer is required.", None
        policy = self.session.scalar(
            select(InsurancePolicy).where(InsurancePolicy.member_id == member, func.lower(InsurancePolicy.payer) == payer_name.lower())
        )
        if policy is None:
            return "NO_POLICY", "No synthetic policy matches that member and payer.", None
        return "OK", "Eligibility checked.", policy

    def coverage(self, member_id: str) -> InsurancePolicy | None:
        return self.session.scalar(select(InsurancePolicy).where(InsurancePolicy.member_id == member_id.strip().upper()))

    def claim(self, claim_number: str) -> Claim | None:
        return self.session.get(Claim, claim_number.strip().upper())

    def create_task(self, title: str, description: str, patient_id: str = "") -> TaskCard:
        if patient_id:
            patient = self.get_patient(patient_id)
            if patient is None:
                raise LookupError("PATIENT_NOT_FOUND")
            patient_id = patient.patient_id
        task = AdminTask(
            task_id=_new_id("TSK"),
            title=title.strip()[:160],
            description=description.strip(),
            status="open",
            patient_id=patient_id,
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        if not task.title:
            raise ValueError("INVALID_ARGUMENT")
        self.session.add(task)
        self.session.flush()
        return TaskCard(
            task_id=task.task_id,
            title=task.title,
            description=task.description,
            status=task.status,
            patient_id=task.patient_id,
        )

    def task(self, task_id: str) -> AdminTask | None:
        return self.session.get(AdminTask, task_id.strip())

    def doctors(self) -> list[DoctorCard]:
        rows = self.session.scalars(select(Doctor).order_by(Doctor.specialty, Doctor.full_name)).all()
        return [
            DoctorCard(doctor_id=row.doctor_id, full_name=row.full_name, specialty=row.specialty, department=row.department)
            for row in rows
        ]


def _empty_patient() -> PatientCard:
    return PatientCard(patient_id="", mrn="", full_name="", date_of_birth="")
