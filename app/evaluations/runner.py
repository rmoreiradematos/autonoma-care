"""Run the scenario suite against a live harness and MCP server."""

from datetime import datetime, timezone

from app.evaluations.scenarios import SCENARIOS, Scenario, remember_elena_slot
from app.evaluations.scoring import (
    approval_compliant,
    argument_validity,
    executed_argument_validity,
    policy_compliant,
    tool_names,
)
from app.harness.engine import AgentHarness


def _latency_ms(view: dict) -> float:
    start = _parse(view["created_at"])
    end = _parse(view["completed_at"] or view["updated_at"])
    return max(0.0, (end - start).total_seconds() * 1000)


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


async def run_suite(harness: AgentHarness) -> dict:
    results = []
    for scenario in SCENARIOS:
        results.append(await _run_scenario(harness, scenario))
    return {"scenarios": results, "summary": summarize(results)}


async def _run_scenario(harness: AgentHarness, scenario: Scenario) -> dict:
    active = harness.with_limits(max_iterations=scenario.max_iterations) if scenario.max_iterations else harness
    error = ""
    view: dict = {}
    try:
        if scenario.scenario_id == "reschedule-with-approval":
            remember_elena_slot({}, [], scenario.box)
        if scenario.direct:
            agent_id, tool, arguments = scenario.direct
            view = await active.execute_single_call(actor=scenario.actor, agent_id=agent_id, tool=tool, arguments=arguments)
        else:
            requests = scenario.requests or (scenario.request,)
            for text in requests:
                view = await active.run(text, scenario.actor, "deterministic")
                if view["status"] == "waiting_approval" and scenario.approve and text == requests[-1]:
                    view = await active.decide(
                        view["pending_approval"]["approval_id"],
                        "eval.lead@example.test",
                        "approve",
                        f"evaluation approval for {scenario.scenario_id}",
                    )
                elif view["status"] == "waiting_approval" and scenario.reject and text == requests[-1]:
                    view = await active.decide(
                        view["pending_approval"]["approval_id"],
                        "eval.lead@example.test",
                        "reject",
                        "evaluation rejection",
                    )
        events = view.get("events") or []
        scenario.check(view, events, scenario.box)
        business_ok = True
    except Exception as exc:
        business_ok = False
        error = str(exc)
        events = view.get("events") or []
    actual = tool_names(events)
    selection = 1.0 if tuple(actual) == scenario.expected_tools else 0.0
    status_ok = view.get("status") == scenario.expected_status
    workflow_ok = business_ok and selection == 1.0 and status_ok
    failures = [
        event for event in events if event["event_type"] == "tool_result" and event["payload"].get("ok") is False
    ]
    recoveries = [event for event in events if event["event_type"] == "recovery"]
    return {
        "id": scenario.scenario_id,
        "description": scenario.description,
        "passed": workflow_ok and policy_compliant(events) and approval_compliant(
            events, expects_approval=scenario.approve, expects_rejection=scenario.reject
        ),
        "task_completed": status_ok and business_ok,
        "tool_selection_accuracy": selection,
        "argument_validity": argument_validity(events),
        "executed_argument_validity": executed_argument_validity(events),
        "workflow_correct": workflow_ok,
        "policy_compliant": policy_compliant(events),
        "approval_compliant": approval_compliant(events, expects_approval=scenario.approve, expects_rejection=scenario.reject),
        "latency_ms": round(_latency_ms(view), 2) if view.get("created_at") else 0,
        "tool_calls": len(actual),
        "expected_tools": list(scenario.expected_tools),
        "actual_tools": actual,
        "failures": len(failures),
        "recoveries": len(recoveries),
        "status": view.get("status"),
        "outcome": view.get("outcome"),
        "summary": view.get("summary", ""),
        "error": error,
    }


def summarize(results: list[dict]) -> dict:
    count = len(results) or 1

    def mean(key: str) -> float:
        return round(sum(item[key] for item in results) / count, 4)

    failures = sum(item["failures"] for item in results)
    recoveries = sum(item["recoveries"] for item in results)
    return {
        "scenarios": len(results),
        "passed": sum(1 for item in results if item["passed"]),
        "task_completion_rate": mean("task_completed"),
        "tool_selection_accuracy": mean("tool_selection_accuracy"),
        "argument_validity": mean("argument_validity"),
        "executed_argument_validity": mean("executed_argument_validity"),
        "workflow_correctness": mean("workflow_correct"),
        "policy_compliance": mean("policy_compliant"),
        "approval_compliance": mean("approval_compliant"),
        "mean_latency_ms": round(sum(item["latency_ms"] for item in results) / count, 2),
        "mean_tool_calls": round(sum(item["tool_calls"] for item in results) / count, 2),
        "tool_failures": failures,
        "recoveries": recoveries,
        "failure_recovery_rate": round(recoveries / failures, 4) if failures else 1.0,
    }
