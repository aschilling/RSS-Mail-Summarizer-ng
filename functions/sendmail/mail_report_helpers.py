import os
import json
import base64
import logging
from datetime import date
from pathlib import Path

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)


def _report_title():
    return f"# News of the Day ({date.today()})\n\n"


def get_gmail_service():
    creds = None
    scopes = ["https://www.googleapis.com/auth/gmail.modify"]
    token_json_str = os.environ.get("GMAIL_TOKEN_JSON")
    if token_json_str:
        try:
            creds_info = json.loads(token_json_str)
            creds = Credentials.from_authorized_user_info(creds_info, scopes)
            logger.info("Gmail-Token erfolgreich aus Secret Manager geladen.")
        except Exception as e:
            logger.error(f"Fehler beim Parsen des Tokens aus Umgebungsvariable: {e}")
    if not creds:
        token_path = "credentials/token.json"
        if os.path.exists(token_path):
            creds = Credentials.from_authorized_user_file(token_path, scopes)
            logger.info(f"Gmail-Token aus lokaler Datei geladen: {token_path}")
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
            logger.info("Gmail-Token wurde erfolgreich erneuert (refreshed).")
        else:
            raise RuntimeError("Kein gültiges Gmail-Token gefunden. Stelle sicher, dass GMAIL_TOKEN_JSON im Secret Manager korrekt gesetzt ist.")
    return build("gmail", "v1", credentials=creds)


def gmail_send_mail(sender_email, recipient_email, subject=None, mail_body_file=None, attachment_filepath=None):
    logger.info("Vorbereitung zum Versenden einer E-Mail an %s", recipient_email)
    msg = MIMEMultipart()
    msg["From"] = sender_email
    msg["To"] = recipient_email
    if subject:
        msg["Subject"] = subject
        logger.debug("Betreff gesetzt: %s", subject)
    if mail_body_file:
        logger.debug("Lese Markdown-Datei: %s", mail_body_file)
        import markdown
        with open(mail_body_file, "r", encoding="utf-8") as md_file:
            markdown_content = md_file.read()
        html_content = markdown.markdown(markdown_content)
        msg.attach(MIMEText(html_content, "html"))
    if attachment_filepath:
        logger.debug("Füge Anhang hinzu: %s", attachment_filepath)
        with open(attachment_filepath, "rb") as attachment:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(attachment.read())
            encoders.encode_base64(part)
            filename = Path(attachment_filepath).name
            part.add_header("Content-Disposition", f"attachment; filename={filename}")
            msg.attach(part)
    try:
        logger.info("Sende E-Mail über Gmail API...")
        service = get_gmail_service()
        raw_message = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        message = {"raw": raw_message}
        sent = service.users().messages().send(userId="me", body=message).execute()
        logger.info("E-Mail erfolgreich gesendet! Gmail API Message ID: %s", sent["id"])
    except Exception as e:
        logger.error("Fehler beim Senden der E-Mail über die Gmail API: %s", e, exc_info=True)


def create_markdown_report(summaries_and_categories, markdown_report_path):
    logger.info("Erstelle Markdown-Report unter %s", markdown_report_path)
    categorized_entries = {}
    for url, details in summaries_and_categories.items():
        logger.debug("Verarbeite Artikel: %s", url)
        category = details.get("category") or "Uncategorized"
        subcategory = details.get("sub_category") or "No Subcategory"
        summary = details.get("summary")
        if not summary or str(summary).strip().lower() in ["n/a", "none", "null"]:
            summary = "(keine Zusammenfassung verfügbar)"
        reading_time = details.get("reading_time")
        hn_points = details.get("hn_points")
        is_alert = details.get("source") == "alerts"
        reading_time_text = (f"read in {reading_time} min" if reading_time else "read time n/a")
        if category not in categorized_entries:
            categorized_entries[category] = {}
        if subcategory not in categorized_entries[category]:
            categorized_entries[category][subcategory] = []
        categorized_entries[category][subcategory].append((summary, url, reading_time_text, hn_points, is_alert))
    try:
        with open(markdown_report_path, "w", encoding="utf-8") as file:
            file.write(_report_title())
            for category, subcategories in categorized_entries.items():
                file.write(f"## {category}\n\n")
                for subcategory, articles in subcategories.items():
                    if subcategory != "No Subcategory":
                        file.write(f"### {subcategory}\n\n")
                    for summary, url, reading_time_text, hn_points, is_alert in articles:
                        emoji = ""
                        if hn_points and not is_alert:
                            if hn_points >= 200:
                                emoji = "🚀 "
                            elif 50 <= hn_points < 200:
                                emoji = "🔥 "
                        line = f"- {emoji}{summary} ([{reading_time_text}]({url}))"
                        if hn_points and not is_alert:
                            line += f" ({hn_points} points)"
                        file.write(line + "\n")
                    file.write("\n")
    except Exception as e:
        logger.error("Fehler beim Erstellen des Markdown-Reports: %s", e, exc_info=True)


