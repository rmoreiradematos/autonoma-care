"""SQLite memory with a keyword retriever.

The retriever is the extension point for a future vector index. Retrieval is not
training: stored runs are not model weights.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import EpisodicMemoryRecord, SemanticMemoryRecord, WorkingMemoryRecord

_TOKEN = re.compile(r"[a-z0-9]{3,}")
_STOP = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "please",
    "from",
    "have",
    "appointment",
    "patient",
}


@dataclass(frozen=True)
class RetrievedMemory:
    kind: str
    topic: str
    content: str
    score: float
    source: str


class MemoryRetriever(Protocol):
    def retrieve(self, query: str, *, limit: int = 3) -> list[RetrievedMemory]:
        """Return memories relevant to `query`. Implementations must not call a model."""


def _tokens(text: str) -> set[str]:
    return {token for token in _TOKEN.findall(text.lower()) if token not in _STOP}


def relevance(query: str, document: str) -> float:
    query_tokens = _tokens(query)
    if not query_tokens:
        return 0.0
    document_tokens = _tokens(document)
    return len(query_tokens & document_tokens) / len(query_tokens)


class MemoryStore:
    def __init__(self, session: Session):
        self.session = session

    def upsert_working(self, run_id: str, key: str, value: object) -> None:
        row = self.session.scalar(
            select(WorkingMemoryRecord).where(
                WorkingMemoryRecord.run_id == run_id,
                WorkingMemoryRecord.memory_key == key,
            )
        )
        payload = json.dumps(value, default=str)
        if row is None:
            self.session.add(
                WorkingMemoryRecord(
                    run_id=run_id,
                    memory_key=key,
                    value_json=payload,
                    updated_at=datetime.now(timezone.utc),
                )
            )
        else:
            row.value_json = payload
            row.updated_at = datetime.now(timezone.utc)
        self.session.flush()

    def working(self, run_id: str) -> dict[str, object]:
        rows = self.session.scalars(select(WorkingMemoryRecord).where(WorkingMemoryRecord.run_id == run_id)).all()
        return {row.memory_key: json.loads(row.value_json) for row in rows}

    def write_episode(
        self,
        *,
        run_id: str,
        actor: str,
        request: str,
        summary: str,
        status: str,
        facts: dict,
    ) -> None:
        existing = self.session.scalar(select(EpisodicMemoryRecord).where(EpisodicMemoryRecord.run_id == run_id))
        payload = json.dumps(facts, default=str)
        if existing is None:
            self.session.add(
                EpisodicMemoryRecord(
                    run_id=run_id,
                    actor=actor,
                    request=request,
                    summary=summary,
                    status=status,
                    facts_json=payload,
                    created_at=datetime.now(timezone.utc),
                )
            )
        else:
            existing.summary = summary
            existing.status = status
            existing.facts_json = payload
        self.session.flush()

    def latest_registered_patient(self, actor: str) -> dict | None:
        rows = self.session.scalars(
            select(EpisodicMemoryRecord)
            .where(EpisodicMemoryRecord.actor == actor)
            .order_by(EpisodicMemoryRecord.id.desc())
            .limit(8)
        ).all()
        for row in rows:
            facts = json.loads(row.facts_json)
            tools = facts.get("tools") or []
            if facts.get("patient_id") and "register_patient" in tools:
                return facts
        return None

    def upsert_semantic(self, topic: str, content: str, source: str, keywords: str) -> None:
        row = self.session.scalar(select(SemanticMemoryRecord).where(SemanticMemoryRecord.topic == topic))
        if row is None:
            self.session.add(
                SemanticMemoryRecord(
                    topic=topic,
                    content=content,
                    source=source,
                    keywords=keywords,
                    updated_at=datetime.now(timezone.utc),
                )
            )
        else:
            row.content = content
            row.source = source
            row.keywords = keywords
            row.updated_at = datetime.now(timezone.utc)
        self.session.flush()

    def retrieve(self, query: str, *, limit: int = 3) -> list[RetrievedMemory]:
        found: list[RetrievedMemory] = []
        for row in self.session.scalars(select(SemanticMemoryRecord)).all():
            score = relevance(query, f"{row.topic} {row.keywords} {row.content}")
            if score >= 0.2:
                found.append(RetrievedMemory("semantic", row.topic, row.content, score, row.source))
        episodes = self.session.scalars(
            select(EpisodicMemoryRecord).order_by(EpisodicMemoryRecord.id.desc()).limit(20)
        ).all()
        for row in episodes:
            score = relevance(query, f"{row.request} {row.summary}")
            if score >= 0.34:
                found.append(RetrievedMemory("episodic", row.run_id, row.summary, score, "prior-run"))
        found.sort(key=lambda item: item.score, reverse=True)
        return found[:limit]


def seed_semantic_defaults(store: MemoryStore) -> None:
    from mcp_server.seed import APPOINTMENT_RULES, INSURANCE_RULES

    store.upsert_semantic(
        "appointment-rules",
        APPOINTMENT_RULES,
        "local-seed",
        "appointment schedule cancel reschedule slot approval cardiology",
    )
    store.upsert_semantic(
        "insurance-rules",
        INSURANCE_RULES,
        "local-seed",
        "insurance eligibility coverage claim member payer",
    )
    store.upsert_semantic(
        "demo-fixtures",
        "Synthetic demo member BH-100200 at BlueHarbor is eligible. "
        "Maria Alvarez is MRN-1001. Elena Rossi has appointment APT-ELENA. "
        "Samir Patel has appointment APT-SAMIR. Slot SLT-CLOSED is already booked. "
        "These fixtures are not real people.",
        "local-seed",
        "demo maria elena samir blueharbor slot closed",
    )
