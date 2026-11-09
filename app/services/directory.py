"""Operator reads of the synthetic hospital directory.

Agent actions do not use this module. They call the MCP server.
"""

from sqlalchemy import select

from mcp_server.database import session_scope
from mcp_server.models import Patient
from mcp_server.repository import HospitalRepository, patient_card


def list_patients(limit: int = 50) -> list[dict]:
    with session_scope() as session:
        rows = session.scalars(select(Patient).order_by(Patient.full_name).limit(limit)).all()
        return [patient_card(row).model_dump() for row in rows]


def list_appointments(limit: int = 50) -> list[dict]:
    with session_scope() as session:
        _code, _message, cards = HospitalRepository(session).list_appointments(None, None, limit)
        return [card.model_dump() for card in cards]
