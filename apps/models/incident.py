from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, String, Text, func, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from apps.api.database import Base
from apps.core.enums import IncidentStatus, IncidentSeverity


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    severity: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=IncidentSeverity.MEDIUM.value,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=IncidentStatus.DETECTED.value,
    )

    # Link to the service that this incident affects
    service_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("services.id"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    service = relationship("Service", back_populates="incidents")
    events = relationship(
        "IncidentEvent",
        back_populates="incident",
        order_by="IncidentEvent.created_at",
    )