"""Application settings loaded from .env + config/settings.yaml via pydantic-settings.

Filesystem locations are owned by `ml_uspto.paths` — this module only
defines configurable values (API keys, page sizes, data dir names).
"""

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
    raw_dir: Path = Path("data/raw")
    processed_dir: Path = Path("data/processed")
    models_dir: Path = Path("data/models")


class FeatureSettings(BaseSettings):
    date_features: bool = True
    technology_center_encoding: str = "onehot"
    text_features: bool = False


class ModelSettings(BaseSettings):
    test_size: float = 0.2
    random_state: int = 42
    cv_folds: int = 5


class Settings(BaseSettings):
    api: APISettings = Field(default_factory=APISettings)
    data: DataSettings = Field(default_factory=DataSettings)
    features: FeatureSettings = Field(default_factory=FeatureSettings)
    model: ModelSettings = Field(default_factory=ModelSettings)

    def __init__(self, **kwargs):
        yaml_conf = _load_yaml()
        # YAML provides defaults; env vars take precedence via pydantic on APISettings
        api_vals = yaml_conf.get("api", {})
        data_vals = yaml_conf.get("data", {})
        feat_vals = yaml_conf.get("features", {})
        model_vals = yaml_conf.get("model", {})
        super().__init__(
            api=APISettings(**api_vals),
            data=DataSettings(**data_vals),
            features=FeatureSettings(**feat_vals),
            model=ModelSettings(**model_vals),
            **kwargs,
        )

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton access to application settings."""
    return Settings()
