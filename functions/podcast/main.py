import os
import json
import logging
import traceback
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from urllib.parse import urlparse, parse_qs, unquote

from google.cloud import texttospeech, storage
from google.oauth2 import service_account
from firebase_admin import credentials, firestore, initialize_app
import firebase_admin

from google import genai
from google.genai import types

import functions_framework

logger = logging.getLogger("podcast_generator")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(handler)

CONFIG = {
    "limit": 15,
    "gcs_bucket_name": os.environ.get("GCS_BUCKET_NAME")
}

def get_gemini_api_key():
    key = os.getenv("GEMINI_API_KEY") or os.getenv("RSS_VERTEX_AI_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY environment variable is missing.")
    return key.strip()

def get_gcp_credentials():
    secret_env = "RSS_FIREBASE_KEY"
    if secret_env in os.environ:
        return service_account.Credentials.from_service_account_info(json.loads(os.environ[secret_env]))
    return service_account.Credentials.from_service_account_file("keys/serviceAccountKey.json")

def initialize_firebase():
    if not firebase_admin._apps:
        secret_env = "RSS_FIREBASE_KEY"
        if secret_env in os.environ:
            cred = credentials.Certificate(json.loads(os.environ[secret_env]))
        else:
            cred = credentials.Certificate("keys/serviceAccountKey.json")
        initialize_app(cred)
    return firestore.client()

class PodcastAIService:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = genai.Client(api_key=self.api_key)

    def _clean_youtube_url(self, url: str) -> str:
        url = url.strip().rstrip(":")
        parsed = urlparse(url)
        if "youtu.be" in parsed.netloc:
            return f"https://www.youtube.com/watch?v={parsed.path.lstrip('/')}"
        if "youtube.com" in parsed.netloc:
            qs = parse_qs(parsed.query)
            if "v" in qs:
                return f"https://www.youtube.com/watch?v={qs['v'][0]}"
        return url

    def fetch_raw_content(self, urls):
        youtube_urls = [u for u in urls if "youtube.com" in u or "youtu.be" in u]
        web_urls = [u for u in urls if u not in youtube_urls]

        content_collection = []
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}

        for url in web_urls:
            logger.info(f"Lade Rohtext von: {url}")
            try:
                res = requests.get(url, headers=headers, timeout=15)
                res.raise_for_status()
                soup = BeautifulSoup(res.content, 'html.parser')
                # Extrahiere nur relevante Text-Tags, um unnötigen Müll zu vermeiden
                text = ' '.join([p.get_text() for p in soup.find_all(['p', 'article', 'h1', 'h2', 'h3'])])
                # Kürzen auf max 15.000 Zeichen pro Artikel, um das Kontextfenster nicht zu sprengen
                clean_text = text.replace('\n', ' ').strip()
                content_collection.append(f"Quelle: {url}\nInhalt: {clean_text[:15000]}")
            except Exception as e:
                logger.error(f"Fehler beim Laden von {url}: {e}")

        for url in youtube_urls:
            logger.info(f"Erstelle detaillierte Zusammenfassung für YouTube: {url}")
            try:
                clean_url = self._clean_youtube_url(url)
                youtube_video = types.Part.from_uri(file_uri=clean_url, mime_type="video/*")

                # Native SDK Call
                response = self.client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=[
                        youtube_video,
                        "Erstelle eine sehr ausführliche, detaillierte Zusammenfassung dieses Videos. Nenne alle wichtigen Argumente, Fakten und Diskussionspunkte, damit daraus später ein tiefergehender Podcast erstellt werden kann."
                    ]
                )
                content_collection.append(f"Quelle (YouTube): {url}\nInhalt: {response.text.strip()}")
            except Exception as e:
                logger.error(f"Fehler bei YouTube Video {url}: {e}")

        return content_collection

    def generate_script(self, content_collection):
        if not content_collection:
            logger.warning("Keine Inhalte zum Generieren des Skripts vorhanden.")
            return []

        logger.info("Generiere ausführliches Podcast-Skript über native GenAI SDK...")
        content_text = "\n\n---\n\n".join(content_collection)

        system_instruction = (
            "Du erstellst ein langes, detailliertes deutsches Podcast-Skript für zwei Moderatoren (Sprecher 1 und Sprecher 2) "
            "basierend auf den übergebenen Rohtexten. Ziel ist eine Podcast-Länge von etwa 15 bis 20 Minuten. "
            "Gehe tief in die Themen ein, lass die Moderatoren die Inhalte diskutieren, Vor- und Nachteile abwägen und Details aus den Texten erklären.\n\n"
            "WICHTIG: Gib ausschließlich ein valides JSON-Array zurück. Jedes Element im Array muss ein reiner String sein, der den gesprochenen Text enthält.\n"
            "Der Text muss abwechselnd von Sprecher 1 und Sprecher 2 gelesen werden. Keine Rollennamen oder Präfixe (wie 'Sprecher 1:') im String."
        )

        try:
            # Native SDK Call für das Skript
            response = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=f"Nachrichten-Rohtexte:\n{content_text}",
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.7,
                    response_mime_type="application/json", # Erzwingt sauberes JSON vom Modell
                )
            )

            script_text = response.text.strip()
            return json.loads(script_text)

        except json.JSONDecodeError as e:
            logger.error(f"JSON Parse Fehler: {response.text}")
            raise RuntimeError(f"Ungültiges JSON von Gemini: {e}")
        except Exception as e:
            logger.error(f"Fehler bei der Skriptgenerierung: {e}")
            raise

