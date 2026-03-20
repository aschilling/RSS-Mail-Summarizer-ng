"""Cloud Function zur Erzeugung und Versendung des täglichen Reports.

Diese Funktion ist bewusst eigenständig von der bisherigen
`rss_mail_summarizer/main.py` getrennt, damit sie einzeln deployed
werden kann. Sie liest nicht gesendete Einträge aus der Firestore‑Datenbank,
bildet optional mit Hilfe der bereits vorhandenen LLM‑Funktionen
Summaries (falls noch keine vorhanden sind) und versendet die
Ergebnisse per Gmail API.

In der GCP‑Konfiguration wird der Entry‑Point auf
`sendmail_trigger` gesetzt; die Funktion wird vom Scheduler oder per HTTP
angestossen.
"""

import os
import sys
import json
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
import functions_framework
from datetime import date

# interne helpers
from config import SendmailConfig
from database import get_unsent_entries, mark_as_sent, add_datarecord
from helpers import get_gemini_api_key
from mail_report_helpers import gmail_send_mail, create_markdown_report, cleanup_markdown_report
from ai_helpers import AIService
from hn_popularity import fetch_hn_points

# Logger setup
load_dotenv()
logger = logging.getLogger(__name__)
logger.setLevel(getattr(logging, os.environ.get("LOG_LEVEL", "DEBUG").upper(), logging.DEBUG))

if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(handler)

# Umgebungskonstanten
MARKDOWN_REPORT_PATH = str(Path(__file__).resolve().parent / "markdown_report.md")
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL")
PROJECT_ID = os.environ.get("PROJECT_ID")


