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
import urllib.error

import requests

DEFAULT_USER_AGENT = "OrbitWatch/1.0"


def download_text(url: str, user_agent: str = DEFAULT_USER_AGENT) -> str:
    """GET `url` and return the response body as text (requests → curl fallback)."""
    headers = {"User-Agent": user_agent}
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code >= 400:
            raise urllib.error.HTTPError(
                url, resp.status_code, resp.reason, hdrs=None, fp=None)
        return resp.text
    except (requests.exceptions.SSLError,
            requests.exceptions.ConnectionError) as e:
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
