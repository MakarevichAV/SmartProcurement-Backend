"""Durable background-job model (T029; data-model.md §10, research.md §3).

A single table drained by the worker with ``SELECT ... FOR UPDATE SKIP LOCKED``. Enqueue
happens in the same transaction as the domain write that schedules the job.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import Timestamps, UUIDPrimaryKey, enum_column

JOB_STATUSES = ("pending", "running", "done", "failed")


class Job(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "job"

    # Nullable for global jobs; scoped jobs still record their enterprise in the payload.
    enterprise_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String, nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    status: Mapped[str] = enum_column("status", *JOB_STATUSES, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    locked_by: Mapped[str | None] = mapped_column(String, nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
