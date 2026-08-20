def _fresh_settings(monkeypatch, **env):
    # 不 reload 模組——reload 會重跑 load_dotenv，本機若有 .env 會污染測試。
    # Settings() 建構時直接讀 os.getenv，monkeypatch 環境變數就夠。
    for key in ("PORT", "HEADLESS", "API_KEYS", "AUDIO_RETENTION_DAYS"):
        monkeypatch.delenv(key, raising=False)
    for key, val in env.items():
        monkeypatch.setenv(key, val)
    from src.config import Settings
    return Settings()


def test_defaults(monkeypatch):
    s = _fresh_settings(monkeypatch)
    assert s.port == 8071
    assert s.headless is False
    assert s.suno_url == "https://suno.com/create"
    assert s.default_timeout == 600
    assert s.queue_max_size == 10
    assert s.audio_retention_days == 14
    assert s.api_keys == set()


def test_env_overrides(monkeypatch):
    s = _fresh_settings(monkeypatch, PORT="9999", HEADLESS="true", API_KEYS="k1, k2,")
    assert s.port == 9999
    assert s.headless is True
    assert s.api_keys == {"k1", "k2"}
