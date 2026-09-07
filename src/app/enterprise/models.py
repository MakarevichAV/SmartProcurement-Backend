"""Enterprise configuration (T017).

The global config boundary: one active enterprise per v1 deployment, but every scoped table
carries ``enterprise_id`` so multiple isolated enterprises can be added later without a
schema rewrite (data-model.md §1).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import Timestamps, UUIDPrimaryKey, enum_column

EXECUTION_ADAPTERS = ("simulated", "generic_rest")


class Enterprise(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "enterprise"

    name: Mapped[str] = mapped_column(String, nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    active_execution_adapter: Mapped[str] = enum_column(
        "active_execution_adapter", *EXECUTION_ADAPTERS, default="simulated"
    )
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
