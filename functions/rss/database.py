"""
Firestore Repository für RSS Connector
"""

import os
import logging
import re
from datetime import datetime
from urllib.parse import urlparse, parse_qs, unquote
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, firestore, initialize_app

load_dotenv()

log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rss_connector")


def initialize_firebase():
    """Initialize Firebase Admin SDK"""
    if not firebase_admin._apps:
        if os.getenv('K_SERVICE'):
            logger.info("Cloud environment: Using Application Default Credentials")
            initialize_app()
        else:
            SERVICE_ACCOUNT_KEY_PATH = "serviceAccountKey.json"
            if os.path.exists(SERVICE_ACCOUNT_KEY_PATH):
                logger.info(f"Local environment: Using {SERVICE_ACCOUNT_KEY_PATH}")
                cred = credentials.Certificate(SERVICE_ACCOUNT_KEY_PATH)
                initialize_app(cred)
            else:
                logger.error("No credentials found (neither cloud nor local file)")
                raise FileNotFoundError("Service Account Key missing for local execution")
    else:
        logger.debug("Firebase already initialized")


def safe_url(url: str) -> str:
    """
    Convert URL to Firestore-safe document ID
    Removes special characters, replaces with hyphens
    """
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    target = qs.get("url")
    if target:
        url = unquote(target[0])
    else:
        url = url
    
    url = url.strip()
    url = re.sub(r"[^a-zA-Z0-9_-]", "-", url)
    url = re.sub(r"-+", "-", url)
    url = url.strip("-")
    
    return url


class FirestoreRepository:
    """Handles all Firestore operations for RSS Connector"""
    
    def __init__(self):
        initialize_firebase()
        self.db = firestore.client()
    
    def add_url_to_website_collection(
        self, 
        url: str, 
        feed_name: str,
        rss_title: str = None,
        rss_summary: str = None,
        rss_published: str = None
    ):
        """
        Save new URL from RSS feed to Firestore
        
        Args:
            url: Article URL
            feed_name: Name of RSS feed (goes to 'source' field)
            rss_title: RSS item title
            rss_summary: RSS item summary/description
            rss_published: RSS item publish date
        """
        doc_ref = self.db.collection("website").document(safe_url(url))
        doc = doc_ref.get()
        
        if not doc.exists:
            data = {
                "url": url,
                "processed": False,
                "mail_sent": False,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"),
                "source": feed_name
            }
            
            # Add optional RSS metadata
            if rss_title:
                data["rss_title"] = rss_title
            if rss_summary:
                data["rss_summary"] = rss_summary
            if rss_published:
                data["rss_published"] = rss_published
            
            doc_ref.set(data)
            logger.info(f"New URL saved: {url} (source: {feed_name})")
        else:
            logger.debug(f"URL already exists (skipped): {url}")
    
    def get_feed_state(self, feed_name: str) -> dict:
        """
        Get last crawl state for specific feed
        
        Returns:
            dict with keys: last_etag, last_modified, last_entry_date, last_crawl
            or empty dict if no state exists
        """
        doc_ref = self.db.collection("rss_feeds_state").document(feed_name)
        doc = doc_ref.get()
        
        if doc.exists:
            return doc.to_dict()
        return {}
    
    def update_feed_state(
        self, 
        feed_name: str, 
        etag: str = None, 
        last_modified: str = None,
        last_entry_date: datetime = None
    ):
        """
        Update crawl state for specific feed
        
        Args:
            feed_name: Feed identifier
            etag: HTTP ETag header value
            last_modified: HTTP Last-Modified header value
            last_entry_date: Timestamp of most recent entry processed
        """
        doc_ref = self.db.collection("rss_feeds_state").document(feed_name)
        
        data = {
            "feed_name": feed_name,
            "last_crawl": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
        }
        
        if etag:
            data["last_etag"] = etag
        if last_modified:
            data["last_modified"] = last_modified
        if last_entry_date:
            data["last_entry_date"] = last_entry_date.strftime("%Y-%m-%d %H:%M:%S.%f")
        
        doc_ref.set(data)
        logger.info(f"Feed state updated: {feed_name}")
