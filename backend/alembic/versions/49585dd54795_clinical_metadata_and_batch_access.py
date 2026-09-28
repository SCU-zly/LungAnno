"""clinical metadata and batch access

- studies += patient_name / study_date（DICOM PatientName / StudyDate）
- series += slice_thickness / series_description（DICOM SliceThickness / SeriesDescription）
- detections += source（候选来源模型变体，老数据默认 dlcsd）
- 新表 patient_clinical（临床 CSV 导入，按规范化住院号关联）
- 新表 batch_user_access（批次可见性授权，reviewer 仅见授权批次）

Revision ID: 49585dd54795
Revises: 500eab6b01ab
Create Date: 2026-08-11
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "49585dd54795"
down_revision = "500eab6b01ab"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("studies", sa.Column("patient_name", sa.String(128), nullable=True))
    op.add_column("studies", sa.Column("study_date", sa.String(8), nullable=True))
    op.add_column("series", sa.Column("slice_thickness", sa.Float(), nullable=True))
    op.add_column("series", sa.Column("series_description", sa.String(128), nullable=True))
    op.add_column(
        "detections",
        sa.Column("source", sa.String(32), nullable=False, server_default="dlcsd"),
    )

    op.create_table(
        "patient_clinical",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("patient_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(64), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
    )
    op.create_index("ix_patient_clinical_id", "patient_clinical", ["id"])
    op.create_index("ix_patient_clinical_patient_id", "patient_clinical", ["patient_id"], unique=True)

    op.create_table(
        "batch_user_access",
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("batches.id"), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("batch_user_access")
    op.drop_table("patient_clinical")
    op.drop_column("detections", "source")
    op.drop_column("series", "series_description")
    op.drop_column("series", "slice_thickness")
    op.drop_column("studies", "study_date")
    op.drop_column("studies", "patient_name")
