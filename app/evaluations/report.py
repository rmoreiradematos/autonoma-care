"""Readable evaluation report."""


def render_markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# Agent evaluation report",
        "",
        "Deterministic checks against the hospital database and the execution trace.",
        "An LLM did not grade these scenarios.",
        "",
        "## Summary",
        "",
        f"- Scenarios passed: {summary['passed']} / {summary['scenarios']}",
        f"- Task completion rate: {summary['task_completion_rate']}",
        f"- Tool selection accuracy: {summary['tool_selection_accuracy']}",
        f"- Argument validity of proposals: {summary['argument_validity']}",
        f"- Executed calls with valid arguments: {summary['executed_argument_validity']}",
        f"- Workflow correctness: {summary['workflow_correctness']}",
        f"- Policy compliance: {summary['policy_compliance']}",
        f"- Approval compliance: {summary['approval_compliance']}",
        f"- Mean latency (ms): {summary['mean_latency_ms']}",
        f"- Mean tool calls: {summary['mean_tool_calls']}",
        f"- Tool failures: {summary['tool_failures']}",
        f"- Recoveries: {summary['recoveries']}",
        f"- Failure recovery rate: {summary['failure_recovery_rate']}",
        "",
        "## Scenarios",
        "",
        "| Scenario | Pass | Status | Tools | Notes |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in report["scenarios"]:
        notes = item["error"] or item["outcome"] or ""
        tools = ", ".join(item["actual_tools"]) or "—"
        lines.append(
            f"| {item['id']} | {'yes' if item['passed'] else 'no'} | {item['status']} | {tools} | {notes.replace('|', '/')} |"
        )
    lines.append("")
    return "\n".join(lines)
