"""Versioned operator API."""

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.database import RunRecord, agent_session
from app.harness.engine import list_pending, present_run, resolve_mode
from app.services.directory import list_appointments, list_patients

router = APIRouter(prefix="/api/v1")


class RunCreate(BaseModel):
    request: str = Field(min_length=3, max_length=4000)
    actor: str = Field(default="demo.operator", min_length=3, max_length=120)
    planner: str | None = Field(default=None, description="auto, deterministic, or llm")


class ApprovalDecision(BaseModel):
    approver: str = Field(min_length=3, max_length=120)
    comment: str = Field(default="", max_length=500)


@router.get("/health", tags=["health"])
async def health(request: Request) -> dict:
    settings = request.app.state.settings
    harness = request.app.state.harness
    mcp_ok = await harness.gateway.ping()
    try:
        effective = resolve_mode(None, settings)
    except Exception:
        effective = "unconfigured"
    return {
        "status": "ok" if mcp_ok else "degraded",
        "mcp": "ok" if mcp_ok else "down",
        "planner_mode": settings.planner_mode,
        "effective_planner": effective,
        "demo_mode": settings.demo_mode,
        "synthetic_data": True,
    }


@router.post("/agents/run", tags=["agents"], status_code=201)
async def create_run(body: RunCreate, request: Request) -> dict:
    return await request.app.state.harness.run(body.request, body.actor, body.planner)


@router.get("/agents/runs", tags=["agents"])
def list_runs(limit: int = 20) -> dict:
    limit = max(1, min(limit, 100))
    with agent_session() as session:
        rows = session.scalars(select(RunRecord).order_by(RunRecord.created_at.desc()).limit(limit)).all()
        return {"runs": [present_run(session, row) for row in rows]}


@router.get("/agents/runs/{run_id}", tags=["agents"])
def get_run(run_id: str) -> dict:
    from app.errors import DomainError

    with agent_session() as session:
        run = session.get(RunRecord, run_id)
        if run is None:
            raise DomainError("RUN_NOT_FOUND", "No run matches that id.", 404)
        return present_run(session, run)


@router.get("/approvals/pending", tags=["approvals"])
def pending_approvals() -> dict:
    with agent_session() as session:
        return {"approvals": list_pending(session)}


@router.post("/approvals/{approval_id}/approve", tags=["approvals"])
async def approve(approval_id: str, body: ApprovalDecision, request: Request) -> dict:
    return await request.app.state.harness.decide(approval_id, body.approver, "approve", body.comment)


@router.post("/approvals/{approval_id}/reject", tags=["approvals"])
async def reject(approval_id: str, body: ApprovalDecision, request: Request) -> dict:
    return await request.app.state.harness.decide(approval_id, body.approver, "reject", body.comment)


@router.get("/patients", tags=["directory"])
def patients(limit: int = 50) -> dict:
    return {"synthetic": True, "patients": list_patients(max(1, min(limit, 100)))}


@router.get("/appointments", tags=["directory"])
def appointments(limit: int = 50) -> dict:
    return {"synthetic": True, "appointments": list_appointments(max(1, min(limit, 100)))}
