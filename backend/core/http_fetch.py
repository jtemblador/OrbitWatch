"""
Shared HTTP text downloader for the CelesTrak fetchers (GP + SOCRATES).

`requests` with certifi verification is the primary path; on a TLS/connection
error it falls back to the system `curl`. Some environments (e.g. certain VPNs)
fail Python's raw TLS handshake to CelesTrak with UNEXPECTED_EOF_WHILE_READING
where curl's stack succeeds — this fallback fixed a real fetch failure (6.9).
HTTP >= 400 is surfaced as urllib.error.HTTPError so callers can apply their own
403/404 no-retry handling regardless of which path was used.
"""

import subprocess
import time
import urllib.error

import requests

DEFAULT_USER_AGENT = "OrbitWatch/1.0"

# Backoff between retried TRANSPORT failures (seconds). Three attempts therefore
# span ~3 min of upstream downtime, which covers the observed CelesTrak blips
# (the 2026-08-12 CI outage went dark for >2 min) without hammering a service
# that only publishes new elements every 2 h.
_BACKOFF_SEC = (60.0, 120.0)


def download_text(url: str, user_agent: str = DEFAULT_USER_AGENT,
                  attempts: int = 1) -> str:
    """GET `url` and return the response body as text (requests → curl fallback).

    `attempts` > 1 adds a bounded retry with backoff, and applies ONLY to
    transport failures — timeout / TLS / DNS, i.e. cases where no HTTP response
    was received. A real HTTP answer (>= 400) is never retried: CelesTrak
    firewalls an IP after >50 3xx/4xx errors in 2 h, so a 403/404 must fail on
    the first try (see progress/notes/key_information.md #5). A timeout costs
    them nothing, so retrying one is both safe and compliant.

    Retry is opt-in per call site rather than the default because the
    single-object `fetch_by_catnr` path runs in a loop over many objects, where
    a blanket retry would multiply a single outage into minutes of backoff per
    object. Bulk group fetches (the CI-critical path) opt in.
    """
    attempts = max(1, attempts)
    last_err: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return _download_once(url, user_agent)
        except urllib.error.HTTPError:
            # A real answer from the server — never retry (IP-ban risk).
            raise
        except (requests.exceptions.RequestException, RuntimeError) as e:
            last_err = e
            if attempt >= attempts:
                break
            delay = _BACKOFF_SEC[min(attempt - 1, len(_BACKOFF_SEC) - 1)]
            print(f"  fetch attempt {attempt}/{attempts} failed "
                  f"({type(e).__name__}); retrying in {delay:.0f}s…")
            time.sleep(delay)
    assert last_err is not None  # loop only exits here after a failure
    raise last_err


def _download_once(url: str, user_agent: str) -> str:
    """One attempt: requests-with-certifi, falling back to system curl."""
    headers = {"User-Agent": user_agent}
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code >= 400:
            raise urllib.error.HTTPError(
                url, resp.status_code, resp.reason, hdrs=None, fp=None)
        return resp.text
    except (requests.exceptions.SSLError,
            requests.exceptions.ConnectionError,
            # Timeout is listed separately on purpose: ReadTimeout subclasses
            # Timeout but NOT ConnectionError, so omitting it silently skipped
            # the curl fallback on read timeouts (the 2026-07-29 CI failure —
            # its log has no "retrying via curl" line at all). ConnectTimeout
            # does subclass ConnectionError and was already covered.
            requests.exceptions.Timeout) as e:
        print(f"  requests fetch failed ({type(e).__name__}); retrying via curl…")
        return _download_via_curl(url, user_agent)


def _download_via_curl(url: str, user_agent: str = DEFAULT_USER_AGENT) -> str:
    """Fallback downloader using the system curl (robust TLS).

    Mirrors the `requests` path's contract: HTTP >= 400 is raised as
    urllib.error.HTTPError (with the real status), so callers' 403/404 no-retry
    logic fires regardless of which path served the request. We drop `-f` (which
    would collapse every HTTP error into a bare exit 22) and instead read the
    status via `-w`; a non-zero curl exit then means a genuine transport failure
    (TLS/timeout/DNS), surfaced as RuntimeError like other fetch failures.
    """
    try:
        result = subprocess.run(
            ["curl", "-sS", "--max-time", "30", "-A", user_agent,
             "-w", "\n%{http_code}", url],
            capture_output=True, timeout=40, check=True,
        )
    except FileNotFoundError:
        raise RuntimeError("curl not available for fallback download")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"curl download failed (exit {e.returncode}): "
            f"{e.stderr.decode('utf-8', 'replace')[:200]}")
    # `-w "\n%{http_code}"` appends a newline + the status after the body.
    body, sep, code = result.stdout.decode("utf-8").rpartition("\n")
    try:
        status = int(code)
    except ValueError:
        status = 0
    if not sep or status == 0:
        # curl exited 0 but emitted no parseable %{http_code} — don't return a
        # possibly-truncated body as if it were a clean 200.
        raise RuntimeError("curl fallback: no HTTP status in response")
    if status >= 400:
        raise urllib.error.HTTPError(
            url, status, f"HTTP {status}", hdrs=None, fp=None)
    return body
