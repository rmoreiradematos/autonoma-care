"""Hospital operational tables. Synthetic administrative data only."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now().astimezone()


class Patient(Base):
    __tablename__ = "patients"

    patient_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    mrn: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120), index=True)
    date_of_birth: Mapped[str] = mapped_column(String(10))
    phone: Mapped[str] = mapped_column(String(40), default="")
    email: Mapped[str] = mapped_column(String(120), default="")
    address: Mapped[str] = mapped_column(String(240), default="")
    insurance_payer: Mapped[str] = mapped_column(String(80), default="")
    insurance_member_id: Mapped[str] = mapped_column(String(40), default="")
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class Doctor(Base):
    __tablename__ = "doctors"

    doctor_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120))
    specialty: Mapped[str] = mapped_column(String(80), index=True)
    department: Mapped[str] = mapped_column(String(80))


class Slot(Base):
    __tablename__ = "slots"
    __table_args__ = (UniqueConstraint("doctor_id", "starts_at", name="uq_slot_doctor_start"),)

    slot_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    doctor_id: Mapped[str] = mapped_column(ForeignKey("doctors.doctor_id"), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)


class Appointment(Base):
    __tablename__ = "appointments"

    appointment_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.patient_id"), index=True)
    doctor_id: Mapped[str] = mapped_column(ForeignKey("doctors.doctor_id"), index=True)
    slot_id: Mapped[str] = mapped_column(ForeignKey("slots.slot_id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="scheduled", index=True)
    reason: Mapped[str] = mapped_column(String(240), default="Administrative visit")
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class InsurancePolicy(Base):
    __tablename__ = "insurance_policies"
    __table_args__ = (UniqueConstraint("payer", "member_id", name="uq_policy_payer_member"),)

    member_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    payer: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(16))
    plan_name: Mapped[str] = mapped_column(String(80))
    copay_cents: Mapped[int] = mapped_column(default=0)
    deductible_cents: Mapped[int] = mapped_column(default=0)
    coverage_summary: Mapped[str] = mapped_column(Text, default="")


class Claim(Base):
    __tablename__ = "claims"

    claim_number: Mapped[str] = mapped_column(String(40), primary_key=True)
    member_id: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(16))
    amount_cents: Mapped[int] = mapped_column(default=0)
    service_date: Mapped[str] = mapped_column(String(10))
    description: Mapped[str] = mapped_column(String(240))


class AdminTask(Base):
    __tablename__ = "admin_tasks"

    task_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    patient_id: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class ActionGrant(Base):
    """One-time authorization written by the trusted agent service and checked by tools."""

    __tablename__ = "action_grants"

    grant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tool_name: Mapped[str] = mapped_column(String(80), index=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    approver: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
