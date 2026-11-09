"""End-to-end checks through FastAPI and the evaluation suite. MCP is a separate process."""

from fastapi.testclient import TestClient

from app.evaluations.report import render_markdown
from app.evaluations.runner import run_suite
from app.main import create_app


def test_health_and_profile_lookup(stack: dict) -> None:
    with TestClient(create_app(stack["settings"])) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["mcp"] == "ok"
        assert health.json()["synthetic_data"] is True
        created = client.post(
            "/api/v1/agents/run",
            json={"request": "Find the patient named Maria Alvarez and show her profile.", "actor": "api.operator"},
        )
        assert created.status_code == 201
        body = created.json()
        assert body["status"] == "completed"
        assert "MRN-1001" in body["summary"]
        listed = client.get("/api/v1/agents/runs")
        assert any(item["run_id"] == body["run_id"] for item in listed.json()["runs"])
        patients = client.get("/api/v1/patients")
        assert any(patient["mrn"] == "MRN-1001" for patient in patients.json()["patients"])
        docs = client.get("/openapi.json")
        assert docs.status_code == 200
        assert "/api/v1/approvals/{approval_id}/approve" in docs.json()["paths"]


async def test_evaluation_suite(stack: dict) -> None:
    report = await run_suite(stack["harness"])
    rendered = render_markdown(report)
    failures = [item for item in report["scenarios"] if not item["passed"]]
    details = "\n".join(
        f"{item['id']}: status={item['status']} outcome={item['outcome']} tools={item['actual_tools']} error={item['error']}"
        for item in failures
    )
    assert not failures, rendered + "\n" + details
    summary = report["summary"]
    assert summary["task_completion_rate"] == 1
    assert summary["policy_compliance"] == 1
    assert summary["approval_compliance"] == 1
    assert summary["workflow_correctness"] == 1
    assert summary["executed_argument_validity"] == 1
    assert summary["recoveries"] >= 1
    assert summary["tool_failures"] >= 1