class SendMailService:
    """Einfache Orchestrierungs-Klasse, die Helfermodule zusammenführt.

    Die früher monolithische Implementierung existiert nun in mehreren
    Dateien (`gmail_utils`, `llm_helpers`, `report`, `utils`). Die Klasse
    selbst übernimmt nur die Konfiguration und den Workflow.
    """

    def __init__(self, sender_email: str, recipient_email: str):
        self.sender_email = sender_email
        self.recipient_email = recipient_email
        self.gemini_api_key = get_gemini_api_key()
        
        if not self.gemini_api_key:
            logger.error("❌ KRITISCH: Kein gültiger Gemini API-Key gefunden! Überprüfe:")
            logger.error("   - Umgebungsvariable GEMINI_API_KEY")
            logger.error("   - Secret Manager 'gemini-api-key' ")
            raise RuntimeError("Kein Gemini API-Key konfiguriert. Cloud Function kann nicht starten.")
        
        logger.info("✓ Gemini API-Key erfolgreich geladen")
        self.ai = AIService(self.gemini_api_key)

    # ---- Core workflow ----
    def run(self):
        unsent = get_unsent_entries()
        if not unsent:
            logger.info("Keine ungesendeten Einträge gefunden.")
            return False

        logger.info(f"Gefundene ungesendete Einträge: {len(unsent)}")

        # --- SOURCES-Filter ---
        sources_cfg = SendmailConfig.SOURCES
        filtered = []
        for e in unsent:
            src = e.get("source") or ""
            allowed = sources_cfg.get(src)
            if allowed is None or allowed == ["*"] or allowed:
                filtered.append(e)
            # leere Liste [] → Quelle deaktiviert
        if len(filtered) != len(unsent):
            logger.info(f"Nach SOURCES-Filter: {len(filtered)} von {len(unsent)} Einträgen verbleiben.")
        unsent = filtered

        # --- TIME_WINDOW_HOURS-Filter ---
        if SendmailConfig.TIME_WINDOW_HOURS is not None:
            from datetime import datetime, timezone, timedelta
            cutoff = datetime.now(timezone.utc) - timedelta(hours=SendmailConfig.TIME_WINDOW_HOURS)
            before = len(unsent)
            unsent = [e for e in unsent if e.get("time_stamp") is None or e["time_stamp"] >= cutoff]
            logger.info(f"Nach TIME_WINDOW_HOURS-Filter ({SendmailConfig.TIME_WINDOW_HOURS}h): {len(unsent)} von {before} Einträgen verbleiben.")

        # --- LIMIT (neueste zuerst) ---
        unsent = sorted(unsent, key=lambda e: e.get("time_stamp") or 0, reverse=True)
        if SendmailConfig.LIMIT is not None and len(unsent) > SendmailConfig.LIMIT:
            logger.info(f"Anzahl Einträge auf LIMIT={SendmailConfig.LIMIT} begrenzt (waren {len(unsent)}).")
            unsent = unsent[:SendmailConfig.LIMIT]

        if not unsent:
            logger.info("Keine Einträge nach Filterung übrig.")
            return False

        urls_without_summary = []
        for e in unsent:
            summary = e.get("summary")
            if not summary or not str(summary).strip() or str(summary).strip().lower() in ["n/a", "none", "null"]:
                urls_without_summary.append(e["url"])

        logger.info(f"URLs ohne gültige Summary: {len(urls_without_summary)}")
        logger.debug("URLs ohne Summary: %s", urls_without_summary)

        if urls_without_summary:
            youtube_urls = [u for u in urls_without_summary if "youtube.com" in u or "youtu.be" in u]
            web_urls = [u for u in urls_without_summary if u not in youtube_urls]
            
            logger.info(f"Web-URLs zu verarbeiten: {len(web_urls)}, YouTube-URLs: {len(youtube_urls)}")
            
            summaries = {}
            
            if web_urls:
                try:
                    logger.info(f"Rufe summarise_and_categorize_websites mit {len(web_urls)} URLs auf...")
                    web_results = self.ai.summarise_and_categorize_websites(web_urls)
                    logger.info(f"summarise_and_categorize_websites returned {len(web_results)} results")
                    summaries.update(web_results)
                except Exception as e:
                    logger.error(f"Fehler bei summarise_and_categorize_websites: {e}", exc_info=True)
                    raise
            
            if youtube_urls:
                try:
                    logger.info(f"Rufe summarise_youtube_videos mit {len(youtube_urls)} URLs auf...")
                    youtube_results = self.ai.summarise_youtube_videos(youtube_urls)
                    logger.info(f"summarise_youtube_videos returned {len(youtube_results)} results")
                    summaries.update(youtube_results)
                except Exception as e:
                    logger.error(f"Fehler bei summarise_youtube_videos: {e}", exc_info=True)
                    raise
            
            logger.info(f"Gesamt Summaries erhalten: {len(summaries)}")

            # HN-Punkte parallel für alle Nicht-Alert-URLs abrufen
            alert_urls = {e["url"] for e in unsent if e.get("source") == "alerts"}
            hn_fetch_urls = [url for url in summaries
                 if url not in alert_urls
                 and "youtube.com" not in url
                 and "youtu.be" not in url]
            hn_points_dict = {}
            if hn_fetch_urls:
                logger.info(f"Rufe HN-Punkte für {len(hn_fetch_urls)} URLs ab (parallel)...")
                with ThreadPoolExecutor(max_workers=10) as executor:
                    results = executor.map(fetch_hn_points, hn_fetch_urls)
                    hn_points_dict = dict(zip(hn_fetch_urls, results))
                logger.info("HN-Punkte abgerufen.")

            for url, meta in summaries.items():
                meta["hn_points"] = hn_points_dict.get(url)
                logger.debug(f"Schreibe Summary für {url}: category={meta.get('category')}, summary={str(meta.get('summary'))[:50]}...")
                add_datarecord(
                    url=url,
                    category=meta.get("category"),
                    summary=meta.get("summary"),
                    sub_category=meta.get("sub_category", None),
                    reading_time=meta.get("reading_time"),
                    hn_points=meta.get("hn_points"),
                    mail_sent=False,
                )
            
            # 🔑 KRITISCH: Nach LLM-Aufrufen DB nochmal laden, um die neuen Summaries zu holen!
            logger.info("Lade aktualisierte Einträge aus DB...")
            allowed_urls = {e["url"] for e in unsent}
            unsent = [e for e in get_unsent_entries() if e["url"] in allowed_urls]
            logger.info(f"Aktualisierte Einträge geladen: {len(unsent)}")

        summaries_from_db = {
            entry["url"]: {
                "category": entry.get("category"),
                "sub_category": entry.get("sub_category"),
                "summary": entry.get("summary"),
                "reading_time": entry.get("reading_time"),
                "hn_points": entry.get("hn_points"),
                "source": entry.get("source", ""),
            }
            for entry in unsent
        }

        today_str = date.today()

        create_markdown_report(summaries_from_db, MARKDOWN_REPORT_PATH)
        cleanup_markdown_report(MARKDOWN_REPORT_PATH)
        gmail_send_mail(
            self.sender_email,
            self.recipient_email,
            subject=f"Today's News ({today_str})",
            mail_body_file=MARKDOWN_REPORT_PATH,
        )
        mark_as_sent(unsent)
        logger.info("Mailversand abgeschlossen.")
        return {"entries_processed": len(unsent), "mail_sent_to": self.recipient_email}



@functions_framework.http

def sendmail_trigger(request=None):
    """HTTP‑Entry‑Point, der einen `SendMailService` ausführt."""
    try:
        service = SendMailService(sender_email=SENDER_EMAIL, recipient_email=RECIPIENT_EMAIL)
        result = service.run()
        if not result:
            return json.dumps({"status": "ok", "resource": "gmail/sendmail", "details": {"message": "Keine ungesendeten Einträge gefunden."}}, indent=2), 200
        response_data = {
            "status": "success",
            "resource": "gmail/sendmail",
            "details": {
                "entries_processed": result["entries_processed"],
                "mail_sent_to": result["mail_sent_to"],
            },
        }
        return json.dumps(response_data, indent=2), 200
    except Exception as e:
        logger.error("Fehler in sendmail_trigger: %s", e, exc_info=True)
        return json.dumps({"status": "error", "resource": "gmail/sendmail", "details": {"error": str(e)}}, indent=2), 500
