from playwright.sync_api import Frame, Page, Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError
import base64
import hashlib
import os
import json
import re
import shutil
import time
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

from PIL import Image, ImageDraw, ImageFont, ImageGrab

from src.assert_engine import compare_visual_screenshot
from src.browser_window import capture_window_metrics, restore_popup_window_state
from src.config_parser import Config
from src.page_aliases import page_aliases


def _safe_name(value: Any) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "unknown")).strip("_")
    return name[:120] or "unknown"


def _safe_download_filename(filename: str, browser_name: str) -> str:
    original = Path(str(filename or "download")).name
    name, ext = os.path.splitext(original)
    safe_base = _safe_name(name)
    safe_ext = re.sub(r"[^A-Za-z0-9.]+", "", ext)[:20]
    return f"{safe_base}_{_safe_name(browser_name)}{safe_ext}"


def _original_download_filename(filename: str) -> str:
    text = str(filename or "download").strip() or "download"
    # Browsers should provide a filename, but strip path components defensively
    # without changing the actual basename.
    windows_name = PureWindowsPath(text).name
    return Path(windows_name).name or "download"


def _configured_download_dir() -> Path:
    raw_dir = os.getenv("DOWNLOAD_DIR") or getattr(Config, "DOWNLOAD_DIR", "") or "~/Downloads"
    expanded = os.path.expandvars(os.path.expanduser(str(raw_dir)))
    return Path(expanded)


def _download_save_path(suggested_filename: str) -> Path:
    base_path = _configured_download_dir() / _original_download_filename(suggested_filename)
    if not base_path.exists():
        return base_path

    stem = base_path.stem or "download"
    suffix = base_path.suffix
    for index in range(1, 10000):
        candidate = base_path.with_name(f"{stem} ({index}){suffix}")
        if not candidate.exists():
            return candidate
    return base_path.with_name(f"{stem} ({int(time.time() * 1000)}){suffix}")


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem or "download"
    suffix = path.suffix
    for index in range(1, 10000):
        candidate = path.with_name(f"{stem} ({index}){suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{stem} ({int(time.time() * 1000)}){suffix}")


def _sha256_file(path: Path) -> Optional[str]:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except Exception:
        return None


def _archive_download_file(
    source_path: Path,
    *,
    capture_dir: Optional[Union[str, Path]] = None,
    test_id: Optional[str] = None,
    browser_name: str = "download",
) -> Tuple[Optional[Path], Optional[str]]:
    if not capture_dir:
        return None, None
    try:
        source_name = _original_download_filename(source_path.name)
        stem, suffix = os.path.splitext(source_name)
        safe_prefix = _safe_name(test_id or browser_name or "download")[:80]
        safe_stem = _safe_name(stem or "download")[:80]
        safe_suffix = re.sub(r"[^A-Za-z0-9.]+", "", suffix)[:20]
        archive_dir = Path(capture_dir) / "downloads"
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = _unique_path(archive_dir / f"{safe_prefix}__{safe_stem}{safe_suffix}")
        shutil.copy2(source_path, archive_path)
        return archive_path, None
    except Exception as exc:
        return None, str(exc)


def _record_download_file_metadata(result: Dict[str, Any], path: Path) -> None:
    try:
        result["download_size"] = path.stat().st_size
    except Exception:
        pass
    digest = _sha256_file(path)
    if digest:
        result["download_sha256"] = digest


def _download_timeout_ms(context: Optional[Dict[str, Any]], default_ms: int = 150000) -> int:
    raw_value = (
        (context or {}).get("download_timeout_ms")
        or (context or {}).get("timeout_ms")
        or os.getenv("DOWNLOAD_TIMEOUT_MS")
        or default_ms
    )
    try:
        return max(1000, int(raw_value))
    except (TypeError, ValueError):
        return default_ms


def _download_stability_ms(context: Optional[Dict[str, Any]], default_ms: int = 750) -> int:
    raw_value = (
        (context or {}).get("download_stability_ms")
        or os.getenv("DOWNLOAD_STABILITY_MS")
        or default_ms
    )
    try:
        return max(100, int(raw_value))
    except (TypeError, ValueError):
        return default_ms


def _download_uuid_fallback_ms(context: Optional[Dict[str, Any]], default_ms: int = 3000) -> int:
    raw_value = (
        (context or {}).get("download_uuid_fallback_ms")
        or os.getenv("DOWNLOAD_UUID_FALLBACK_MS")
        or default_ms
    )
    try:
        return max(0, int(raw_value))
    except (TypeError, ValueError):
        return default_ms


def _download_expect_timeout_ms(
    context: Optional[Dict[str, Any]],
    *,
    action_timeout_ms: int,
    download_timeout_ms: int,
    default_ms: int = 10000,
) -> int:
    raw_value = (
        (context or {}).get("download_expect_timeout_ms")
        or os.getenv("DOWNLOAD_EXPECT_TIMEOUT_MS")
        or default_ms
    )
    try:
        requested_ms = max(1000, int(raw_value))
    except (TypeError, ValueError):
        requested_ms = default_ms
    return min(download_timeout_ms, max(1000, min(action_timeout_ms, requested_ms)))


def _download_watch_dirs(context: Optional[Dict[str, Any]] = None) -> List[Path]:
    raw_values: List[Any] = [
        (context or {}).get("download_dir"),
        (context or {}).get("download_watch_dir"),
        os.getenv("DOWNLOAD_DIR"),
        getattr(Config, "DOWNLOAD_DIR", ""),
    ]
    extra_dirs = (context or {}).get("download_watch_dirs") or []
    if isinstance(extra_dirs, (str, Path)):
        raw_values.append(extra_dirs)
    else:
        raw_values.extend(extra_dirs)

    dirs: List[Path] = []
    seen = set()
    for raw_value in raw_values:
        if not raw_value:
            continue
        for part in str(raw_value).split(";"):
            text = part.strip()
            if not text:
                continue
            path = Path(os.path.expandvars(os.path.expanduser(text)))
            key = str(path.resolve() if path.exists() else path).lower()
            if key in seen:
                continue
            seen.add(key)
            dirs.append(path)
    return dirs


def _configure_browser_download_dir(page: Page, download_dir: Path) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "download_dir": str(download_dir),
        "attempts": [],
        "status": "SKIPPED",
    }
    try:
        download_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        state.update({"status": "ERROR", "reason": f"Failed to create download dir: {exc}"})
        return state

    target_pages: List[Any] = []
    seen = set()
    for candidate in [page, *_safe_context_pages(page)]:
        if candidate is None or _page_is_closed(candidate):
            continue
        key = id(candidate)
        if key in seen:
            continue
        seen.add(key)
        target_pages.append(candidate)

    for target_page in target_pages:
        session = None
        try:
            session = target_page.context.new_cdp_session(target_page)
        except Exception as exc:
            state["attempts"].append(
                {
                    "status": "ERROR",
                    "method": "new_cdp_session",
                    "page_url": _safe_page_url(target_page),
                    "reason": str(exc),
                }
            )
            continue

        configured = False
        for method, payload in (
            (
                "Browser.setDownloadBehavior",
                {
                    "behavior": "allowAndName",
                    "downloadPath": str(download_dir),
                    "eventsEnabled": True,
                },
            ),
            (
                "Browser.setDownloadBehavior",
                {
                    "behavior": "allow",
                    "downloadPath": str(download_dir),
                    "eventsEnabled": True,
                },
            ),
            (
                "Page.setDownloadBehavior",
                {
                    "behavior": "allow",
                    "downloadPath": str(download_dir),
                },
            ),
        ):
            try:
                session.send(method, payload)
                state["attempts"].append(
                    {
                        "status": "PASS",
                        "method": method,
                        "page_url": _safe_page_url(target_page),
                    }
                )
                configured = True
                break
            except Exception as exc:
                state["attempts"].append(
                    {
                        "status": "ERROR",
                        "method": method,
                        "page_url": _safe_page_url(target_page),
                        "reason": str(exc),
                    }
                )
        try:
            session.detach()
        except Exception:
            pass
        if configured:
            state["status"] = "PASS"

    if state["status"] == "SKIPPED" and state["attempts"]:
        state["status"] = "ERROR"
    return state


def _attach_cdp_download_observer(
    page: Page,
    download_dir: Path,
    *,
    on_will_begin: Any,
    on_progress: Any,
) -> Tuple[Dict[str, Any], List[Any]]:
    state: Dict[str, Any] = {
        "download_dir": str(download_dir),
        "attempts": [],
        "status": "SKIPPED",
    }
    sessions: List[Any] = []
    try:
        download_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        state.update({"status": "ERROR", "reason": f"Failed to create download dir: {exc}"})
        return state, sessions

    target_pages: List[Any] = []
    seen = set()
    for candidate in [page, *_safe_context_pages(page)]:
        if candidate is None or _page_is_closed(candidate):
            continue
        key = id(candidate)
        if key in seen:
            continue
        seen.add(key)
        target_pages.append(candidate)

    for target_page in target_pages:
        try:
            session = target_page.context.new_cdp_session(target_page)
        except Exception as exc:
            state["attempts"].append(
                {
                    "status": "ERROR",
                    "method": "new_cdp_session",
                    "page_url": _safe_page_url(target_page),
                    "reason": str(exc),
                }
            )
            continue

        configured = False
        for method, payload in (
            (
                "Browser.setDownloadBehavior",
                {
                    "behavior": "allowAndName",
                    "downloadPath": str(download_dir),
                    "eventsEnabled": True,
                },
            ),
            (
                "Browser.setDownloadBehavior",
                {
                    "behavior": "allow",
                    "downloadPath": str(download_dir),
                    "eventsEnabled": True,
                },
            ),
            (
                "Page.setDownloadBehavior",
                {
                    "behavior": "allow",
                    "downloadPath": str(download_dir),
                },
            ),
        ):
            try:
                session.send(method, payload)
                state["attempts"].append(
                    {
                        "status": "PASS",
                        "method": method,
                        "page_url": _safe_page_url(target_page),
                    }
                )
                configured = True
                break
            except Exception as exc:
                state["attempts"].append(
                    {
                        "status": "ERROR",
                        "method": method,
                        "page_url": _safe_page_url(target_page),
                        "reason": str(exc),
                    }
                )

        if configured:
            try:
                session.on("Browser.downloadWillBegin", on_will_begin)
                session.on("Browser.downloadProgress", on_progress)
                sessions.append(session)
                state["status"] = "PASS"
            except Exception as exc:
                state["attempts"].append(
                    {
                        "status": "ERROR",
                        "method": "attach_download_events",
                        "page_url": _safe_page_url(target_page),
                        "reason": str(exc),
                    }
                )
                try:
                    session.detach()
                except Exception:
                    pass
        else:
            try:
                session.detach()
            except Exception:
                pass

    if state["status"] == "SKIPPED" and state["attempts"]:
        state["status"] = "ERROR"
    return state, sessions


_TEMP_DOWNLOAD_SUFFIXES = (".crdownload", ".part", ".tmp", ".download")
_BROWSER_TEMP_DOWNLOAD_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_TEST_STEP_DOWNLOAD_RE = re.compile(r".+_(?:legacy|new)_step\d+$", re.IGNORECASE)
_DOWNLOAD_FILENAME_HINT_EXTENSIONS = {
    ".csv",
    ".tsv",
    ".xls",
    ".xlsx",
    ".pdf",
    ".txt",
    ".zip",
    ".xml",
    ".json",
    ".dat",
}


def _looks_like_browser_temp_download_name(filename: Any) -> bool:
    name = Path(str(filename or "").replace("\\", "/")).name
    return bool(name and not Path(name).suffix and _BROWSER_TEMP_DOWNLOAD_RE.match(name))


def _looks_like_generated_test_download_name(filename: Any, test_id: Optional[str] = None) -> bool:
    name = Path(str(filename or "").replace("\\", "/")).name
    if not name:
        return False
    if test_id and name == _original_download_filename(str(test_id)):
        return True
    return bool(not Path(name).suffix and _TEST_STEP_DOWNLOAD_RE.match(name))


def _is_temporary_download_path(path: Path) -> bool:
    name = path.name.lower()
    return (
        name.startswith("unconfirmed ")
        or any(name.endswith(suffix) for suffix in _TEMP_DOWNLOAD_SUFFIXES)
        or _looks_like_browser_temp_download_name(path.name)
    )


def _filesystem_download_target_filename(
    source_path: Path,
    *,
    suggested_filename: Any = None,
    test_id: Optional[str] = None,
    browser_name: str = "download",
) -> str:
    suggested = _original_download_filename(str(suggested_filename or "").strip())
    if suggested and suggested != "download":
        return suggested
    source_filename = _original_download_filename(source_path.name)
    if (
        source_filename
        and not _looks_like_browser_temp_download_name(source_filename)
        and not _looks_like_generated_test_download_name(source_filename, test_id)
    ):
        return source_filename
    return ""


