from src.browser_window import capture_window_metrics, restore_popup_window_state, set_main_window_bounds


class FakeSession:
    def __init__(self, window_state="normal"):
        self.commands = []
        self.detached = False
        self.window_state = window_state

    def send(self, command, params=None):
        self.commands.append((command, params))
        if command == "Browser.getWindowForTarget":
            return {"windowId": 7}
        if command == "Browser.getWindowBounds":
            return {"bounds": {"windowState": self.window_state, "width": 1000, "height": 720}}
        return {}

    def detach(self):
        self.detached = True


class FakeContext:
    def __init__(self, session):
        self.session = session

    def new_cdp_session(self, page):
        return self.session


class FakePage:
    def __init__(self, *, opener=None, window_state="normal"):
        self.session = FakeSession(window_state=window_state)
        self.context = FakeContext(self.session)
        self.front = False
        self.waited = []
        self._opener = opener

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

    def opener(self):
        return self._opener


def test_set_main_window_bounds_maximizes_top_level_window_by_default():
    page = FakePage()

    result = set_main_window_bounds(page)

    assert result["applied"] is True
    assert result["method"] == "cdp_browser_window_maximized"
    assert result["target_window_state"] == "maximized"
    assert page.front is True
    assert page.session.commands == [
        ("Browser.getWindowForTarget", None),
        ("Browser.setWindowBounds", {"windowId": 7, "bounds": {"windowState": "maximized"}}),
    ]


def test_set_main_window_bounds_does_not_resize_popup_window():
    page = FakePage(opener=FakePage(), window_state="maximized")

    result = set_main_window_bounds(page)

    assert result["applied"] is False
    assert result["method"] == "skip_popup_window_resize"
    assert result["reason"] == "popup_window_preserved"
    assert page.front is True
    assert page.session.commands == []


def test_set_main_window_bounds_keeps_explicit_fixed_size_mode():
    page = FakePage()

    result = set_main_window_bounds(page, maximize=False, width=1600, height=900, left=10, top=20)

    assert result["applied"] is True
    assert result["method"] == "cdp_browser_window_bounds"
    assert result["target_window_state"] == "normal"
    assert page.session.commands == [
        ("Browser.getWindowForTarget", None),
        ("Browser.setWindowBounds", {"windowId": 7, "bounds": {"windowState": "normal"}}),
        (
            "Browser.setWindowBounds",
            {
                "windowId": 7,
                "bounds": {
                    "left": 10,
                    "top": 20,
                    "width": 1600,
                    "height": 900,
                },
            },
        ),
    ]


def test_capture_window_metrics_reads_browser_values():
    assert capture_window_metrics(FakePage())["outerWidth"] == 1920


def test_restore_popup_window_state_records_popup_without_resizing():
    page = FakePage(opener=FakePage(), window_state="maximized")

    result = restore_popup_window_state(page)

    assert result["applied"] is False
    assert result["reason"] == "popup_window_preserved"
    assert result["browser_window_state"] == "maximized"
    assert page.session.commands == [
        ("Browser.getWindowForTarget", None),
        ("Browser.getWindowBounds", {"windowId": 7}),
    ]


def test_restore_popup_window_state_leaves_main_window_unchanged():
    page = FakePage(window_state="maximized")

    result = restore_popup_window_state(page)

    assert result["applied"] is False
    assert result["reason"] == "not_popup"
    assert page.session.commands == []
