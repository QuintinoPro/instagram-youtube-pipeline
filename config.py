from pathlib import Path
from dotenv import load_dotenv
import json
import os

load_dotenv()

BASE_DIR = Path(__file__).parent
SETTINGS_FILE = BASE_DIR / "settings.json"


def _load_settings() -> dict:
    """Lê settings.json se existir. Retorna dict vazio se não existir."""
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _get(key: str, default: str = "") -> str:
    """Lê do settings.json primeiro, depois .env, depois usa o default."""
    s = _load_settings()
    if key in s and str(s[key]).strip():
        return str(s[key])
    return os.getenv(key, default)


def _get_int(key: str, default: int) -> int:
    try:
        return int(_get(key, str(default)))
    except (ValueError, TypeError):
        return default


def get_daily_slots() -> list[tuple[int, int]]:
    """Retorna lista de (hora, minuto) UTC para publicação diária. Lido em tempo real."""
    s = _load_settings()
    if "DAILY_SLOTS" in s and s["DAILY_SLOTS"]:
        try:
            return [(int(slot[0]), int(slot[1])) for slot in s["DAILY_SLOTS"]]
        except Exception:
            pass
    # Backward compat: usa UPLOAD_HOUR/UPLOAD_MINUTE
    hour = int(s.get("UPLOAD_HOUR") or os.getenv("UPLOAD_HOUR", "13"))
    minute = int(s.get("UPLOAD_MINUTE") or os.getenv("UPLOAD_MINUTE", "0"))
    return [(hour, minute)]


# Instagram
INSTAGRAM_SESSION_ID = _get("INSTAGRAM_SESSION_ID", "")
TARGET_INSTAGRAM_PROFILE = _get("TARGET_INSTAGRAM_PROFILE", "")

# Branding (white-label)
PIPELINE_BRAND_NAME = _get("PIPELINE_BRAND_NAME", "My Pipeline")
PIPELINE_BRAND_LOGO = _get("PIPELINE_BRAND_LOGO", "YT")
PIPELINE_TITLE      = _get("PIPELINE_TITLE", "Instagram → YouTube Pipeline")

# YouTube OAuth
YOUTUBE_CLIENT_SECRETS_FILE = BASE_DIR / _get("YOUTUBE_CLIENT_SECRETS_FILE", "auth/client_secrets.json")
YOUTUBE_TOKEN_FILE = BASE_DIR / _get("YOUTUBE_TOKEN_FILE", "auth/token.json")
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

# Pipeline
DOWNLOAD_DIR = BASE_DIR / _get("DOWNLOAD_DIR", "downloads")
METADATA_FILE = BASE_DIR / _get("METADATA_FILE", "metadata/videos.json")
LOG_FILE = BASE_DIR / _get("LOG_FILE", "logs/pipeline.log")

UPLOAD_HOUR = _get_int("UPLOAD_HOUR", 13)
UPLOAD_MINUTE = _get_int("UPLOAD_MINUTE", 0)
MAX_COLLECT_PER_RUN = _get_int("MAX_COLLECT_PER_RUN", 30)
MAX_UPLOADS_PER_RUN = _get_int("MAX_UPLOADS_PER_RUN", 6)

REPORT_FILE = BASE_DIR / _get("REPORT_FILE", "metadata/report.csv")

# Garante que os diretórios existam
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
METADATA_FILE.parent.mkdir(parents=True, exist_ok=True)
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
YOUTUBE_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
