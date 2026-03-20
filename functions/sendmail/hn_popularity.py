"""Ruft die Popularität eines Artikels auf Hacker News ab.

Nutzt die Algolia-API von Hacker News, um Stories anhand der URL zu suchen.
Gibt die Anzahl der Points der gefundenen Story zurück,
oder None falls keine Ergebnisse gefunden werden oder ein Fehler auftritt.
"""

import logging
import requests

logger = logging.getLogger(__name__)


def fetch_hn_points(url: str, timeout: int = 8):
    """Ruft die HN-Punkte für eine URL ab. Gibt None zurück bei Fehler oder keinem Treffer."""
    try:
        logger.debug(f"Abrufen der Hacker-News-Punkte für URL: {url} mit Timeout={timeout}")

        r = requests.get(
            "https://hn.algolia.com/api/v1/search",
            params={
                "query": url,
                "restrictSearchableAttributes": "url",
                "tags": "story",
            },
            timeout=timeout,
        )
        r.raise_for_status()
        hits = r.json().get("hits", [])

        if not hits:
            logger.info(f"Keine HackerNews-Ergebnisse für URL: {url}")
            return None

        best = max(hits, key=lambda x: (x.get("points") or 0))
        points = best.get("points") or 0
        logger.info(f"HackerNews Story gefunden: {points} Punkte für {url}")
        return points

    except requests.exceptions.Timeout:
        logger.warning(f"Zeitüberschreitung nach {timeout}s für URL: {url}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Anfrage fehlgeschlagen für URL {url}: {e}")
        return None
    except Exception as e:
        logger.error(f"Unerwarteter Fehler beim Abrufen der HN-Punkte für {url}: {e}")
        return None
