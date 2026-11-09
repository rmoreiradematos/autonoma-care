"""Execution loop: plan, authorize, approve, call MCP, trace, and resume."""

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents import AGENT_ROLES, COORDINATOR_ID, SYSTEM_INSTRUCTIONS
from app.config import Settings
from app.context.builder import build_context
from app.database import ApprovalRecord, RunRecord, agent_session
from app.errors import DomainError, MissingFact, ToolTransportError
from app.harness.summary import render_summary
from app.mcp_client.gateway import McpGateway, ToolSpec
from app.memory.store import MemoryStore
from app.observability.tracing import Tracer, load_events
from app.orchestration.deterministic import build_plan
from app.orchestration.llm import OpenAICompatiblePlanner, openai_tools
from app.orchestration.parsing import missing_fields, parse_request
from app.security.authorization import specialist_for, tool_allowed
from app.security.redaction import redact
from app.skills.registry import get_skill, precondition_failure, skills_for_intents
from mcp_server.database import session_scope as hospital_session
from mcp_server.security import SENSITIVE_TOOLS, issue_grant

logger = logging.getLogger("app.harness")

HANDLED_OUTCOMES = {
    "PATIENT_NOT_FOUND",
    "AMBIGUOUS_PATIENT",
    "NO_AVAILABILITY",
    "APPOINTMENT_NOT_FOUND",
    "INVALID_ARGUMENTS",
    "POLICY_NOT_FOUND",
    "CLAIM_NOT_FOUND",
    "NO_POLICY",
    "PRECONDITION_FAILED",
}
PUBLIC_FACT_KEYS = (
    "patient_id",
    "mrn",
    "full_name",
    "appointment_id",
    "specialty",
    "eligible",
    "appointment_status",
    "appointment_action",
    "task_id",
    "doctor_name",
    "slot_starts_at",
    "claim_number",
    "claim_status",
    "plan_name",
    "outcome",
)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def resolve_mode(requested: str | None, settings: Settings) -> str:
    mode = (requested or settings.planner_mode or "auto").lower()
    if mode == "auto":
        return "llm" if settings.llm_api_key else "deterministic"
    if mode not in {"llm", "deterministic"}:
        raise DomainError("UNKNOWN_PLANNER", "Planner must be auto, deterministic, or llm.", 400)
    if mode == "llm" and not settings.llm_api_key:
        raise DomainError("LLM_NOT_CONFIGURED", "Set LLM_API_KEY or use the deterministic planner.", 400)
    return mode


