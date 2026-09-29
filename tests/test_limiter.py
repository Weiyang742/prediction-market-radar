from data.config import ASSUMPTIONS, ESTIMATES, Config
from data.limiter import RateLimiter, backoff_delay, run_with_backoff


def test_assumption_keys_are_real_config_fields():
    names = {f.name for f in Config.__dataclass_fields__.values()}
    for key in list(ASSUMPTIONS) + list(ESTIMATES):
        assert key in names, f"{key} not a Config field"


def test_apply_overrides_and_reject_unknown():
    cfg = Config()
    cfg.apply_overrides({"min_volume_24h": 123.0, "top_n": 7})
    assert cfg.min_volume_24h == 123.0
    assert cfg.top_n == 7
    import pytest
    with pytest.raises(KeyError):
        cfg.apply_overrides({"not_a_field": 1})


def test_backoff_sequence_is_exponential_and_capped():
    import random
    rng = random.Random(0)
    seq = [backoff_delay(a, base=0.25, cap=2.0, jitter=0.0, rng=rng) for a in range(6)]
    assert seq[0] == 0.25
    assert seq[1] == 0.5
    assert seq[2] == 1.0
    assert seq[3] == 2.0
    assert seq[4] == 2.0  # capped
    assert seq[5] == 2.0


def test_rate_limiter_blocks_then_allows():
    clock = {"t": 0.0}
    sleeps: list[float] = []

    def fake_clock():
        return clock["t"]

    def fake_sleep(s):
        sleeps.append(s)
        clock["t"] += s

    lim = RateLimiter(rate_per_sec=2.0, clock=fake_clock, sleep=fake_sleep)
    lim.wait()  # consume token 1
    lim.wait()  # consume token 2
    lim.wait()  # out of tokens -> must sleep ~0.5s
    assert sleeps, "third call should have slept"
    assert abs(sleeps[0] - 0.5) < 1e-9


def test_run_with_backoff_re_raises_after_attempts():
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise RuntimeError("always")

    import pytest
    with pytest.raises(RuntimeError):
        run_with_backoff(boom, attempts=3, base=0.0, cap=0.0, jitter=0.0,
                         sleep=lambda s: None)
    assert calls["n"] == 3
