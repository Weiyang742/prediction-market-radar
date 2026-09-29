"""Polymarket clobTokenId resolution — synthetic parsing tests (UNVERIFIED
against a real single-market response: the host is SNI-blocked here)."""
from data.polymarket_client import PolymarketClient


def _client(monkeypatch, body):
    client = PolymarketClient.__new__(PolymarketClient)
    client.gamma_base = "https://gamma.example"
    monkeypatch.setattr(client, "get_json", lambda url: body)
    return client


def test_resolve_parses_clob_token_ids(monkeypatch):
    client = _client(monkeypatch, {"clobTokenIds": '["0xabc", "0xdef"]'})
    assert client.resolve_clob_token_id("123") == "0xabc"


def test_resolve_none_when_field_absent(monkeypatch):
    assert _client(monkeypatch, {}).resolve_clob_token_id("123") is None


def test_resolve_none_when_empty_array(monkeypatch):
    assert _client(monkeypatch, {"clobTokenIds": "[]"}).resolve_clob_token_id("123") is None


def test_resolve_none_on_invalid_json(monkeypatch):
    assert _client(monkeypatch, {"clobTokenIds": "not-json"}).resolve_clob_token_id("123") is None
