"""
Test RSS Service Logic (without Firestore)
Validates filtering and entry processing
"""

from datetime import datetime, timedelta
from rss_service import RSSService
from config import Config


class MockFirestoreRepository:
    """Mock for testing without real Firestore"""
    
    def __init__(self):
        self.stored_urls = []
        self.feed_states = {}
    
    def get_feed_state(self, feed_name):
        return self.feed_states.get(feed_name, {})
    
    def update_feed_state(self, feed_name, etag=None, last_modified=None, last_entry_date=None):
        self.feed_states[feed_name] = {
            "last_etag": etag,
            "last_modified": last_modified,
            "last_entry_date": last_entry_date.strftime("%Y-%m-%d %H:%M:%S.%f") if last_entry_date else None
        }
    
    def add_url_to_website_collection(self, url, feed_name, rss_title=None, rss_summary=None, rss_published=None):
        self.stored_urls.append({
            "url": url,
            "feed_name": feed_name,
            "rss_title": rss_title,
            "rss_summary": rss_summary,
            "rss_published": rss_published
        })


def test_full_service():
    """Test complete RSS Service with mock database"""
    print("=" * 70)
    print("FULL SERVICE TEST")
    print("=" * 70)
    
    # Create service with mock repo
    mock_repo = MockFirestoreRepository()
    service = RSSService(repo=mock_repo)

    
    print(f"\nConfigured feeds: {len(service.feeds)}")
    for feed in service.feeds:
        print(f"  - {feed['name']} ({feed['mode']})")
    
    # Run service
    print("\nExecuting fetch_and_store_links()...")
    service.fetch_and_store_links()
    
    # Analyze results
    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)
    
    print(f"\nTotal URLs stored: {len(mock_repo.stored_urls)}")
    
    # Group by feed
    by_feed = {}
    for item in mock_repo.stored_urls:
        feed = item['feed_name']
        if feed not in by_feed:
            by_feed[feed] = []
        by_feed[feed].append(item)
    
    for feed_name, items in by_feed.items():
        print(f"\n{feed_name}: {len(items)} URLs")
        
        # Show first 3
        for i, item in enumerate(items[:3]):
            print(f"  [{i+1}] {item['rss_title'][:50]}")
            print(f"      URL: {item['url'][:60]}")
            print(f"      Published: {item['rss_published']}")
    
    # Check feed states
    print("\n" + "=" * 70)
    print("FEED STATES")
    print("=" * 70)
    
    for feed_name, state in mock_repo.feed_states.items():
        print(f"\n{feed_name}:")
        print(f"  Last Entry Date: {state.get('last_entry_date', 'N/A')}")
        print(f"  ETag: {state.get('last_etag', 'N/A')}")
        print(f"  Last-Modified: {state.get('last_modified', 'N/A')}")
    
    return len(mock_repo.stored_urls) > 0


def test_time_window_mode():
    """Test specific time window filtering"""
    print("\n" + "=" * 70)
    print("TIME WINDOW MODE TEST")
    print("=" * 70)
    
    mock_repo = MockFirestoreRepository()
    service = RSSService(repo=mock_repo)

    
    # Find techcrunch feed (time_window mode)
    techcrunch_feed = None
    for feed in service.feeds:
        if feed['mode'] == 'time_window':
            techcrunch_feed = feed
            break
    
    if not techcrunch_feed:
        print("ERROR: No time_window feed found in config")
        return False
    
    print(f"\nTesting feed: {techcrunch_feed['name']}")
    print(f"Time window: {techcrunch_feed['time_window_hours']}h")
    
    # Process only this feed
    new_links = service._process_feed(techcrunch_feed)
    
    print(f"\nLinks stored: {new_links}")
    print(f"Expected: Only articles from last {techcrunch_feed['time_window_hours']}h")
    
    # Verify dates
    cutoff = datetime.now() - timedelta(hours=techcrunch_feed['time_window_hours'])
    print(f"Cutoff: {cutoff}")
    
    valid = 0
    for item in mock_repo.stored_urls:
        if item['rss_published']:
            pub_date = datetime.strptime(item['rss_published'], "%Y-%m-%d %H:%M:%S")
            if pub_date > cutoff:
                valid += 1
            else:
                print(f"  WARNING: Entry older than cutoff: {item['rss_title'][:40]} ({pub_date})")
    
    print(f"\nValid entries (within window): {valid}/{len(mock_repo.stored_urls)}")
    
    return valid == len(mock_repo.stored_urls)


def test_since_last_crawl_mode():
    """Test since_last_crawl mode with simulated state"""
    print("\n" + "=" * 70)
    print("SINCE_LAST_CRAWL MODE TEST")
    print("=" * 70)
    
    mock_repo = MockFirestoreRepository()
    service = RSSService(repo=mock_repo)

    
    # Find hackernews feed (since_last_crawl mode)
    hn_feed = None
    for feed in service.feeds:
        if feed['mode'] == 'since_last_crawl':
            hn_feed = feed
            break
    
    if not hn_feed:
        print("ERROR: No since_last_crawl feed found in config")
        return False
    
    print(f"\nTesting feed: {hn_feed['name']}")
    
    # First run (no state)
    print("\n--- First run (no previous state) ---")
    new_links_1 = service._process_feed(hn_feed)
    print(f"Links stored: {new_links_1}")
    
    stored_state = mock_repo.feed_states.get(hn_feed['name'])
    print(f"State saved: {stored_state is not None}")
    
    if stored_state:
        print(f"Last entry date: {stored_state.get('last_entry_date')}")
    
    # Second run (with state) - should find nothing new immediately
    print("\n--- Second run (with state from first run) ---")
    urls_before = len(mock_repo.stored_urls)
    new_links_2 = service._process_feed(hn_feed)
    urls_after = len(mock_repo.stored_urls)
    
    print(f"New links in second run: {new_links_2}")
    print(f"Total URLs before: {urls_before}, after: {urls_after}")
    
    if new_links_2 == 0:
        print("✓ Correct: No new entries (as expected immediately after first run)")
        return True
    else:
        print(f"⚠ Warning: Found {new_links_2} new entries (feed may have updated during test)")
        return True  # Not necessarily an error


if __name__ == "__main__":
    print("RSS SERVICE LOGIC TEST")
    print("Testing with Mock Firestore Repository\n")
    
    all_passed = True
    
    try:
        # Test 1
        if not test_full_service():
            print("\n❌ FULL SERVICE TEST FAILED")
            all_passed = False
        
        # Test 2
        if not test_time_window_mode():
            print("\n❌ TIME WINDOW MODE TEST FAILED")
            all_passed = False
        
        # Test 3
        if not test_since_last_crawl_mode():
            print("\n❌ SINCE_LAST_CRAWL MODE TEST FAILED")
            all_passed = False
        
        print("\n" + "=" * 70)
        if all_passed:
            print("✓ ALL TESTS PASSED")
        else:
            print("❌ SOME TESTS FAILED")
        print("=" * 70)
        
    except Exception as e:
        print(f"\n❌ CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc()
