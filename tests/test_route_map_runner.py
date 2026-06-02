from src import route_map_runner
from src.config_parser import Config
from src.route_map_runner import CHROMIUM_ARGS, _close_browser_safely, _launch_browser


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


def test_close_browser_safely_detaches_dialog_handlers_before_context_close():
    calls = []

    class Context:
        def remove_listener(self, event_name, handler):
            calls.append(("remove_listener", event_name, handler))

        def close(self):
            calls.append(("context_close",))

    class Browser:
        contexts = [Context()]

        def close(self):
            calls.append(("browser_close",))

    _close_browser_safely(Browser())

    assert calls == [
        ("remove_listener", "dialog", route_map_runner._safe_accept),
        ("context_close",),
        ("browser_close",),
    ]
