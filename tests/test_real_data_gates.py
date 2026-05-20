from real_crypto_carry_ibkr.data import source_is_real


def test_blocks_synthetic_sources():
    ok, reason = source_is_real("synthetic", ["databento"], ["synthetic", "yfinance"])
    assert not ok
    assert "blocked" in reason


def test_accepts_configured_real_source():
    ok, reason = source_is_real("databento", ["databento"], ["synthetic", "yfinance"])
    assert ok
    assert "accepted" in reason


def test_rejects_unknown_source():
    ok, reason = source_is_real("unknown_vendor", ["databento"], ["synthetic"])
    assert not ok
    assert "not in accepted" in reason
