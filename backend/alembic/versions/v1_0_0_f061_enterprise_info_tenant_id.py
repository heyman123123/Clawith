"""Add tenant_id and composite unique constraint to enterprise_info.

Background:
  EnterpriseInfo currently lacks a tenant_id column, causing multi-tenant data bleed where an update
  from one tenant administrator overwrote global EnterpriseInfo entries and pushed synced files to all running agents across tenants.

Scope:
  Ensure enterprise_info exists and carries a tenant_id column (indexed).
  Drop legacy single info_type unique constraint.
  Add composite unique constraint uq_enterprise_info_tenant_type on (tenant_id, info_type).

Idempotence:
  Fully idempotent. Uses sa.inspect to check table/column/constraint existence and conditionally
  issues DDL. Safe to retry. Pure DDL migration without blocking data locks.

  Also handles the case where EnterpriseInfo was created via SQLModel
  ``Base.metadata.create_all`` *before* the alembic chain ever reached this
  revision (which is the normal flow when ``DATABASE_AUTO_CREATE_TABLES`` has
  been True historically). In that case the table already has ``tenant_id``
  and ``uq_enterprise_info_tenant_type`` and we must NOT re-issue them.

Revision ID: f061_enterprise_info_tenant_id
Revises: f060_tenant_id_backfill
Create Date: 2026-08-06 14:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f061_enterprise_info_tenant_id"
down_revision: Union[str, None] = "f060_tenant_id_backfill"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "enterprise_info"
_CONSTRAINT_LEGACY = "enterprise_info_info_type_key"
_CONSTRAINT_COMPOSITE = "uq_enterprise_info_tenant_type"
_INDEX_TENANT = "ix_enterprise_info_tenant_id"


def _table_columns(inspector: sa.Inspector) -> set[str]:
    return {col["name"] for col in inspector.get_columns(_TABLE)}


def _table_indexes(inspector: sa.Inspector) -> set[str]:
    return {idx["name"] for idx in inspector.get_indexes(_TABLE)}


def _table_unique_constraints(inspector: sa.Inspector) -> set[str]:
    # get_unique_constraints returns only named unique constraints (not
    # anonymous ones and not primary keys), which is exactly what we need.
    return {uc["name"] for uc in inspector.get_unique_constraints(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing_tables = set(inspector.get_table_names())
    if _TABLE not in existing_tables:
        # Mirror the SQLModel definition so the table exists before f061's
        # ALTER / DROP / CREATE statements run. This is the path taken on a
        # brand new database where the alembic chain never created the table.
        op.create_table(
            _TABLE,
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
            ),
            sa.Column(
                "tenant_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
            ),
            sa.Column("info_type", sa.String(length=50), nullable=False),
            sa.Column("content", postgresql.JSON, nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column(
                "visible_roles",
                postgresql.JSON,
                nullable=True,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column(
                "updated_by",
                postgresql.UUID(as_uuid=True),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "tenant_id",
                "info_type",
                name=_CONSTRAINT_COMPOSITE,
            ),
        )
        op.create_index(
            op.f(_INDEX_TENANT),
            _TABLE,
            ["tenant_id"],
            unique=False,
        )
        # Table is now in its target shape; nothing else to do.
        return

    columns = _table_columns(inspector)
    indexes = _table_indexes(inspector)
    unique_constraints = _table_unique_constraints(inspector)

    # 1. Ensure tenant_id column exists.
    if "tenant_id" not in columns:
        op.add_column(
            _TABLE,
            sa.Column(
                "tenant_id",
                postgresql.UUID(as_uuid=True),
                nullable=True,
            ),
        )
        # Backfill any pre-existing rows with the first available tenant. This
        # is best-effort: if no tenant exists yet the rows simply remain NULL,
        # which is acceptable because there should be no production data on a
        # legacy enterprise_info row without a tenant.
        bind.execute(
            sa.text(
                "UPDATE enterprise_info SET tenant_id = "
                "(SELECT id FROM tenants ORDER BY created_at LIMIT 1) "
                "WHERE tenant_id IS NULL"
            )
        )
        op.alter_column(
            _TABLE,
            "tenant_id",
            nullable=False,
        )

    # 2. Ensure the tenant_id index exists.
    if _INDEX_TENANT not in indexes:
        op.create_index(
            op.f(_INDEX_TENANT),
            _TABLE,
            ["tenant_id"],
            unique=False,
        )

    # 3. Drop legacy single-column unique constraint on info_type if present.
    if _CONSTRAINT_LEGACY in unique_constraints:
        op.drop_constraint(
            _CONSTRAINT_LEGACY,
            _TABLE,
            type_="unique",
        )
        unique_constraints.discard(_CONSTRAINT_LEGACY)

    # 4. Ensure composite (tenant_id, info_type) unique constraint exists.
    if _CONSTRAINT_COMPOSITE not in unique_constraints:
        op.create_unique_constraint(
            _CONSTRAINT_COMPOSITE,
            _TABLE,
            ["tenant_id", "info_type"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _TABLE not in set(inspector.get_table_names()):
        return

    unique_constraints = _table_unique_constraints(inspector)

    if _CONSTRAINT_COMPOSITE in unique_constraints:
        op.drop_constraint(
            _CONSTRAINT_COMPOSITE,
            _TABLE,
            type_="unique",
        )

    if "enterprise_info_info_type_key" not in unique_constraints:
        op.create_unique_constraint(
            "enterprise_info_info_type_key",
            _TABLE,
            ["info_type"],
        )

    indexes = _table_indexes(inspector)
    if _INDEX_TENANT in indexes:
        op.drop_index(op.f(_INDEX_TENANT), table_name=_TABLE)

    columns = _table_columns(inspector)
    if "tenant_id" in columns:
        op.drop_column(_TABLE, "tenant_id")
