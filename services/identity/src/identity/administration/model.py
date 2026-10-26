from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from identity.kernel.db import Base


class UserModerationLog(Base):
    __tablename__ = "user_moderation_log"
    __table_args__ = (
        CheckConstraint("action IN ('block', 'unblock', 'role_change')", name="action_valid"),
        Index("ix_user_moderation_log_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    admin_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    action: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime]
