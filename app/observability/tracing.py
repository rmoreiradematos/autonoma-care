"""Persist redacted trace events for a run."""

import json
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import TraceEventRecord
from app.security.redaction import redact


class Tracer:
    def __init__(self, session: Session):
        self.session = session

    def record(
        self,
        run_id: str,
        event_type: str,
        *,
        agent_id: str = "",
        task: str = "",
        payload: dict | None = None,
        latency_ms: float | None = None,
    ) -> None:
        current = self.session.scalar(
            select(func.max(TraceEventRecord.sequence)).where(TraceEventRecord.run_id == run_id)
        )
        self.session.add(
            TraceEventRecord(
                run_id=run_id,
                sequence=int(current or 0) + 1,
                event_type=event_type,
                agent_id=agent_id,
                task=task,
                payload_json=json.dumps(redact(payload or {}), default=str),
                latency_ms=latency_ms,
                created_at=datetime.now(timezone.utc),
            )
        )
        self.session.flush()


def load_events(session: Session, run_id: str) -> list[dict]:
    rows = session.scalars(
        select(TraceEventRecord).where(TraceEventRecord.run_id == run_id).order_by(TraceEventRecord.sequence)
    ).all()
    return [
        {
            "sequence": row.sequence,
            "event_type": row.event_type,
            "agent_id": row.agent_id,
            "task": row.task,
            "payload": json.loads(row.payload_json),
            "latency_ms": row.latency_ms,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]
