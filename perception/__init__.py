"""Perception module — camera → mask → depth → reconstruction → map → planning."""

__version__ = "0.1.0"

__all__ = [
    # Types
    "CameraFrame",
    "ObstacleMask",
    "DepthMap",
    "PointCloud",
    "ObstacleInfo",
    "ObstacleMap",
    "PlanPath",
    "ExecutionStep",
    # Pipeline
    "PerceptionPlanningPipeline",
    "PipelineResult",
    "create_pipeline",
    # Height estimation
    "HeightEstimator",
    # Config
    "get_default_config",
    "load_config",
]

from perception.domain_types import (
    CameraFrame,
    ObstacleMask,
    DepthMap,
    PointCloud,
    ObstacleInfo,
    ObstacleMap,
    PlanPath,
    ExecutionStep,
)
from perception.pipeline import (
    PerceptionPlanningPipeline,
    PipelineResult,
    create_pipeline,
)
from perception.height_estimator import HeightEstimator
from perception.config import get_default_config, load_config
