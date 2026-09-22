"""Durable-queue bindings for ``detect_risks`` (T070) and ``generate_explanation`` (T071).

``detect_risks`` enqueues ``generate_explanation`` itself (see
``rules._maybe_enqueue_explanation``) whenever it creates or updates a finding whose
``ai_status`` is not yet ``ready``, guarded against duplicate pending/running jobs for the
same finding — this module only supplies the handler that receives that job.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.provider import LLMUnavailable
from app.analysis.explain import explain_risk
from app.analysis.rules import detect_risks
from app.jobs.models import Job
from app.jobs.registry import register


@register("detect_risks")
async def detect_risks_handler(session: AsyncSession, job: Job) -> None:
    payload = job.payload
    await detect_risks(session, enterprise_id=uuid.UUID(payload["enterprise_id"]))


@register("generate_explanation")
async def generate_explanation_handler(session: AsyncSession, job: Job) -> None:
    payload = job.payload
    try:
        await explain_risk(
            session,
            enterprise_id=uuid.UUID(payload["enterprise_id"]),
            risk_finding_id=uuid.UUID(payload["risk_finding_id"]),
        )
    except LLMUnavailable:
        # explain_risk already wrote the fail-safe (ai_status=unavailable, observability gap,
        # ai_unavailable audit record) into this same session before raising. Swallowing here
        # lets the worker's normal success path (app.worker._run_job) commit those writes,
        # instead of its generic-exception path rolling them back. Retry happens naturally:
        # the next detect_risks pass re-enqueues generate_explanation for any finding whose
        # ai_status is still not "ready" (rules.py._maybe_enqueue_explanation).
        pass
