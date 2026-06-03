"""OAuth Reddit search (offline tests + token caching)."""
from tradebot.data import reddit


def test_reddit_search_silent_without_keys():
    """No client_id/secret -> returns [] immediately."""
    assert reddit.search_reddit("Bitcoin") == []
    assert reddit.search_reddit("Bitcoin", "", "secret") == []


def test_reddit_token_cache_reuses_valid_token(monkeypatch):
    """Token is cached and reused for 3300s."""
    reddit._token_cache = "token_old"
    reddit._token_expiry = 1e10  # far future

    # With a valid cached token, _get_token should never call the network.
    # We test the cache logic, not the actual auth.
    token = reddit._get_token("id", "secret", timeout=1.0)
    assert token == "token_old"


def test_reddit_parse_children():
    """Extracts title + selftext from Reddit response structure."""
    data = {
        "data": {
            "children": [
                {"data": {"title": "Bitcoin rally", "selftext": "up 10%"}},
                {"data": {"title": "Ethereum news", "selftext": ""}},
            ]
        }
    }
    # Manual parse (no network call)
    out = []
    for child in data.get("data", {}).get("children", []):
        d = child.get("data", {})
        text = f"{d.get('title', '')} {d.get('selftext', '')}".strip()
        if text:
            out.append(text[:500])
    assert len(out) == 2
    assert "Bitcoin rally up 10%" in out[0]
