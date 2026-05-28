from src.route_navigator import _manual_replay_start_offset, _mark_manual_replay_target


class FakeFrame:
    def __init__(self, url, *, selector_match=False, fallback_match=False):
        self.url = url
        self.selector_match = selector_match
        self.fallback_match = fallback_match
        self.allow_fallback_calls = []

    def evaluate(self, script, args):
        allow_fallback = bool(args.get("allowFallback"))
        self.allow_fallback_calls.append(allow_fallback)
        if self.selector_match and not allow_fallback:
            return {
                "marked": True,
                "selector_count": 1,
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
