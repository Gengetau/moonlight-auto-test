from src.browser_window import capture_window_metrics, set_main_window_bounds


class FakeSession:
    def __init__(self):
        self.commands = []
        self.detached = False

    def send(self, command, params=None):
        self.commands.append((command, params))
        if command == "Browser.getWindowForTarget":
            return {"windowId": 7}
        return {}

    def detach(self):
        self.detached = True


class FakeContext:
    def __init__(self, session):
        self.session = session

    def new_cdp_session(self, page):
        return self.session


class FakePage:
    def __init__(self):
        self.session = FakeSession()
        self.context = FakeContext(self.session)
        self.front = False
        self.waited = []

    def is_closed(self):
        return False

    def evaluate(self, script, params=None):
        return {
            "innerWidth": 1900,
            "innerHeight": 990,
            "outerWidth": 1920,
            "outerHeight": 1080,
            "screenX": 0,
            "screenY": 0,
            "devicePixelRatio": 1,
        }

    def bring_to_front(self):
        self.front = True

    def wait_for_timeout(self, timeout):
        self.waited.append(timeout)


def test_set_main_window_bounds_only_updates_explicit_page():
    page = FakePage()

    result = set_main_window_bounds(page)

    assert result["applied"] is True
    assert result["method"] == "cdp_browser_window_bounds"
    assert page.front is True
    assert page.session.commands == [
        ("Browser.getWindowForTarget", None),
        ("Browser.setWindowBounds", {"windowId": 7, "bounds": {"windowState": "normal"}}),
        (
            "Browser.setWindowBounds",
            {
                "windowId": 7,
                "bounds": {
                    "left": 0,
                    "top": 0,
                    "width": 1920,
                    "height": 1080,
                },
            },
        ),
    ]


def test_capture_window_metrics_reads_browser_values():
    assert capture_window_metrics(FakePage())["outerWidth"] == 1920
