"""
Sendmail Generator – Konfiguration
"""

import os

class SendmailConfig:
    """Zentrale Config für den Sendmail-Generator."""

    # Absender / Empfänger (aus Umgebungsvariablen)
    SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
    RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL")

    # Quellen-Filter – ["*"] = alle, [] = deaktiviert
    SOURCES = {
        "mastodon": ["*"],
        "alerts": ["*"],
        "rss": [],
    }

    # Max. Anzahl Links über alle Quellen
    LIMIT = 20

    # Nur Einträge der letzten X Stunden (None = kein Limit)
    TIME_WINDOW_HOURS = None