def _content_disposition_filename(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    match = re.search(r"filename\*\s*=\s*(?:[A-Za-z0-9_-]+)?''([^;\r\n]+)", text, re.IGNORECASE)
    if match:
        return _original_download_filename(unquote(match.group(1).strip().strip('"')))

    match = re.search(r'filename\s*=\s*"([^"\r\n]+)"', text, re.IGNORECASE)
    if match:
        return _original_download_filename(match.group(1).strip())

    match = re.search(r"filename\s*=\s*([^;\r\n]+)", text, re.IGNORECASE)
    if match:
        return _original_download_filename(match.group(1).strip().strip('"'))
    return ""


def _url_download_filename(value: Any) -> str:
    try:
        path = urlparse(str(value or "")).path
    except Exception:
        path = ""
    filename = _original_download_filename(unquote(path.rsplit("/", 1)[-1] if path else ""))
    suffix = Path(filename).suffix.lower()
    return filename if filename and suffix in _DOWNLOAD_FILENAME_HINT_EXTENSIONS else ""


def _download_filename_hint_from_response(response: Any) -> str:
    headers: Dict[str, Any] = {}
    try:
        headers = response.headers or {}
    except Exception:
        headers = {}
    content_disposition = ""
    for key, value in headers.items():
        if str(key or "").lower() == "content-disposition":
            content_disposition = str(value or "")
            break
    filename = _content_disposition_filename(content_disposition)
    if filename:
        return filename
    try:
        return _url_download_filename(response.url)
    except Exception:
        return ""


def _download_dir_snapshot(watch_dirs: Iterable[Path]) -> Dict[str, Tuple[int, int]]:
    snapshot: Dict[str, Tuple[int, int]] = {}
    for watch_dir in watch_dirs:
        try:
            entries = list(watch_dir.iterdir())
        except Exception:
            continue
        for entry in entries:
            try:
                if not entry.is_file():
                    continue
                stat = entry.stat()
            except Exception:
                continue
            snapshot[str(entry)] = (int(stat.st_size), int(stat.st_mtime_ns))
    return snapshot


def _stable_download_from_dirs(
    watch_dirs: Iterable[Path],
    before_snapshot: Dict[str, Tuple[int, int]],
    stable_seen: Dict[str, Tuple[int, float]],
    *,
    started_at: float,
    stability_ms: int,
    allow_temp_names: Optional[Iterable[Any]] = None,
    allow_browser_uuid_names: bool = False,
) -> Optional[Path]:
    allowed_temp_names = {
        Path(str(name or "").replace("\\", "/")).name.lower()
        for name in (allow_temp_names or [])
        if str(name or "").strip()
    }
    candidates: List[Tuple[float, Path, int]] = []
    for watch_dir in watch_dirs:
        try:
            entries = list(watch_dir.iterdir())
        except Exception:
            continue
        for entry in entries:
            try:
                if not entry.is_file():
                    continue
                is_temporary = _is_temporary_download_path(entry)
                temp_name_allowed = entry.name.lower() in allowed_temp_names
                browser_uuid_allowed = allow_browser_uuid_names and _looks_like_browser_temp_download_name(entry.name)
                if is_temporary and not temp_name_allowed and not browser_uuid_allowed:
                    continue
                stat = entry.stat()
            except Exception:
                continue
            key = str(entry)
            size = int(stat.st_size)
            mtime_ns = int(stat.st_mtime_ns)
            if size <= 0:
                continue
            existed = before_snapshot.get(key)
            changed_since_start = existed is None or existed != (size, mtime_ns)
            if not changed_since_start or float(stat.st_mtime) < started_at - 1.0:
                continue
            candidates.append((float(stat.st_mtime), entry, size))

    now = time.time()
    for _, entry, size in sorted(candidates, key=lambda item: item[0], reverse=True):
        key = str(entry)
        previous_size, stable_since = stable_seen.get(key, (-1, now))
        if previous_size != size:
            stable_seen[key] = (size, now)
            continue
        if (now - stable_since) * 1000 >= stability_ms:
            return entry
    return None


def _changed_download_file(
    path: Path,
    before_snapshot: Dict[str, Tuple[int, int]],
    *,
    started_at: float,
) -> Optional[Path]:
    try:
        if not path.is_file():
            return None
        stat = path.stat()
    except Exception:
        return None
    size = int(stat.st_size)
    if size <= 0:
        return None
    mtime_ns = int(stat.st_mtime_ns)
    existed = before_snapshot.get(str(path))
    if existed == (size, mtime_ns):
        return None
    if float(stat.st_mtime) < started_at - 1.0:
        return None
    return path


def _completed_cdp_download_file_from_dirs(
    cdp_downloads: Dict[str, Dict[str, Any]],
    watch_dirs: Iterable[Path],
    before_snapshot: Dict[str, Tuple[int, int]],
    *,
    started_at: float,
) -> Tuple[Optional[Path], Optional[Dict[str, Any]]]:
    completed = [
        item
        for item in cdp_downloads.values()
        if str(item.get("state") or "").lower() == "completed"
        and str(item.get("guid") or "").strip()
    ]
    completed.sort(key=lambda item: float(item.get("completed_at") or 0.0), reverse=True)
    for item in completed:
        guid = Path(str(item.get("guid") or "").replace("\\", "/")).name
        suggested = _original_download_filename(str(item.get("suggested_filename") or ""))
        candidate_names = [name for name in (guid, suggested) if name]
        for watch_dir in watch_dirs:
            for name in candidate_names:
                candidate = _changed_download_file(
                    watch_dir / name,
                    before_snapshot,
                    started_at=started_at,
                )
                if candidate:
                    return candidate, item
    return None, None


def _record_filesystem_download_result(
    result: Dict[str, Any],
    path: Path,
    *,
    capture_dir: Optional[Union[str, Path]] = None,
    test_id: Optional[str] = None,
    browser_name: str = "download",
    suggested_filename: Any = None,
) -> None:
    filename = path.name or "download"
    target_filename = _filesystem_download_target_filename(
        path,
        suggested_filename=suggested_filename,
        test_id=test_id,
        browser_name=browser_name,
    )
    configured_download_dir = _configured_download_dir()
    source_is_named_download = False
    source_in_configured_dir = False
    try:
        source_in_configured_dir = path.resolve().parent == configured_download_dir.resolve()
        source_is_named_download = (
            source_in_configured_dir
            and not _looks_like_browser_temp_download_name(path.name)
            and not _looks_like_generated_test_download_name(path.name, test_id)
        )
    except OSError:
        source_in_configured_dir = str(path.parent) == str(configured_download_dir)
        source_is_named_download = (
            source_in_configured_dir
            and not _looks_like_browser_temp_download_name(path.name)
            and not _looks_like_generated_test_download_name(path.name, test_id)
        )
    saved_path: Optional[Path]
    if source_is_named_download:
        saved_path = path
    elif target_filename:
        saved_path = _download_save_path(target_filename)
    else:
        saved_path = None
    if saved_path:
        saved_path.parent.mkdir(parents=True, exist_ok=True)
    if saved_path and not source_is_named_download:
        try:
            if path.resolve() != saved_path.resolve():
                if source_in_configured_dir:
                    shutil.move(str(path), str(saved_path))
                else:
                    shutil.copy2(path, saved_path)
        except OSError:
            if str(path) != str(saved_path):
                if source_in_configured_dir:
                    shutil.move(str(path), str(saved_path))
                else:
                    shutil.copy2(path, saved_path)
    result["download_source"] = "filesystem"
    result["download_detected_filename"] = filename
    if suggested_filename:
        result["download_suggested_filename"] = _original_download_filename(str(suggested_filename))
        result["download_filename"] = _original_download_filename(str(suggested_filename))
    elif _looks_like_browser_temp_download_name(filename) or _looks_like_generated_test_download_name(filename, test_id):
        result["download_suggested_filename"] = ""
        result["download_filename"] = ""
        result["download_filename_unknown"] = True
    else:
        result["download_suggested_filename"] = target_filename
        result["download_filename"] = _original_download_filename(target_filename)
    result["download_original_dir"] = str(path.parent)
    result["download_original_path"] = str(path)
    if saved_path:
        result["saved_filename"] = saved_path.name
        result["download_renamed"] = saved_path.name != result["download_filename"]
        result["download_saved_dir"] = str(saved_path.parent)
        result["download_saved_path"] = str(saved_path)
    else:
        result["saved_filename"] = ""
        result["download_renamed"] = False
        result["download_saved_dir"] = ""
        result["download_saved_path"] = ""
    archive_path, archive_error = _archive_download_file(
        saved_path or path,
        capture_dir=capture_dir,
        test_id=test_id,
        browser_name=browser_name,
    )
    if archive_path:
        result["download_archive_path"] = str(archive_path)
    if archive_error:
        result["download_archive_error"] = archive_error
    if saved_path:
        result["download_dir"] = str(saved_path.parent)
        result["download_path"] = str(saved_path)
        _record_download_file_metadata(result, saved_path)
    elif archive_path:
        result["download_dir"] = str(archive_path.parent)
        result["download_path"] = str(archive_path)
        _record_download_file_metadata(result, archive_path)
    else:
        result["download_dir"] = str(path.parent)
        result["download_path"] = str(path)
        _record_download_file_metadata(result, path)


def _record_download_result(
    result: Dict[str, Any],
    download: Any,
    *,
    capture_dir: Optional[Union[str, Path]] = None,
    test_id: Optional[str] = None,
    browser_name: str = "download",
    suggested_filename: Any = None,
) -> None:
    playwright_filename = str(getattr(download, "suggested_filename", "") or "").strip()
    hint_filename = str(suggested_filename or "").strip()
    normalized_playwright_filename = _original_download_filename(playwright_filename) if playwright_filename else ""
    normalized_hint_filename = _original_download_filename(hint_filename) if hint_filename else ""
    save_filename = (
        normalized_playwright_filename
        if normalized_playwright_filename and normalized_playwright_filename != "download"
        else normalized_hint_filename
        if normalized_hint_filename and normalized_hint_filename != "download"
        else normalized_playwright_filename
        or "download"
    )
    save_path = _download_save_path(save_filename)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    download.save_as(str(save_path))
    result["download_source"] = "playwright_event"
    if playwright_filename:
        result["download_playwright_suggested_filename"] = _original_download_filename(playwright_filename)
    if normalized_hint_filename and normalized_hint_filename != save_filename:
        result["download_response_filename_hint"] = normalized_hint_filename
    result["download_suggested_filename"] = save_filename
    result["download_filename"] = _original_download_filename(save_filename)
    result["saved_filename"] = save_path.name
    result["download_renamed"] = save_path.name != result["download_filename"]
    result["download_original_dir"] = str(save_path.parent)
    result["download_original_path"] = str(save_path)
    result["download_saved_dir"] = str(save_path.parent)
    result["download_saved_path"] = str(save_path)
    archive_path, archive_error = _archive_download_file(
        save_path,
        capture_dir=capture_dir,
        test_id=test_id,
        browser_name=browser_name,
    )
    if archive_path:
        result["download_archive_path"] = str(archive_path)
    if archive_error:
        result["download_archive_error"] = archive_error
    result["download_dir"] = str(save_path.parent)
    result["download_path"] = str(save_path)
    _record_download_file_metadata(result, save_path)


def _console_font(size: int = 15, sample_text: str = ""):
    japanese_candidates = [
        r"C:\Windows\Fonts\NotoSansJP-VF.ttf",
        r"C:\Windows\Fonts\meiryo.ttc",
        r"C:\Windows\Fonts\YuGothM.ttc",
        r"C:\Windows\Fonts\msgothic.ttc",
    ]
    latin_candidates = [
        r"C:\Windows\Fonts\consola.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    ]
    if any(ord(char) > 127 for char in str(sample_text or "")):
        candidates = japanese_candidates + latin_candidates
    else:
        candidates = latin_candidates + japanese_candidates
    for candidate in candidates:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _wrap_console_line(text: str, limit: int = 150) -> List[str]:
    raw = str(text or "")
    if len(raw) <= limit:
        return [raw]
    lines = []
    current = raw
    while len(current) > limit:
        split_at = current.rfind(" ", 0, limit)
        if split_at < 40:
            split_at = limit
        lines.append(current[:split_at].rstrip())
        current = current[split_at:].lstrip()
    if current:
        lines.append(current)
    return lines


def _render_console_evidence_image(events: List[Dict[str, Any]], output_dir: Path, name: str) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / f"{name}_console.png"
    width = 1280
    line_height = 24
    header_height = 54
    lines: List[Tuple[str, str]] = []

    if not events:
        lines.append(("info", "No console/pageerror/requestfailed/http error events captured."))
    for event in events:
        timestamp = event.get("time") or ""
        level = str(event.get("level") or event.get("type") or "info").lower()
        event_type = event.get("type") or "EVENT"
        detail = event.get("detail") or ""
        status = f" status={event.get('status')}" if event.get("status") else ""
        url = f" {event.get('url')}" if event.get("url") else ""
        text = f"{timestamp} [{event_type}]{status} {detail}{url}".strip()
        for wrapped in _wrap_console_line(text):
            lines.append((level, wrapped))

    evidence_text = "\n".join(text for _, text in lines)
    font = _console_font(15, evidence_text)
    small_font = _console_font(13, evidence_text)
    height = max(240, header_height + (len(lines) + 1) * line_height + 24)
    image = Image.new("RGB", (width, height), (31, 31, 31))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, header_height), fill=(38, 38, 38))
    draw.text((18, 16), "Console evidence", fill=(232, 234, 237), font=font)
    draw.text(
        (220, 18),
        "Captured from Playwright console/pageerror/requestfailed/HTTP response events",
        fill=(154, 160, 166),
        font=small_font,
    )
    y = header_height + 14
    colors = {
        "error": (255, 128, 128),
        "pageerror": (255, 128, 128),
        "requestfailed": (255, 185, 117),
        "http_error": (255, 185, 117),
        "warning": (255, 214, 102),
        "warn": (255, 214, 102),
        "info": (207, 216, 220),
        "log": (207, 216, 220),
        "debug": (180, 190, 200),
    }
    for level, text in lines:
        draw.text((18, y), text, fill=colors.get(level, (207, 216, 220)), font=font)
        y += line_height
    image.save(image_path)
    return str(image_path)


def _needs_console_evidence(action_context: Optional[Dict[str, Any]], action_type: str) -> bool:
    context = action_context or {}
    blob = " ".join(
        str(value or "")
        for value in (
            action_type,
            context.get("case_type"),
            context.get("action_type"),
            context.get("expected_type"),
            context.get("test_title"),
            context.get("label"),
            context.get("objective"),
        )
    ).lower()
    return any(token in blob for token in ("js_error", "javascript", "console", "http_500", "http error", "network_abort"))


NEGATIVE_ACTIONS = {"negative_js_error", "negative_http_500", "negative_network_abort"}


def _negative_url_pattern(action_context: Optional[Dict[str, Any]], value: Any = None) -> str:
    context = action_context or {}
    for key in ("url_pattern", "expected_value", "test_data", "value"):
        candidate = context.get(key)
        if candidate:
            return str(candidate)
    if value:
        return str(value)
    return "**/*"


def _negative_visual_evidence_payload(
    action: str,
    *,
    detail: Any = "",
    url: Any = "",
    phase: str = "triggered",
) -> Dict[str, str]:
    labels = {
        "negative_js_error": "Simulated JavaScript Error",
        "negative_http_500": "Simulated HTTP 500",
        "negative_network_abort": "Simulated Network Abort",
    }
    title = labels.get(str(action or ""), "Simulated Negative Case")
    detail_text = str(detail or "").strip()
    url_text = str(url or "").strip()
    return {
        "title": title,
        "phase": phase,
        "detail": detail_text,
        "url": url_text,
    }


def _inject_negative_visual_evidence(
    page: Page,
    action: str,
    *,
    detail: Any = "",
    url: Any = "",
    phase: str = "triggered",
) -> List[Dict[str, Any]]:
    if _page_is_closed(page):
        return [{"injected": False, "reason": "page_closed"}]

    payload = _negative_visual_evidence_payload(action, detail=detail, url=url, phase=phase)
    script = """
    payload => {
      const doc = document;
      const root = doc.body || doc.documentElement;
      if (!root) return { injected: false, reason: 'no document root' };

      const id = 'moonlight-negative-visual-evidence';
      let panel = doc.getElementById(id);
      if (!panel) {
        panel = doc.createElement('div');
        panel.id = id;
        root.appendChild(panel);
      }

      panel.replaceChildren();
      const title = doc.createElement('div');
      title.textContent = payload.title || 'Simulated Negative Case';
      title.style.cssText = 'font-weight:700;font-size:16px;line-height:1.35;margin-bottom:8px;';

      const phase = doc.createElement('div');
      phase.textContent = 'Phase: ' + (payload.phase || 'triggered');
      phase.style.cssText = 'font-size:12px;line-height:1.35;margin-bottom:6px;opacity:.95;';

      const detail = doc.createElement('div');
      detail.textContent = payload.detail || 'A visible error evidence marker was injected for screenshot verification.';
      detail.style.cssText = 'font-size:13px;line-height:1.45;margin-bottom:6px;';

      const url = doc.createElement('div');
      url.textContent = payload.url ? ('Target: ' + payload.url) : 'Target: current page';
      url.style.cssText = 'font-size:11px;line-height:1.35;word-break:break-all;opacity:.9;';

      panel.appendChild(title);
      panel.appendChild(phase);
      panel.appendChild(detail);
      panel.appendChild(url);
      panel.setAttribute('data-moonlight-negative-evidence', 'true');
      panel.style.cssText = [
        'position:fixed',
        'top:14px',
        'right:14px',
        'width:min(440px, calc(100vw - 32px))',
        'box-sizing:border-box',
        'padding:14px 16px',
        'z-index:2147483647',
        'background:#7f1d1d',
        'color:#fff',
        'border:3px solid #fecaca',
        'box-shadow:0 12px 32px rgba(0,0,0,.38)',
        'font-family:Arial, Meiryo, sans-serif',
        'text-align:left',
        'letter-spacing:0',
        'pointer-events:none'
      ].join(';');

      const body = doc.body;
      if (body) {
        body.setAttribute('data-moonlight-negative-state', payload.title || 'negative');
        body.style.outline = '4px solid #ef4444';
        body.style.outlineOffset = '-4px';
      }

      return {
        injected: true,
        title: payload.title,
        phase: payload.phase,
        url: location.href,
        text: panel.innerText
      };
    }
    """

    evidence: List[Dict[str, Any]] = []
    for frame in page.frames:
        try:
            state = frame.evaluate(script, payload)
            if state:
                state["frame_url"] = str(frame.url or "")
                evidence.append(state)
        except PlaywrightError as exc:
            evidence.append({"injected": False, "frame_url": str(frame.url or ""), "reason": str(exc)})
    return evidence or [{"injected": False, "reason": "no frames"}]


def _clear_negative_visual_evidence(page: Page) -> None:
    if _page_is_closed(page):
        return
    script = """
    () => {
      const panel = document.getElementById('moonlight-negative-visual-evidence');
      if (panel) panel.remove();
      if (document.body) {
        document.body.removeAttribute('data-moonlight-negative-state');
        document.body.style.outline = '';
        document.body.style.outlineOffset = '';
      }
    }
    """
    for frame in page.frames:
        try:
            frame.evaluate(script)
        except PlaywrightError:
            pass


def _page_is_closed(page: Page) -> bool:
    try:
        return page.is_closed()
    except Exception:
        return True


def _is_target_closed_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return (
        "target page, context or browser has been closed" in message
        or "target closed" in message
        or "page has been closed" in message
        or "page closed" in message
    )


