from src.route_navigator import RouteNavigator, _manual_replay_start_offset, _mark_manual_replay_target


class FakeFrame:
    def __init__(
        self,
        url,
        *,
        selector_match=False,
        fallback_match=False,
        score=1,
        text="",
        onclick="",
    ):
        self.url = url
        self.selector_match = selector_match
        self.fallback_match = fallback_match
        self.score = score
        self.text = text
        self.onclick = onclick
        self.allow_fallback_calls = []

    def evaluate(self, script, args):
        allow_fallback = bool(args.get("allowFallback"))
        self.allow_fallback_calls.append(allow_fallback)
        if self.selector_match and not allow_fallback:
            return {
                "marked": True,
                "selector_count": 1,
                "score": self.score,
                "onclick": self.onclick,
                "text": "書誌一覧表示",
                "tag": "input",
                "type": "button",
            }
        if self.fallback_match and allow_fallback:
            return {
                "marked": True,
                "selector_count": 0,
                "text": "書誌一覧表示の設定",
                "tag": "td",
                "type": "",
            }
        return {"marked": False, "selector_count": 0}


class FakePage:
    def __init__(self, frames):
        self.frames = frames


def test_manual_replay_prefers_original_selector_in_later_frame_over_text_fallback():
    menu_frame = FakeFrame("http://example.test/menu", fallback_match=True)
    history_frame = FakeFrame("http://example.test/history", selector_match=True)
    page = FakePage([menu_frame, history_frame])

    selector, state = _mark_manual_replay_target(
        page,
        {
            "action_type": "click",
            "selector": "input[onclick*=\"JpBiblioListForEasySearch\"]",
            "text": "書誌一覧表示",
            "tag": "input",
            "type": "button",
        },
        replay_index=6,
        test_id="manual_route",
    )

    assert selector.startswith('[data-moonlight-manual-replay-id="')
    assert state["frame_url"] == "http://example.test/history"
    assert state["allow_fallback"] is False
    assert menu_frame.allow_fallback_calls == [False]


def test_manual_replay_prefers_recorded_onclick_across_generic_button_frames():
    input_frame = FakeFrame(
        "http://example.test/WwEasySearchMain.do",
        selector_match=True,
        score=11,
        onclick="clearForm('WwEasySearchForm')",
    )
    history_frame = FakeFrame(
        "http://example.test/WwEasySearch.do",
        selector_match=True,
        score=371,
        onclick="submitForm('WwHistoryForm','./WwExpPrint.do','winPrintView')",
    )
    page = FakePage([input_frame, history_frame])

    selector, state = _mark_manual_replay_target(
        page,
        {
            "action_type": "click",
            "selector": 'input[name="button"][type="button"]',
            "text": "print",
            "onclick": "submitForm('WwHistoryForm','./WwExpPrint.do','winPrintView')",
            "tag": "input",
            "type": "button",
        },
        replay_index=4,
        test_id="manual_wwprintview",
    )

    assert selector.startswith('[data-moonlight-manual-replay-id="')
    assert state["frame_url"] == "http://example.test/WwEasySearch.do"
    assert state["onclick"] == "submitForm('WwHistoryForm','./WwExpPrint.do','winPrintView')"


class CountLocator:
    def __init__(self, count):
        self._count = count

    def count(self):
        return self._count


class CountFrame:
    def __init__(self, available_selectors):
        self.available_selectors = set(available_selectors)

    def locator(self, selector):
        return CountLocator(1 if selector in self.available_selectors else 0)


def test_manual_replay_start_offset_can_resume_from_current_container_page():
    page = FakePage([CountFrame({"input[name='docNo'][value='12021470614']"})])
    replay = [
        {"selector": "p > a:nth-of-type(1) > span > img"},
        {"selector": "img#tgId1"},
        {"selector": "input[name='docNo'][value='12021470614']"},
        {"selector": "table#blocTb"},
    ]

    assert _manual_replay_start_offset(page, replay) == 2


def test_manual_route_navigation_takes_over_reused_named_target_popup(tmp_path, monkeypatch):
    class Context:
        def __init__(self):
            self.pages = []

    class Page:
        def __init__(self, url, context):
            self.url = url
            self.context = context
            self.frames = []
            self.front = 0

        def is_closed(self):
            return False

        def bring_to_front(self):
            self.front += 1

        def wait_for_load_state(self, *_args, **_kwargs):
            return None

    class Catalog:
        def __init__(self, route):
            self.route = route

        def find_for_target(self, _target_page, *, side=None):
            return self.route

    context = Context()
    parent = Page("http://example.test/patlics/PatlicsTopMain.do", context)
    popup = Page("http://example.test/patlics/WwSearchAidDispForEasySearch.do", context)
    context.pages = [parent, popup]
    route = {
        "route_id": "manual_wwsearchaid",
        "target_page": "WwSearchAid.jsp",
        "manual_route": True,
        "manual_replay": [{"action_type": "click", "selector": "#open-search-aid"}],
        "state": {"url": popup.url},
    }
    monkeypatch.setattr("src.route_navigator._manual_replay_start_offset", lambda *_args: 0)
    monkeypatch.setattr(
        "src.route_navigator._execute_manual_replay",
        lambda page, *_args, **_kwargs: (page, {"status": "PASS", "steps": []}),
    )
    monkeypatch.setattr(
        "src.route_navigator._capture_state",
        lambda page, *_args, **_kwargs: {"url": page.url},
    )
    monkeypatch.setattr("src.route_navigator._visible_controls", lambda *_args, **_kwargs: [])

    selected, result = RouteNavigator(Catalog(route)).navigate(
        parent,
        entry_url="http://example.test/patlics/",
        target_page="WwSearchAid.jsp",
        capture_dir=tmp_path,
        side="legacy",
    )

    assert selected is popup
    assert result["status"] == "PASS"
    assert result["target_takeover"]["status"] == "PASS"
    assert result["target_takeover"]["reused_named_popup"] is True
    assert result["url"] == popup.url
