from src import route_runtime_verifier as verifier


class FakeKeyboard:
    def __init__(self):
        self.pressed = []

    def press(self, key):
        self.pressed.append(key)


class FakeContext:
    def __init__(self):
        self.pages = []


class FakePage:
    def __init__(self, url):
        self.url = url
        self.keyboard = FakeKeyboard()
        self.context = FakeContext()
        self.frames = []
        self.brought_to_front = 0
        self.reloads = 0
        self.waits = []
        self.timeouts = []

    def is_closed(self):
        return False

    def bring_to_front(self):
        self.brought_to_front += 1

    def wait_for_load_state(self, state, timeout=None):
        self.waits.append((state, timeout))

    def wait_for_timeout(self, timeout):
        self.timeouts.append(timeout)

    def reload(self, wait_until=None, timeout=None):
        self.reloads += 1
        self.waits.append((f"reload:{wait_until}", timeout))


def test_refresh_manual_route_page_takes_over_popup_before_reload():
    original_page = FakePage("http://example.test/menu")
    popup_page = FakePage("http://example.test/PopupTarget.do")
    context = FakeContext()
    context.pages = [original_page, popup_page]
    original_page.context = context
    popup_page.context = context

    result = verifier._refresh_manual_route_page(
        original_page,
        [original_page],
        timeout=5000,
        route={"target_page": "PopupTarget.jsp"},
    )

    assert result["popup_taken_over"] is True
    assert result["url"] == popup_page.url
    assert popup_page.brought_to_front >= 1
    assert popup_page.reloads == 1
    assert original_page.reloads == 0


def test_refresh_manual_route_page_waits_for_manual_page_selection(monkeypatch):
    original_page = FakePage("http://example.test/menu")
    popup_page = FakePage("http://example.test/unmatched-popup")
    context = FakeContext()
    context.pages = [original_page, popup_page]
    original_page.context = context
    popup_page.context = context
    answers = iter(["1"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    result = verifier._refresh_manual_route_page(
        original_page,
        [original_page],
        timeout=5000,
        route={"target_page": "TargetNotInUrl.jsp"},
    )

    assert result["popup_taken_over"] is True
    assert result["url"] == popup_page.url
    assert popup_page.reloads == 1
    assert original_page.reloads == 0


def test_manual_replay_from_events_turns_checkbox_change_into_check():
    replay = verifier._manual_replay_from_events(
        [
            {
                "event_type": "change",
                "selector": 'input[name="itemId"][type="checkbox"]',
                "tag": "input",
                "type": "checkbox",
                "value": "0020",
                "checked": True,
                "text": "0020",
            }
        ]
    )

    assert replay == [
        {
            "action_type": "check",
            "selector": 'input[name="itemId"][type="checkbox"]',
            "value": "0020",
            "event_type": "change",
            "file_names": [],
            "checked": True,
            "tag": "input",
            "type": "checkbox",
            "text": "0020",
            "onclick": "",
        }
    ]