def _should_accept_database_dialog(context: Optional[Dict[str, Any]]) -> bool:
    if not context:
        return False
    evidence = " ".join(
        str(context.get(key) or "")
        for key in (
            "case_type",
            "action_type",
            "label",
            "semantic_key",
            "locator",
            "onclick",
            "expected_type",
        )
    ).lower()
    return any(
        token in evidence
        for token in (
            "delete_action",
            "delete",
            "削除",
            "update",
            "更新",
            "register",
            "登録",
            "create",
            "追加",
            "保存",
            "upload_submit",
        )
    )


def _expects_browser_dialog(context: Optional[Dict[str, Any]]) -> bool:
    if not context:
        return False
    explicit_fields = (
        "case_type",
        "action_type",
        "expected_type",
        "semantic_key",
    )
    explicit_values = [str(context.get(key) or "").strip().lower() for key in explicit_fields]
    if any(value in {"browser_dialog", "dialog", "alert", "confirm", "prompt"} for value in explicit_values):
        return True
    if any("browser_dialog" in value or "js_dialog" in value for value in explicit_values):
        return True

    script_evidence = " ".join(
        str(context.get(key) or "")
        for key in ("raw", "onclick", "locator", "expected_value")
    ).lower()
    return bool(re.search(r"\b(?:alert|confirm|prompt)\s*\(", script_evidence))


def _should_capture_browser_dialogs(context: Optional[Dict[str, Any]], semantic_action: Any) -> bool:
    if context and context.get("manual_replay"):
        return True
    if str(semantic_action or "").strip().lower() in {"browser_dialog", "download"}:
        return True
    return _should_accept_database_dialog(context) or _expects_browser_dialog(context)


def _accept_dialog_safely(dialog) -> str:
    return _handle_dialog_safely(dialog, "accept")


def _dialog_action_from_context(context: Optional[Dict[str, Any]]) -> str:
    if not context:
        return "accept"
    raw = " ".join(
        str(context.get(key) or "")
        for key in (
            "dialog_action",
            "dialog_button",
            "dialog_response",
            "confirm_action",
            "confirm_button",
        )
    ).strip().lower()
    if not raw:
        return "accept"
    if any(token in raw for token in ("dismiss", "cancel", "キャンセル", "取消", "取消し", "no", "false", "reject")):
        return "dismiss"
    return "accept"


def _handle_dialog_safely(dialog, action: str = "accept", *, prompt_text: str = "") -> str:
    action = str(action or "accept").strip().lower()
    try:
        if action == "dismiss":
            dialog.dismiss()
            return "dismissed"
        if prompt_text:
            dialog.accept(prompt_text)
        else:
            dialog.accept()
        return "accepted"
    except Exception as exc:
        message = str(exc)
        lowered = message.lower()
        if "already handled" in lowered or "already been handled" in lowered:
            return "already_handled"
        return f"{action}_failed: {message}"


def _expected_dialog_message(context: Optional[Dict[str, Any]], value: Optional[str]) -> str:
    context = context or {}
    for candidate in (
        value,
        context.get("expected_message"),
        context.get("dialog_message"),
        context.get("expected_value"),
        context.get("value"),
    ):
        text = str(candidate or "").strip()
        if text:
            return text
    return ""


def _install_print_observer(page: Page) -> Dict[str, Any]:
    result: Dict[str, Any] = {"installed": False, "frames": 0, "errors": []}
    if _page_is_closed(page):
        return {"installed": False, "frames": 0, "errors": [{"error": "page_closed"}]}

    script_body = """
        window.__moonlightPrintEvents = window.__moonlightPrintEvents || [];
        if (!window.__moonlightPrintInstalled) {
            window.__moonlightPrintInstalled = true;
            const originalPrint = window.print ? window.print.bind(window) : null;
            window.__moonlightOriginalPrint = originalPrint;
            window.print = () => {
                window.__moonlightPrintEvents.push({
                    time: new Date().toISOString(),
                    url: String(location.href || ""),
                    title: String(document.title || "")
                });
                window.dispatchEvent(new Event("beforeprint"));
                window.dispatchEvent(new Event("afterprint"));
            };
        }
    """
    try:
        page.context.add_init_script(script=script_body)
        result["context_init_script"] = True
    except Exception as exc:
        result.setdefault("errors", []).append({"scope": "context_init_script", "error": str(exc)})
    try:
        page.add_init_script(script=script_body)
        result["page_init_script"] = True
    except Exception as exc:
        result.setdefault("errors", []).append({"scope": "page_init_script", "error": str(exc)})

    script = f"() => {{ {script_body} return window.__moonlightPrintInstalled ? 'installed' : 'not_installed'; }}"
    for frame in _walk_frames(page.main_frame):
        try:
            frame.evaluate(script)
            result["frames"] += 1
            result["installed"] = True
        except Exception as exc:
            result.setdefault("errors", []).append({"frame_url": str(getattr(frame, "url", "") or ""), "error": str(exc)})
    return result


def _read_print_events(page: Page) -> Dict[str, Any]:
    events: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    if _page_is_closed(page):
        return {"events": events, "errors": [{"error": "page_closed"}]}
    for frame in _walk_frames(page.main_frame):
        try:
            frame_events = frame.evaluate("() => window.__moonlightPrintEvents || []")
            if isinstance(frame_events, list):
                events.extend(item for item in frame_events if isinstance(item, dict))
        except Exception as exc:
            errors.append({"frame_url": str(getattr(frame, "url", "") or ""), "error": str(exc)})
    return {"events": events, "errors": errors}


def _closed_page_state(output_dir: Path, name: str) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    screenshot = output_dir / f"{name}.png"
    image = Image.new("RGB", (640, 360), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 639, 359), outline=(210, 216, 224), width=2)
    draw.text((32, 32), "Page closed", fill=(20, 30, 42))
    draw.text((32, 64), "The action closed this browser page.", fill=(70, 84, 102))
    image.save(screenshot)
    return {
        "screenshot": str(screenshot),
        "page_closed": True,
        "url": "about:closed",
        "title": "",
        "text": "Page closed",
        "dom": "<page-closed />",
        "target_frame": {"name": "closed", "url": "about:closed"},
        "frame_candidates": [],
    }


def _capture_native_browser_screen(page: Page, screenshot: Path, timeout: int = 15000) -> Dict[str, Any]:
    try:
        page.bring_to_front()
        try:
            page.keyboard.press("Control+0")
        except (PlaywrightTimeoutError, PlaywrightError):
            pass
        page.wait_for_timeout(250)
        image = ImageGrab.grab(bbox=(0, 0, 1920, 1080))
        image.save(screenshot)
        return {
            "ok": True,
            "screenshot_scope": "browser_screen_1920x1080",
            "screenshot_resolution": "1920x1080",
            "includes_browser_chrome": True,
        }
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}


def _capture_composited_browser_screen(page: Page, screenshot: Path, timeout: int = 15000) -> Dict[str, Any]:
    """
    Render a browser-like evidence image from the exact Playwright page.

    Native OS screen grabs can capture whichever browser window is foreground,
    which is fragile when legacy/new pages run side-by-side. This composited
    mode keeps the URL evidence while making the content come from the page
    object that is being compared.
    """
    temp_content = screenshot.with_name(f"{screenshot.stem}_content_tmp.png")
    try:
        page.bring_to_front()
        try:
            page.keyboard.press("Control+0")
        except (PlaywrightTimeoutError, PlaywrightError):
            pass
        page.wait_for_timeout(150)
        page.screenshot(path=str(temp_content), full_page=False, timeout=timeout)

        content = Image.open(temp_content).convert("RGB")
        width, height = 1920, 1080
        chrome_h = 86
        canvas = Image.new("RGB", (width, height), (255, 255, 255))
        draw = ImageDraw.Draw(canvas)

        draw.rectangle((0, 0, width, chrome_h), fill=(240, 242, 245))
        draw.rectangle((0, 0, width, 34), fill=(229, 232, 237))
        draw.rounded_rectangle((14, 8, 360, 34), radius=8, fill=(255, 255, 255), outline=(206, 212, 220))
        draw.text((28, 16), "Moonlight Regression", fill=(48, 57, 70))
        draw.rounded_rectangle((74, 44, width - 120, 76), radius=14, fill=(255, 255, 255), outline=(196, 203, 213))
        draw.text((92, 53), _safe_page_url(page), fill=(30, 41, 59))
        draw.line((0, chrome_h - 1, width, chrome_h - 1), fill=(205, 211, 220))

        content_h = height - chrome_h
        scale = min(width / max(content.width, 1), content_h / max(content.height, 1))
        next_size = (max(1, int(content.width * scale)), max(1, int(content.height * scale)))
        resample_filter = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.BICUBIC)
        resized = content.resize(next_size, resample_filter)
        canvas.paste(resized, (0, chrome_h))
        canvas.save(screenshot)
        return {
            "ok": True,
            "screenshot_scope": "browser_screen_composited_1920x1080",
            "screenshot_resolution": "1920x1080",
            "includes_browser_chrome": True,
            "browser_chrome_source": "synthetic_url_bar",
            "url_bar": _safe_page_url(page),
        }
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}
    finally:
        try:
            temp_content.unlink(missing_ok=True)
        except Exception:
            pass


def _capture_browser_screen(page: Page, screenshot: Path, timeout: int = 15000) -> Dict[str, Any]:
    """
    Capture browser evidence with URL bar at 1920x1080.

    Default mode is native playwright screenshot without browser chrome.
    Set MOONLIGHT_BROWSER_SCREEN_MODE=composited to use synthetic URL bar.
    Set MOONLIGHT_BROWSER_SCREEN_MODE=native_os to use a real OS screen grab.
    """
    mode = str(os.environ.get("MOONLIGHT_BROWSER_SCREEN_MODE") or "playwright_native").strip().lower()
    if mode in {"native_os", "os", "imagegrab"}:
        return _capture_native_browser_screen(page, screenshot, timeout=timeout)
    if mode == "composited":
        return _capture_composited_browser_screen(page, screenshot, timeout=timeout)
    
    # playwright_native mode (Default)
    try:
        page.screenshot(path=str(screenshot), full_page=False, timeout=timeout)
        return {
            "ok": True,
            "screenshot_scope": "playwright_native",
            "screenshot_resolution": "viewport",
            "includes_browser_chrome": False,
        }
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}


ACTION_ALIASES = {
    "button": "click",
    "click": "click",
    "link": "navigate",
    "navigate": "navigate",
    "goto": "goto",
    "field": "fill",
    "fill": "fill",
    "input": "fill",
    "text": "fill",
    "textarea": "fill",
    "clear": "clear",
    "set_value": "set_value",
    "setvalue": "set_value",
    "hidden": "set_value",
    "check": "check",
    "checked": "check",
    "checkbox": "check",
    "radio": "check",
    "set_checked": "check",
    "uncheck": "uncheck",
    "unchecked": "uncheck",
    "press": "press",
    "key": "press",
    "special_key": "press",
    "close_window": "click",
    "select": "select",
    "change": "select",
    "form": "submit",
    "submit": "submit",
    "file": "upload",
    "upload": "upload",
    "download": "download",
    "save_pdf": "save_pdf",
    "pdf_save": "save_pdf",
    "saved_pdf": "save_pdf",
    "print_to_pdf": "save_pdf",
    "browser_dialog": "browser_dialog",
    "dialog": "browser_dialog",
    "alert": "browser_dialog",
    "confirm": "browser_dialog",
    "prompt": "browser_dialog",
    "print": "print",
    "print_dialog": "print",
    "print_invocation": "print",
    "child_navigation": "click",
    "child_page": "click",
    "child_route": "click",
    "open_child": "click",
    "open_child_page": "click",
    "open_subpage": "click",
    "popup_navigation": "click",
    "popup_or_navigation": "click",
    "subpage_navigation": "click",
    "negative_js_error": "negative_js_error",
    "negative_http_500": "negative_http_500",
    "negative_network_abort": "negative_network_abort",
    "wait": "wait",
    "snapshot": "wait",
    "page_snapshot": "wait",
    "visual_check": "wait",
    "assert_visible": "assert_visible",
    "expect_visible": "assert_visible",
    "verify_visible": "assert_visible",
    "assert_hidden": "assert_hidden",
    "expect_hidden": "assert_hidden",
    "verify_hidden": "assert_hidden",
    "wait_visible": "assert_visible",
    "wait_for_visible": "assert_visible",
    "assert_attached": "assert_attached",
    "expect_attached": "assert_attached",
    "verify_attached": "assert_attached",
    "assert_text": "assert_text",
    "expect_text": "assert_text",
    "verify_text": "assert_text",
    "text_visible": "assert_text",
    "assert_value": "assert_value",
    "expect_value": "assert_value",
    "verify_value": "assert_value",
    "assert_enabled": "assert_enabled",
    "expect_enabled": "assert_enabled",
    "verify_enabled": "assert_enabled",
    "assert_disabled": "assert_disabled",
    "expect_disabled": "assert_disabled",
    "verify_disabled": "assert_disabled",
    "assert_checked": "assert_checked",
    "expect_checked": "assert_checked",
    "verify_checked": "assert_checked",
    "assert_unchecked": "assert_unchecked",
    "expect_unchecked": "assert_unchecked",
    "verify_unchecked": "assert_unchecked",
    "assert_url": "assert_url",
    "expect_url": "assert_url",
}


def _infer_manual_assert_action(context: Dict[str, Any]) -> str:
    locator = str(context.get("locator") or context.get("selector") or "").strip().lower()
    text = " ".join(
        str(context.get(key) or "")
        for key in ("value", "expected_value", "text", "label", "title")
    ).lower()
    if not locator or locator in {"-", "__page__"}:
        return "assert_visible"
    if any(token in locator for token in ("browser", "native", "popup", "dialog", "window", "saved pdf")):
        return "manual_assert"

    wants_hidden = any(token in text for token in ("hidden", "not visible", "非表示", "表示されない"))
    wants_visible = any(token in text for token in ("visible", "shown", "displayed", "表示"))
    if wants_hidden:
        return "assert_hidden"
    if wants_visible:
        return "assert_visible"

    wants_disabled = any(token in text for token in ("disabled", "disable", "非活性", "無効"))
    wants_enabled = any(token in text for token in ("enabled", "enable", "活性", "有効"))
    if wants_disabled and not wants_enabled:
        return "assert_disabled"
    if wants_enabled and not wants_disabled:
        return "assert_enabled"

    wants_unchecked = any(token in text for token in ("unchecked", "not checked", "チェックが外", "チェックを外"))
    wants_checked = any(token in text for token in ("checked", "check", "チェック"))
    if wants_unchecked:
        return "assert_unchecked"
    if wants_checked and not wants_unchecked:
        return "assert_checked"

    return "assert_attached"


def infer_semantic_action(action_type: Optional[str], context: Optional[Dict[str, Any]] = None) -> str:
    """
    Normalize scanner/mapping hints into an executable semantic action.

    The mapping files mix historical action names with JSP scanner concepts such
    as kind=form and action_hint=submit. This function is intentionally
    conservative: explicit upload/select/form hints win, then aliases are used.
    """
    context = context or {}
    raw_values = [
        action_type,
        context.get("action_type"),
        context.get("action_hint"),
        context.get("kind"),
        context.get("tag"),
    ]
    raw = " ".join(str(value or "").lower() for value in raw_values)
    locator = str(context.get("locator") or context.get("selector") or "").lower()
    evidence = " ".join(
        str(context.get(key) or "").lower()
        for key in ("raw", "label", "semantic_key", "expected_type", "expected_value", "onclick")
    )

    for value in (
        action_type,
        context.get("action_type"),
        context.get("action_hint"),
        context.get("kind"),
    ):
        key = str(value or "").strip().lower()
        if not key:
            continue
        if key in {"manual_assert", "manual_review"}:
            return _infer_manual_assert_action(context)
        alias = ACTION_ALIASES.get(key)
        if alias and alias not in {"click", "navigate"}:
            return alias

    if "set_value" in raw or "setvalue" in raw or "hidden" in raw:
        return "set_value"
    for negative_action in NEGATIVE_ACTIONS:
        if negative_action in raw or negative_action in evidence:
            return negative_action
    if "clear" in raw:
        return "clear"
    if any(token in raw for token in ("assert_visible", "expect_visible", "verify_visible", "wait_visible", "wait_for_visible")):
        return "assert_visible"
    if any(token in raw for token in ("assert_attached", "expect_attached", "verify_attached")):
        return "assert_attached"
    if any(token in raw for token in ("assert_text", "expect_text", "verify_text", "text_visible")):
        return "assert_text"
    if any(token in raw for token in ("assert_value", "expect_value", "verify_value")):
        return "assert_value"
    if any(token in raw for token in ("assert_url", "expect_url")):
        return "assert_url"
    if "uncheck" in raw or "unchecked" in raw:
        return "uncheck"
    if "check" in raw or "checkbox" in raw or "radio" in raw:
        return "check"
    if "press" in raw or "special_key" in raw:
        return "press"
    if "save_pdf" in raw or "pdf_save" in raw or "saved_pdf" in raw or "print_to_pdf" in raw:
        return "save_pdf"
    if "saved_pdf" in evidence or "save as pdf" in evidence:
        return "save_pdf"
    if "print" in raw or "window.print" in evidence or "print_invocation" in evidence or "印刷" in evidence:
        return "print"
    if _expects_browser_dialog(context):
        return "browser_dialog"
    if "file_download" in raw or "download_template" in raw or "download" in raw or "download" in evidence or "ダウンロード" in evidence:
        return "download"
    if "upload" in raw or "file" in raw or "type='file'" in evidence or 'type="file"' in evidence:
        return "upload"
    if re.search(r"\bselect\b", raw) or ":select" in evidence or re.search(r"\bselect\b", evidence):
        return "select"
    if "form" in raw or "submit" in raw or locator.startswith("form") or "[name=" in locator and "form" in locator:
        return "submit"
    if "navigate" in raw or "link" in raw or "href" in evidence:
        return "navigate"
    if "input" in raw or "field" in raw or "textarea" in raw or ":input" in evidence:
        return "fill"
    for value in raw_values:
        alias = ACTION_ALIASES.get(str(value or "").lower())
        if alias:
            return alias
    return "click"


