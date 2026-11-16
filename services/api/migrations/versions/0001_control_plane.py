"""control plane

Revision ID: 0001
Revises:
Create Date: 2026-11-08 01:05:53.540400
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("slug", sa.String(length=32), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="registered", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "category IN ('medical', 'legal', 'insurance', 'financial', 'beauty', 'other')",
            name=op.f("ck_tenants_category"),
        ),
        sa.CheckConstraint("slug ~ '^[a-z][a-z0-9-]{1,30}[a-z0-9]$'", name=op.f("ck_tenants_slug")),
        sa.CheckConstraint(
            "status IN ('registered', 'in_review', 'info_requested', 'rejected', 'whatsapp_pending', 'agent_setup', 'provisioning', 'staging_ready', 'live', 'suspended')",
            name=op.f("ck_tenants_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenants")),
        sa.UniqueConstraint("slug", name=op.f("uq_tenants_slug")),
        schema="platform",
    )
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("auth_subject", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("auth_subject", name=op.f("uq_users_auth_subject")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
        schema="platform",
    )
    op.create_table(
        "applications",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("status", sa.Text(), server_default="draft", nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.UUID(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'submitted', 'info_requested', 'approved', 'rejected')",
            name=op.f("ck_applications_status"),
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["platform.users.id"], name=op.f("fk_applications_decided_by_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["platform.tenants.id"], name=op.f("fk_applications_tenant_id_tenants"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_applications")),
        sa.UniqueConstraint("tenant_id", name=op.f("uq_applications_tenant_id")),
        schema="platform",
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["platform.users.id"], name=op.f("fk_audit_log_actor_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["platform.tenants.id"], name=op.f("fk_audit_log_tenant_id_tenants"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
        schema="platform",
    )
    op.create_index(op.f("ix_audit_log_tenant_id"), "audit_log", ["tenant_id"], unique=False, schema="platform")
    op.create_table(
        "deployments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("execution_arn", sa.Text(), nullable=True),
        sa.Column("requested_by", sa.UUID(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('provision', 'update', 'promote', 'teardown')", name=op.f("ck_deployments_kind")),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'waiting', 'succeeded', 'failed', 'cancelled')",
            name=op.f("ck_deployments_status"),
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"], ["platform.users.id"], name=op.f("fk_deployments_requested_by_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["platform.tenants.id"], name=op.f("fk_deployments_tenant_id_tenants"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deployments")),
        schema="platform",
    )
    op.create_index(op.f("ix_deployments_tenant_id"), "deployments", ["tenant_id"], unique=False, schema="platform")
    op.create_table(
        "documents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("s3_key", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="uploaded", nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('uploaded', 'extracting', 'ready', 'failed')", name=op.f("ck_documents_status")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["platform.tenants.id"], name=op.f("fk_documents_tenant_id_tenants"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.UniqueConstraint("s3_key", name=op.f("uq_documents_s3_key")),
        schema="platform",
    )
    op.create_index(op.f("ix_documents_tenant_id"), "documents", ["tenant_id"], unique=False, schema="platform")
    op.create_table(
        "environments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("stack_name", sa.Text(), nullable=False),
        sa.Column("db_schema", sa.Text(), nullable=False),
        sa.Column("image_tag", sa.Text(), nullable=True),
        sa.Column("config_version", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), server_default="not_deployed", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("name IN ('staging', 'prod')", name=op.f("ck_environments_name")),
        sa.CheckConstraint(
            "status IN ('not_deployed', 'deploying', 'running', 'stopped', 'failed')",
            name=op.f("ck_environments_status"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["platform.tenants.id"], name=op.f("fk_environments_tenant_id_tenants"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_environments")),
        sa.UniqueConstraint("db_schema", name=op.f("uq_environments_db_schema")),
        sa.UniqueConstraint("stack_name", name=op.f("uq_environments_stack_name")),
        sa.UniqueConstraint("tenant_id", "name", name=op.f("uq_environments_tenant_id_name")),
        schema="platform",
    )
    op.create_table(
        "memberships",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("role IN ('owner', 'staff')", name=op.f("ck_memberships_role")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["platform.tenants.id"], name=op.f("fk_memberships_tenant_id_tenants"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["platform.users.id"], name=op.f("fk_memberships_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "tenant_id", name=op.f("pk_memberships")),
        schema="platform",
    )
    op.create_table(
        "deployment_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("deployment_id", sa.UUID(), nullable=False),
        sa.Column("step", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'waiting', 'succeeded', 'failed', 'cancelled')",
            name=op.f("ck_deployment_events_status"),
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id"],
            ["platform.deployments.id"],
            name=op.f("fk_deployment_events_deployment_id_deployments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deployment_events")),
        schema="platform",
    )
    op.create_index(
        op.f("ix_deployment_events_deployment_id"),
        "deployment_events",
        ["deployment_id"],
        unique=False,
        schema="platform",
    )
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["platform.documents.id"],
            name=op.f("fk_knowledge_chunks_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenants.id"],
            name=op.f("fk_knowledge_chunks_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_chunks")),
        schema="platform",
    )
    op.create_index(
        op.f("ix_knowledge_chunks_tenant_id"), "knowledge_chunks", ["tenant_id"], unique=False, schema="platform"
    )
    # Cosine-distance nearest-neighbour search for the agent's knowledge tool.
    op.execute(
        "CREATE INDEX ix_knowledge_chunks_embedding ON platform.knowledge_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.create_table(
        "reviews",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("application_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("kind IN ('ai', 'admin')", name=op.f("ck_reviews_kind")),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'waiting', 'succeeded', 'failed', 'cancelled')",
            name=op.f("ck_reviews_status"),
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["platform.applications.id"],
            name=op.f("fk_reviews_application_id_applications"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["platform.users.id"], name=op.f("fk_reviews_created_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reviews")),
        schema="platform",
    )
    op.create_index(op.f("ix_reviews_application_id"), "reviews", ["application_id"], unique=False, schema="platform")


def downgrade() -> None:
    op.drop_index(op.f("ix_reviews_application_id"), table_name="reviews", schema="platform")
    op.drop_table("reviews", schema="platform")
    op.drop_index(op.f("ix_knowledge_chunks_tenant_id"), table_name="knowledge_chunks", schema="platform")
    op.drop_table("knowledge_chunks", schema="platform")
    op.drop_index(op.f("ix_deployment_events_deployment_id"), table_name="deployment_events", schema="platform")
    op.drop_table("deployment_events", schema="platform")
    op.drop_table("memberships", schema="platform")
    op.drop_table("environments", schema="platform")
    op.drop_index(op.f("ix_documents_tenant_id"), table_name="documents", schema="platform")
    op.drop_table("documents", schema="platform")
    op.drop_index(op.f("ix_deployments_tenant_id"), table_name="deployments", schema="platform")
    op.drop_table("deployments", schema="platform")
    op.drop_index(op.f("ix_audit_log_tenant_id"), table_name="audit_log", schema="platform")
    op.drop_table("audit_log", schema="platform")
    op.drop_table("applications", schema="platform")
    op.drop_table("users", schema="platform")
    op.drop_table("tenants", schema="platform")
