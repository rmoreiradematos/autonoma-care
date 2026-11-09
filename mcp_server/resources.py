"""MCP resources: policies and the synthetic clinician directory."""

import json

from mcp.server.mcpserver import MCPServer

from mcp_server.database import session_scope
from mcp_server.repository import HospitalRepository
from mcp_server.seed import APPOINTMENT_RULES, DOCTOR_DIRECTORY_NOTE, INSURANCE_RULES


def register_resources(server: MCPServer) -> None:
    @server.resource(
        "hospital://policies/appointment-rules",
        name="appointment-rules",
        description="Administrative rules for booking, rescheduling, and cancelling appointments.",
        mime_type="text/markdown",
    )
    def appointment_rules() -> str:
        """Return the appointment policy used by the agent context builder."""
        return APPOINTMENT_RULES

    @server.resource(
        "hospital://policies/insurance-rules",
        name="insurance-rules",
        description="Administrative rules for synthetic eligibility, coverage, and claim lookups.",
        mime_type="text/markdown",
    )
    def insurance_rules() -> str:
        """Return the insurance policy used by the agent context builder."""
        return INSURANCE_RULES

    @server.resource(
        "hospital://directory/doctors",
        name="doctor-directory",
        description="Synthetic directory of clinicians available for administrative scheduling.",
        mime_type="application/json",
    )
    def doctor_directory() -> str:
        """Return the current clinician directory as JSON."""
        with session_scope() as session:
            doctors = [card.model_dump() for card in HospitalRepository(session).doctors()]
        return json.dumps({"note": DOCTOR_DIRECTORY_NOTE, "doctors": doctors}, indent=2)
