"""initial schema

Revision ID: 500eab6b01ab
Revises:
Create Date: 2026-08-04
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "500eab6b01ab"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="reviewer"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_users_id", "users", ["id"])
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_table(
        "batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_batches_id", "batches", ["id"])

    op.create_table(
        "studies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("study_uid", sa.String(128), nullable=False),
        sa.Column("patient_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_studies_id", "studies", ["id"])
    op.create_index("ix_studies_study_uid", "studies", ["study_uid"], unique=True)

    op.create_table(
        "batch_studies",
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("batches.id"), primary_key=True),
        sa.Column("study_id", sa.Integer(), sa.ForeignKey("studies.id"), primary_key=True),
    )

    op.create_table(
        "series",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("series_uid", sa.String(128), nullable=False),
        sa.Column("study_id", sa.Integer(), sa.ForeignKey("studies.id"), nullable=False),
        sa.Column("processing_status", sa.String(32), nullable=False, server_default="registered"),
        sa.Column("review_status", sa.String(32), nullable=False, server_default="not_reviewed"),
        sa.Column("reviewer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("raw_path", sa.String(512), nullable=True),
        sa.Column("preprocessed_path", sa.String(512), nullable=True),
        sa.Column("meta_path", sa.String(512), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_series_id", "series", ["id"])
    op.create_index("ix_series_series_uid", "series", ["series_uid"], unique=True)

    op.create_table(
        "detections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("series_id", sa.Integer(), sa.ForeignKey("series.id"), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("box_x", sa.Float(), nullable=False),
        sa.Column("box_y", sa.Float(), nullable=False),
        sa.Column("box_z", sa.Float(), nullable=False),
        sa.Column("box_w", sa.Float(), nullable=False),
        sa.Column("box_h", sa.Float(), nullable=False),
        sa.Column("box_d", sa.Float(), nullable=False),
        sa.Column("voxel_x", sa.Integer(), nullable=False),
        sa.Column("voxel_y", sa.Integer(), nullable=False),
        sa.Column("voxel_z", sa.Integer(), nullable=False),
        sa.Column("voxel_w", sa.Integer(), nullable=False),
        sa.Column("voxel_h", sa.Integer(), nullable=False),
        sa.Column("voxel_d", sa.Integer(), nullable=False),
        sa.Column("slice_index", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_detections_id", "detections", ["id"])
    op.create_index("ix_detections_series_id", "detections", ["series_id"])

    op.create_table(
        "review_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("detection_id", sa.Integer(), sa.ForeignKey("detections.id"), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("verdict", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_review_results_id", "review_results", ["id"])
    op.create_index("ix_review_results_detection_id", "review_results", ["detection_id"])


def downgrade() -> None:
    op.drop_table("review_results")
    op.drop_table("detections")
    op.drop_table("series")
    op.drop_table("batch_studies")
    op.drop_table("studies")
    op.drop_table("batches")
    op.drop_table("users")
