"""Hospital repository rules that must hold without an LLM."""

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from mcp_server.database import init_database, session_scope
from mcp_server.models import Appointment, Slot
from mcp_server.repository import HospitalRepository
from mcp_server.security import canonical_payload_hash, issue_grant, utcnow
from mcp_server.seed import seed_if_empty


@pytest.fixture()
def hospital(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.chdir(tmp_path)
    init_database(f"sqlite:///{tmp_path / 'hospital.db'}")
    with session_scope() as session:
        seed_if_empty(session)
    with session_scope() as session:
        yield session


def test_seed_is_idempotent_and_search_finds_maria(hospital: Session) -> None:
    assert seed_if_empty(hospital) is False
    found = HospitalRepository(hospital).search_patients("maria alvarez")
    assert len(found) == 1
    assert found[0].mrn == "MRN-1001"
    assert found[0].insurance_member_id == "BH-100100"


def test_register_requires_real_dob_and_replays_same_identity(hospital: Session) -> None:
    repo = HospitalRepository(hospital)
    code, _message, _card = repo.register_patient(full_name="John Smith", date_of_birth="")
    assert code == "INVALID_ARGUMENT"
    code, message, card = repo.register_patient(
        full_name="John Smith",
        date_of_birth="1984-03-12",
        phone="555-0148",
        insurance_payer="BlueHarbor",
        insurance_member_id="BH-100200",
        idempotency_key="register:john-smith:1984-03-12",
    )
    assert code == "CREATED"
    assert card.mrn == "MRN-1004"
    again, _message, replay = repo.register_patient(
        full_name="John Smith",
        date_of_birth="1984-03-12",
        idempotency_key="register:john-smith:1984-03-12",
    )
    assert again == "IDEMPOTENT_REPLAY"
    assert replay.patient_id == card.patient_id
    assert "registered" in message.lower() or card.patient_id


def test_cancel_without_grant_does_not_release_the_slot(hospital: Session) -> None:
    repo = HospitalRepository(hospital)
    before = hospital.get(Appointment, "APT-ELENA")
    assert before is not None and before.status == "scheduled"
    slot = hospital.get(Slot, before.slot_id)
    assert slot is not None and slot.status == "booked"
    code, _message, _card = repo.cancel("APT-ELENA", "operator request", {"appointment_id": "APT-ELENA", "reason": "operator request"})
    assert code == "APPROVAL_REQUIRED"
    hospital.refresh(before)
    hospital.refresh(slot)
    assert before.status == "scheduled"
    assert slot.status == "booked"


def test_cancel_with_matching_grant_releases_the_slot(hospital: Session) -> None:
    repo = HospitalRepository(hospital)
    arguments = {"appointment_id": "APT-ELENA", "reason": "operator request"}
    issue_grant(hospital, grant_id="GRT-TEST", tool_name="cancel_appointment", arguments=arguments, approver="ops.lead@example.test")
    code, _message, card = repo.cancel("APT-ELENA", "operator request", {**arguments, "grant_id": "GRT-TEST"})
    assert code == "CANCELLED"
    assert card is not None and card.status == "cancelled"
    slot = hospital.get(Slot, card.slot_id)
    assert slot is not None and slot.status == "open"


def test_grant_rejects_a_different_payload(hospital: Session) -> None:
    arguments = {"appointment_id": "APT-SAMIR", "reason": "approved reason"}
    issue_grant(hospital, grant_id="GRT-BOUND", tool_name="cancel_appointment", arguments=arguments, approver="ops.lead@example.test")
    code, _message, _card = HospitalRepository(hospital).cancel(
        "APT-SAMIR",
        "different reason",
        {"appointment_id": "APT-SAMIR", "reason": "different reason", "grant_id": "GRT-BOUND"},
    )
    assert code == "GRANT_MISMATCH"
    assert canonical_payload_hash("cancel_appointment", arguments) != canonical_payload_hash(
        "cancel_appointment", {"appointment_id": "APT-SAMIR", "reason": "different reason"}
    )


def test_closed_slot_cannot_be_scheduled_and_open_slot_can(hospital: Session) -> None:
    repo = HospitalRepository(hospital)
    code, _message, _card = repo.schedule(patient_id="PAT-MARIA", slot_id="SLT-CLOSED", reason="Administrative visit", idempotency_key=None)
    assert code == "SLOT_UNAVAILABLE"
    slots = repo.availability(specialty="Cardiology", doctor_id=None, earliest_only=True, limit=5)
    assert slots
    assert slots[0].slot_id != "SLT-CLOSED"
    assert slots[0].specialty == "Cardiology"
    code, _message, booked = repo.schedule(
        patient_id="MRN-1001",
        slot_id=slots[0].slot_id,
        reason="Administrative cardiology visit",
        idempotency_key="schedule:test",
    )
    assert code == "SCHEDULED"
    assert booked is not None
    replay, _message, again = repo.schedule(
        patient_id="MRN-1001",
        slot_id=slots[0].slot_id,
        reason="Administrative cardiology visit",
        idempotency_key="schedule:test",
    )
    assert replay == "IDEMPOTENT_REPLAY"
    assert again is not None and again.appointment_id == booked.appointment_id


def test_eligibility_does_not_invent_a_policy(hospital: Session) -> None:
    repo = HospitalRepository(hospital)
    code, _message, policy = repo.eligibility("BH-100200", "BlueHarbor")
    assert code == "OK" and policy is not None and policy.status == "eligible"
    code, _message, missing = repo.eligibility("ZZ-000000", "BlueHarbor")
    assert code == "NO_POLICY" and missing is None
    code, _message, lapsed = repo.eligibility("NW-100100", "Northwind")
    assert code == "OK" and lapsed is not None and lapsed.status == "ineligible"


def test_expired_grant_cannot_cancel(hospital: Session) -> None:
    arguments = {"appointment_id": "APT-SAMIR", "reason": "late"}
    grant = issue_grant(hospital, grant_id="GRT-OLD", tool_name="cancel_appointment", arguments=arguments, approver="ops.lead@example.test")
    grant.expires_at = utcnow() - timedelta(minutes=1)
    code, _message, _card = HospitalRepository(hospital).cancel("APT-SAMIR", "late", {**arguments, "grant_id": "GRT-OLD"})
    assert code == "GRANT_EXPIRED"
