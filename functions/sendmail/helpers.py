import os
import json
import logging
import re

logger = logging.getLogger(__name__)

# --------------------- Utilities ---------------------------------------
LOCAL_GEMINI_KEY_ENV = "GEMINI_API_KEY"
SECRET_ENV = "GEMINI_API_KEY_SECRET"


def _sanitize_api_key(raw_value, env_name):
    if not raw_value:
        return None
    value = str(raw_value).strip().strip('"').strip("'")
    if not value:
        return None
    if value.startswith("{"):
        try:
            payload = json.loads(value)
            for field in ("api_key", "gemini_api_key", "GEMINI_API_KEY", "key"):
                extracted = payload.get(field)
                if isinstance(extracted, str) and extracted.strip():
                    value = extracted.strip()
                    break
            else:
                logger.warning("Umgebungsvariable %s enthält JSON ohne API-Key-Feld und wird ignoriert.", env_name)
                return None
        except Exception:
            logger.warning("Umgebungsvariable %s enthält ungültiges JSON und wird als Plain-Text versucht.", env_name)
    value = value.replace("\r", "").replace("\n", "")
    if any(char.isspace() for char in value):
        value = "".join(value.split())
    return value or None


def get_gemini_api_key():
    candidates = [LOCAL_GEMINI_KEY_ENV, SECRET_ENV, "GOOGLE_API_KEY"]
    for env_name in candidates:
        sanitized = _sanitize_api_key(os.getenv(env_name), env_name)
        if sanitized:
            logger.info("Gemini API-Key aus %s geladen.", env_name)
            return sanitized
    return None