def cleanup_markdown_report(markdown_report_path):
    """Bereinigt den erzeugten Markdown-Report.

    - Führt doppelte Kategorien/Subkategorien zusammen (auch bei abweichender Groß-/Kleinschreibung/Whitespace).
    - Entfernt Platzhalter-Einträge ohne verwertbare Summary.
    """
    logger.info("Bereinige Markdown-Report: %s", markdown_report_path)

    if not os.path.exists(markdown_report_path):
        logger.warning("Markdown-Report nicht gefunden, Cleanup übersprungen: %s", markdown_report_path)
        return

    try:
        with open(markdown_report_path, "r", encoding="utf-8") as file:
            lines = file.readlines()
    except Exception as e:
        logger.error("Konnte Markdown-Report nicht lesen: %s", e, exc_info=True)
        return

    grouped = {}
    category_order = []
    subcategory_order = {}

    current_category_key = None
    current_subcategory_key = "No Subcategory"

    def _norm(value):
        return " ".join((value or "").strip().split())

    def _ensure_category(category_label):
        category_display = _norm(category_label) or "Uncategorized"
        category_key = category_display.casefold()
        if category_key not in grouped:
            grouped[category_key] = {
                "display": category_display,
                "subcategories": {},
            }
            category_order.append(category_key)
            subcategory_order[category_key] = []
        return category_key

    def _ensure_subcategory(category_key, subcategory_label):
        sub_display = _norm(subcategory_label) or "No Subcategory"
        sub_key = sub_display.casefold()
        if sub_key not in grouped[category_key]["subcategories"]:
            grouped[category_key]["subcategories"][sub_key] = {
                "display": sub_display,
                "entries": [],
            }
            subcategory_order[category_key].append(sub_key)
        return sub_key

    for raw_line in lines:
        line = raw_line.rstrip("\n")
        stripped = line.strip()

        if stripped.startswith("## "):
            current_category_key = _ensure_category(stripped[3:])
            current_subcategory_key = _ensure_subcategory(current_category_key, "No Subcategory")
            continue

        if stripped.startswith("### ") and current_category_key is not None:
            current_subcategory_key = _ensure_subcategory(current_category_key, stripped[4:])
            continue

        if stripped.startswith("- ") and current_category_key is not None:
            if "(keine Zusammenfassung verfügbar)" in stripped and "read time n/a" in stripped:
                continue
            entry_list = grouped[current_category_key]["subcategories"][current_subcategory_key]["entries"]
            if stripped not in entry_list:
                entry_list.append(stripped)

    try:
        with open(markdown_report_path, "w", encoding="utf-8") as file:
            file.write(_report_title())
            for category_key in category_order:
                category_data = grouped[category_key]
                has_any_entries = any(
                    sub_data["entries"]
                    for sub_data in category_data["subcategories"].values()
                )
                if not has_any_entries:
                    continue

                file.write(f"## {category_data['display']}\n\n")
                for sub_key in subcategory_order[category_key]:
                    sub_data = category_data["subcategories"][sub_key]
                    if not sub_data["entries"]:
                        continue

                    if sub_data["display"] != "No Subcategory":
                        file.write(f"### {sub_data['display']}\n\n")

                    for entry in sub_data["entries"]:
                        file.write(entry + "\n")
                    file.write("\n")
    except Exception as e:
        logger.error("Fehler beim Bereinigen des Markdown-Reports: %s", e, exc_info=True)