def _wait_for_semantic_ready(page: Page, timeout: int = 10000) -> Dict[str, Any]:
    wait_state: Dict[str, Any] = {
        "networkidle": False,
        "domcontentloaded": False,
        "settle_timeout_ms": 0,
        "body_visible": False,
        "business_elements": 0,
        "semantic_stable": False,
    }
    if _page_is_closed(page):
        wait_state["page_closed"] = True
        return wait_state
    wait_state["popup_window_restore"] = restore_popup_window_state(page)
    # Prefer domcontentloaded for legacy systems.
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout)
        wait_state["domcontentloaded"] = True
    except (PlaywrightTimeoutError, PlaywrightError) as exc:
        wait_state["domcontentloaded_error"] = str(exc)

    try:
        page.wait_for_load_state("networkidle", timeout=min(timeout, 1500))
        wait_state["networkidle"] = True
    except (PlaywrightTimeoutError, PlaywrightError):
        # networkidle often times out on legacy systems and is not a hard blocker.
        pass

    # Use stable business content instead of a fixed sleep before screenshots.
    target, diagnostics = _find_business_frame(page, timeout=min(timeout, 3000))
    wait_state["target_frame"] = _frame_identity(target)
    wait_state["frame_candidates"] = diagnostics[:8]
    try:
        target.locator("body").wait_for(state="visible", timeout=min(timeout, 5000))
        wait_state["body_visible"] = True
    except (PlaywrightTimeoutError, PlaywrightError) as exc:
        wait_state["body_error"] = str(exc)

    try:
        target.locator("form, table").first.wait_for(state="attached", timeout=2000)
    except (PlaywrightTimeoutError, PlaywrightError) as exc:
        wait_state["business_elements_error"] = str(exc)

    try:
        wait_state["business_elements"] = target.locator("form, table, input, select, textarea, button, a").count()
    except PlaywrightError as exc:
        wait_state["business_elements_error"] = str(exc)

    stable_samples = 0
    previous_signature: Optional[str] = None
    deadline = time.monotonic() + max(1.0, timeout / 1000)
    while time.monotonic() < deadline and not _page_is_closed(page):
        try:
            semantic_state = target.evaluate(
                """() => {
                    const visible = (element) => {
                        if (!(element instanceof Element)) return false;
                        const style = getComputedStyle(element);
                        if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity) === 0) {
                            return false;
                        }
                        const rect = element.getBoundingClientRect();
                        return rect.width > 8 && rect.height > 8;
                    };
                    const loadingSelectors = [
                        '[aria-busy="true"]',
                        '.blockUI',
                        '[class*="spinner" i]',
                        '[class*="loading" i]',
                        '[class*="loader" i]',
                        '[id*="spinner" i]',
                        '[id*="loading" i]',
                        '[id*="loader" i]'
                    ];
                    const loading = Array.from(document.querySelectorAll(loadingSelectors.join(",")))
                        .filter(visible)
                        .slice(0, 20)
                        .map((element) => element.id || element.className || element.tagName);
                    const bodyText = (document.body?.innerText || "").replace(/\\s+/g, " ").trim();
                    const businessElements = document.querySelectorAll(
                        "form, table, input, select, textarea, button, a"
                    ).length;
                    const loadCounter = document.querySelector("#LoadCounter");
                    const screeningPage = Boolean(loadCounter && document.querySelector("#btBunkenIchiran"));
                    const selectedLoadedDocument = Boolean(
                        document.querySelector(
                            ".bunkenClicked.loaded, tr.loaded.bunkenselect, .loaded.bunkenselect"
                        )
                    );
                    const contentTabCount = document.querySelectorAll('a[href^="#koumoku"]').length;
                    const loadCounterValue = loadCounter ? String(loadCounter.value || "").trim() : "";
                    const screeningReady = !screeningPage || (
                        (loadCounterValue === "" || loadCounterValue === "0") &&
                        selectedLoadedDocument &&
                        contentTabCount >= 3 &&
                        bodyText.length >= 500
                    );
                    const visiblePendingImages = Array.from(document.images)
                        .filter((image) => visible(image) && !image.complete).length;
                    return {
                        ready: loading.length === 0 && screeningReady && (
                            !screeningPage || visiblePendingImages === 0
                        ),
                        loading,
                        screeningPage,
                        screeningReady,
                        loadCounterValue,
                        selectedLoadedDocument,
                        contentTabCount,
                        visiblePendingImages,
                        textLength: bodyText.length,
                        businessElements,
                        signature: [
                            bodyText.length,
                            businessElements,
                            loading.length,
                            loadCounterValue,
                            selectedLoadedDocument,
                            contentTabCount,
                            visiblePendingImages
                        ].join("|")
                    };
                }"""
            )
        except PlaywrightError as exc:
            if _is_target_closed_error(exc) or _page_is_closed(page):
                wait_state["page_closed"] = True
                wait_state["semantic_error"] = str(exc)
                return wait_state
            wait_state["semantic_error"] = str(exc)
            break

        wait_state["semantic_state"] = semantic_state
        signature = str(semantic_state.get("signature") or "")
        if semantic_state.get("ready") and signature == previous_signature:
            stable_samples += 1
        elif semantic_state.get("ready"):
            stable_samples = 1
        else:
            stable_samples = 0
        previous_signature = signature
        if stable_samples >= 2:
            wait_state["semantic_stable"] = True
            break
        try:
            page.wait_for_timeout(400)
            wait_state["settle_timeout_ms"] += 400
        except PlaywrightError as exc:
            if _is_target_closed_error(exc) or _page_is_closed(page):
                wait_state["page_closed"] = True
                wait_state["settle_error"] = str(exc)
                return wait_state
            raise

    if not wait_state["semantic_stable"]:
        wait_state["semantic_timeout"] = True

    return wait_state


def _frame_identity(frame: Frame) -> Dict[str, Any]:
    return {
        "name": frame.name,
        "url": frame.url,
    }


