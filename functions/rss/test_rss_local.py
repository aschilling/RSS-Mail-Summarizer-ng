"""
Local RSS Connector Test (without Firestore)
Tests feed parsing and filtering logic
"""

import feedparser
from datetime import datetime, timedelta


def test_feed_parsing():
    """Test basic RSS feed parsing"""
    print("=" * 60)
    print("TEST 1: Feed Parsing")
    print("=" * 60)
    
    test_feeds = [
        ("HackerNews", "https://news.ycombinator.com/rss"),
        ("TechCrunch", "https://techcrunch.com/feed/")
    ]
    
    for name, url in test_feeds:
        print(f"\nFetching: {name}")
        print(f"URL: {url}")
        
        feed = feedparser.parse(url)
        
        if feed.bozo:
            print(f"  WARNING: Parsing issues - {feed.bozo_exception}")
        
        print(f"  Status: {feed.get('status', 'N/A')}")
        print(f"  Entries: {len(feed.entries)}")
        
        if feed.entries:
            entry = feed.entries[0]
            print(f"  First entry:")
            print(f"    Title: {entry.get('title', 'N/A')[:60]}")
            print(f"    Link: {entry.get('link', 'N/A')}")
            
            # Check date fields
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                pub_date = datetime(*entry.published_parsed[:6])
                print(f"    Published: {pub_date}")
            elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
                pub_date = datetime(*entry.updated_parsed[:6])
                print(f"    Updated: {pub_date}")
            else:
                print(f"    Date: N/A")


def test_time_window_filter():
    """Test time window filtering"""
    print("\n" + "=" * 60)
    print("TEST 2: Time Window Filtering (24h)")
    print("=" * 60)
    
    url = "https://news.ycombinator.com/rss"
    feed = feedparser.parse(url)
    
    cutoff = datetime.now() - timedelta(hours=24)
    print(f"\nCutoff time: {cutoff}")
    print(f"Total entries: {len(feed.entries)}")
    
    filtered = 0
    for entry in feed.entries:
        if hasattr(entry, 'published_parsed') and entry.published_parsed:
            entry_date = datetime(*entry.published_parsed[:6])
            if entry_date > cutoff:
                filtered += 1
    
    print(f"Entries within 24h: {filtered}")


def test_etag_support():
    """Test ETag header support"""
    print("\n" + "=" * 60)
    print("TEST 3: ETag Support")
    print("=" * 60)
    
    url = "https://news.ycombinator.com/rss"
    
    # First request
    print("\nFirst request:")
    feed1 = feedparser.parse(url)
    etag = feed1.headers.get('etag')
    last_modified = feed1.headers.get('last-modified')
    
    print(f"  ETag: {etag}")
    print(f"  Last-Modified: {last_modified}")
    
    if etag:
        # Second request with ETag
        print("\nSecond request with If-None-Match:")
        feed2 = feedparser.parse(url, request_headers={'If-None-Match': etag})
        print(f"  Status: {feed2.status}")
        
        if feed2.status == 304:
            print("  ✓ Feed not modified (304) - ETag working!")
        else:
            print(f"  Note: Got status {feed2.status} (feed may have been updated)")
    else:
        print("  Feed does not support ETags")


if __name__ == "__main__":
    print("RSS Connector - Local Test Suite")
    print("Testing feed parsing without Firestore connection\n")
    
    try:
        test_feed_parsing()
        test_time_window_filter()
        test_etag_support()
        
        print("\n" + "=" * 60)
        print("ALL TESTS COMPLETED")
        print("=" * 60)
        
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
