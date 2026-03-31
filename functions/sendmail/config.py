"""
Sendmail Generator – Konfiguration
"""

import os

class SendmailConfig:
    """Zentrale Config für den Sendmail-Generator."""

    # Verwendetes Gemini-Modell für die Generierung von Zusammenfassungen
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite-preview")

    # Quellen-Filter – ["*"] = alle, [] = deaktiviert
    SOURCES = {
        "mastodon": ["*"],
        "alerts": ["*"],
        "rss": ["*"],
    }

    # Max. Anzahl Links über alle Quellen
    LIMIT = None

    # Nur Einträge der letzten X Stunden (None = kein Limit)
    TIME_WINDOW_HOURS = None