def generate_audio_and_upload(script):
    if not script:
        return None

    if not CONFIG["gcs_bucket_name"]:
        raise RuntimeError("GCS_BUCKET_NAME ist nicht gesetzt.")

    logger.info("Starte GCP Text-to-Speech...")
    creds = get_gcp_credentials()
    tts_client = texttospeech.TextToSpeechClient(credentials=creds)
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3,
        sample_rate_hertz=44100
    )

    voices = ["de-DE-Journey-D", "de-DE-Journey-F"]
    combined_audio = b""

    # Generiere das Audio (dieser Teil dauert am längsten)
    for index, text in enumerate(script):
        logger.debug(f"Vertone Passage {index+1}/{len(script)}...")
        synthesis_input = texttospeech.SynthesisInput(text=text)
        voice_params = texttospeech.VoiceSelectionParams(language_code="de-DE", name=voices[index % 2])
        response = tts_client.synthesize_speech(input=synthesis_input, voice=voice_params, audio_config=audio_config)
        combined_audio += response.audio_content

    logger.info("Audio-Generierung abgeschlossen. Lade Datei hoch...")

    # Auth Token explizit neu laden, um SSL/Token Timeouts nach langer TTS Generierung zu vermeiden
    fresh_creds = get_gcp_credentials()
    storage_client = storage.Client(credentials=fresh_creds, project=fresh_creds.project_id)
    bucket = storage_client.bucket(CONFIG["gcs_bucket_name"])

    filename = f"podcast_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp3"
    blob = bucket.blob(filename)
    blob.upload_from_string(combined_audio, content_type="audio/mpeg")

    logger.info(f"Podcast hochgeladen: gs://{CONFIG['gcs_bucket_name']}/{filename}")
    return filename

@functions_framework.http
def podcast_trigger(request=None):
    logger.info("Starte podcast_trigger.")
    response_data = {"status": "started", "details": {}}

    try:
        db = initialize_firebase()
        query = db.collection("website").where("mail_sent", "==", False).limit(CONFIG["limit"]).stream()
        entries = [doc.to_dict() for doc in query]

        if not entries:
            return json.dumps({"status": "ok", "message": "Keine neuen Einträge gefunden."}), 200

        urls = [e.get("url") for e in entries if e.get("url")]
        logger.info(f"Verarbeite {len(urls)} URLs für den Podcast.")

        ai_service = PodcastAIService(get_gemini_api_key())
        raw_content = ai_service.fetch_raw_content(urls)

        script = ai_service.generate_script(raw_content)
        response_data["details"]["script_passages"] = len(script)

        filename = generate_audio_and_upload(script)

        # Optional: Hier könntest du die Dokumente in Firestore auf mail_sent=True setzen,
        # falls der Podcast-Generator unabhängig vom E-Mail-Versand laufen soll.

        response_data["status"] = "success"
        response_data["file"] = filename
        return json.dumps(response_data, indent=2), 200

    except Exception as e:
        logger.error(f"Fehler: {e}\n{traceback.format_exc()}")
        response_data["status"] = "error"
        response_data["message"] = str(e)
        return json.dumps(response_data, indent=2), 500

if __name__ == "__main__":
    podcast_trigger()