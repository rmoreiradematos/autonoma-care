"""Structured tool payloads. These models become the MCP output schemas."""

from pydantic import BaseModel, Field


class PatientCard(BaseModel):
    patient_id: str
    mrn: str
    full_name: str
    date_of_birth: str
    phone: str = ""
    email: str = ""
    address: str = ""
    insurance_payer: str = ""
    insurance_member_id: str = ""


class SearchResponse(BaseModel):
    ok: bool
    code: str
    message: str
    patients: list[PatientCard] = Field(default_factory=list)


class PatientResponse(BaseModel):
    ok: bool
    code: str
    message: str
    patient: PatientCard | None = None


class SlotCard(BaseModel):
    slot_id: str
    doctor_id: str
    doctor_name: str
    specialty: str
    starts_at: str
    ends_at: str
    status: str


class AvailabilityResponse(BaseModel):
    ok: bool
    code: str
    message: str
    slots: list[SlotCard] = Field(default_factory=list)


class AppointmentCard(BaseModel):
    appointment_id: str
    patient_id: str
    doctor_id: str
    doctor_name: str
    specialty: str
    slot_id: str
    starts_at: str
    ends_at: str
    status: str
    reason: str


class AppointmentListResponse(BaseModel):
    ok: bool
    code: str
    message: str
    appointments: list[AppointmentCard] = Field(default_factory=list)


class AppointmentResponse(BaseModel):
    ok: bool
    code: str
    message: str
    appointment: AppointmentCard | None = None


class EligibilityResponse(BaseModel):
    ok: bool
    code: str
    message: str
    eligible: bool = False
    payer: str = ""
    member_id: str = ""
    plan_name: str = ""
    reason: str = ""


class CoverageResponse(BaseModel):
    ok: bool
    code: str
    message: str
    payer: str = ""
    member_id: str = ""
    plan_name: str = ""
    status: str = ""
    copay_cents: int = 0
    deductible_cents: int = 0
    coverage_summary: str = ""


class ClaimCard(BaseModel):
    claim_number: str
    member_id: str
    status: str
    amount_cents: int
    service_date: str
    description: str


class ClaimResponse(BaseModel):
    ok: bool
    code: str
    message: str
    claim: ClaimCard | None = None


class TaskCard(BaseModel):
    task_id: str
    title: str
    description: str
    status: str
    patient_id: str = ""


class TaskResponse(BaseModel):
    ok: bool
    code: str
    message: str
    task: TaskCard | None = None


class DoctorCard(BaseModel):
    doctor_id: str
    full_name: str
    specialty: str
    department: str
