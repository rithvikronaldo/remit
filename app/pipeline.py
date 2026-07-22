"""End-to-end pipeline orchestrator.

Ties the phases together for one remittance:

    ingest → parse(835) | extract(PDF) → match → settle (decide inside)
           → detect (revenue integrity) → reconcile

and routes every stage's exceptions into a shared queue. This is what the eval
harness (Phase 9) and the dashboard/demo (Phase 10) drive. Settlement commits only
if the remittance reconciles and has no held lines (fail-closed, atomic).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.detect import UnderpaymentResult, apply_findings, detect_underpayments
from app.exceptions import ExceptionQueue, ingest_results
from app.ingest import content_hash, detect_source_type
from app.match import OpenClaimRepository, match_remittance
from app.models import Remittance
from app.reconcile import ReconciliationResult, reconcile
from app.settle import SettlementResult, SettlementStore, settle_remittance


@dataclass
class RemittanceRun:
    remittance_id: str
    payer: str
    source_type: str
    content_hash: str
    matched_count: int
    unmatched_count: int
    settlement: SettlementResult
    reconciliation: ReconciliationResult
    committed: bool
    records_written: int
    latency_ms: float
    parse_exceptions: list[dict] = field(default_factory=list)
    underpayment: Optional[UnderpaymentResult] = None


def process_remittance(
    remit: Remittance, repo: OpenClaimRepository, queue: ExceptionQueue,
    *, store=None, chain=None, cache=None, settlement_store: Optional[SettlementStore] = None,
    source_type: str = "835", content_hash_value: str = "", parse_exceptions=None,
) -> RemittanceRun:
    t0 = time.perf_counter()
    parse_exceptions = parse_exceptions or []

    match_result = match_remittance(remit, repo)
    settlement = settle_remittance(remit, store=store, chain=chain, cache=cache)
    # Revenue integrity: a contract break isn't an arithmetic break — the line
    # balances and the deposit ties, so only the contract comparison can see it.
    underpayment = detect_underpayments(remit)
    apply_findings(settlement, underpayment)
    recon = reconcile(remit, settlement)

    # Route everything fail-closed into the queue.
    ingest_results(queue, match_result=match_result, settlement=settlement, recon=recon)
    for e in parse_exceptions:
        queue.add(e["reason"], evidence={"detail": e.get("detail")})

    committed = recon.tied and not recon.held and not parse_exceptions
    written = settlement_store.commit(settlement) if (committed and settlement_store) else 0

    return RemittanceRun(
        remittance_id=remit.trn, payer=remit.payer, source_type=source_type,
        content_hash=content_hash_value, matched_count=match_result.matched_count,
        unmatched_count=len(match_result.exceptions), settlement=settlement,
        reconciliation=recon, committed=committed, records_written=written,
        latency_ms=round((time.perf_counter() - t0) * 1000, 2),
        parse_exceptions=parse_exceptions, underpayment=underpayment,
    )


def run_file(
    path: str, repo: OpenClaimRepository, queue: ExceptionQueue,
    *, store=None, chain=None, cache=None, settlement_store: Optional[SettlementStore] = None,
    extractor=None,
) -> RemittanceRun:
    """Detect 835 vs PDF, produce the canonical Remittance, then process it."""
    p = Path(path)
    data = p.read_bytes()
    source = detect_source_type(p.name, data)

    if source == "835":
        from app.parse import parse_835
        pr = parse_835(data.decode())
        remit, chash, pexc = pr.remittance, pr.content_hash, pr.exceptions
    elif source == "pdf":
        from app.extract import extract_pdf
        er = extract_pdf(str(p), extractor=extractor)
        remit, chash, pexc = er.remittance, content_hash(data), er.exceptions
    else:
        raise ValueError(f"unrecognized source type for {p.name}")

    return process_remittance(
        remit, repo, queue, store=store, chain=chain, cache=cache,
        settlement_store=settlement_store, source_type=source,
        content_hash_value=chash, parse_exceptions=pexc,
    )
