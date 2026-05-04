"""Central path resolution for the project."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

CREDENTIALS_ENV = PROJECT_ROOT / ".credentials.env"
CONFIG_DIR = PROJECT_ROOT / "config"

SETTINGS_YAML = CONFIG_DIR / "settings.yaml"
LABELS_YAML = CONFIG_DIR / "labels.yaml"
PETITION_PICKER_YAML = CONFIG_DIR / "petition_picker.yaml"
PARSERS_YAML = CONFIG_DIR / "parsers" / "patents.yaml"
PATENT_EVENT_CODES_YAML = CONFIG_DIR / "patents" / "event_codes.yaml"
PTAB_ERAS_YAML = CONFIG_DIR / "ptab_eras.yaml"


def raw_dir() -> Path:
    """Absolute path to the raw data directory (settings-derived)."""
    from ml_uspto.settings import get_settings

    return PROJECT_ROOT / get_settings().data.raw_dir


def processed_dir() -> Path:
    """Absolute path to the processed data directory (settings-derived)."""
    from ml_uspto.settings import get_settings

    return PROJECT_ROOT / get_settings().data.processed_dir


def models_dir() -> Path:
    """Absolute path to the models artifact directory (settings-derived)."""
    from ml_uspto.settings import get_settings

    return PROJECT_ROOT / get_settings().data.models_dir


def resolve(relative: Path | str) -> Path:
    """Resolve a project-relative path to absolute."""
    return PROJECT_ROOT / relative


def ensure_data_dirs() -> None:
    """Create raw / processed / models data directories if missing."""
    for d in (raw_dir(), processed_dir(), models_dir()):
        d.mkdir(parents=True, exist_ok=True)
