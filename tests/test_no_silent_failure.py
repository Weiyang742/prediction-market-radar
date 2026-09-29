"""Delivery-contract 铁律 2: external-call failures must be visible, never
silently absorbed into an empty success."""
import main
from data.config import Config


def _fail(*args, **kwargs):
    raise RuntimeError("simulated network failure")


def test_run_reports_error_when_both_platforms_fail(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(main.PolymarketClient, "fetch_markets", _fail)
    monkeypatch.setattr(main.KalshiClient, "fetch_markets", _fail)
    cfg = Config(mock=False, db_path=str(tmp_path / "t.db"))
    rc = main.cmd_run(None, cfg)
    assert rc == 2
    err = capsys.readouterr().err
    assert "ERROR" in err
    assert "0 markets" in err


def test_scan_reports_error_when_no_markets(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(main.PolymarketClient, "fetch_markets", _fail)
    monkeypatch.setattr(main.KalshiClient, "fetch_markets", _fail)
    cfg = Config(mock=False)
    rc = main.cmd_scan(None, cfg)
    assert rc == 2
    assert "ERROR" in capsys.readouterr().err
