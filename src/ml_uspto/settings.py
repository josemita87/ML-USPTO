"""Application settings loaded from .env + config/settings.yaml via pydantic-settings."""

from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ml_uspto import paths


def _load_yaml() -> dict:
    with open(paths.SETTINGS_YAML) as f:
        return yaml.safe_load(f)


class APISettings(BaseSettings):
    """USPTO ODP API client knobs (key, base URL, pagination, retry backoff)."""

    model_config = SettingsConfigDict(
        env_file=paths.CREDENTIALS_ENV,
        env_file_encoding="utf-8",
        env_prefix="USPTO_",
        extra="ignore",
    )

    api_key: str = ""
    base_url: str = "https://api.uspto.gov/api/v1/patent"
    page_size: int = 100
    max_pages: int = 50
    backoff_seconds: list[int] = Field(default_factory=lambda: [5, 10, 20])


class DataSettings(BaseSettings):
    """Project-relative data directory locations (raw / processed / models)."""

    raw_dir: Path = Path("data/raw")
    processed_dir: Path = Path("data/processed")
    models_dir: Path = Path("data/models")


class StorageSettings(BaseSettings):
    """Storage backend selector.

    Read from env (`ML_USPTO_STORAGE`, `ML_USPTO_S3_BUCKET`); the Fargate
    task definition injects these, laptop runs default to `local`.
    """

    model_config = SettingsConfigDict(
        env_file=paths.CREDENTIALS_ENV,
        env_file_encoding="utf-8",
        env_prefix="ML_USPTO_",
        extra="ignore",
    )

    backend: str = "local"
    s3_bucket: str = ""


class FeatureSettings(BaseSettings):
    """Feature-engineering toggles (date breakdown, TC encoding, text features)."""

    date_features: bool = True
    technology_center_encoding: str = "onehot"
    text_features: bool = False


class ModelSettings(BaseSettings):
    """Train/test split and CV knobs."""

    test_size: float = 0.2
    random_state: int = 42
    cv_folds: int = 5
    # Frozen so re-running tomorrow produces the same train/test rows.
    mature_days: int = 600
    experiment_today: date = date(2026, 5, 5)
    holdout_after: date = date(2023, 1, 1)


class Settings(BaseSettings):
    """Top-level settings tree composed of the per-section sub-settings."""

    api: APISettings = Field(default_factory=APISettings)
    data: DataSettings = Field(default_factory=DataSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    features: FeatureSettings = Field(default_factory=FeatureSettings)
    model: ModelSettings = Field(default_factory=ModelSettings)

    def __init__(self, **kwargs):
        """Compose sub-settings from `config/settings.yaml` defaults and env overrides."""
        yaml_conf = _load_yaml()
        # YAML provides defaults; env vars take precedence via pydantic on
        # APISettings/StorageSettings.
        api_vals = yaml_conf.get("api", {})
        data_vals = yaml_conf.get("data", {})
        feat_vals = yaml_conf.get("features", {})
        model_vals = yaml_conf.get("model", {})
        super().__init__(
            api=APISettings(**api_vals),
            data=DataSettings(**data_vals),
            storage=StorageSettings(),
            features=FeatureSettings(**feat_vals),
            model=ModelSettings(**model_vals),
            **kwargs,
        )

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton access to application settings."""
    return Settings()
