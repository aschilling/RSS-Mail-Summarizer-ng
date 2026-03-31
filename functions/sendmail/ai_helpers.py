import os
import logging
import re
from collections import defaultdict
from urllib.parse import urlparse, parse_qs, unquote

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.rate_limiters import InMemoryRateLimiter
from google import genai
from google.genai import types
from config import SendmailConfig

logger = logging.getLogger(__name__)


class AIService:
    def __init__(self, gemini_api_key: str):
        self.gemini_api_key = gemini_api_key
        self.genai_client = None
        self.llm = None
        self.website_batch_size = int(os.getenv("WEBSITE_SUMMARY_BATCH_SIZE", "20"))

    def _init_llm(self):
        rate_limiter = InMemoryRateLimiter(requests_per_second=0.2, check_every_n_seconds=0.1, max_bucket_size=1)
        return ChatGoogleGenerativeAI(
            model=SendmailConfig.GEMINI_MODEL,
            google_api_key=self.gemini_api_key,
            temperature=0,
            max_tokens=None,
            timeout=None,
            max_retries=2,
            rate_limiter=rate_limiter,
        )

    def _ensure_ai_clients(self):
        if self.llm is not None and self.genai_client is not None:
            return
        if not self.gemini_api_key:
            raise RuntimeError("Kein gültiger Gemini API-Key gefunden. Bitte setze GEMINI_API_KEY als Secret ohne Zeilenumbrüche.")
        self.genai_client = genai.Client(api_key=self.gemini_api_key)
        self.llm = self._init_llm()

    def summarise_and_categorize_websites(self, links_list):
        """Fasst URLs zusammen und kategorisiert sie in stabilen Batches."""
        self._ensure_ai_clients()
        logger.info(f"Starte Zusammenfassung & Kategorisierung für {len(links_list)} URLs.")

        if not links_list:
            return {}

        batch_size = max(1, self.website_batch_size)
        all_results = {}
        total_batches = (len(links_list) + batch_size - 1) // batch_size

        for batch_index, start in enumerate(range(0, len(links_list), batch_size), start=1):
            batch_urls = links_list[start:start + batch_size]
            logger.info(
                "Verarbeite Website-Batch %s/%s (%s URLs)",
                batch_index,
                total_batches,
                len(batch_urls),
            )
            prompt = self._build_prompt(batch_urls)
            batch_results = self._process_llm_response(prompt)
            all_results.update(batch_results)

            missing_urls = [url for url in batch_urls if url not in batch_results]
            if missing_urls:
                logger.warning(
                    "Batch %s/%s: %s URLs ohne parsebares Ergebnis.",
                    batch_index,
                    total_batches,
                    len(missing_urls),
                )

        logger.info("Batch-Verarbeitung abgeschlossen: %s/%s URLs mit Ergebnis.", len(all_results), len(links_list))
        return all_results

    def _build_prompt(self, links_list):
        """Erstellt den Prompt für Gemini (EXAKT wie in llm_calls.py)."""
        logger.debug("Erstelle Prompt für Gemini-Anfrage...")
        combined_input = "\n\n".join(f"Input {i+1} (URL: {url})" for i, url in enumerate(links_list))

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    """
                You are a daily news editor at The Economist who processes multiple URLs provided by the user.
                For each input, perform the following tasks:

                1. Write a safe, high-level English summary (1–3 sentences) for the content of the Website. 
                    - Be factual, neutral, and compact. Prefer active voice. No fluff.
                    - Use neutral, non-graphic language
                    - NEVER write "the article", "the podcast", "the text" or any self-referential mention of the summary's original piece. Focus on the content, and refer to it directly.
                    - If possible, mention the author by name.
                    - Translate to English if needed. 
                2. Categorize it into one of the following categories:
                   - Technology and Gadgets
                   - Artificial Intelligence
                   - Programming and Development
                   - Politics
                   - Business and Finance
                   - Sports
                   - Education and Learning
                   - Health and Wellness
                   - Entertainment and Lifestyle
                   - Travel and Tourism
                   If a website does not fit into one of these categories, return 'Uncategorized'.
                3. Identify specific topics or entities mentioned in the articles. These should be precise and clearly defined, such as names of technologies, events, organizations, or specific concepts discussed in the text. Prefer consistency across items; avoid inventing many distinct topics.
                4. Estimate the reading time of the article in minutes based on the length and complexity of the content. Make sure you assess each article individually!

                SPECIAL RULE FOR GITHUB URLs:
                - If the URL is a GitHub **repository** page, set Category **exactly** to "GitHub" (override the list above).
                - If content is a **GitHub Blog** Post, treat it like any other website, do not categorize it as "GitHub". Check the content of the page to determine if it is a blog post.
                DO NOT use the "GitHub" category; choose from the normal list above if the post is a blog post. Only repositories should be categorized as "GitHub".

                If you are unable to access the contents of the provided website, return "Website content could not be reached!" for that input.

                Format your response as follows:
                Input 1 (URL: <url>):
                Summary: <summary>
                Category: <category> # "GitHub" only for repository URLs as defined above
                Topics: <topic1>, <topic2>, ...
                Reading Time: <X> minutes

                Input 2 (URL: <url>):
                Summary: <summary>
                Category: <category>
                Topics: <topic1>, <topic2>, ...
                Reading Time: <X> minutes

                ...

                Ensure that the topics are specific and relevant to the main content of the article.
                """,
                ),
                ("human", f"{combined_input}"),
            ]
        )

        logger.debug("Prompt erfolgreich erstellt.")
        return prompt

    def _process_llm_response(self, prompt):
        """Verarbeitet LLM-Ausgabe und extrahiert strukturierte Daten (EXAKT wie in llm_calls.py)."""
        logger.info("Rufe Gemini LLM zur Zusammenfassung und Kategorisierung auf...")
        chain = prompt | self.llm
        response = chain.invoke({}).content
        logger.debug(f"LLM raw response:\n{response}")

        results = {}
        topic_counts = defaultdict(list)

        for entry in response.split("\n\n"):
            if "Input" in entry:
                url_match = re.search(r"URL:\s*(https?://[^\s)]+)", entry, re.IGNORECASE)
                if not url_match:
                    logger.warning("URL konnte aus Eintrag nicht extrahiert werden, überspringe Eintrag.")
                    continue

                url = url_match.group(1)
                summary_match = re.search(r"Summary:\s*(.+)", entry, re.IGNORECASE)
                category_match = re.search(r"Category:\s*(.+)", entry, re.IGNORECASE)
                topics_match = re.search(r"Topics:\s*(.+)", entry, re.IGNORECASE)
                reading_time_match = re.search(r"Reading\s*Time:\s*(\d+)\s*minute[s]?", entry, re.IGNORECASE)

                summary = summary_match.group(1).strip() if summary_match else None
                category = category_match.group(1).strip() if category_match else None
                topics = ([topic.strip() for topic in topics_match.group(1).split(",")] if topics_match else [])
                reading_time = (int(reading_time_match.group(1)) if reading_time_match else None)

                results[url] = {
                    "summary": summary,
                    "category": category,
                    "topics": topics,
                    "reading_time": reading_time,
                    "sub_category": None,
                }
                logger.debug(f"Eintrag für URL {url} mit Kategorie '{category}' verarbeitet.")

                for topic in topics:
                    topic_counts[topic].append(url)

        # Subkategorisierung basierend auf Themen
        for topic, urls in topic_counts.items():
            if len(urls) >= 3:
                logger.info(f"Subkategorie '{topic}' für {len(urls)} URLs zugewiesen.")
                for url in urls:
                    if results[url]["sub_category"] is None:
                        results[url]["sub_category"] = topic

        return results

    def summarise_alerts(self, alerts_dict):
        """Erstellt Zusammenfassungen für Google Alerts (EXAKT wie in llm_calls.py)."""
        logger.info(f"Starte Zusammenfassung für {len(alerts_dict)} Google Alerts.")
        self._ensure_ai_clients()
        all_results = {}
        for label, urls in alerts_dict.items():
            logger.debug(f"Verarbeite Alert '{label}' mit {len(urls)} URLs.")
            combined_input = "\n".join(f"{url}" for url in urls)
            prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        f"""
            You are an assistant that summarizes multiple URLs for the alert '{label}'.
            For each URL, provide:
            1. Summary (2-3 sentences)
            2. Estimated reading time in minutes

            If you cannot access the website, return:
            "Website content could not be reached!"

            Format your response as follows:
            <URL>:
            Summary: <summary>
            Reading Time: <X> minutes
            """,
                    ),
                    ("human", combined_input),
                ]
            )
            result = self._process_alert_response(prompt, urls)
            all_results.update(result)
        return all_results

    def _process_alert_response(self, prompt, urls):
        """Verarbeitet Gemini Antwort für Google Alerts (EXAKT wie in llm_calls.py)."""
        logger.info("Rufe Gemini LLM für Google Alerts Zusammenfassung auf...")
        chain = prompt | self.llm
        response = chain.invoke({}).content
        logger.debug(f"LLM alert raw response:\n{response}")

        results = {}
        for entry in response.split("\n\n"):
            url_match = re.search(r"(https?://\S+)", entry)
            summary_match = re.search(r"Summary:\s*(.+)", entry, re.IGNORECASE)
            reading_time_match = re.search(r"Reading\s*Time:\s*(\d+)\s*minute[s]?", entry, re.IGNORECASE)

            if url_match:
                url = url_match.group(1)
                results[url] = {
                    "summary": summary_match.group(1).strip() if summary_match else None,
                    "reading_time": (int(reading_time_match.group(1)) if reading_time_match else None),
                }
                logger.debug(f"Eintrag für Alert-URL {url} verarbeitet.")
            else:
                logger.warning("Konnte URL aus Gemini Alert-Antwort nicht extrahieren.")

        return results

    def summarise_youtube_videos(self, youtube_urls):
        """Fasst YouTube-Videos zusammen und kategorisiert sie."""
        self._ensure_ai_clients()
        results = {}
        categories = [
            "Technology and Gadgets", "Artificial Intelligence", "Programming and Development",
            "Politics", "Business and Finance", "Sports", "Education and Learning",
            "Health and Wellness", "Entertainment and Lifestyle", "Travel and Tourism"
        ]
        for url in youtube_urls:
            url = url.rstrip(":")
            try:
                clean_url = self._clean_youtube_url(url)
                youtube_video = types.Part.from_uri(file_uri=clean_url, mime_type="video/*")
                prompt_text = f"""
Du bist ein Assistent, der YouTube-Videos zusammenfasst und kategorisiert.
Anweisungen:
1. Fasse das Video in 2-3 Sätzen zusammen.
2. Schätze die Betrachtungszeit in Minuten.
3. Ordne das Video einer der folgenden Kategorien zu:
   {', '.join(categories)}
   Wenn keine Kategorie passt, gib 'Uncategorized' zurück.
"""
                contents = [youtube_video, types.Part.from_text(text=prompt_text)]
                generate_config = types.GenerateContentConfig(temperature=0, max_output_tokens=1024, response_modalities=["TEXT"])
                response = self.genai_client.models.generate_content(
                    model=SendmailConfig.GEMINI_MODEL,
                    contents=contents,
                    config=generate_config,
                )
                text = response.text.strip()
                summary_match = re.search(r"Summary:\s*(.+)", text, re.IGNORECASE)
                reading_time_match = re.search(r"Reading\s*Time:\s*(\d+)", text, re.IGNORECASE)
                category_match = re.search(r"Category:\s*(.+)", text, re.IGNORECASE)
                results[url] = {
                    "summary": summary_match.group(1).strip() if summary_match else None,
                    "reading_time": int(reading_time_match.group(1)) if reading_time_match else None,
                    "category": category_match.group(1).strip() if category_match else "Uncategorized",
                }
            except Exception as e:
                logger.error(f"Fehler bei Verarbeitung des Videos {url}: {e}")
                results[url] = {"summary": None, "reading_time": None, "category": None}
        return results

    def summarise_youtube_alerts(self, youtube_urls):
        """Fasst YouTube-Videos aus Google Alerts zusammen."""
        self._ensure_ai_clients()
        results = {}
        for url in youtube_urls:
            url = url.rstrip(":")
            try:
                clean_url = self._clean_youtube_url(url)
                youtube_video = types.Part.from_uri(file_uri=clean_url, mime_type="video/*")
                prompt_text = """
Du bist ein Assistent, der YouTube-Videos aus Google Alerts zusammenfasst.
Anweisungen:
1. Fasse das Video in 2-3 Sätzen zusammen.
2. Schätze die Betrachtungszeit in Minuten.

Format:
Summary: <Zusammenfassung>
Reading Time: <geschätzte Minuten>
"""
                contents = [youtube_video, types.Part.from_text(text=prompt_text)]
                generate_config = types.GenerateContentConfig(temperature=0, max_output_tokens=1024, response_modalities=["TEXT"])
                response = self.genai_client.models.generate_content(
                    model=SendmailConfig.GEMINI_MODEL,
                    contents=contents,
                    config=generate_config,
                )
                text = response.text.strip()
                summary_match = re.search(r"Summary:\s*(.+)", text, re.IGNORECASE)
                reading_time_match = re.search(r"Reading\s*Time:\s*(\d+)", text, re.IGNORECASE)
                results[url] = {"summary": summary_match.group(1).strip() if summary_match else None, "reading_time": int(reading_time_match.group(1)) if reading_time_match else None}
                logger.info(f"Alert erfolgreich verarbeitet: {url}")
            except Exception as e:
                logger.error(f"Fehler bei Verarbeitung des Alerts {url}: {e}")
                results[url] = {"summary": None, "reading_time": None}
        return results

    def _clean_youtube_url(self, url: str) -> str:
        url = url.strip().rstrip(":")
        parsed = urlparse(url)
        if "google.com" in parsed.netloc and "youtube.com" in url:
            qs = parse_qs(parsed.query)
            if "url" in qs:
                return self._clean_youtube_url(unquote(qs["url"][0]))
        url = url.replace("&amp;", "&")
        parsed = urlparse(url)
        if "youtu.be" in parsed.netloc:
            video_id = parsed.path.lstrip("/")
            return f"https://www.youtube.com/watch?v={video_id}"
        if "youtube.com" in parsed.netloc:
            qs = parse_qs(parsed.query)
            if "v" in qs:
                return f"https://www.youtube.com/watch?v={qs['v'][0]}"
        return url
