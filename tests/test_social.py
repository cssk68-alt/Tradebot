"""Offline tests for the free social/forum sources (data/social.py).

Only the pure parsers and the de-dup/cap logic are tested — the network backends
are best-effort and wrapped, so they need no live calls."""
from tradebot.data.social import _parse_bluesky, _parse_hn, _parse_lemmy


def test_parse_bluesky():
    data = {"posts": [
        {"record": {"text": "Trump leads the polls"}},
        {"record": {"text": ""}},          # empty -> dropped
        {"author": {"handle": "x"}},        # no record -> dropped
        {"record": {"text": "BTC to the moon"}},
    ]}
    assert _parse_bluesky(data) == ["Trump leads the polls", "BTC to the moon"]


def test_parse_hn():
    data = {"hits": [
        {"title": "Show HN: my bot", "story_text": "details here"},
        {"title": "", "story_text": ""},    # both empty -> dropped
        {"title": "Nvidia earnings"},        # title only -> kept
    ]}
    assert _parse_hn(data) == ["Show HN: my bot details here", "Nvidia earnings"]


def test_parse_lemmy():
    data = {"posts": [
        {"post": {"name": "Ethereum ETF", "body": "approved soon?"}},
        {"post": {"name": "", "body": ""}},  # empty -> dropped
        {"other": 1},                          # no post key -> dropped
    ]}
    assert _parse_lemmy(data) == ["Ethereum ETF approved soon?"]


def test_parsers_tolerate_garbage():
    for p in (_parse_bluesky, _parse_hn, _parse_lemmy):
        assert p({}) == []
        assert p({"posts": []}) == []
        assert p(None) == []


def test_empty_query_returns_empty_without_network():
    from tradebot.data.social import search_social
    assert search_social("   ") == []
