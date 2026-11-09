"""Recruiter walkthrough against a running API and MCP server.

The first request is intentionally incomplete. The agent must ask for the missing
fields. The second request supplies synthetic values and books the visit.
The third request cancels an existing appointment and waits for approval.
"""

import os
import sys

import httpx

INCOMPLETE = (
    "Please register a new patient named John Smith, check whether his insurance is eligible, "
    "find the earliest available appointment with a cardiologist, and schedule it. "
    "If the appointment requires any sensitive operation, ask me for approval before proceeding."
)
COMPLETE = (
    "Please register a new patient named John Smith, date of birth 1984-03-12, phone 555-0148, "
    "insurance payer BlueHarbor, member id BH-100200. Check whether his insurance is eligible, "
    "find the earliest available appointment with a cardiologist, and schedule it."
)


def _print_run(title: str, payload: dict) -> None:
    print(f"\n== {title} ==")
    print(f"status: {payload['status']}  outcome: {payload.get('outcome')}")
    print(payload["summary"])
    if payload.get("missing_fields"):
        print("missing:", ", ".join(payload["missing_fields"]))
    if payload.get("pending_approval"):
        approval = payload["pending_approval"]
        print(f"approval: {approval['approval_id']} tool={approval['tool_name']}")
    for event in payload.get("events") or []:
        if event["event_type"] in {"tool_call", "tool_result", "approval_requested", "approval_resolved", "recovery"}:
            tool = event["payload"].get("tool") or event["payload"].get("decision") or ""
            print(f"  {event['sequence']:02d} {event['agent_id'] or '-':24} {event['event_type']:20} {tool}")


def main() -> None:
    base = os.environ.get("API_BASE_URL", "http://127.0.0.1:8091")
    actor = "demo.operator"
    with httpx.Client(base_url=base, timeout=60) as client:
        health = client.get("/api/v1/health")
        health.raise_for_status()
        print("health:", health.json())
        first = client.post("/api/v1/agents/run", json={"request": INCOMPLETE, "actor": actor})
        first.raise_for_status()
        _print_run("missing information", first.json())
        second = client.post("/api/v1/agents/run", json={"request": COMPLETE, "actor": actor})
        second.raise_for_status()
        _print_run("register, verify, schedule", second.json())
        third = client.post(
            "/api/v1/agents/run",
            json={"request": "Cancel Elena Rossi's appointment.", "actor": actor},
        )
        third.raise_for_status()
        paused = third.json()
        _print_run("cancellation paused", paused)
        approval = paused.get("pending_approval")
        if approval is None:
            print("expected an approval and did not receive one", file=sys.stderr)
            raise SystemExit(1)
        resumed = client.post(
            f"/api/v1/approvals/{approval['approval_id']}/approve",
            json={"approver": "demo.lead@example.test", "comment": "Confirmed with the front desk."},
        )
        resumed.raise_for_status()
        _print_run("cancellation resumed", resumed.json())
        patients = client.get("/api/v1/patients")
        patients.raise_for_status()
        names = [patient["full_name"] for patient in patients.json()["patients"]]
        print("\npatients:", ", ".join(names))


if __name__ == "__main__":
    main()
