"""Delivery-contract 铁律 3 / 失败透明: the GT/calibration path must fail loudly
when there are zero resolved markets, rather than print a plausible empty result."""
import main
from data.config import Config


def test_calibrate_exits_nonzero_on_empty_gt(tmp_path, capsys):
    cfg = Config(mock=False, db_path=str(tmp_path / "empty.db"))
    rc = main.cmd_calibrate(None, cfg)
    assert rc == 2
    assert "0 resolved markets" in capsys.readouterr().err
