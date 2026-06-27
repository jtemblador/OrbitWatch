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

    `-f` makes curl exit non-zero on HTTP >= 400; we surface those so the caller
    treats them like other fetch failures (cache fallback).
    """
    try:
        result = subprocess.run(
            ["curl", "-fsS", "--max-time", "30", "-A", user_agent, url],
            capture_output=True, timeout=40, check=True,
        )
    except FileNotFoundError:
        raise RuntimeError("curl not available for fallback download")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"curl download failed (exit {e.returncode}): "
            f"{e.stderr.decode('utf-8', 'replace')[:200]}")
    return result.stdout.decode("utf-8")