def _find_business_frame(page: Page, timeout: int = 3000) -> Tuple[Frame, List[Dict[str, Any]]]:
    diagnostics: List[Dict[str, Any]] = []
    best_frame = page.main_frame
    best_score = -1

    for frame in _walk_frames(page.main_frame):
        score = 0
        details: Dict[str, Any] = {
            "name": frame.name,
            "url": frame.url,
            "score": 0,
            "forms": 0,
            "tables": 0,
            "controls": 0,
            "body_visible": False,
        }
        try:
            if frame.url and frame.url not in ("about:blank", "about:srcdoc"):
                score += 20
            if re.search(r"\.(jsp|do|action)(?:[?#]|$)|/(admin|main|search|list|entry|edit|disp|download|upload)", frame.url, re.I):
                score += 30

            body = frame.locator("body")
            details["body_visible"] = body.is_visible(timeout=timeout)
            if details["body_visible"]:
                score += 10

            forms = frame.locator("form").count()
            tables = frame.locator("table").count()
            controls = frame.locator("input, select, textarea, button, a").count()
            text_len = min(len(body.inner_text(timeout=timeout)), 1000)
            details.update({"forms": forms, "tables": tables, "controls": controls, "text_len": text_len})
            score += forms * 15 + tables * 10 + min(controls, 20) * 2 + min(text_len // 80, 10)
        except (PlaywrightTimeoutError, PlaywrightError) as exc:
            details["error"] = str(exc)
        details["score"] = score
        diagnostics.append(details)
        if score > best_score:
            best_score = score
            best_frame = frame

    diagnostics.sort(key=lambda item: item.get("score", 0), reverse=True)
    return best_frame, diagnostics


def _walk_frames(frame: Frame) -> Iterable[Frame]:
    yield frame
    for child in frame.child_frames:
        yield from _walk_frames(child)


def _frame_for_selector(page: Page, selector: str, timeout: int = 3000) -> Tuple[Frame, Dict[str, Any]]:
    if _page_is_closed(page):
        raise ValueError("Page is closed before action")
    target, diagnostics = _find_business_frame(page, timeout=timeout)
    for frame in _walk_frames(page.main_frame):
        try:
            locator = frame.locator(selector)
            if locator.count() > 0:
                try:
                    locator.first.wait_for(state="visible", timeout=timeout)
                except (PlaywrightTimeoutError, PlaywrightError):
                    pass
                return frame, {"target_frame": _frame_identity(frame), "selector_found": True, "frame_candidates": diagnostics[:8]}
        except (PlaywrightTimeoutError, PlaywrightError):
            continue
    return target, {"target_frame": _frame_identity(target), "selector_found": False, "frame_candidates": diagnostics[:8]}


def _default_upload_file(capture_dir: Optional[Union[str, Path]] = None) -> str:
    root = Path(capture_dir or "./output/uploads")
    root.mkdir(parents=True, exist_ok=True)
    sample = root / "moonlight_cyber_sample.txt"
    if not sample.exists():
        sample.write_text(
            "Moonlight cyber sample file\n"
            "purpose=semantic-upload-pressure-test\n"
            "payload=legacy-new-regression\n",
            encoding="utf-8",
        )
    return str(sample)


def _upload_filename(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = text.replace("\\", "/")
    return Path(normalized).name or PureWindowsPath(text).name


def _upload_placeholder_env_names(raw: str) -> List[str]:
    token = re.sub(r"[^A-Za-z0-9_]+", "_", raw.strip("${}$ ").upper()).strip("_")
    names = []
    if token:
        names.append(f"MOONLIGHT_{token}")
    names.append("MOONLIGHT_UPLOAD_FILE")
    return list(dict.fromkeys(names))


def _is_upload_placeholder(value: Any) -> bool:
    raw = str(value or "").strip()
    lowered = raw.lower()
    return (
        not raw
        or lowered.startswith("${")
        or lowered.startswith("$upload")
        or lowered in {"upload_file", "upload_invalid_file", "upload_empty_file", "upload_large_file"}
        or "fakepath" in lowered
    )


def _env_upload_file(raw: str) -> Optional[str]:
    for env_name in _upload_placeholder_env_names(raw):
        env_value = str(os.environ.get(env_name) or "").strip()
        if not env_value:
            continue
        env_path = Path(env_value)
        if env_path.exists():
            return str(env_path)
    return None


def _resolve_single_upload_file_value(value: Any, capture_dir: Optional[Union[str, Path]] = None) -> str:
    raw = str(value or "").strip()

    if raw:
        raw_path = Path(raw)
        if raw_path.exists():
            return str(raw_path)

        filename = _upload_filename(raw)
        if "fakepath" in raw.lower() and filename:
            search_roots = [
                Path("test_data/upload"),
                Path("test_data"),
                Path("data/upload"),
                Path("data"),
            ]
            for root in search_roots:
                if not root.exists():
                    continue
                direct = root / filename
                if direct.exists():
                    return str(direct)
                for candidate in root.rglob(filename):
                    if candidate.is_file():
                        return str(candidate)

    if _is_upload_placeholder(raw):
        env_value = _env_upload_file(raw)
        if env_value:
            return env_value
        return _default_upload_file(capture_dir)

    return raw


def _resolve_upload_file_value(value: Any, capture_dir: Optional[Union[str, Path]] = None) -> Union[str, List[str]]:
    if isinstance(value, (list, tuple)):
        resolved = [
            _resolve_single_upload_file_value(item, capture_dir)
            for item in value
            if str(item or "").strip()
        ]
        return resolved or _default_upload_file(capture_dir)
    return _resolve_single_upload_file_value(value, capture_dir)




def _resolve_upload_locator(frame: Frame, selector: str) -> Tuple[str, Dict[str, Any]]:
    """
    Resolve upload-specific selector mismatches.

    Legacy Struts JSP such as <html:file name="FormBean" property="uploadFile" />
    can be scanned as [name='FormBean'], which may point to a <form> at runtime
    and cannot run set_input_files(). Before upload execution, confirm that the
    target is <input type="file">; otherwise fall back to a file input on the page.
    """
    diagnostics: Dict[str, Any] = {
        "original_selector": selector,
        "resolved_selector": selector,
        "fallback_used": False,
    }

    def _is_file_input(candidate_selector: str) -> bool:
        try:
            locator = frame.locator(candidate_selector).first
            if locator.count() == 0:
                return False
            tag_name = locator.evaluate("el => (el.tagName || '').toLowerCase()")
            input_type = locator.evaluate("el => (el.getAttribute('type') || '').toLowerCase()")
            diagnostics["resolved_tag"] = tag_name
            diagnostics["resolved_type"] = input_type
            return tag_name == "input" and input_type == "file"
        except Exception as exc:
            diagnostics["validation_error"] = str(exc)
            return False

    if selector and _is_file_input(selector):
        return selector, diagnostics

    fallback_selectors = [
        "input[type='file']",
        "input[name='uploadFile']",
        "input[type='file'][name='uploadFile']",
    ]

    for fallback in fallback_selectors:
        try:
            count = frame.locator(fallback).count()
            if count > 0 and _is_file_input(fallback):
                diagnostics.update(
                    {
                        "resolved_selector": fallback,
                        "fallback_used": True,
                        "fallback_count": count,
                    }
                )
                return fallback, diagnostics
        except Exception as exc:
            diagnostics[f"fallback_error:{fallback}"] = str(exc)

    return selector, diagnostics


def _opens_popup_hint(context: Optional[Dict[str, Any]]) -> bool:
    context = context or {}
    attributes = {str(key).lower(): value for key, value in (context.get("attributes") or {}).items()}
    target = str(attributes.get("target") or "").strip().lower()
    evidence = " ".join(
        str(context.get(key) or "").lower()
        for key in (
            "action_type",
            "action_hint",
            "case_type",
            "expected_type",
            "expected_value",
            "raw",
            "label",
            "semantic_key",
            "onclick",
            "href",
            "target",
            "recorded_onclick",
            "recorded_href",
        )
    )
    explicit_popup = _truthy_context_value(context.get("opens_popup")) or _truthy_context_value(context.get("popup"))
    submit_target_match = re.search(
        r"submitform\s*\([^)]*,\s*[^)]*,\s*['\"]([^'\"]+)['\"]",
        evidence,
        re.IGNORECASE,
    )
    submit_target = submit_target_match.group(1).strip().lower() if submit_target_match else ""
    popup_submit_target = bool(
        submit_target
        and submit_target not in {"_self", "self", "_parent", "parent", "_top", "top"}
        and not submit_target.startswith("fr")
        and "dummy" not in submit_target
    )
    child_or_popup_navigation = any(
        token in evidence
        for token in (
            "child_navigation",
            "child_page",
            "child route",
            "child_route",
            "open_child",
            "popup_navigation",
            "popup_or_navigation",
            "subpage_navigation",
        )
    )
    return (
        bool(target and target not in {"_self", "self"})
        or explicit_popup
        or popup_submit_target
        or "window.open" in evidence
        or "target=" in evidence
        or child_or_popup_navigation
    )


def _truthy_context_value(value: Any) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes", "y", "on"}


def _falsey_context_value(value: Any) -> bool:
    if value is False:
        return True
    if value is None:
        return False
    return str(value).strip().lower() in {"false", "0", "no", "n", "off"}


def _is_child_navigation_context(context: Optional[Dict[str, Any]]) -> bool:
    context = context or {}
    evidence = " ".join(
        str(context.get(key) or "").lower()
        for key in (
            "action_type",
            "action_hint",
            "case_type",
            "expected_type",
            "label",
            "semantic_key",
        )
    )
    return any(
        token in evidence
        for token in (
            "child_navigation",
            "child_page",
            "child route",
            "child_route",
            "open_child",
            "open child",
            "popup_navigation",
            "popup_or_navigation",
            "subpage_navigation",
            "subpage",
            "sub page",
        )
    )


def _is_pdf_child_navigation_context(context: Optional[Dict[str, Any]]) -> bool:
    context = context or {}
    if not _is_child_navigation_context(context):
        return False
    expected = context.get("expected")
    evidence_values = [
        context.get("action_type"),
        context.get("action_hint"),
        context.get("case_type"),
        context.get("expected_type"),
        context.get("expected_value"),
        context.get("expected_url"),
        context.get("expected_page"),
        context.get("label"),
        context.get("semantic_key"),
        context.get("locator"),
        context.get("onclick"),
        context.get("href"),
    ]
    if isinstance(expected, dict):
        evidence_values.extend(expected.values())
    evidence = " ".join(str(value or "").lower() for value in evidence_values)
    return any(
        token in evidence
        for token in (
            "viewpdf",
            "pdf preview",
            "pdf child",
            "paper-pdf",
            "paper pdf",
            ".pdf",
        )
    )


def _expected_navigation_needles(context: Optional[Dict[str, Any]]) -> List[str]:
    context = context or {}
    expected = context.get("expected")
    needles: List[str] = []

    for key in (
        "expected_url",
        "expected_url_fragment",
        "target_url",
        "url_pattern",
        "expected_page",
        "target_page",
        "target_jsp",
    ):
        value = str(context.get(key) or "").strip()
        if value:
            needles.append(value)

    if isinstance(expected, dict):
        for key in (
            "url",
            "url_pattern",
            "expected_url",
            "page",
            "expected_page",
            "target_page",
            "value",
        ):
            value = str(expected.get(key) or "").strip()
            if value:
                needles.append(value)

    expected_type = str(context.get("expected_type") or "").lower()
    expected_value = str(context.get("expected_value") or "").strip()
    if expected_value and (
        _is_child_navigation_context(context)
        or any(token in expected_type for token in ("url", "page", "navigation", "route", "popup"))
    ):
        needles.append(expected_value)

    unique: List[str] = []
    seen = set()
    for needle in needles:
        normalized = re.sub(r"\s+", " ", needle).strip()
        if not normalized or normalized.lower() in {"same page", "same target page", "child page opens"}:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(normalized)
    return unique


def _navigation_url_candidates(result: Dict[str, Any]) -> List[str]:
    state = result.get("state") or {}
    candidates: List[str] = []
    for value in (
        result.get("popup_url"),
        result.get("after_url"),
        result.get("current_url"),
        state.get("url"),
    ):
        if value:
            candidates.append(str(value))
    target_frame = state.get("target_frame") if isinstance(state.get("target_frame"), dict) else {}
    if target_frame.get("url"):
        candidates.append(str(target_frame["url"]))
    for value in result.get("after_frame_urls") or []:
        if value:
            candidates.append(str(value))
    for value in result.get("popup_frame_urls") or []:
        if value:
            candidates.append(str(value))

    unique: List[str] = []
    seen = set()
    for candidate in candidates:
        key = candidate.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(key)
    return unique


def _url_needle_matches(needle: str, candidates: Iterable[str]) -> bool:
    needle_text = str(needle or "").strip()
    if not needle_text:
        return True
    needle_lower = needle_text.lower()
    needle_aliases = page_aliases(needle_text)
    needle_url = normalize_url_for_compare(needle_text).lower()

    for candidate in candidates:
        candidate_text = str(candidate or "").strip()
        if not candidate_text:
            continue
        candidate_lower = candidate_text.lower()
        if needle_lower in candidate_lower:
            return True
        candidate_url = normalize_url_for_compare(candidate_text).lower()
        if needle_url and (needle_url in candidate_url or candidate_url in needle_url):
            return True
        if needle_aliases and needle_aliases & page_aliases(candidate_text):
            return True
    return False


def _apply_navigation_expectation(result: Dict[str, Any], context: Optional[Dict[str, Any]]) -> None:
    child_navigation = _is_child_navigation_context(context)
    needles = _expected_navigation_needles(context)
    if not child_navigation and not needles:
        return

    candidates = _navigation_url_candidates(result)
    result["child_navigation"] = bool(child_navigation)
    result["navigation_url_candidates"] = candidates[:20]
    if needles:
        matches = {needle: _url_needle_matches(needle, candidates) for needle in needles}
        result["expected_navigation_needles"] = needles
        result["expected_navigation_matches"] = matches
        result["expected_navigation_match"] = all(matches.values())
        if result.get("status") == "PASS" and not result["expected_navigation_match"]:
            result.update(
                {
                    "status": "BLOCKED",
                    "reason": "Expected child navigation target was not reached: "
                    + ", ".join(needle for needle, matched in matches.items() if not matched),
                }
            )
    elif result.get("status") == "PASS" and not (
        result.get("popup_detected") or result.get("navigation_detected") or result.get("frame_changed")
    ):
        result.update(
            {
                "status": "BLOCKED",
                "reason": "Expected child navigation did not open a popup or change the page/frame.",
            }
        )


def _safe_page_url(page: Page) -> str:
    try:
        if _page_is_closed(page):
            return "about:closed"
        return page.url
    except Exception:
        return "about:closed"


def _safe_opener_page(page: Page) -> Optional[Page]:
    try:
        opener = page.opener()
    except Exception:
        return None
    if opener is None or _page_is_closed(opener):
        return None
    return opener


def _should_close_capture_page(capture_page: Page, action_page: Page, opener_page: Optional[Page], keep_popup: bool) -> bool:
    """Close only a child popup opened by the action, never the parent used for evidence."""
    return not keep_popup and capture_page is not action_page and capture_page is not opener_page


def _should_close_opened_popup_page(
    popup_page: Optional[Page],
    action_page: Page,
    opener_page: Optional[Page],
    *,
    keep_popup: bool,
    close_after: Any = None,
) -> bool:
    """Close the popup explicitly opened by this action after evidence capture."""
    if popup_page is None or keep_popup or _falsey_context_value(close_after):
        return False
    if popup_page is action_page or popup_page is opener_page:
        return False
    return not _page_is_closed(popup_page)


def _state_capture_page_after_child_navigation(
    capture_page: Page,
    action_page: Page,
    opener_page: Optional[Page],
    context: Optional[Dict[str, Any]],
) -> Tuple[Page, Optional[str]]:
    capture_parent = _truthy_context_value(
        (context or {}).get("capture_opener_after_child")
    ) or _is_pdf_child_navigation_context(context)
    if not capture_parent:
        return capture_page, None
    if capture_page is action_page:
        return capture_page, None
    if not _page_is_closed(action_page):
        scope = (
            "action_page_after_pdf_child_navigation"
            if _is_pdf_child_navigation_context(context)
            else "action_page_after_child_navigation"
        )
        return action_page, scope
    if opener_page is not None and not _page_is_closed(opener_page):
        scope = (
            "opener_after_pdf_child_navigation"
            if _is_pdf_child_navigation_context(context)
            else "opener_after_child_navigation"
        )
        return opener_page, scope
    return capture_page, None


def _safe_frame_urls(page: Page) -> List[str]:
    try:
        if _page_is_closed(page):
            return []
        return [str(frame.url or "") for frame in page.frames]
    except Exception:
        return []


def _normalize_transition_url(url: Any) -> str:
    value = str(url or "")
    return value[:-1] if value.endswith("#") else value


def _frame_urls_changed(before_urls: List[str], after_urls: List[str]) -> bool:
    return [
        _normalize_transition_url(url) for url in before_urls
    ] != [
        _normalize_transition_url(url) for url in after_urls
    ]


def _safe_context_pages(page: Page) -> List[Page]:
    try:
        return list(page.context.pages)
    except Exception:
        return []


def _pump_playwright_events(timeout_ms: int, *pages: Any) -> bool:
    candidates: List[Any] = []
    seen = set()

    def add_candidate(candidate: Any) -> None:
        if candidate is None or _page_is_closed(candidate):
            return
        key = id(candidate)
        if key in seen:
            return
        seen.add(key)
        candidates.append(candidate)

    for root_page in pages:
        for context_page in _safe_context_pages(root_page):
            add_candidate(context_page)
        add_candidate(root_page)

    for candidate in candidates:
        try:
            candidate.wait_for_timeout(timeout_ms)
            return True
        except Exception:
            continue
    time.sleep(max(0, timeout_ms) / 1000.0)
    return False


def _first_new_context_page(page: Page, pages_before: List[Page]) -> Optional[Page]:
    before_ids = {id(item) for item in pages_before}
    for candidate in _safe_context_pages(page):
        if id(candidate) in before_ids or _page_is_closed(candidate):
            continue
        return candidate
    return None


def execute_action(
    page: Page,
    action_type: str,
    selector: str,
    value: str = None,
    browser_name: str = "chrome",
    *,
    capture_dir: Optional[Union[str, Path]] = None,
    test_id: Optional[str] = None,
    timeout: int = 10000,
    action_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Execute Playwright UI actions with cross-browser tolerance.

    Returns a structured status and, when capture_dir is provided, an automatic
    post-action screenshot/state payload. Failures are converted to BLOCKED so
    higher-level regression flows can continue collecting evidence.
    """
    result: Dict[str, Any] = {
        "status": "PASS",
        "action_type": action_type,
        "semantic_action": infer_semantic_action(action_type, action_context),
        "selector": selector,
        "browser_name": browser_name,
    }
    action_dispatched = False
    capture_page = page
    keep_popup = _truthy_context_value((action_context or {}).get("keep_popup"))
    opener_page = _safe_opener_page(page)
    before_url = _safe_page_url(page)
    before_frame_urls = _safe_frame_urls(page)
    result["before_url"] = before_url
    result["before_frame_urls"] = before_frame_urls
    console_events: List[Dict[str, Any]] = []
    download_filename_hints: List[str] = []
    popup_page_to_close: Optional[Page] = None
    event_handlers: List[Tuple[str, Any]] = []
    page_event_handlers: List[Tuple[Any, str, Any]] = []
    context_event_handlers: List[Tuple[Any, str, Any]] = []
    temporary_routes: List[Tuple[str, Any]] = []
    cleanup_frame: Optional[Frame] = None
    cleanup_script = ""
    event_log_initialized = False

    def _record_event(event_type: str, details: Any, *, level: str = "info", url: str = "", status: Optional[int] = None):
        nonlocal event_log_initialized
        # Record Playwright events for diagnostics.
        event = {
            "time": time.strftime("%H:%M:%S"),
            "type": event_type,
            "level": level,
            "detail": str(details),
            "url": str(url or ""),
        }
        if status is not None:
            event["status"] = status
        console_events.append(event)
        test_id_str = _safe_name(test_id or "global")
        log_dir = Path(capture_dir) if capture_dir else Path("./output/logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        mode = "a" if event_log_initialized else "w"
        with open(log_dir / f"{test_id_str}_events.log", mode, encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {event_type}: {details}\n")
        event_log_initialized = True

    def _console_location(message: Any) -> str:
        try:
            location = message.location
        except Exception:
            location = {}
        if isinstance(location, dict):
            return str(location.get("url") or "")
        return ""

    def _on_console(message: Any):
        try:
            message_type = str(message.type)
        except Exception:
            message_type = "log"
        try:
            message_text = message.text
        except Exception as exc:
            message_text = f"<unable to read console message: {exc}>"
        _record_event("CONSOLE", f"{message_type}: {message_text}", level=message_type, url=_console_location(message))

    def _on_pageerror(error: Any):
        _record_event("JS_ERROR", str(error), level="pageerror")

    def _on_requestfailed(request: Any):
        try:
            failure = request.failure
        except Exception:
            failure = ""
        try:
            request_url = request.url
        except Exception:
            request_url = ""
        _record_event("REQ_FAILED", failure or request_url, level="requestfailed", url=request_url)

    def _on_response(response: Any):
        if result.get("semantic_action") == "download":
            filename_hint = _download_filename_hint_from_response(response)
            if filename_hint:
                download_filename_hints.append(filename_hint)
        try:
            status = int(response.status)
        except Exception:
            status = 0
        if status < 400:
            return
        try:
            response_url = response.url
        except Exception:
            response_url = ""
        _record_event("HTTP_ERROR", f"HTTP {status}", level="http_error", url=response_url, status=status)

    def _attach_event(event_name: str, handler: Any):
        page.on(event_name, handler)
        event_handlers.append((event_name, handler))

    def _attach_page_event(target_page: Any, event_name: str, handler: Any):
        if target_page is None or _page_is_closed(target_page):
            return
        target_page.on(event_name, handler)
        page_event_handlers.append((target_page, event_name, handler))

    def _attach_context_event(context: Any, event_name: str, handler: Any):
        context.on(event_name, handler)
        context_event_handlers.append((context, event_name, handler))

    try:
        # Attach runtime diagnostics.
        _attach_event("console", _on_console)
        _attach_event("pageerror", _on_pageerror)
        _attach_event("requestfailed", _on_requestfailed)
        _attach_event("response", _on_response)
        if _should_capture_browser_dialogs(action_context, result.get("semantic_action")):
            def _accept_dialog(dialog):
                dialog_action = _dialog_action_from_context(action_context)
                prompt_text = str((action_context or {}).get("prompt_text") or "")
                accept_status = _handle_dialog_safely(dialog, dialog_action, prompt_text=prompt_text)
                dialog_state = {
                    "type": getattr(dialog, "type", ""),
                    "message": getattr(dialog, "message", ""),
                    "handled_action": dialog_action,
                    "accept_status": accept_status,
                }
                result.setdefault("dialogs", []).append(dialog_state)
                _record_event("DIALOG", f"{dialog_state['type']}: {dialog_state['message']} ({accept_status})", level="info")

            _attach_event("dialog", _accept_dialog)

        if _page_is_closed(page):
            raise ValueError("Page is closed before action")

        result["wait_state"] = _wait_for_semantic_ready(page, timeout=max(timeout, 15000 if browser_name == "firefox" else timeout))
        semantic_action = result["semantic_action"]
        navigated_directly = False
        if semantic_action == "print":
            result["print_observer"] = _install_print_observer(page)

        if semantic_action in NEGATIVE_ACTIONS:
            action_dispatched = True
            result["negative_action"] = semantic_action
            result["status"] = "PASS"
            result["reason"] = f"Negative action injected: {semantic_action}"
            result.setdefault("negative_visual_evidence", []).extend(
                _inject_negative_visual_evidence(
                    page,
                    semantic_action,
                    detail="Preparing simulated negative scenario.",
                    phase="before trigger",
                )
            )

            if semantic_action == "negative_js_error":
                result.setdefault("negative_visual_evidence", []).extend(
                    _inject_negative_visual_evidence(
                        page,
                        semantic_action,
                        detail="console.error and runtime throw were injected.",
                        phase="error injected",
                    )
                )
                page.evaluate(
                    """() => {
                        console.error('MOONLIGHT_NEGATIVE: simulated console error before submit');
                        setTimeout(() => { throw new Error('MOONLIGHT_NEGATIVE: simulated JavaScript runtime error'); }, 0);
                    }"""
                )
                page.wait_for_timeout(300)
            elif semantic_action in {"negative_http_500", "negative_network_abort"}:
                pattern = _negative_url_pattern(action_context, value)

                is_http_500 = semantic_action == "negative_http_500"

                negative_label = (
                    "HTTP 500"
                    if is_http_500
                    else "network abort"
                )

                result["status"] = "PASS"
                result["negative_action"] = semantic_action
                result["negative_url_pattern"] = pattern
                result["reason"] = (
                    f"Negative {negative_label} route injected: {pattern}"
                )
                result.setdefault("negative_visual_evidence", []).extend(
                    _inject_negative_visual_evidence(
                        page,
                        semantic_action,
                        detail=f"{negative_label} route is active.",
                        url=pattern,
                        phase="route mocked",
                    )
                )

                if is_http_500:
                    def _mock_http_500(route):
                        route.fulfill(
                            status=500,
                            content_type="text/html; charset=utf-8",
                            body=(
                                "<html><body>"
                                "<h1>MOONLIGHT_NEGATIVE simulated HTTP 500</h1>"
                                "</body></html>"
                            ),
                        )

                    route_handler = _mock_http_500

                else:
                    def _mock_network_abort(route):
                        route.abort("failed")

                    route_handler = _mock_network_abort

                page.route(pattern, route_handler)
                temporary_routes.append((pattern, route_handler))

                trigger_url = (
                    str(action_context.get("trigger_url") or "")
                    if action_context
                    else ""
                ).strip()

                if not trigger_url:
                    trigger_url = (
                        pattern
                        .replace("**/", "")
                        .replace("**", "")
                        .replace("*", "")
                        .strip()
                    )

                if not trigger_url:
                    trigger_url = "/"

                page.evaluate(
                    """async ({ url, label }) => {
                        console.error(
                            "MOONLIGHT_NEGATIVE: triggering " + label + " request " + url
                        );

                        try {
                            await fetch(url, { cache: "no-store" });
                        } catch (e) {
                            console.error(
                                "MOONLIGHT_NEGATIVE: " + label + " fetch failed " + e.message
                            );
                        }
                    }""",
                    {
                        "url": trigger_url,
                        "label": negative_label,
                    },
                )

                page.wait_for_timeout(800)

            if selector and selector not in {"-", "__page__"}:
                frame, frame_state = _frame_for_selector(page, selector, timeout=min(timeout, 5000))
                result.update(frame_state)
                locator = frame.locator(selector).first
                locator.wait_for(state="attached", timeout=timeout)
                try:
                    locator.click(timeout=timeout)
                except PlaywrightError:
                    locator.evaluate(
                        """element => {
                            const tag = element.tagName && element.tagName.toLowerCase();
                            if (tag === 'form') {
                                if (element.requestSubmit) element.requestSubmit();
                                else element.submit();
                            } else {
                                element.click();
                            }
                        }"""
                    )
            else:
                result["selector_found"] = True
                if semantic_action in {"negative_http_500", "negative_network_abort"}:
                    page.evaluate(
                        """pattern => fetch(String(pattern).replace(/^\\*\\*\\//, '/').replace(/\\*$/, ''), { cache: 'no-store' }).catch(() => null)""",
                        result.get("negative_url_pattern") or "/",
                    )
            result.setdefault("negative_visual_evidence", []).extend(
                _inject_negative_visual_evidence(
                    page,
                    semantic_action,
                    detail="Negative scenario completed; screenshot should include this visual marker.",
                    url=result.get("negative_url_pattern") or "",
                    phase="after trigger",
                )
            )
            page.wait_for_timeout(800)
        elif semantic_action in ("goto", "navigate") and re.match(r"^https?://|^/", str(selector or "")):
            action_dispatched = True
            page.goto(selector, wait_until="domcontentloaded", timeout=max(timeout, 30000))
            navigated_directly = True
        elif semantic_action == "save_pdf":
            frame = page.main_frame
            result["target_frame"] = _frame_identity(frame)
            result["selector_found"] = True
        else:
            frame, frame_state = _frame_for_selector(page, selector, timeout=min(timeout, 5000))
            result.update(frame_state)

        if semantic_action in NEGATIVE_ACTIONS or navigated_directly:
            pass
        elif semantic_action in ("click", "navigate", "browser_dialog", "print"):
            # Check element visibility before dispatch.
            locator = frame.locator(selector).first
            action_dispatched = True
            setup_script = str((action_context or {}).get("setup_script") or "").strip()
            cleanup_script = str((action_context or {}).get("cleanup_script") or "").strip()
            if setup_script:
                frame.evaluate(setup_script)
                result["setup_script_executed"] = True
            if cleanup_script:
                cleanup_frame = frame

            def _manual_click_fallback() -> None:
                locator.wait_for(state="attached", timeout=timeout)
                try:
                    locator.click(timeout=timeout, force=True)
                    result["manual_click_fallback"] = "force_click"
                    return
                except PlaywrightError:
                    locator.evaluate(
                        """element => {
                            const options = { bubbles: true, cancelable: true, view: window };
                            element.dispatchEvent(new MouseEvent("mousedown", options));
                            element.dispatchEvent(new MouseEvent("mouseup", options));
                            if (typeof element.click === "function") element.click();
                            else element.dispatchEvent(new MouseEvent("click", options));
                        }"""
                    )
                    result["manual_click_fallback"] = "dom_mouse_events"

            try:
                locator.wait_for(state="visible", timeout=timeout)
                if _opens_popup_hint(action_context):
                    result["popup_expected"] = True
                    result["popup_wait_strategy"] = "context.expect_page"
                    popup_pages_before = _safe_context_pages(page)
                    popup: Optional[Page] = None
                    try:
                        with page.context.expect_page(timeout=min(max(timeout, 3000), 8000)) as popup_info:
                            locator.click(timeout=timeout)
                        popup = popup_info.value
                    except PlaywrightTimeoutError as exc:
                        result["popup_opened"] = False
                        result["popup_wait_error"] = str(exc)
                        try:
                            page.wait_for_timeout(800)
                        except PlaywrightError:
                            pass
                        popup = _first_new_context_page(page, popup_pages_before)

                    if popup is not None and not _page_is_closed(popup):
                        pdf_child_popup = _is_pdf_child_navigation_context(action_context)
                        if pdf_child_popup:
                            try:
                                popup.wait_for_timeout(500)
                            except PlaywrightError as exc:
                                result["popup_load_error"] = str(exc)
                            capture_page = page
                        else:
                            try:
                                popup.wait_for_load_state("domcontentloaded", timeout=min(timeout, 10000))
                            except PlaywrightError as exc:
                                result["popup_load_error"] = str(exc)
                            capture_page = popup
                        popup_page_to_close = popup
                        result["popup_opened"] = True
                        result["popup_url"] = popup.url
                        result["popup_frame_urls"] = _safe_frame_urls(popup)
                        if pdf_child_popup:
                            result["popup_capture_mode"] = "parent_after_pdf_child_navigation"
                        if keep_popup:
                            result["popup_page"] = popup
                else:
                    locator.click(timeout=timeout)
            except (PlaywrightTimeoutError, PlaywrightError):
                if not (action_context or {}).get("manual_replay"):
                    raise
                _manual_click_fallback()
            if semantic_action == "browser_dialog":
                page.wait_for_timeout(300)
                if not result.get("dialogs"):
                    result.update({"status": "BLOCKED", "reason": "Expected browser dialog was not observed."})
                else:
                    expected_dialog_message = _expected_dialog_message(action_context, value)
                    if expected_dialog_message and not any(
                        expected_dialog_message in str(dialog.get("message") or "")
                        for dialog in result.get("dialogs", [])
                    ):
                        result.update(
                            {
                                "status": "BLOCKED",
                                "reason": f"Expected browser dialog message was not observed: {expected_dialog_message}",
                            }
                        )
            elif semantic_action == "print":
                try:
                    capture_page.wait_for_timeout(500)
                except PlaywrightError:
                    pass
                print_state = _read_print_events(capture_page)
                result["print_events"] = print_state.get("events", [])
                result["print_errors"] = print_state.get("errors", [])
                result["print_invoked"] = bool(result["print_events"])
                if not result["print_invoked"]:
                    result.update({"status": "BLOCKED", "reason": "Print action did not call window.print()."})
        elif semantic_action == "fill":
            frame.locator(selector).first.wait_for(state="visible", timeout=timeout)
            frame.locator(selector).first.fill(value or "moonlight-semantic-sample", timeout=timeout)
        elif semantic_action == "assert_visible":
            if not selector or selector in {"-", "__page__"}:
                frame.locator("body").first.wait_for(state="visible", timeout=timeout)
                result["assertion"] = "page_body_visible"
            else:
                frame.locator(selector).first.wait_for(state="visible", timeout=timeout)
                result["assertion"] = "locator_visible"
        elif semantic_action == "assert_hidden":
            if not selector or selector in {"-", "__page__"}:
                result.update({"status": "BLOCKED", "reason": "assert_hidden requires a locator"})
            else:
                frame.locator(selector).first.wait_for(state="hidden", timeout=timeout)
                result["assertion"] = "locator_hidden"
        elif semantic_action == "assert_attached":
            if not selector or selector in {"-", "__page__"}:
                frame.locator("body").first.wait_for(state="attached", timeout=timeout)
                result["assertion"] = "page_body_attached"
            else:
                frame.locator(selector).first.wait_for(state="attached", timeout=timeout)
                result["assertion"] = "locator_attached"
        elif semantic_action == "assert_text":
            expected_text = str(
                value
                or (action_context or {}).get("expected_value")
                or (action_context or {}).get("text")
                or ""
            )
            if selector and selector not in {"-", "__page__"}:
                frame.locator(selector).first.wait_for(state="attached", timeout=timeout)
                actual_text = frame.locator(selector).first.inner_text(timeout=timeout)
            else:
                frame.locator("body").first.wait_for(state="attached", timeout=timeout)
                actual_text = frame.locator("body").first.inner_text(timeout=timeout)
            result["assertion"] = "text_contains"
            result["actual_text_sample"] = str(actual_text or "")[:1000]
            result["expected_text"] = expected_text
            if expected_text and expected_text not in str(actual_text or ""):
                result.update(
                    {
                        "status": "BLOCKED",
                        "reason": f"Expected text was not found: {expected_text}",
                    }
                )
        elif semantic_action == "assert_value":
            expected_value = str(
                value
                or (action_context or {}).get("expected_value")
                or (action_context or {}).get("text")
                or ""
            )
            locator = frame.locator(selector).first
            locator.wait_for(state="attached", timeout=timeout)
            actual_value = locator.evaluate(
                """element => {
                    const tag = (element.tagName || '').toLowerCase();
                    if (tag === 'select') {
                        return Array.from(element.selectedOptions || [])
                            .map(option => option.value || option.textContent || '')
                            .join('|');
                    }
                    if ('value' in element) return String(element.value || '');
                    return String(element.textContent || '');
                }"""
            )
            result["assertion"] = "value_contains"
            result["actual_value"] = str(actual_value or "")
            result["expected_value"] = expected_value
            if expected_value and expected_value not in str(actual_value or ""):
                result.update(
                    {
                        "status": "BLOCKED",
                        "reason": f"Expected value was not found: {expected_value}",
                    }
                )
        elif semantic_action in {"assert_enabled", "assert_disabled", "assert_checked", "assert_unchecked"}:
            if not selector or selector in {"-", "__page__"}:
                result.update({"status": "BLOCKED", "reason": f"{semantic_action} requires a locator"})
            else:
                locator = frame.locator(selector)
                locator.first.wait_for(state="attached", timeout=timeout)
                states = locator.evaluate_all(
                    """elements => elements.map(element => {
                        const tag = (element.tagName || '').toLowerCase();
                        const disabled = !!element.disabled || element.getAttribute('aria-disabled') === 'true';
                        const checked = !!element.checked;
                        const value = 'value' in element ? String(element.value || '') : '';
                        const text = String(element.textContent || '').replace(/\\s+/g, ' ').trim();
                        return { tag, disabled, checked, value, text };
                    })"""
                )
                result["assertion"] = semantic_action
                result["element_states"] = states
                if not states:
                    result.update({"status": "BLOCKED", "reason": f"No elements matched: {selector}"})
                elif semantic_action == "assert_enabled" and not all(not state.get("disabled") for state in states):
                    result.update({"status": "BLOCKED", "reason": f"Expected all matched elements to be enabled: {states}"})
                elif semantic_action == "assert_disabled" and not all(state.get("disabled") for state in states):
                    result.update({"status": "BLOCKED", "reason": f"Expected all matched elements to be disabled: {states}"})
                elif semantic_action == "assert_checked" and not all(state.get("checked") for state in states):
                    result.update({"status": "BLOCKED", "reason": f"Expected all matched elements to be checked: {states}"})
                elif semantic_action == "assert_unchecked" and not all(not state.get("checked") for state in states):
                    result.update({"status": "BLOCKED", "reason": f"Expected all matched elements to be unchecked: {states}"})
        elif semantic_action == "assert_url":
            expected_url = str(
                value
                or (action_context or {}).get("expected_value")
                or (action_context or {}).get("url_pattern")
                or ""
            )
            current_url = _safe_page_url(page)
            result["assertion"] = "url_contains"
            result["current_url"] = current_url
            result["expected_url"] = expected_url
            if expected_url and expected_url not in current_url:
                result.update(
                    {
                        "status": "BLOCKED",
                        "reason": f"Expected URL fragment was not found: {expected_url}",
                    }
                )
        elif semantic_action == "clear":
            frame.locator(selector).first.wait_for(state="visible", timeout=timeout)
            frame.locator(selector).first.fill("", timeout=timeout)
        elif semantic_action == "set_value":
            locator = frame.locator(selector).first
            locator.wait_for(state="attached", timeout=timeout)
            locator.evaluate(
                """(element, nextValue) => {
                    element.value = nextValue || "";
                    element.dispatchEvent(new Event("input", { bubbles: true }));
                    element.dispatchEvent(new Event("change", { bubbles: true }));
                }""",
                value or "moonlight-hidden-auto",
            )
        elif semantic_action in ("check", "uncheck"):
            locator = frame.locator(selector).first
            locator.wait_for(state="attached", timeout=timeout)
            desired = semantic_action == "check"
            action_dispatched = True
            try:
                locator.set_checked(desired, timeout=timeout, force=True)
                result["check_dispatch"] = "set_checked"
            except PlaywrightError:
                locator.evaluate(
                    """(element, checked) => {
                        const current = !!element.checked;
                        if (current !== checked) {
                            const options = { bubbles: true, cancelable: true, view: window };
                            element.dispatchEvent(new MouseEvent("mousedown", options));
                            element.dispatchEvent(new MouseEvent("mouseup", options));
                            if (typeof element.click === "function") element.click();
                            else element.dispatchEvent(new MouseEvent("click", options));
                        }
                        element.checked = checked;
                        element.dispatchEvent(new Event("input", { bubbles: true }));
                        element.dispatchEvent(new Event("change", { bubbles: true }));
                    }""",
                    desired,
                )
                result["check_dispatch"] = "dom_checked_events"
            result["checked"] = desired
        elif semantic_action == "press":
            key = value or "Enter"
            if selector and selector not in {"-", "__page__"}:
                frame.locator(selector).first.wait_for(state="attached", timeout=timeout)
                frame.locator(selector).first.focus(timeout=timeout)
            page.keyboard.press(key, timeout=timeout)
        elif semantic_action == "select":
            locator = frame.locator(selector).first
            locator.wait_for(state="visible", timeout=timeout)
            if value is None or str(value) == "":
                selected_value = locator.evaluate(
                    """element => {
                        const options = Array.from(element.options || []).filter(option => !option.disabled);
                        return (options.find(option => option.value) || options[0] || {}).value || "";
                    }"""
                )
            else:
                selected_value = value
            try:
                locator.select_option(str(selected_value), timeout=timeout)
                result["select_match"] = "value"
            except (PlaywrightTimeoutError, PlaywrightError) as value_exc:
                try:
                    locator.select_option(label=str(selected_value), timeout=timeout)
                    result["select_match"] = "label"
                except (PlaywrightTimeoutError, PlaywrightError):
                    if str(selected_value).isdigit():
                        locator.select_option(index=int(str(selected_value)), timeout=timeout)
                        result["select_match"] = "index"
                    else:
                        raise value_exc
            locator.dispatch_event("change", timeout=timeout)
            result["selected_value"] = selected_value
        elif semantic_action == "manual_assert":
            result["assertion"] = "manual_boundary"
            result["manual_assert_value"] = value or (action_context or {}).get("expected_value") or ""
            result["status"] = "BLOCKED"
            result["reason"] = "Manual assertion requires manual review."
        elif semantic_action == "upload":
            resolved_selector, upload_locator_state = _resolve_upload_locator(frame, selector)
            result["upload_locator_state"] = upload_locator_state
            frame.locator(resolved_selector).first.wait_for(state="attached", timeout=timeout)
            upload_file = _resolve_upload_file_value(value, capture_dir)
            frame.locator(resolved_selector).first.set_input_files(upload_file, timeout=timeout)
            result["upload_file"] = upload_file
            result["resolved_selector"] = resolved_selector
        elif semantic_action == "submit":
            locator = frame.locator(selector).first
            locator.wait_for(state="attached", timeout=timeout)
            action_dispatched = True
            result["submit_dispatch"] = locator.evaluate(
                """element => {
                    const tag = element.tagName && element.tagName.toLowerCase();
                    if (tag !== "form") {
                        element.click();
                        return "clicked_element";
                    }
                    const form = element;
                    if (form && form.requestSubmit) form.requestSubmit();
                    else if (form) form.submit();
                    return "submitted_form";
                }"""
            )
        elif semantic_action == "download":
            action_dispatched = True
            click_closed_error: Optional[BaseException] = None
            download_events: List[Any] = []
            download_timeout = _download_timeout_ms(action_context)
            download_stability_ms = _download_stability_ms(action_context)
            download_uuid_fallback_ms = _download_uuid_fallback_ms(action_context)
            download_watch_dirs = _download_watch_dirs(action_context)
            primary_download_dir = download_watch_dirs[0] if download_watch_dirs else _configured_download_dir()
            download_started_at = time.time()
            download_snapshot = _download_dir_snapshot(download_watch_dirs)
            stable_download_seen: Dict[str, Tuple[int, float]] = {}
            download_listener_pages = set()
            cdp_downloads: Dict[str, Dict[str, Any]] = {}
            cdp_download_sessions: List[Any] = []

            def _on_download(download: Any) -> None:
                download_events.append(download)

            def _attach_download_listener(target_page: Any) -> None:
                if target_page is None or _page_is_closed(target_page):
                    return
                key = id(target_page)
                if key in download_listener_pages:
                    return
                download_listener_pages.add(key)
                _attach_page_event(target_page, "download", _on_download)

            def _on_download_page(new_page: Any) -> None:
                _attach_download_listener(new_page)
                behavior, sessions = _attach_cdp_download_observer(
                    new_page,
                    primary_download_dir,
                    on_will_begin=_on_cdp_download_will_begin,
                    on_progress=_on_cdp_download_progress,
                )
                cdp_download_sessions.extend(sessions)
                result.setdefault("download_behavior_page_events", []).append(behavior)

            def _on_cdp_download_will_begin(params: Any) -> None:
                if not isinstance(params, dict):
                    return
                guid = str(params.get("guid") or "").strip()
                if not guid:
                    return
                suggested_filename = _original_download_filename(str(params.get("suggestedFilename") or ""))
                cdp_downloads.setdefault(guid, {}).update(
                    {
                        "guid": guid,
                        "url": str(params.get("url") or ""),
                        "suggested_filename": suggested_filename,
                        "state": "willBegin",
                    }
                )
                if suggested_filename and suggested_filename != "download":
                    download_filename_hints.append(suggested_filename)
                result["cdp_downloads"] = list(cdp_downloads.values())

            def _on_cdp_download_progress(params: Any) -> None:
                if not isinstance(params, dict):
                    return
                guid = str(params.get("guid") or "").strip()
                if not guid:
                    return
                cdp_downloads.setdefault(guid, {"guid": guid}).update(
                    {
                        "state": str(params.get("state") or ""),
                        "received_bytes": params.get("receivedBytes"),
                        "total_bytes": params.get("totalBytes"),
                    }
                )
                if str(params.get("state") or "").lower() == "completed":
                    cdp_downloads.setdefault(guid, {"guid": guid}).setdefault("completed_at", time.time())
                result["cdp_downloads"] = list(cdp_downloads.values())

            try:
                behavior, sessions = _attach_cdp_download_observer(
                    page,
                    primary_download_dir,
                    on_will_begin=_on_cdp_download_will_begin,
                    on_progress=_on_cdp_download_progress,
                )
                result["download_behavior"] = behavior
                cdp_download_sessions.extend(sessions)
                for candidate_page in [page, opener_page, *_safe_context_pages(page)]:
                    _attach_download_listener(candidate_page)
                _attach_context_event(page.context, "page", _on_download_page)
                _attach_context_event(page.context, "download", _on_download)
                locator = frame.locator(selector).first
                locator.wait_for(state="attached", timeout=timeout)
                expect_download_timeout = _download_expect_timeout_ms(
                    action_context,
                    action_timeout_ms=timeout,
                    download_timeout_ms=download_timeout,
                )
                result["download_expect_timeout_ms"] = expect_download_timeout
                try:
                    with page.expect_download(timeout=expect_download_timeout) as download_info:
                        locator.click(timeout=timeout)
                    download_events.append(download_info.value)
                    result["download_wait_strategy"] = "page.expect_download"
                except PlaywrightTimeoutError as click_exc:
                    result["download_wait_strategy"] = "event_or_filesystem_after_expect_timeout"
                    result["download_expect_timeout"] = str(click_exc)
                except (PlaywrightTimeoutError, PlaywrightError) as click_exc:
                    if _is_target_closed_error(click_exc) or _page_is_closed(page):
                        click_closed_error = click_exc
                    else:
                        raise

                result["download_wait_timeout_ms"] = download_timeout
                result["download_watch_dirs"] = [str(path) for path in download_watch_dirs]
                result["download_stability_ms"] = download_stability_ms
                result["download_uuid_fallback_ms"] = download_uuid_fallback_ms
                deadline = time.time() + (download_timeout / 1000.0)
                while time.time() < deadline:
                    if download_events:
                        _record_download_result(
                            result,
                            download_events[0],
                            capture_dir=capture_dir,
                            test_id=test_id,
                            browser_name=browser_name,
                            suggested_filename=download_filename_hints[-1] if download_filename_hints else None,
                        )
                        if click_closed_error or _page_is_closed(page):
                            result["page_closed_after_action"] = True
                            result["download_closed_page"] = True
                            result["reason"] = "Download event captured before/while the download window closed."
                        break
                    cdp_download_file, matched_cdp_download = _completed_cdp_download_file_from_dirs(
                        cdp_downloads,
                        download_watch_dirs,
                        download_snapshot,
                        started_at=download_started_at,
                    )
                    if cdp_download_file and matched_cdp_download:
                        suggested_filename = str(matched_cdp_download.get("suggested_filename") or "")
                        _record_filesystem_download_result(
                            result,
                            cdp_download_file,
                            capture_dir=capture_dir,
                            test_id=test_id,
                            browser_name=browser_name,
                            suggested_filename=suggested_filename,
                        )
                        result["download_filename_hint_source"] = "cdp"
                        result["download_cdp_guid"] = str(matched_cdp_download.get("guid") or "")
                        result["download_cdp_file_path"] = str(cdp_download_file)
                        result["download_received_bytes"] = matched_cdp_download.get("received_bytes")
                        result["download_total_bytes"] = matched_cdp_download.get("total_bytes")
                        if click_closed_error or _page_is_closed(page):
                            result["page_closed_after_action"] = True
                            result["download_closed_page"] = True
                        result["reason"] = "CDP completed download file appeared in the configured download directory."
                        break
                    completed_cdp_guids = [
                        str(item.get("guid") or "")
                        for item in cdp_downloads.values()
                        if str(item.get("state") or "").lower() == "completed"
                        and str(item.get("suggested_filename") or "").strip()
                    ]
                    uuid_fallback_ready = (
                        download_uuid_fallback_ms >= 0
                        and (time.time() - download_started_at) * 1000 >= download_uuid_fallback_ms
                    )
                    filesystem_download = _stable_download_from_dirs(
                        download_watch_dirs,
                        download_snapshot,
                        stable_download_seen,
                        started_at=download_started_at,
                        stability_ms=download_stability_ms,
                        allow_temp_names=completed_cdp_guids,
                        allow_browser_uuid_names=uuid_fallback_ready,
                    )
                    if filesystem_download:
                        suggested_filename = download_filename_hints[-1] if download_filename_hints else ""
                        filename_hint_source = "response" if suggested_filename else ""
                        if not suggested_filename:
                            matched_cdp = cdp_downloads.get(filesystem_download.name)
                            if matched_cdp:
                                suggested_filename = str(matched_cdp.get("suggested_filename") or "")
                                filename_hint_source = "cdp"
                        if _looks_like_browser_temp_download_name(filesystem_download.name) and not suggested_filename:
                            for _ in range(4):
                                _pump_playwright_events(250, page, opener_page)
                                result["cdp_downloads"] = list(cdp_downloads.values())
                                matched_cdp = cdp_downloads.get(filesystem_download.name)
                                if matched_cdp:
                                    suggested_filename = str(matched_cdp.get("suggested_filename") or "")
                                    filename_hint_source = "cdp"
                                if suggested_filename:
                                    break
                        _record_filesystem_download_result(
                            result,
                            filesystem_download,
                            capture_dir=capture_dir,
                            test_id=test_id,
                            browser_name=browser_name,
                            suggested_filename=suggested_filename,
                        )
                        if filename_hint_source:
                            result["download_filename_hint_source"] = filename_hint_source
                        if click_closed_error or _page_is_closed(page):
                            result["page_closed_after_action"] = True
                            result["download_closed_page"] = True
                        result["reason"] = "Download file appeared in the configured download directory."
                        break
                    try:
                        cdp_missing_grace_ms = max(1000, int(os.getenv("DOWNLOAD_CDP_FILE_GRACE_MS") or "5000"))
                    except ValueError:
                        cdp_missing_grace_ms = 5000
                    missing_completed_cdp = [
                        item
                        for item in cdp_downloads.values()
                        if str(item.get("state") or "").lower() == "completed"
                        and str(item.get("suggested_filename") or "").strip()
                        and time.time() - float(item.get("completed_at") or time.time()) >= cdp_missing_grace_ms / 1000.0
                    ]
                    if missing_completed_cdp:
                        latest = missing_completed_cdp[-1]
                        result.update(
                            {
                                "status": "BLOCKED",
                                "download_event_missing": True,
                                "download_cdp_completed_without_file": True,
                                "download_suggested_filename": str(latest.get("suggested_filename") or ""),
                                "download_filename": str(latest.get("suggested_filename") or ""),
                                "download_received_bytes": latest.get("received_bytes"),
                                "download_total_bytes": latest.get("total_bytes"),
                                "reason": (
                                    "CDP reported the download completed, but no file appeared in the configured "
                                    f"download directory: {primary_download_dir}"
                                ),
                            }
                        )
                        break
                    if result.get("dialogs"):
                        messages = " | ".join(
                            str(dialog.get("message") or dialog.get("type") or "")
                            for dialog in result.get("dialogs", [])
                            if str(dialog.get("message") or dialog.get("type") or "").strip()
                        )
                        result.update(
                            {
                                "status": "BLOCKED",
                                "download_dialog_blocked": True,
                                "download_event_missing": True,
                                "reason": (
                                    "Download action showed a browser dialog instead of a download"
                                    + (f": {messages}" if messages else ".")
                                ),
                            }
                        )
                        break
                    if click_closed_error or _page_is_closed(page):
                        result["page_closed_after_action"] = True
                        result["download_closed_page"] = True
                    _pump_playwright_events(250, page, opener_page)
                else:
                    result.update(
                        {
                            "status": "BLOCKED",
                            "download_event_missing": True,
                            "reason": f"Download blocked or failed: timeout {download_timeout}ms waiting for download event",
                        }
                    )
            except (PlaywrightTimeoutError, PlaywrightError) as e:
                if click_closed_error or _is_target_closed_error(e) or _page_is_closed(page):
                    result.update(
                        {
                            "status": "BLOCKED",
                            "page_closed_after_action": True,
                            "download_closed_page": True,
                            "download_event_missing": True,
                            "reason": "Download action closed the page before Playwright exposed a download event.",
                            "post_wait_state": {"page_closed": True, "settle_error": str(e)},
                        }
                    )
                elif result.get("dialogs"):
                    messages = " | ".join(
                        str(dialog.get("message") or dialog.get("type") or "")
                        for dialog in result.get("dialogs", [])
                        if str(dialog.get("message") or dialog.get("type") or "").strip()
                    )
                    result.update(
                        {
                            "status": "BLOCKED",
                            "download_dialog_blocked": True,
                            "download_event_missing": True,
                            "reason": (
                                "Download action showed a browser dialog instead of a download"
                                + (f": {messages}" if messages else ".")
                            ),
                        }
                    )
                else:
                    result.update({"status": "BLOCKED", "reason": f"Download blocked or failed: {e}"})
            finally:
                for session in cdp_download_sessions:
                    try:
                        session.detach()
                    except Exception:
                        pass
        elif semantic_action == "save_pdf":
            action_dispatched = True
            pdf_dir = Path(capture_dir or ".") / "pdf"
            pdf_dir.mkdir(parents=True, exist_ok=True)
            pdf_name_source = value or (action_context or {}).get("value") or test_id or "print_page"
            pdf_name_stem = Path(str(pdf_name_source)).stem or str(pdf_name_source)
            pdf_path = pdf_dir / f"{_safe_name(pdf_name_stem)}_{_safe_name(browser_name)}.pdf"
            result["pdf_path"] = str(pdf_path)
            pdf_error: Optional[Exception] = None
            try:
                result["pdf_backend"] = "playwright_page_pdf"
                page.pdf(path=str(pdf_path), print_background=True, prefer_css_page_size=True)
            except Exception as exc:
                pdf_error = exc
                try:
                    result["pdf_backend"] = "cdp_print_to_pdf"
                    session = page.context.new_cdp_session(page)
                    pdf_payload = session.send(
                        "Page.printToPDF",
                        {
                            "printBackground": True,
                            "preferCSSPageSize": True,
                        },
                    )
                    pdf_path.write_bytes(base64.b64decode(str(pdf_payload.get("data") or "")))
                except Exception as cdp_exc:
                    result.update(
                        {
                            "status": "BLOCKED",
                            "pdf_backend": "unavailable",
                            "reason": (
                                "PDF save is not available through Playwright page.pdf or CDP Page.printToPDF: "
                                f"page.pdf={pdf_error}; cdp={cdp_exc}"
                            ),
                        }
                    )
            if result.get("status") == "PASS":
                if not pdf_path.exists():
                    result.update({"status": "BLOCKED", "reason": f"PDF file was not created: {pdf_path}"})
                else:
                    result["pdf_size"] = pdf_path.stat().st_size
                    result["pdf_filename"] = pdf_path.name
                    if int(result["pdf_size"]) <= 0:
                        result.update({"status": "BLOCKED", "reason": f"PDF file is empty: {pdf_path}"})
        elif semantic_action == "wait":
            if not selector or selector in {"-", "__page__"}:
                frame.locator("body").first.wait_for(state="visible", timeout=20000)
                result["wait_target"] = "page_body"
            else:
                frame.locator(selector).first.wait_for(timeout=20000)
        else:
            result.update({"status": "BLOCKED", "reason": f"Unsupported semantic action: {semantic_action}"})

        if result["status"] == "PASS":
            ready_page = capture_page if not _page_is_closed(capture_page) else page
            result["post_wait_state"] = _wait_for_semantic_ready(ready_page, timeout=min(timeout, 15000))
            if result["post_wait_state"].get("page_closed"):
                result["page_closed_after_action"] = True
    except (PlaywrightTimeoutError, PlaywrightError, ValueError) as exc:
        closes_page = result.get("semantic_action") in ("click", "navigate", "submit", "goto", "download", "browser_dialog", "print")
        if action_dispatched and closes_page and (_is_target_closed_error(exc) or _page_is_closed(page)):
            result.update(
                {
                    "status": "PASS",
                    "page_closed_after_action": True,
                    "post_wait_state": {"page_closed": True, "settle_error": str(exc)},
                }
            )
        else:
            result.update({"status": "BLOCKED", "reason": str(exc)})
    finally:
        if cleanup_frame is not None and cleanup_script:
            try:
                cleanup_frame.evaluate(cleanup_script)
                result["cleanup_script_executed"] = True
            except PlaywrightError as exc:
                result["cleanup_script_error"] = str(exc)
        action_page_closed = bool(result.get("page_closed_after_action")) or _page_is_closed(capture_page)
        if action_page_closed and capture_page is page and opener_page is not None:
            capture_page = opener_page
            result["capture_scope"] = "opener_after_popup_close"
            result["opener_url"] = _safe_page_url(opener_page)
        after_url = _safe_page_url(capture_page)
        after_frame_urls = _safe_frame_urls(capture_page)
        page_closed_after = action_page_closed or _page_is_closed(capture_page)
        popup_detected = bool(result.get("popup_opened"))
        navigation_detected = _normalize_transition_url(before_url) != _normalize_transition_url(after_url)
        frame_changed = _frame_urls_changed(before_frame_urls, after_frame_urls)
        result.update(
            {
                "after_url": after_url,
                "after_frame_urls": after_frame_urls,
                "navigation_detected": navigation_detected,
                "popup_detected": popup_detected,
                "frame_changed": frame_changed,
                "validation_only": bool(
                    result.get("status") == "PASS"
                    and not navigation_detected
                    and not popup_detected
                    and not frame_changed
                    and not page_closed_after
                ),
            }
        )
        result["console_events"] = console_events
        result["console_error_count"] = sum(
            1
            for event in console_events
            if str(event.get("level") or "").lower() in {"error", "pageerror"}
            or str(event.get("type") or "").upper() == "JS_ERROR"
        )
        result["http_error_count"] = sum(1 for event in console_events if str(event.get("type") or "").upper() == "HTTP_ERROR")
        result["request_failed_count"] = sum(1 for event in console_events if str(event.get("type") or "").upper() == "REQ_FAILED")
        if capture_dir:
            name = _safe_name(test_id or f"{action_type}_{int(time.time() * 1000)}")
            _apply_navigation_expectation(result, action_context)
            state_capture_page, state_capture_scope = _state_capture_page_after_child_navigation(
                capture_page,
                page,
                opener_page,
                action_context,
            )
            if state_capture_scope:
                result["state_capture_scope"] = state_capture_scope
                result["state_capture_original_url"] = after_url
            result["state"] = _capture_state(state_capture_page, Path(capture_dir), name)
            _apply_navigation_expectation(result, action_context)
            if console_events or _needs_console_evidence(action_context, action_type):
                result["console_evidence_screenshot"] = _render_console_evidence_image(
                    console_events,
                    Path(capture_dir),
                    name,
                )
            close_target = popup_page_to_close or capture_page
            should_close_popup = _should_close_opened_popup_page(
                popup_page_to_close,
                page,
                opener_page,
                keep_popup=keep_popup,
                close_after=(action_context or {}).get("close_after"),
            )
            should_close_capture = (
                popup_page_to_close is None
                and _should_close_capture_page(capture_page, page, opener_page, keep_popup)
            )
            result["popup_close_attempted"] = bool(should_close_popup or should_close_capture)
            if should_close_popup or should_close_capture:
                try:
                    close_target.close()
                    result["popup_close_status"] = "PASS"
                    result["popup_closed_after_capture"] = True
                except PlaywrightError as exc:
                    result["popup_close_status"] = "BLOCKED"
                    result["popup_close_error"] = str(exc)
        if result.get("semantic_action") in NEGATIVE_ACTIONS:
            _clear_negative_visual_evidence(page)
        for event_name, handler in event_handlers:
            try:
                page.remove_listener(event_name, handler)
            except Exception:
                pass
        for target_page, event_name, handler in page_event_handlers:
            try:
                target_page.remove_listener(event_name, handler)
            except Exception:
                pass
        for context, event_name, handler in context_event_handlers:
            try:
                context.remove_listener(event_name, handler)
            except Exception:
                pass
        for pattern, handler in temporary_routes:
            try:
                page.unroute(pattern, handler)
            except Exception:
                pass

    return result


def _capture_state(page: Page, output_dir: Path, name: str) -> Dict[str, Any]:
    if _page_is_closed(page):
        return _closed_page_state(output_dir, name)

    output_dir.mkdir(parents=True, exist_ok=True)
    screenshot = output_dir / f"{name}.png"
    state: Dict[str, Any] = {
        "screenshot": str(screenshot),
        "popup_window_restore": restore_popup_window_state(page),
    }
    try:
        target_frame, diagnostics = _find_business_frame(page, timeout=3000)
    except PlaywrightError as exc:
        if _is_target_closed_error(exc) or _page_is_closed(page):
            state = _closed_page_state(output_dir, name)
            state["capture_error"] = str(exc)
            return state
        raise
    state["target_frame"] = _frame_identity(target_frame)
    state["frame_candidates"] = diagnostics[:8]
    state["window_metrics"] = capture_window_metrics(page)
    browser_screen = _capture_browser_screen(page, screenshot)
    if browser_screen.get("ok"):
        state.update({key: value for key, value in browser_screen.items() if key != "ok"})
    else:
        state["browser_screen_error"] = browser_screen.get("reason")
        try:
            page.screenshot(path=str(screenshot), full_page=True, timeout=15000)
            state["screenshot_scope"] = "page_full"
            state["includes_browser_chrome"] = False
        except (PlaywrightTimeoutError, PlaywrightError) as exc:
            state["screenshot_error"] = str(exc)
            try:
                target_frame.locator("body").screenshot(path=str(screenshot), timeout=15000)
                state["screenshot_fallback"] = "target_frame_body"
                state["includes_browser_chrome"] = False
            except (PlaywrightTimeoutError, PlaywrightError) as fallback_exc:
                state["screenshot_fallback_error"] = str(fallback_exc)

    for key, getter in (
        ("url", lambda: target_frame.url or page.url),
        ("title", lambda: target_frame.evaluate("() => document.title") or page.title()),
        ("text", lambda: target_frame.locator("body").inner_text(timeout=10000)),
        ("dom", lambda: target_frame.content()),
    ):
        try:
            state[key] = getter()
        except (PlaywrightTimeoutError, PlaywrightError) as exc:
            state[f"{key}_error"] = str(exc)
            state[key] = ""
    return state


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


DYNAMIC_URL_QUERY_KEYS = {
    "userId",
    "userid",
    "sessionId",
    "sessionid",
    "JSESSIONID",
    "jsessionid",
    "token",
    "csrf",
    "_csrf",
    "_",
    "timestamp",
    "ts",
    "r",
}


def normalize_url_for_compare(url: str) -> str:
    """
    Normalize Legacy/New URLs for migration comparison.

    We intentionally ignore scheme/host because Legacy and New run on different
    servers. We also ignore dynamic query parameters such as encrypted userId.
    """
    if not url:
        return ""

    parsed = urlparse(str(url))
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key not in DYNAMIC_URL_QUERY_KEYS
    ]

    return urlunparse(
        (
            "",
            "",
            parsed.path,
            "",
            urlencode(query, doseq=True),
            "",
        )
    )


COMPARE_POLICY = {
    "page_snapshot": {
        "url_required": True,
        "title_required": False,
        "text_required": False,
        "dom_required": False,
        "visual_required": True,
    },
    "wait": {
        "url_required": True,
        "title_required": False,
        "text_required": False,
        "dom_required": False,
        "visual_required": True,
    },
    "click": {
        "url_required": False,
        "title_required": False,
        "text_required": False,
        "dom_required": False,
        "visual_required": True,
    },
    "submit": {
        "url_required": False,
        "title_required": False,
        "text_required": False,
        "dom_required": False,
        "visual_required": True,
    },
    "upload": {
        "url_required": False,
        "title_required": False,
        "text_required": False,
        "dom_required": False,
        "visual_required": True,
    },
    "download": {
        "url_required": False,
        "title_required": False,
        "text_required": False,
        "dom_required": False,
        "visual_required": False,
    },
    "browser_dialog": {
        "url_required": False,
        "title_required": False,
        "text_required": False,
        "dom_required": False,
        "visual_required": False,
    },
    "print": {
        "url_required": False,
        "title_required": False,
        "text_required": False,
        "dom_required": False,
        "visual_required": False,
    },
    "save_pdf": {
        "url_required": False,
        "title_required": False,
        "text_required": False,
        "dom_required": False,
        "visual_required": False,
    },
    "navigate": {
        "url_required": True,
        "title_required": False,
        "text_required": False,
        "dom_required": False,
        "visual_required": True,
    },
    "close_window": {
        "url_required": False,
        "title_required": False,
        "text_required": False,
        "dom_required": False,
        "visual_required": False,
    },
}


def compare_captured_state(
    legacy_state: Dict[str, Any],
    new_state: Dict[str, Any],
    *,
    action_type: str = "wait",
    visual_status: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Compare stable page data captured after the same action on Legacy and New.

    Important migration-testing rule:
    - Full DOM equality is usually too strict for Struts -> Spring migration.
    - Dynamic URL query values such as encrypted userId must not fail the case.
    - If visual comparison passes and DOM is not required, return WARN instead
      of DIFF so pytest does not fail on harmless implementation differences.
    """
    policy = COMPARE_POLICY.get(action_type, COMPARE_POLICY["click"])

    legacy_url_normalized = normalize_url_for_compare(legacy_state.get("url", ""))
    new_url_normalized = normalize_url_for_compare(new_state.get("url", ""))
    url_match = legacy_url_normalized == new_url_normalized

    title_match = legacy_state.get("title") == new_state.get("title")

    legacy_text = _normalize_text(legacy_state.get("text", ""))
    new_text = _normalize_text(new_state.get("text", ""))
    text_match = legacy_text == new_text

    legacy_dom = _normalize_text(legacy_state.get("dom", ""))
    new_dom = _normalize_text(new_state.get("dom", ""))
    dom_match = legacy_dom == new_dom

    hard_failures: List[str] = []
    warnings: List[str] = []

    if policy.get("url_required") and not url_match:
        hard_failures.append("URL differs")
    elif not url_match:
        warnings.append("URL differs")

    if policy.get("title_required") and not title_match:
        hard_failures.append("Title differs")
    elif not title_match:
        warnings.append("Title differs")

    if policy.get("text_required") and not text_match:
        hard_failures.append("Text differs")
    elif not text_match:
        warnings.append("Text differs")

    if policy.get("dom_required") and not dom_match:
        hard_failures.append("DOM differs")
    elif not dom_match:
        warnings.append("DOM differs")

    if visual_status and policy.get("visual_required") and visual_status != "PASS":
        hard_failures.append(f"Visual comparison {visual_status}")

    if hard_failures:
        status = "DIFF"
    elif warnings:
        status = "WARN"
    else:
        status = "PASS"

    return {
        "status": status,
        "url_match": url_match,
        "url_required": policy.get("url_required", False),
        "legacy_url_normalized": legacy_url_normalized,
        "new_url_normalized": new_url_normalized,
        "title_match": title_match,
        "title_required": policy.get("title_required", False),
        "text_match": text_match,
        "text_required": policy.get("text_required", False),
        "dom_match": dom_match,
        "dom_required": policy.get("dom_required", False),
        "dom_warning": "DOM differs; visual comparison may still pass" if not dom_match and not policy.get("dom_required") else None,
        "warnings": warnings,
        "hard_failures": hard_failures,
        "legacy_screenshot": legacy_state.get("screenshot"),
        "new_screenshot": new_state.get("screenshot"),
    }


def build_steps_from_page_mapping(
    mapping: Dict[str, Any],
    *,
    risk_levels: Optional[Iterable[str]] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    risks = list(risk_levels or ["High", "Medium"])
    selected_risks = {risk.lower() for risk in risks}
    risk_rank = {risk.lower(): index for index, risk in enumerate(risks)}
    steps: List[Dict[str, Any]] = []

    pages = [
        page
        for page in mapping.get("page_mappings", [])
        if str(page.get("risk", "")).lower() in selected_risks
    ]
    pages.sort(key=lambda page: (risk_rank[str(page.get("risk", "")).lower()], page.get("page_id", "")))

    for page in pages:
        if str(page.get("risk", "")).lower() not in selected_risks:
            continue

        locator_changes = page.get("locator_changes") or []
        if locator_changes:
            for item in locator_changes:
                steps.append(
                    {
                        "page_id": page.get("page_id"),
                        "risk": page.get("risk"),
                        "action": item.get("label") or item.get("semantic_key"),
                        "action_type": infer_semantic_action(item.get("action_hint") or item.get("kind"), item),
                        "legacy_locator": item.get("legacy_locator"),
                        "new_locator": item.get("new_locator"),
                        "semantic_context": item,
                    }
                )
        else:
            steps.append(
                {
                    "page_id": page.get("page_id"),
                    "risk": page.get("risk"),
                    "action": "page_snapshot",
                    "action_type": "wait",
                    "legacy_locator": "body",
                    "new_locator": "body",
                }
            )

        if limit and len(steps) >= limit:
            return steps[:limit]

    return steps


def execute_consistency_flow(
    legacy_page: Page,
    new_page: Page,
    steps: Optional[List[Dict[str, Any]]] = None,
    *,
    page_mapping: Optional[Union[Dict[str, Any], str, Path]] = None,
    output_dir: str = "./output/consistency",
    browser_name: str = "chrome",
    risk_levels: Optional[Iterable[str]] = None,
    visual_threshold_percent: float = 0.1,
) -> List[Dict[str, Any]]:
    """
    Execute a Legacy-first/New-replay migration consistency flow.

    Expected step shape:
    {
        "page_id": "AbstListEdit.jsp",
        "action": "AbstListViewEntry",
        "action_type": "click",
        "legacy_locator": "[name='btAdd']",
        "new_locator": "[name='btAdd']",
        "value": None
    }
    """
    if steps is None:
        if page_mapping is None:
            raise ValueError("steps or page_mapping is required")
        if isinstance(page_mapping, (str, Path)):
            page_mapping = json.loads(Path(page_mapping).read_text(encoding="utf-8"))
        steps = build_steps_from_page_mapping(page_mapping, risk_levels=risk_levels)

    results: List[Dict[str, Any]] = []
    root = Path(output_dir)

    for index, step in enumerate(steps, start=1):
        page_id = step.get("page_id", "unknown")
        action = step.get("action") or step.get("action_type")
        action_type = infer_semantic_action(step.get("action_type", "click"), step)
        legacy_locator: Optional[str] = step.get("legacy_locator")
        new_locator: Optional[str] = step.get("new_locator")

        if not legacy_locator or not new_locator:
            results.append(
                {
                    "page_id": page_id,
                    "action": action,
                    "action_type": action_type,
                    "status": "BLOCKED",
                    "reason": "legacy_locator or new_locator is missing",
                }
            )
            continue

        legacy_action = execute_action(
            legacy_page,
            action_type,
            legacy_locator,
            step.get("value"),
            browser_name=browser_name,
            capture_dir=root,
            test_id=f"{index:04d}_{page_id}_legacy",
            action_context={**step, "locator": legacy_locator},
        )
        legacy_state = legacy_action.get("state") or _capture_state(legacy_page, root, f"{index:04d}_legacy")

        new_action = execute_action(
            new_page,
            action_type,
            new_locator,
            step.get("value"),
            browser_name=browser_name,
            capture_dir=root,
            test_id=f"{index:04d}_{page_id}_new",
            action_context={**step, "locator": new_locator},
        )

        new_state = new_action.get("state") or _capture_state(new_page, root, f"{index:04d}_new")

        if legacy_action.get("status") == "BLOCKED" or new_action.get("status") == "BLOCKED":
            results.append(
                {
                    "page_id": page_id,
                    "risk": step.get("risk"),
                    "action": action,
                    "action_type": action_type,
                    "status": "BLOCKED",
                    "legacy_action": legacy_action,
                    "new_action": new_action,
                    "legacy_screenshot": legacy_state.get("screenshot"),
                    "new_screenshot": new_state.get("screenshot"),
                    "legacy_frame": legacy_state.get("target_frame"),
                    "new_frame": new_state.get("target_frame"),
                }
            )
            continue

        visual = {"status": "SKIPPED", "reason": "Visual comparison is disabled for this action type"}
        if COMPARE_POLICY.get(action_type, COMPARE_POLICY["click"]).get("visual_required", True):
            visual = compare_visual_screenshot(
                legacy_state.get("screenshot", ""),
                new_state.get("screenshot", ""),
                str(root / f"{index:04d}_{_safe_name(page_id)}_diff.png"),
                threshold_percent=visual_threshold_percent,
            )

        result = compare_captured_state(
            legacy_state,
            new_state,
            action_type=action_type,
            visual_status=visual.get("status"),
        )
        result["visual"] = visual
        result.update(
            {
                "page_id": page_id,
                "risk": step.get("risk"),
                "action": action,
                "action_type": action_type,
                "legacy_locator": legacy_locator,
                "new_locator": new_locator,
                "legacy_frame": legacy_state.get("target_frame"),
                "new_frame": new_state.get("target_frame"),
            }
        )
        results.append(result)

    return results
