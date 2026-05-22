from src.config_parser import Config


def test_login_entries_uses_parallel_url_lists(monkeypatch):
    monkeypatch.setenv("LOGIN_ENTRY_NAMES", "alpha,beta")
    monkeypatch.setenv("LEGACY_URLS", "http://legacy-alpha/app/,http://legacy-beta/app/")
    monkeypatch.setenv("NEW_URLS", "http://new-alpha/app/,http://new-beta/app/")
    monkeypatch.delenv("LOGIN_ENTRY", raising=False)

    entries = Config.login_entries()

    assert entries == [
        {
            "name": "alpha",
            "legacy_url": "http://legacy-alpha/app/",
            "new_url": "http://new-alpha/app/",
        },
        {
            "name": "beta",
            "legacy_url": "http://legacy-beta/app/",
            "new_url": "http://new-beta/app/",
        },
    ]


def test_select_login_entry_accepts_name(monkeypatch):
    monkeypatch.setenv("LOGIN_ENTRY_NAMES", "alpha,beta")
    monkeypatch.setenv("LEGACY_URLS", "http://legacy-alpha/app/,http://legacy-beta/app/")
    monkeypatch.setenv("NEW_URLS", "http://new-alpha/app/,http://new-beta/app/")
    monkeypatch.delenv("LOGIN_ENTRY", raising=False)
    original_legacy = Config.LEGACY_URL
    original_new = Config.NEW_URL

    try:
        selected = Config.select_login_entry("beta", interactive=False)
    finally:
        Config.LEGACY_URL = original_legacy
        Config.NEW_URL = original_new

    assert selected["name"] == "beta"
    assert selected["legacy_url"] == "http://legacy-beta/app/"
    assert selected["new_url"] == "http://new-beta/app/"
