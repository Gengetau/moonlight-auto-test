from typing import Any, Dict


SUPPRESS_NATIVE_PRINT_SCRIPT = """
(() => {
  if (window.__moonlightNativePrintSuppressed) return;
  window.__moonlightNativePrintSuppressed = true;
  window.__moonlightPrintEvents = window.__moonlightPrintEvents || [];
  window.__moonlightOriginalNativePrint = window.print ? window.print.bind(window) : null;
  window.print = () => {
    window.__moonlightPrintEvents.push({
      time: new Date().toISOString(),
      url: String(location.href || ""),
      title: String(document.title || ""),
      suppressedNativeDialog: true
    });
    try { window.dispatchEvent(new Event("beforeprint")); } catch (_) {}
    try { window.dispatchEvent(new Event("afterprint")); } catch (_) {}
  };
})();
"""


def install_print_suppression(context: Any) -> Dict[str, Any]:
    if context is None:
        return {"installed": False, "reason": "missing_context"}
    try:
        if getattr(context, "_moonlight_print_suppression_installed", False):
            return {"installed": True, "already_installed": True}
    except Exception:
        pass

    try:
        context.add_init_script(script=SUPPRESS_NATIVE_PRINT_SCRIPT)
    except Exception as exc:
        return {"installed": False, "reason": str(exc)}

    try:
        setattr(context, "_moonlight_print_suppression_installed", True)
    except Exception:
        pass
    return {"installed": True}
