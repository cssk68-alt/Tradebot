"""Source reliability scoring from resolved trades (Phase 2)."""
from tradebot.brain.source_quality import SourceCounter, SourceScores
from tradebot.models import Experience, Mode


def test_source_counter_defaults_neutral():
    sc = SourceCounter()
    scores = sc.score_from_experiences([])
    assert scores.rss == 0.5  # (0+1)/(0+2)
    assert scores.reddit == 0.5
    assert scores.web == 0.5
    assert scores.fact == 0.5


def test_source_counter_learns_hit_rate():
    sc = SourceCounter()
    # Trade with RSS sentiment, and it won.
    exp_win = Experience(
        features=[0.5] * 21,  # 21 dims post-upgrade
        won=True, is_yes=True, edge=0.1, size=10.0, brain_score=0.5, pnl=2.5, mode=Mode.PAPER,
    )
    exp_win.features[10] = 0.3  # rss_sentiment=0.3 (active)

    # Trade with RSS sentiment, but it lost.
    exp_loss = Experience(
        features=[0.5] * 21,
        won=False, is_yes=False, edge=0.05, size=5.0, brain_score=0.4, pnl=-1.0, mode=Mode.PAPER,
    )
    exp_loss.features[10] = 0.2  # rss_sentiment=0.2 (active)

    scores = sc.score_from_experiences([exp_win, exp_loss])
    # RSS: 1 hit, 2 trials -> (1+1)/(2+2) = 0.5
    assert scores.rss == 0.5
    # Others: 0 hits, 0 trials -> (0+1)/(0+2) = 0.5
    assert scores.reddit == 0.5


def test_source_counter_ignores_inactive_sources():
    sc = SourceCounter()
    # A trade where web_sentiment=0.0 (inactive).
    exp = Experience(
        features=[0.5] * 21,
        won=True, is_yes=True, edge=0.15, size=20.0, brain_score=0.6, pnl=5.0, mode=Mode.PAPER,
    )
    exp.features[17] = 0.0  # web_sentiment=0 -> not counted

    scores = sc.score_from_experiences([exp])
    # Web source was not active (sentiment=0), so web_total stays 0 -> 0.5
    assert scores.web == 0.5


def test_source_scores_as_dict():
    ss = SourceScores(rss=0.7, reddit=0.3, web=0.6, fact=0.8)
    d = ss.as_dict()
    assert d == {"rss": 0.7, "reddit": 0.3, "web": 0.6, "fact": 0.8}
