"""Theme bucketing and cross-platform question similarity."""
from analysis.themes import classify_theme, similarity


def test_classify_theme_buckets():
    assert classify_theme("Will the Fed cut rates in September?") == "fed_rates"
    assert classify_theme("Who wins the 2026 presidential election?") == "us_elections"
    assert classify_theme("Bitcoin above $100k on Sep 30?") == "btc_price"
    assert classify_theme("Will it rain in NYC tomorrow?") == "weather"
    assert classify_theme("Something completely unrelated") == "other"


def test_similarity_identical_is_one():
    assert similarity("Fed cuts rates", "Fed cuts rates") == 1.0


def test_similarity_bounded_and_symmetric():
    a, b = "Bitcoin above 100k", "BTC price over 100k"
    assert 0.0 <= similarity(a, b) <= 1.0
    assert similarity(a, b) == similarity(b, a)
