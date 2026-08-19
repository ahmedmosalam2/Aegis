from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from apps.api.database import Base


class IncidentEvent(Base):
    """Records every state change and action taken on an incident.

    This creates the audit trail / timeline for each incident.
    Every status transition, diagnosis step, remediation action,
    and approval decision is logged as an event.
    """

    __tablename__ = "incident_events"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    incident_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("incidents.id", ondelete="CASCADE"),
        nullable=False,
    )

    event_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    old_status: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    new_status: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    metadata_: Mapped[dict | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
    )

    created_by: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="system",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    incident = relationship("Incident", back_populates="events")
