"""ChiefMessage model — 1:1 PM chat with a Chief Runtime.

Separate from chat_messages so the Chief flow stays decoupled from
regular Agent chats and the Chief Runtime lifecycle.
"""
from __future__ import annotations
import uuid
from datetime import datetime
from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ChiefMessage(Base):
    """A user or Chief message in a Chief 1:1 chat."""

    __tablename__ = "chief_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    chief_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # 'user' | 'chief'
    content: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
