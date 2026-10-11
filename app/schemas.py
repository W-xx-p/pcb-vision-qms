from __future__ import annotations

from pydantic import BaseModel, Field


class ProductCreate(BaseModel):
    code: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=500)
    threshold: float = Field(default=0.18, ge=0.01, le=0.99)


class ProductThresholdUpdate(BaseModel):
    threshold: float = Field(ge=0.01, le=0.99)


class RoiConfig(BaseModel):
    x: float = Field(ge=0, lt=1)
    y: float = Field(ge=0, lt=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None
    roi: RoiConfig | None = None


class InspectionReview(BaseModel):
    decision: str = Field(pattern=r"^(normal|anomaly)$")
    note: str = Field(default="", max_length=500)


class ModelTrainRequest(BaseModel):
    backend: str = Field(default="reference", pattern=r"^(reference|patchcore|padim|efficientad)$")
    image_size: int = Field(default=256, ge=128, le=1024)
    coreset_sampling_ratio: float = Field(default=0.1, gt=0, le=1)
    dataset_version_id: int | None = Field(default=None, gt=0)
    tiling_enabled: bool = False
    tile_size: int = Field(default=512, ge=128, le=2048)
    tile_overlap: float = Field(default=0.2, ge=0, lt=0.75)
    tile_batch_size: int = Field(default=8, ge=1, le=64)


class DatasetSnapshotCreate(BaseModel):
    name: str = Field(default="production-references", min_length=2, max_length=120)
    description: str = Field(default="", max_length=500)


class ModelActivation(BaseModel):
    model_id: int = Field(gt=0)


class ModelRollback(BaseModel):
    target_model_id: int | None = Field(default=None, gt=0)
    reason: str = Field(min_length=3, max_length=500)


class CalibrationRequest(BaseModel):
    apply_threshold: bool = False


class ProductionLineCreate(BaseModel):
    code: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=2, max_length=120)
    location: str = Field(default="", max_length=200)


class StationCreate(BaseModel):
    line_id: int = Field(gt=0)
    code: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=2, max_length=120)
    camera_identifier: str = Field(default="", max_length=120)


class QualityRuleCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    product_id: int | None = Field(default=None, gt=0)
    window_size: int = Field(default=20, ge=5, le=1000)
    max_anomaly_rate: float = Field(default=0.2, ge=0, le=1)
    max_consecutive_anomalies: int = Field(default=3, ge=1, le=100)


class AlertAcknowledge(BaseModel):
    note: str = Field(default="", max_length=500)


class AlertWorkflowUpdate(BaseModel):
    status: str = Field(pattern=r"^(open|acknowledged|closed)$")
    assigned_to: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=500)


class StationHeartbeat(BaseModel):
    software_version: str = Field(default="", max_length=80)


class FeedbackCreate(BaseModel):
    category: str = Field(pattern=r"^(false_positive|false_negative|hard_sample|confirmed)$")
    note: str = Field(default="", max_length=500)


class IntegrationEventAcknowledge(BaseModel):
    external_reference: str = Field(default="", max_length=200)
