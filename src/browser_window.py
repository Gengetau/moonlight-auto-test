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


def restore_popup_window_state(page) -> Dict[str, Any]:
    """Record popup window metrics without changing the application supplied size."""
    result: Dict[str, Any] = {
        "applied": False,
        "reason": "not_popup",
        "before": capture_window_metrics(page),
    }
    try:
        opener = page.opener()
    except Exception as exc:
        result["opener_error"] = str(exc)
        return result
    if opener is None:
        return result

    result["reason"] = "popup_window_preserved"
    session = None
    try:
        session = page.context.new_cdp_session(page)
        window_info = session.send("Browser.getWindowForTarget")
        window_id = window_info.get("windowId")
        if window_id is None:
            result["reason"] = "window_id_unavailable"
        else:
            bounds_info = session.send("Browser.getWindowBounds", {"windowId": window_id})
            bounds = bounds_info.get("bounds") or {}
            result["browser_bounds_before"] = bounds
            result["browser_window_state"] = str(bounds.get("windowState") or "normal").lower()
    except Exception as exc:
        result["cdp_error"] = str(exc)
        result["reason"] = "popup_window_preserved_cdp_failed"
    finally:
        if session is not None:
            try:
                session.detach()
            except Exception:
                pass

    result["after"] = capture_window_metrics(page)
    return result


def set_main_window_bounds(
    page,
    *,
    maximize: bool = True,
    width: int = 1920,
    height: int = 1080,
    left: int = 0,
    top: int = 0,
) -> Dict[str, Any]:
    """Resize only a top-level regression browser window."""
    result: Dict[str, Any] = {
        "applied": False,
        "target_window_state": "maximized" if maximize else "normal",
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
        opener = page.opener()
    except Exception as exc:
        result["opener_error"] = str(exc)
        opener = None
    if opener is not None:
        result["method"] = "skip_popup_window_resize"
        result["reason"] = "popup_window_preserved"
        result["after"] = capture_window_metrics(page)
        return result

    try:
        session = page.context.new_cdp_session(page)
        window_info = session.send("Browser.getWindowForTarget")
        window_id = window_info.get("windowId")
        if window_id is not None:
            if maximize:
                session.send(
                    "Browser.setWindowBounds",
                    {"windowId": window_id, "bounds": {"windowState": "maximized"}},
                )
                result["method"] = "cdp_browser_window_maximized"
            else:
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
                result["method"] = "cdp_browser_window_bounds"
            result["applied"] = True
        try:
            session.detach()
        except Exception:
            pass
    except Exception as exc:
        result["cdp_error"] = str(exc)
        result["reason"] = "cdp_window_resize_failed"

    if not result["applied"]:
        try:
            page.evaluate(
                """({maximize, left, top, width, height}) => {
                    if (maximize) {
                        window.moveTo(screen.availLeft || 0, screen.availTop || 0);
                        window.resizeTo(screen.availWidth, screen.availHeight);
                        return;
                    }
                    window.moveTo(left, top);
                    window.resizeTo(width, height);
                }""",
                {"maximize": maximize, "left": left, "top": top, "width": width, "height": height},
            )
            result["applied"] = True
            result["method"] = "window_resizeTo_available_screen" if maximize else "window_resizeTo"
        except Exception as exc:
            result["resize_to_error"] = str(exc)

    try:
        page.wait_for_timeout(200)
    except Exception:
        pass
    result["after"] = capture_window_metrics(page)
    return result
