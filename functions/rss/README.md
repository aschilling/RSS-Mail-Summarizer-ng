# RSS Connector – Google Cloud Function

Modularer Microservice zur Synchronisation von RSS/Atom Feeds.  
Ruft neue Artikel aus konfigurierten Feeds ab, extrahiert Metadaten und persistiert sie strukturiert in Firestore zur weiteren Verarbeitung (z. B. KI-Zusammenfassung, E-Mail-Pipeline).

## Projektstruktur

    functions/rss/
    ├── main.py              # HTTP Entry-Point (Cloud Function)
    ├── rss_service.py       # Geschäftslogik (RSSService)
    ├── database.py          # Firestore-Zugriffsschicht
    ├── config.py            # Feed-Konfiguration
    ├── requirements.txt     # Python-Abhängigkeiten
    ├── cloudbuild.yaml      # Build- & Deployment-Konfiguration
    └── README.md            # Dokumentation

## Architektur & Kernfunktionen

Der Service folgt dem gleichen Pattern wie der Mastodon Connector und ermöglicht die parallele Verarbeitung mehrerer RSS Feeds mit unterschiedlichen Crawl-Modi.

### 1. RSSService (rss_service.py)

**Feed Processing:** Verarbeitet alle in `config.py` definierten Feeds sequenziell.

**Dual-Mode Filtering:**
- `since_last_crawl`: Lädt nur neue Artikel seit dem letzten Crawl (nutzt State-Tracking)
- `time_window`: Lädt Artikel innerhalb eines definierten Zeitfensters (z. B. letzte 24h)

**Conditional GET:** Unterstützt HTTP ETag und Last-Modified Header zur Bandbreitenoptimierung.

**Metadata Extraction:** Extrahiert nicht nur URLs, sondern auch Titel (rss_title), Zusammenfassung (rss_summary) und Veröffentlichungsdatum (rss_published) aus den Feed-Einträgen.

### 2. FirestoreRepository (database.py)

Bietet eine saubere Schnittstelle zur Firebase-Datenbank.

Verwaltet zwei Collections:
- **website**: Speichert die extrahierten URLs und deren Metadaten (analog zu Mastodon).
- **rss_feeds_state**: Speichert den Status pro Feed (last_etag, last_modified, last_entry_date).

### 3. Config (config.py)

Zentrale Konfiguration aller RSS Feeds mit folgenden Parametern pro Feed:
- `name`: Feed-Identifier (wird als `source` in Firestore gespeichert)
- `url`: Feed-URL
- `mode`: Crawl-Modus (`since_last_crawl` oder `time_window`)
- `time_window_hours`: Zeitfenster in Stunden (nur für `time_window` Mode)
- `use_etag`: Boolean für ETag-Support

### 4. Entry-Point (main.py)

Verwendet das functions-framework, um die Logik als HTTP-Trigger bereitzustellen.

Nimmt Anfragen entgegen und stößt den Synchronisationsprozess für alle konfigurierten Feeds an.

## Setup & Deployment

### Lokale Vorbereitung

Installiere die notwendigen Bibliotheken für die Entwicklung:

    cd functions/rss
    pip install -r requirements.txt

### Feed-Konfiguration

Bearbeite `config.py` um neue RSS Feeds hinzuzufügen:

    RSS_FEEDS = [
        {
            "name": "my_feed",
            "url": "https://example.com/feed.xml",
            "mode": "since_last_crawl",
            "use_etag": True
        }
    ]

### Deployment zur Google Cloud

Das Deployment wird über Google Cloud Build gesteuert. Der RSS Connector wird automatisch deployed, wenn die Master-Build-Datei ausgeführt wird:

    # Aus dem Projekt-Root
    gcloud builds submit . --config functions/cloudbuild.yaml

Alternativ kann der RSS Connector einzeln deployed werden:

    cd functions/rss
    gcloud builds submit . --config cloudbuild.yaml --substitutions=_PROJECT_ID=[DEINE_PROJECT_ID]

## Konfiguration (Secrets & Umgebungsvariablen)

Der Service benötigt Zugriff auf folgende Ressourcen in GCP:

**Secrets:**
- `rss-firebase-key`: Enthält die Service-Account-Informationen für Firestore.

**Umgebungsvariablen:**
- `PROJECT_ID`: Google Cloud Projekt-ID
- `LOG_LEVEL`: Legt die Log-Ausführlichkeit fest (z. B. `DEBUG`, `INFO`)

## Crawl-Modi im Detail

### Mode: since_last_crawl

Konfiguration:

    {
        "mode": "since_last_crawl",
        "use_etag": True
    }

**Verhalten:**
- Speichert Datum des neuesten Artikels im Feed
- Beim nächsten Crawl: Lädt nur Artikel, die neuer sind als das gespeicherte Datum
- Ideal für Feeds mit unregelmäßigen Updates
- Nutzt ETag wenn verfügbar (verhindert unnötige Downloads)

**Beispiel:** HackerNews RSS Feed (Updates mehrmals täglich, aber unregelmäßig)

### Mode: time_window

Konfiguration:

    {
        "mode": "time_window",
        "time_window_hours": 24,
        "use_etag": False
    }

**Verhalten:**
- Lädt alle Artikel innerhalb der letzten N Stunden
- Ignoriert vorherigen Crawl-Status
- Ideal für hochfrequente Feeds oder wenn Vollständigkeit wichtiger ist als Effizienz

**Beispiel:** TechCrunch Feed (konstanter Artikel-Flow, will keine verpassen)

## Integration mit anderen Services

### Workflow im Gesamtsystem:

1. **RSS Connector** (diese Function) → Schreibt URLs in Firestore Collection `website`
2. **Sendmail Function** → Liest `website`, generiert KI-Summaries, versendet E-Mail
3. **Alerts Function** → Parallel: Verarbeitet Google Alerts

**Wichtig:** RSS Connector speichert URLs mit `source: "feed_name"`, analog zu Mastodon (`source: "mastodon"`). Die Sendmail Function erkennt automatisch alle Quellen und verarbeitet sie einheitlich.

## Monitoring & Logs

Logs können in der Google Cloud Console eingesehen werden:

    gcloud functions logs read rss_connector --region=europe-west3

**Wichtige Log-Meldungen:**
- `Starting RSS Connector - processing N feeds`
- `Processing feed: [name] (mode: [mode], etag: [bool])`
- `Feed [name]: N new entries to process`
- `RSS Connector completed in X.XXs - N new links saved`

## Troubleshooting

**Problem:** Feed wird nicht abgerufen (0 neue Einträge)

**Lösung:** Prüfe ob Feed ETag-Support hat aber `use_etag: False` gesetzt ist, oder ob das gespeicherte Datum in `rss_feeds_state` aktuell ist.

**Problem:** Doppelte URLs

**Lösung:** Firestore Document-IDs sind URL-basiert (via `safe_url()`). Duplikate werden automatisch übersprungen.

**Problem:** Memory-Fehler bei großen Feeds

**Lösung:** Erhöhe Memory in `cloudbuild.yaml` (aktuell 1GiB, kann auf 2GiB erhöht werden).
