"""Persistence schema (PRD §12). PostgreSQL; snake_case; UUID primary keys;
monetary values as ``numeric`` (never float).

These SQLModel tables are the foundation the later phases write into. Phase 0's
generator does not require a live database — it emits files — but the schema is
defined here so the ingest/settle/reconcile phases have a target. The
``knowledge_chunk`` vector column (Phase 1) is declared with pgvector.

Note: the canonical in-memory model lives in ``app.models``. These tables are the
durable projection of it plus the settlement/exception/eval rows.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlmodel import Field, SQLModel, Column
from sqlalchemy import Numeric, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

MONEY = Column(Numeric(12, 2), nullable=False)


def _uuid() -> str:
    return str(uuid.uuid4())


class Remittance(SQLModel, table=True):
    __tablename__ = "remittance"
    id: str = Field(default_factory=_uuid, primary_key=True)
    payer: str
    trn_trace_number: str = Field(index=True)
    payment_method: str
    eft_amount: Decimal = Field(sa_column=Column(Numeric(12, 2)))
    paid_date: date
    source_type: str                                # "835" | "pdf"
    content_hash: str = Field(index=True, unique=True)  # idempotency
    status: str = "received"


class Claim(SQLModel, table=True):
    __tablename__ = "claim"
    id: str = Field(default_factory=_uuid, primary_key=True)
    remittance_id: str = Field(foreign_key="remittance.id", index=True)
    patient_ref: str
    date_of_service: date
    clp_status_code: str
    billed_total: Decimal = Field(sa_column=Column(Numeric(12, 2)))
    status: str = "open"                            # open | settled | exception


class ClaimLine(SQLModel, table=True):
    __tablename__ = "claim_line"
    id: str = Field(default_factory=_uuid, primary_key=True)
    claim_id: str = Field(foreign_key="claim.id", index=True)
    cdt_code: str
    billed: Decimal = Field(sa_column=Column(Numeric(12, 2)))
    allowed: Decimal = Field(sa_column=Column(Numeric(12, 2)))
    paid: Decimal = Field(sa_column=Column(Numeric(12, 2)))
    adjustment: Decimal = Field(sa_column=Column(Numeric(12, 2)))
    group_code: Optional[str] = None
    carc_code: Optional[str] = Field(default=None, foreign_key="carc_code.code")
    patient_responsibility: Decimal = Field(sa_column=Column(Numeric(12, 2)))
    extraction_confidence: float = 1.0


class LineSettlement(SQLModel, table=True):
    """Append-only / immutable. Corrections are new records (Phase 6)."""
    __tablename__ = "line_settlement"
    id: str = Field(default_factory=_uuid, primary_key=True)
    claim_line_id: str = Field(foreign_key="claim_line.id", index=True)
    insurance_paid: Decimal = Field(sa_column=Column(Numeric(12, 2)))
    contractual_writeoff: Decimal = Field(sa_column=Column(Numeric(12, 2)))
    patient_responsibility: Decimal = Field(sa_column=Column(Numeric(12, 2)))
    secondary_responsibility: Decimal = Field(sa_column=Column(Numeric(12, 2)))
    action: str
    remittance_id: str = Field(foreign_key="remittance.id", index=True)  # audit
    settled_at: datetime = Field(default_factory=datetime.utcnow)


class CarcCode(SQLModel, table=True):
    """Source for the RAG corpus (Phase 1)."""
    __tablename__ = "carc_code"
    code: str = Field(primary_key=True)
    group_code: Optional[str] = None
    description: str
    recommended_action: Optional[str] = None


class KnowledgeChunk(SQLModel, table=True):
    """The retrievable corpus (Phase 1). ``embedding`` is a pgvector column added
    via migration/DDL (see BuildSpec_Phase0_Phase1.md); declared loosely here so
    the table imports without the pgvector SQLAlchemy type at Phase 0."""
    __tablename__ = "knowledge_chunk"
    id: str = Field(primary_key=True)
    chunk_type: str                                 # carc | rarc | group_code | payer_rule | playbook
    code: Optional[str] = Field(default=None, index=True)
    group_applicable: Optional[list[str]] = Field(default=None, sa_column=Column(ARRAY(String)))
    payer: Optional[str] = Field(default=None, index=True)
    content: str
    chunk_metadata: Optional[dict] = Field(default=None, sa_column=Column("metadata", JSONB))
    # embedding vector(1536) — created in Phase 1 DDL alongside the HNSW index.


class Exception(SQLModel, table=True):
    __tablename__ = "exception"
    id: str = Field(default_factory=_uuid, primary_key=True)
    claim_line_id: Optional[str] = Field(default=None, foreign_key="claim_line.id", index=True)
    reason: str
    confidence: Optional[float] = None
    evidence: Optional[dict] = Field(default=None, sa_column=Column(JSONB))  # citations / draft decision
    recommended_action: Optional[str] = None
    status: str = "open"


class EvalCase(SQLModel, table=True):
    """The golden set."""
    __tablename__ = "eval_case"
    id: str = Field(default_factory=_uuid, primary_key=True)
    input_ref: str
    expected_decision: Optional[dict] = Field(default=None, sa_column=Column(JSONB))
    expected_settlement: Optional[dict] = Field(default=None, sa_column=Column(JSONB))
