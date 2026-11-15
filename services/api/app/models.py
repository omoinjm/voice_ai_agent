"""Control-plane tables (Postgres schema `platform`).

Each tenant's call data (calls, turns, tool calls, bookings) lives in its own schema `t_<slug>_<env>`, created by
provisioning; nothing here points into those schemas. Statuses are text with CHECK constraints so adding a value is a
one-line migration.
"""

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

SCHEMA = "platform"
EMBEDDING_DIMENSIONS = 1024  # Amazon Titan Text Embeddings v2 default

TENANT_STATUSES = (
    "registered",  # signed up, application not yet submitted
    "in_review",
    "info_requested",
    "rejected",
    "whatsapp_pending",  # approved; connecting the WhatsApp number
    "agent_setup",  # number ready; business configures the agent
    "provisioning",
    "staging_ready",  # deployed to staging, waiting for our test + promote
    "live",
    "suspended",
)
BUSINESS_CATEGORIES = ("medical", "legal", "insurance", "financial", "beauty", "other")
APPLICATION_STATUSES = ("draft", "submitted", "info_requested", "approved", "rejected")
DOCUMENT_STATUSES = ("uploaded", "extracting", "ready", "failed")
ENVIRONMENT_NAMES = ("staging", "prod")
ENVIRONMENT_STATUSES = ("not_deployed", "deploying", "running", "stopped", "failed")
DEPLOYMENT_KINDS = ("provision", "update", "promote", "teardown")
RUN_STATUSES = ("pending", "running", "waiting", "succeeded", "failed", "cancelled")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Base(DeclarativeBase):
    metadata = MetaData(
        schema=SCHEMA,
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_N_name)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        },
    )


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = (
        CheckConstraint(_in("status", TENANT_STATUSES), name="status"),
        CheckConstraint(_in("category", BUSINESS_CATEGORIES), name="category"),
        # Used in schema, stack and DNS names: t_<slug>_<env>, tenant-<slug>-<env>.
        CheckConstraint("slug ~ '^[a-z][a-z0-9-]{1,30}[a-z0-9]$'", name="slug"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    slug: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="registered")
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    memberships: Mapped[list["Membership"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    application: Mapped["Application | None"] = relationship(back_populates="tenant", cascade="all, delete-orphan")


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # Cognito `sub` (or `dev:<email>` locally).
    auth_subject: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()

    memberships: Mapped[list["Membership"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (CheckConstraint("role IN ('owner', 'staff')", name="role"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created_at()

    user: Mapped[User] = relationship(back_populates="memberships")
    tenant: Mapped[Tenant] = relationship(back_populates="memberships")


class Application(Base):
    """The business's registration form (one per tenant; resubmitted after an info request)."""

    __tablename__ = "applications"
    __table_args__ = (CheckConstraint(_in("status", APPLICATION_STATUSES), name="status"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decision_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()

    tenant: Mapped[Tenant] = relationship(back_populates="application")


class Document(Base):
    """A file the business uploaded to describe itself (services, prices, policies)."""

    __tablename__ = "documents"
    __table_args__ = (CheckConstraint(_in("status", DOCUMENT_STATUSES), name="status"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    s3_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="uploaded")
    extracted_text: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class KnowledgeChunk(Base):
    """A searchable piece of a tenant's documents or form answers, used by the agent's search tool."""

    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=False)
    created_at: Mapped[datetime] = _created_at()


class Review(Base):
    """An AI pre-review or an admin decision on an application."""

    __tablename__ = "reviews"
    __table_args__ = (
        CheckConstraint("kind IN ('ai', 'admin')", name="kind"),
        CheckConstraint(_in("status", RUN_STATUSES), name="status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    # AI: {"items": [{"requirement", "status": met|missing|unclear, "evidence"}], "risks": [...], "message": "..."}
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    model: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created_at()


class Environment(Base):
    """A tenant's staging or prod deployment: its CloudFormation stack and what's running there."""

    __tablename__ = "environments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name"),
        CheckConstraint(_in("name", ENVIRONMENT_NAMES), name="name"),
        CheckConstraint(_in("status", ENVIRONMENT_STATUSES), name="status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    stack_name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    db_schema: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    image_tag: Mapped[str | None] = mapped_column(Text)
    config_version: Mapped[int | None] = mapped_column()
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="not_deployed")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Deployment(Base):
    """One run of the provisioning state machine; its steps are deployment_events."""

    __tablename__ = "deployments"
    __table_args__ = (
        CheckConstraint(_in("kind", DEPLOYMENT_KINDS), name="kind"),
        CheckConstraint(_in("status", RUN_STATUSES), name="status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    execution_arn: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    started_at: Mapped[datetime] = _created_at()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeploymentEvent(Base):
    __tablename__ = "deployment_events"
    __table_args__ = (CheckConstraint(_in("status", RUN_STATUSES), name="status"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    deployment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("deployments.id", ondelete="CASCADE"), index=True, nullable=False
    )
    step: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    at: Mapped[datetime] = _created_at()


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    at: Mapped[datetime] = _created_at()
