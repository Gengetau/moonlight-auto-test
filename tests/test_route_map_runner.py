from src.config_parser import Config
from src.route_map_runner import CHROMIUM_ARGS, _launch_browser


class _FakeChromium:
    def __init__(self):
        self.kwargs = None

    def launch(self, **kwargs):
        self.kwargs = kwargs
        return kwargs


class _FakePlaywright:
    def __init__(self):
        self.chromium = _FakeChromium()


def test_launch_browser_uses_system_chrome_fallback(monkeypatch):
    playwright = _FakePlaywright()
    monkeypatch.setattr(Config, "CHROME_PORTABLE_PATH", "")

    result = _launch_browser(playwright, "chrome_port")

    assert result["channel"] == "chrome"
    assert result["headless"] is False
    assert result["args"] == CHROMIUM_ARGS
