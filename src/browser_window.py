from typing import Any, Dict


def capture_window_metrics(page) -> Dict[str, Any]:
    try:
        if page.is_closed():
            return {"page_closed": True}
    except Exception:
        return {"page_closed": True}

    try:
        return page.evaluate(
            """() => ({
                innerWidth: window.innerWidth,
                innerHeight: window.innerHeight,
                outerWidth: window.outerWidth,
                outerHeight: window.outerHeight,
                screenX: window.screenX,
                screenY: window.screenY,
                devicePixelRatio: window.devicePixelRatio
            })"""
        )
    except Exception as exc:
        return {"error": str(exc)}


def set_main_window_bounds(page, *, width: int = 1920, height: int = 1080, left: int = 0, top: int = 0) -> Dict[str, Any]:
    """Resize only a top-level regression window without affecting future popups."""
    result: Dict[str, Any] = {
        "applied": False,
        "target_bounds": {
            "left": left,
            "top": top,
            "width": width,
            "height": height,
        },
        "before": capture_window_metrics(page),
    }

    try:
        page.bring_to_front()
    except Exception as exc:
        result["bring_to_front_error"] = str(exc)

    try:
        session = page.context.new_cdp_session(page)
        window_info = session.send("Browser.getWindowForTarget")
        window_id = window_info.get("windowId")
        if window_id is not None:
            session.send("Browser.setWindowBounds", {"windowId": window_id, "bounds": {"windowState": "normal"}})
            session.send(
                "Browser.setWindowBounds",
                {
                    "windowId": window_id,
                    "bounds": {
                        "left": left,
                        "top": top,
                        "width": width,
                        "height": height,
                    },
                },
            )
            result["applied"] = True
            result["method"] = "cdp_browser_window_bounds"
        try:
            session.detach()
        except Exception:
            pass
    except Exception as exc:
        result["cdp_error"] = str(exc)

    if not result["applied"]:
        try:
            page.evaluate(
                """({left, top, width, height}) => {
                    window.moveTo(left, top);
                    window.resizeTo(width, height);
                }""",
                {"left": left, "top": top, "width": width, "height": height},
            )
            result["applied"] = True
            result["method"] = "window_resizeTo"
        except Exception as exc:
            result["resize_to_error"] = str(exc)

    try:
        page.wait_for_timeout(200)
    except Exception:
        pass
    result["after"] = capture_window_metrics(page)
    return result