def resolve_arguments(arguments: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for key, value in arguments.items():
        if value in ("", None):
            continue
        if isinstance(value, str) and value.startswith("$fact:"):
            fact_key = value.removeprefix("$fact:")
            if facts.get(fact_key) in ("", None):
                raise MissingFact(fact_key)
            resolved[key] = facts[fact_key]
        elif value == "$generated:schedule":
            if facts.get("patient_id") in ("", None) or facts.get("slot_id") in ("", None):
                raise MissingFact("schedule_key")
            resolved[key] = f"schedule:{facts['patient_id']}:{facts['slot_id']}"
        else:
            resolved[key] = value
    return resolved


def validate_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> list[str]:
    guarded = dict(schema)
    if guarded.get("type") == "object":
        guarded["additionalProperties"] = False
    try:
        validator = Draft202012Validator(guarded)
    except SchemaError:
        required = schema.get("required") or []
        return [f"missing {name}" for name in required if name not in arguments]
    errors = []
    for error in sorted(validator.iter_errors(arguments), key=lambda item: list(item.path)):
        location = ".".join(str(part) for part in error.path) or "arguments"
        errors.append(f"{location}: {error.message}")
    return errors


def _take_patient(facts: dict[str, Any], patient: dict[str, Any]) -> None:
    if patient.get("patient_id"):
        facts["patient_id"] = patient["patient_id"]
    if patient.get("mrn"):
        facts["mrn"] = patient["mrn"]
    if patient.get("full_name"):
        facts["full_name"] = patient["full_name"]
    if patient.get("insurance_member_id"):
        facts["member_id"] = patient["insurance_member_id"]
    if patient.get("insurance_payer"):
        facts["payer"] = patient["insurance_payer"]


def absorb(tool: str, payload: dict[str, Any], facts: dict[str, Any]) -> None:
    if tool == "search_patients":
        patients = payload.get("patients") or []
        facts["search_count"] = len(patients)
        if len(patients) == 1:
            _take_patient(facts, patients[0])
        return
    if tool in {"register_patient", "get_patient_details", "update_patient_information"}:
        patient = payload.get("patient") or {}
        if patient:
            _take_patient(facts, patient)
        return
    if tool == "get_doctor_availability":
        rejected = facts.get("rejected_slot_id")
        current = facts.get("current_slot_id")
        for slot in payload.get("slots") or []:
            if slot.get("slot_id") in {rejected, current}:
                continue
            facts["slot_id"] = slot["slot_id"]
            facts["slot_starts_at"] = slot.get("starts_at", "")
            facts["doctor_name"] = slot.get("doctor_name", "")
            facts["specialty"] = slot.get("specialty", facts.get("specialty", ""))
            break
        return
    if tool == "list_appointments":
        scheduled = [item for item in payload.get("appointments") or [] if item.get("status") == "scheduled"]
        if scheduled:
            facts["appointment_id"] = scheduled[0]["appointment_id"]
            facts["current_slot_id"] = scheduled[0]["slot_id"]
            facts["doctor_name"] = scheduled[0].get("doctor_name") or facts.get("doctor_name", "")
            facts["specialty"] = facts.get("specialty") or scheduled[0].get("specialty", "")
        return
    if tool in {"schedule_appointment", "reschedule_appointment", "cancel_appointment"}:
        appointment = payload.get("appointment") or {}
        if not appointment:
            return
        facts["appointment_id"] = appointment.get("appointment_id", "")
        facts["slot_id"] = appointment.get("slot_id") or facts.get("slot_id", "")
        facts["doctor_name"] = appointment.get("doctor_name", "")
        facts["specialty"] = appointment.get("specialty") or facts.get("specialty", "")
        facts["slot_starts_at"] = appointment.get("starts_at", "")
        facts["appointment_status"] = appointment.get("status", "")
        facts["appointment_action"] = tool
        return
    if tool == "verify_insurance_eligibility":
        facts["eligible"] = bool(payload.get("eligible"))
        facts["plan_name"] = payload.get("plan_name", "")
        facts["eligibility_code"] = payload.get("code", "")
        if payload.get("member_id"):
            facts["member_id"] = payload["member_id"]
        if payload.get("payer"):
            facts["payer"] = payload["payer"]
        return
    if tool == "get_insurance_coverage" and payload.get("ok"):
        facts["plan_name"] = payload.get("plan_name", "")
        facts["coverage_status"] = payload.get("status", "")
        return
    if tool == "get_claim_status":
        claim = payload.get("claim") or {}
        facts["claim_number"] = claim.get("claim_number", facts.get("claim_number", ""))
        facts["claim_status"] = claim.get("status", "")
        return
    if tool == "create_administrative_task":
        task = payload.get("task") or {}
        if task.get("task_id"):
            facts["task_id"] = task["task_id"]


def public_facts(facts: dict[str, Any]) -> dict[str, Any]:
    published = {key: facts[key] for key in PUBLIC_FACT_KEYS if facts.get(key) not in (None, "")}
    published["tools"] = list(facts.get("tools") or [])
    return published


class AgentHarness:
    def __init__(self, settings: Settings, gateway: McpGateway | None = None):
        self.settings = settings
        self.gateway = gateway or McpGateway(
            settings.mcp_server_url,
            settings.tool_timeout_seconds,
            settings.tool_max_retries,
        )
        self.max_iterations = settings.max_iterations

    def with_limits(self, *, max_iterations: int) -> "AgentHarness":
        clone = AgentHarness(self.settings, self.gateway)
        clone.max_iterations = max_iterations
        return clone

    async def run(self, request: str, actor: str, planner: str | None = None) -> dict[str, Any]:
        with agent_session() as session:
            return await self._start(session, request, actor, planner)

    async def execute_single_call(self, *, actor: str, agent_id: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Run one tool call through the same validation and MCP path as a planned step."""
        with agent_session() as session:
            run = self._new_run(session, request=f"direct:{tool}", actor=actor, mode="deterministic", injection=False)
            state = {
                "mode": "deterministic",
                "steps": [
                    {
                        "step_id": "direct",
                        "agent_id": agent_id,
                        "skill": "",
                        "tool": tool,
                        "description": "Direct harness tool call",
                        "arguments": arguments,
                        "resolved_arguments": None,
                        "status": "pending",
                        "grant_id": "",
                        "result": None,
                    }
                ],
                "cursor": 0,
                "facts": {"tools": []},
                "iteration": 0,
                "recoveries": 0,
                "messages": [],
            }
            run.state_json = json.dumps(state)
            try:
                specs, _resources = await self.gateway.discover()
            except Exception as exc:
                return self._finish(session, run, state, "failed", "MCP_UNAVAILABLE", error=str(exc))
            return await self._execute_plan(session, run, state, specs)

    async def decide(self, approval_id: str, approver: str, decision: str, comment: str = "") -> dict[str, Any]:
        if decision not in {"approve", "reject"}:
            raise DomainError("INVALID_DECISION", "Decision must be approve or reject.", 400)
        if len(approver.strip()) < 3:
            raise DomainError("INVALID_APPROVER", "Approver must be at least 3 characters.", 400)
        with agent_session() as session:
            approval = session.get(ApprovalRecord, approval_id)
            if approval is None:
                raise DomainError("APPROVAL_NOT_FOUND", "No approval matches that id.", 404)
            if approval.status != "pending":
                raise DomainError("APPROVAL_CLOSED", "This approval was already resolved.", 409)
            run = session.get(RunRecord, approval.run_id)
            if run is None or run.status != "waiting_approval":
                raise DomainError("RUN_NOT_WAITING", "The run is not waiting for approval.", 409)
            approval.approver = approver.strip()
            approval.comment = comment.strip()
            approval.resolved_at = _now()
            state = json.loads(run.state_json)
            tracer = Tracer(session)
            if decision == "reject":
                approval.status = "rejected"
                tracer.record(
                    run.run_id,
                    "approval_resolved",
                    agent_id=COORDINATOR_ID,
                    task=approval.tool_name,
                    payload={"decision": "reject", "approver": approval.approver, "tool": approval.tool_name},
                )
                return self._finish(session, run, state, "rejected", "REJECTED")
            arguments = json.loads(approval.arguments_json)
            grant_id = _new_id("GRT")
            with hospital_session() as hospital:
                issue_grant(
                    hospital,
                    grant_id=grant_id,
                    tool_name=approval.tool_name,
                    arguments=arguments,
                    approver=approval.approver,
                )
            approval.status = "approved"
            approval.grant_id = grant_id
            tracer.record(
                run.run_id,
                "approval_resolved",
                agent_id=COORDINATOR_ID,
                task=approval.tool_name,
                payload={
                    "decision": "approve",
                    "approver": approval.approver,
                    "tool": approval.tool_name,
                    "grant_id": grant_id,
                },
            )
            if state.get("mode") == "llm":
                state.setdefault("pending_call", {})["grant_id"] = grant_id
            else:
                state["steps"][state["cursor"]]["grant_id"] = grant_id
            run.status = "running"
            run.updated_at = _now()
            run.state_json = json.dumps(state)
            session.flush()
            try:
                specs, _resources = await self.gateway.discover()
            except Exception as exc:
                return self._finish(session, run, state, "failed", "MCP_UNAVAILABLE", error=str(exc))
            if state.get("mode") == "llm":
                return await self._execute_llm(session, run, state, specs)
            return await self._execute_plan(session, run, state, specs)

    async def _start(self, session: Session, request: str, actor: str, planner: str | None) -> dict[str, Any]:
        mode = resolve_mode(planner, self.settings)
        parsed = parse_request(request)
        run = self._new_run(session, request=request, actor=actor, mode=mode, injection=parsed.injection)
        tracer = Tracer(session)
        tracer.record(
            run.run_id,
            "run_started",
            agent_id=COORDINATOR_ID,
            payload={"planner": mode, "injection": parsed.injection, "actor": actor},
        )
        try:
            specs, resources = await self.gateway.discover()
        except Exception as exc:
            return self._finish(session, run, {"facts": {}, "steps": []}, "failed", "MCP_UNAVAILABLE", error=str(exc))
        store = MemoryStore(session)
        for uri, body in resources.items():
            topic = uri.rsplit("/", 1)[-1]
            store.upsert_semantic(topic, body, uri, topic.replace("-", " "))
        memories = store.retrieve(request, limit=3)
        memory_patient = store.latest_registered_patient(actor)
        if mode == "deterministic":
            missing = missing_fields(parsed, memory_patient if parsed.use_memory_patient else None)
            facts = self._initial_facts(parsed, memory_patient)
            context = self._context(
                request=request,
                skills=skills_for_intents(parsed.intents),
                specs=specs,
                facts=facts,
                memories=memories,
                resources=resources,
                state_note="Planning has not executed any tools yet.",
            )
            tracer.record(
                run.run_id,
                "context_built",
                agent_id=COORDINATOR_ID,
                payload={"dropped": context.dropped, "estimated_tokens": context.estimated_tokens, "manifest": context.manifest},
            )
            tracer.record(
                run.run_id,
                "memory_retrieved",
                agent_id=COORDINATOR_ID,
                payload={"memories": [{"kind": item.kind, "topic": item.topic, "score": round(item.score, 3)} for item in memories]},
            )
            if missing:
                run.missing_fields = json.dumps(missing)
                return self._finish(session, run, {"facts": facts, "steps": []}, "waiting_input", "NEEDS_INPUT", missing=missing)
            steps = build_plan(parsed, memory_patient if parsed.use_memory_patient else None)
            state = {
                "mode": "deterministic",
                "steps": steps,
                "cursor": 0,
                "facts": facts,
                "iteration": 0,
                "recoveries": 0,
                "messages": context.messages,
            }
            tracer.record(
                run.run_id,
                "plan_created",
                agent_id=COORDINATOR_ID,
                payload={"steps": [{"id": step["step_id"], "agent": step["agent_id"], "tool": step["tool"], "skill": step["skill"]} for step in steps]},
            )
            run.state_json = json.dumps(state)
            return await self._execute_plan(session, run, state, specs)

        context = self._context(
            request=request,
            skills=skills_for_intents(parsed.intents) or skills_for_intents(["register_patient", "schedule_appointment", "verify_insurance"]),
            specs=specs,
            facts={},
            memories=memories,
            resources=resources,
            state_note="No tool has run yet.",
        )
        tracer.record(
            run.run_id,
            "context_built",
            agent_id=COORDINATOR_ID,
            payload={"dropped": context.dropped, "estimated_tokens": context.estimated_tokens},
        )
        state = {
            "mode": "llm",
            "steps": [],
            "cursor": 0,
            "facts": {"tools": [], "specialty": parsed.specialty},
            "iteration": 0,
            "recoveries": 0,
            "messages": context.messages,
            "pending_call": None,
        }
        run.state_json = json.dumps(state)
        return await self._execute_llm(session, run, state, specs)

    async def _execute_plan(self, session: Session, run: RunRecord, state: dict[str, Any], specs: list[ToolSpec]) -> dict[str, Any]:
        catalog = {spec.name: spec for spec in specs}
        while state["cursor"] < len(state["steps"]):
            if state["iteration"] >= self.max_iterations:
                return self._finish(session, run, state, "failed", "ITERATION_LIMIT")
            step = state["steps"][state["cursor"]]
            try:
                arguments = step.get("resolved_arguments") or resolve_arguments(step["arguments"], state["facts"])
            except MissingFact as exc:
                if exc.key == "slot_id":
                    return self._finish(session, run, state, "completed", "NO_AVAILABILITY")
                if exc.key in {"member_id", "payer", "patient_id"}:
                    run.missing_fields = json.dumps([exc.key])
                    return self._finish(session, run, state, "waiting_input", "NEEDS_INPUT", missing=[exc.key])
                return self._finish(session, run, state, "failed", "MISSING_FACT", error=exc.key)
            step["resolved_arguments"] = arguments
            if not tool_allowed(step["agent_id"], step["tool"]):
                return self._finish(session, run, state, "failed", "POLICY_DENIED", error=f"{step['agent_id']} cannot call {step['tool']}")
            skill = get_skill(step["skill"]) if step.get("skill") else None
            if skill is not None:
                failure = precondition_failure(skill, step["tool"], arguments)
                if failure:
                    Tracer(session).record(
                        run.run_id,
                        "precondition_failed",
                        agent_id=step["agent_id"],
                        task=step["step_id"],
                        payload={"skill": skill.name, "message": failure},
                    )
                    outcome = "NO_AVAILABILITY" if "slot" in failure else "PRECONDITION_FAILED"
                    return self._finish(session, run, state, "completed", outcome, error=failure)
            spec = catalog.get(step["tool"])
            if spec is None:
                return self._finish(session, run, state, "failed", "UNKNOWN_TOOL", error=step["tool"])
            errors = validate_arguments(spec.input_schema, arguments)
            if errors:
                Tracer(session).record(
                    run.run_id,
                    "argument_rejected",
                    agent_id=step["agent_id"],
                    task=step["tool"],
                    payload={"tool": step["tool"], "errors": errors},
                )
                return self._finish(session, run, state, "completed", "INVALID_ARGUMENTS", error="; ".join(errors))
            if step["tool"] in SENSITIVE_TOOLS and not step.get("grant_id"):
                return self._pause(session, run, state, step, arguments)
            if step.get("grant_id"):
                arguments = {**arguments, "grant_id": step["grant_id"]}
            payload = await self._call_mcp(session, run, step["agent_id"], step["tool"], arguments, state)
            if payload.get("code") == "MCP_UNAVAILABLE":
                return self._finish(session, run, state, "failed", "MCP_UNAVAILABLE", error=payload.get("message", ""))
            step["result"] = {"ok": payload.get("ok"), "code": payload.get("code")}
            self._remember(session, run, state)
            if payload.get("ok"):
                stop = self._business_stop(state, step["tool"])
                if stop:
                    step["status"] = "completed"
                    return self._finish(session, run, state, "completed", stop)
                step["status"] = "completed"
                state["cursor"] += 1
                run.state_json = json.dumps(state)
                session.flush()
                continue
            if payload.get("code") == "SLOT_UNAVAILABLE" and step["tool"] == "schedule_appointment" and state["recoveries"] < 1:
                recovered = await self._recover_schedule(session, run, state, step, arguments, catalog)
                if recovered:
                    step["status"] = "completed"
                    state["cursor"] += 1
                    continue
                return self._finish(session, run, state, "completed", "SLOT_UNAVAILABLE")
            step["status"] = "failed"
            code = str(payload.get("code") or "TOOL_ERROR")
            status = "completed" if code in HANDLED_OUTCOMES else "failed"
            return self._finish(session, run, state, status, code, error=str(payload.get("message") or ""))
        return self._finish(session, run, state, "completed", "COMPLETED")

    async def _execute_llm(self, session: Session, run: RunRecord, state: dict[str, Any], specs: list[ToolSpec]) -> dict[str, Any]:
        catalog = {spec.name: spec for spec in specs}
        messages = state["messages"]
        pending = state.get("pending_call")
        if pending and pending.get("grant_id"):
            payload = await self._call_mcp(
                session,
                run,
                pending["agent_id"],
                pending["name"],
                {**pending["arguments"], "grant_id": pending["grant_id"]},
                state,
            )
            messages.append(
                {"role": "tool", "tool_call_id": pending["id"], "content": json.dumps(redact(payload), default=str)}
            )
            state["pending_call"] = None
            state["messages"] = messages
            if not payload.get("ok"):
                return self._finish(session, run, state, "completed" if payload.get("code") in HANDLED_OUTCOMES else "failed", str(payload.get("code")))
        planner = OpenAICompatiblePlanner(self.settings.llm_base_url, self.settings.llm_api_key, self.settings.llm_model)
        while state["iteration"] < self.max_iterations:
            try:
                decision = await planner.complete(messages, openai_tools(specs))
            except Exception as exc:
                return self._finish(session, run, state, "failed", "LLM_ERROR", error=str(exc))
            Tracer(session).record(
                run.run_id,
                "model_response",
                agent_id=COORDINATOR_ID,
                payload={"model": decision.model, "provider": self.settings.llm_base_url, "usage": decision.usage, "tool_calls": [call["name"] for call in decision.calls]},
            )
            messages.append(decision.raw_message)
            if not decision.calls:
                state["messages"] = messages
                summary = decision.text.strip() or render_summary("completed", "COMPLETED", state["facts"], [])
                return self._finish(session, run, state, "completed", "COMPLETED", summary=summary)
            call = decision.calls[0]
            arguments = {
                key: value
                for key, value in dict(call["arguments"]).items()
                if key != "grant_id" and value not in ("", None)
            }
            agent_id = specialist_for(call["name"])
            if call["name"] not in catalog or not tool_allowed(agent_id, call["name"]):
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": json.dumps({"ok": False, "code": "UNKNOWN_TOOL", "message": "Tool is not available to this agent."}),
                    }
                )
                state["iteration"] += 1
                continue
            errors = validate_arguments(catalog[call["name"]].input_schema, arguments)
            if errors:
                Tracer(session).record(
                    run.run_id,
                    "argument_rejected",
                    agent_id=agent_id,
                    task=call["name"],
                    payload={"tool": call["name"], "errors": errors},
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": json.dumps({"ok": False, "code": "INVALID_ARGUMENTS", "errors": errors}),
                    }
                )
                state["iteration"] += 1
                continue
            if call["name"] in SENSITIVE_TOOLS:
                state["pending_call"] = {"id": call["id"], "name": call["name"], "arguments": arguments, "agent_id": agent_id}
                state["messages"] = messages
                return self._pause_llm(session, run, state, call["name"], arguments)
            if state["iteration"] >= self.max_iterations:
                break
            payload = await self._call_mcp(session, run, agent_id, call["name"], arguments, state)
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(redact(payload), default=str)})
            state["messages"] = messages
            if payload.get("code") == "MCP_UNAVAILABLE":
                return self._finish(session, run, state, "failed", "MCP_UNAVAILABLE", error=payload.get("message", ""))
        return self._finish(session, run, state, "failed", "ITERATION_LIMIT")

    async def _call_mcp(
        self,
        session: Session,
        run: RunRecord,
        agent_id: str,
        tool: str,
        arguments: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        if state["iteration"] >= self.max_iterations:
            return {"ok": False, "code": "ITERATION_LIMIT", "message": "Iteration limit reached."}
        state["iteration"] += 1
        tracer = Tracer(session)
        tracer.record(run.run_id, "tool_call", agent_id=agent_id, task=tool, payload={"tool": tool, "arguments": arguments})
        started = time.perf_counter()
        try:
            payload = await self.gateway.call_tool(tool, arguments)
        except ToolTransportError as exc:
            payload = {"ok": False, "code": "MCP_UNAVAILABLE", "message": str(exc)}
        latency = (time.perf_counter() - started) * 1000
        tracer.record(
            run.run_id,
            "tool_result",
            agent_id=agent_id,
            task=tool,
            payload={"tool": tool, "ok": payload.get("ok"), "code": payload.get("code"), "message": payload.get("message")},
            latency_ms=round(latency, 2),
        )
        absorb(tool, payload, state["facts"])
        tools = list(state["facts"].get("tools") or [])
        tools.append(tool)
        state["facts"]["tools"] = tools
        self._remember(session, run, state)
        run.state_json = json.dumps(state)
        run.updated_at = _now()
        session.commit()
        return payload

    async def _recover_schedule(
        self,
        session: Session,
        run: RunRecord,
        state: dict[str, Any],
        step: dict[str, Any],
        arguments: dict[str, Any],
        catalog: dict[str, ToolSpec],
    ) -> bool:
        state["recoveries"] = int(state.get("recoveries") or 0) + 1
        Tracer(session).record(
            run.run_id,
            "recovery",
            agent_id="scheduling",
            task="schedule_appointment",
            payload={"reason": "SLOT_UNAVAILABLE", "rejected_slot_id": arguments.get("slot_id")},
        )
        state["facts"]["rejected_slot_id"] = arguments.get("slot_id")
        state["facts"].pop("slot_id", None)
        if "get_doctor_availability" not in catalog:
            return False
        availability = await self._call_mcp(
            session,
            run,
            "scheduling",
            "get_doctor_availability",
            {"specialty": state["facts"].get("specialty", ""), "earliest_only": False, "limit": 5},
            state,
        )
        if not availability.get("ok") or not state["facts"].get("slot_id"):
            return False
        retry_arguments = {
            "patient_id": state["facts"]["patient_id"],
            "slot_id": state["facts"]["slot_id"],
            "reason": arguments.get("reason") or "Administrative visit",
            "idempotency_key": f"schedule:{state['facts']['patient_id']}:{state['facts']['slot_id']}",
        }
        retried = await self._call_mcp(session, run, "scheduling", "schedule_appointment", retry_arguments, state)
        step["result"] = {"ok": retried.get("ok"), "code": retried.get("code"), "recovered": True}
        return bool(retried.get("ok"))

    def _pause(self, session: Session, run: RunRecord, state: dict[str, Any], step: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
        approval = ApprovalRecord(
            approval_id=_new_id("APR"),
            run_id=run.run_id,
            tool_name=step["tool"],
            arguments_json=json.dumps(arguments),
            status="pending",
            created_at=_now(),
        )
        session.add(approval)
        step["status"] = "waiting_approval"
        state["approval_tool"] = step["tool"]
        run.state_json = json.dumps(state)
        Tracer(session).record(
            run.run_id,
            "approval_requested",
            agent_id=step["agent_id"],
            task=step["tool"],
            payload={"approval_id": approval.approval_id, "tool": step["tool"], "arguments": arguments, "reason": SENSITIVE_TOOLS[step["tool"]]},
        )
        return self._finish(
            session,
            run,
            state,
            "waiting_approval",
            "APPROVAL_REQUIRED",
            approval_tool=step["tool"],
        )

    def _pause_llm(self, session: Session, run: RunRecord, state: dict[str, Any], tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        approval = ApprovalRecord(
            approval_id=_new_id("APR"),
            run_id=run.run_id,
            tool_name=tool,
            arguments_json=json.dumps(arguments),
            status="pending",
            created_at=_now(),
        )
        session.add(approval)
        state["approval_tool"] = tool
        run.state_json = json.dumps(state)
        Tracer(session).record(
            run.run_id,
            "approval_requested",
            agent_id=specialist_for(tool),
            task=tool,
            payload={"approval_id": approval.approval_id, "tool": tool, "arguments": arguments, "reason": SENSITIVE_TOOLS[tool]},
        )
        return self._finish(session, run, state, "waiting_approval", "APPROVAL_REQUIRED", approval_tool=tool)

    def _business_stop(self, state: dict[str, Any], tool: str) -> str | None:
        facts = state["facts"]
        later = [step["tool"] for step in state["steps"][state["cursor"] + 1 :]]
        if tool == "search_patients":
            count = facts.get("search_count")
            needs_one = any(
                name in later
                for name in ("get_patient_details", "update_patient_information", "schedule_appointment", "list_appointments", "create_administrative_task")
            )
            if count == 0:
                return "PATIENT_NOT_FOUND"
            if count != 1 and needs_one:
                return "AMBIGUOUS_PATIENT"
        if tool == "get_doctor_availability" and "slot_id" not in facts and "schedule_appointment" in later:
            return "NO_AVAILABILITY"
        if tool == "get_doctor_availability" and "slot_id" not in facts and "reschedule_appointment" in later:
            return "NO_AVAILABILITY"
        if tool == "list_appointments" and "appointment_id" not in facts and any(
            name in later for name in ("cancel_appointment", "reschedule_appointment")
        ):
            return "APPOINTMENT_NOT_FOUND"
        if tool == "get_patient_details" and "patient_id" not in facts:
            return "PATIENT_NOT_FOUND"
        return None

    def _remember(self, session: Session, run: RunRecord, state: dict[str, Any]) -> None:
        MemoryStore(session).upsert_working(run.run_id, "facts", public_facts(state["facts"]))
        MemoryStore(session).upsert_working(
            run.run_id,
            "cursor",
            {"cursor": state.get("cursor", 0), "iteration": state.get("iteration", 0)},
        )

    def _finish(
        self,
        session: Session,
        run: RunRecord,
        state: dict[str, Any],
        status: str,
        outcome: str,
        *,
        missing: list[str] | None = None,
        error: str = "",
        summary: str = "",
        approval_tool: str = "",
    ) -> dict[str, Any]:
        facts = state.get("facts") or {}
        facts["outcome"] = outcome
        missing = missing if missing is not None else json.loads(run.missing_fields or "[]")
        run.status = status
        run.outcome = outcome
        run.error = error
        run.missing_fields = json.dumps(missing)
        run.summary = summary or render_summary(status, outcome, facts, missing, approval_tool or state.get("approval_tool", ""))
        run.result_json = json.dumps({"facts": redact(public_facts(facts)), "outcome": outcome, "recoveries": state.get("recoveries", 0)})
        run.state_json = json.dumps(state)
        run.updated_at = _now()
        if status in {"completed", "failed", "rejected"}:
            run.completed_at = _now()
        MemoryStore(session).write_episode(
            run_id=run.run_id,
            actor=run.actor,
            request=run.request,
            summary=run.summary,
            status=status,
            facts=public_facts(facts),
        )
        Tracer(session).record(
            run.run_id,
            "run_status",
            agent_id=COORDINATOR_ID,
            payload={"status": status, "outcome": outcome, "summary": run.summary},
        )
        session.flush()
        return present_run(session, run)

    def _new_run(self, session: Session, *, request: str, actor: str, mode: str, injection: bool) -> RunRecord:
        run = RunRecord(
            run_id=_new_id("RUN"),
            actor=actor,
            request=request,
            status="running",
            planner=mode,
            agent_id=COORDINATOR_ID,
            injection_flag=injection,
            created_at=_now(),
            updated_at=_now(),
            state_json="{}",
            result_json="{}",
            missing_fields="[]",
        )
        session.add(run)
        session.flush()
        return run

    def _initial_facts(self, parsed, memory_patient: dict | None) -> dict[str, Any]:
        facts: dict[str, Any] = {"tools": []}
        if parsed.specialty:
            facts["specialty"] = parsed.specialty
        if parsed.member_id:
            facts["member_id"] = parsed.member_id
        if parsed.payer:
            facts["payer"] = parsed.payer
        if parsed.claim_number:
            facts["claim_number"] = parsed.claim_number
        if parsed.appointment_id:
            facts["appointment_id"] = parsed.appointment_id
        if parsed.slot_id:
            facts["slot_id"] = parsed.slot_id
        if parsed.use_memory_patient and memory_patient and memory_patient.get("patient_id"):
            facts["patient_id"] = memory_patient["patient_id"]
            facts["full_name"] = memory_patient.get("full_name", "")
            facts["mrn"] = memory_patient.get("mrn", "")
        return facts

    def _context(self, *, request, skills, specs, facts, memories, resources, state_note):
        skill_text = "\n".join(
            f"- {skill.name}: {skill.instructions} Tools: {', '.join(skill.tools)}. Failure: {skill.failure_handling}"
            for skill in skills
        )
        tool_text = "\n".join(f"- {spec.name}: {spec.description}" for spec in specs)
        memory_text = "\n".join(f"- [{item.kind}] {item.topic}: {item.content[:500]}" for item in memories)
        policy_text = "\n\n".join(f"{uri}\n{body[:1500]}" for uri, body in resources.items())
        role = AGENT_ROLES[COORDINATOR_ID]
        return build_context(
            system=SYSTEM_INSTRUCTIONS,
            role=role,
            request=request,
            skills=skill_text,
            tools=tool_text,
            state=state_note + "\n" + json.dumps(redact(facts), default=str),
            memories=memory_text,
            policies=policy_text,
            budget=self.settings.context_token_budget,
        )


def present_run(session: Session, run: RunRecord) -> dict[str, Any]:
    pending = session.scalar(
        select(ApprovalRecord).where(ApprovalRecord.run_id == run.run_id, ApprovalRecord.status == "pending")
    )
    return {
        "run_id": run.run_id,
        "status": run.status,
        "planner": run.planner,
        "agent_id": run.agent_id,
        "summary": run.summary,
        "outcome": run.outcome,
        "missing_fields": json.loads(run.missing_fields or "[]"),
        "result": json.loads(run.result_json or "{}"),
        "injection_flag": run.injection_flag,
        "error": run.error,
        "pending_approval": _approval_view(pending) if pending else None,
        "events": load_events(session, run.run_id),
        "created_at": _iso(run.created_at),
        "updated_at": _iso(run.updated_at),
        "completed_at": _iso(run.completed_at),
    }


def _approval_view(approval: ApprovalRecord) -> dict[str, Any]:
    return {
        "approval_id": approval.approval_id,
        "run_id": approval.run_id,
        "tool_name": approval.tool_name,
        "arguments": redact(json.loads(approval.arguments_json)),
        "status": approval.status,
        "approver": approval.approver,
        "comment": approval.comment,
        "grant_id": approval.grant_id,
        "reason": SENSITIVE_TOOLS.get(approval.tool_name, ""),
        "created_at": _iso(approval.created_at),
        "resolved_at": _iso(approval.resolved_at),
    }


def list_pending(session: Session) -> list[dict[str, Any]]:
    rows = session.scalars(select(ApprovalRecord).where(ApprovalRecord.status == "pending").order_by(ApprovalRecord.created_at)).all()
    return [_approval_view(row) for row in rows]
