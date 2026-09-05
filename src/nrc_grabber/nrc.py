"""NRC login, edition manifest, and download (stdlib urllib only)."""

from __future__ import annotations

import http.client
import http.cookiejar
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from .config import MAGIC_OFFSET, MAGIC_PREFIX
from .dates import edition_date_for_request

ALLOWED_HOSTS = {"www.nrc.nl", "nrc.nl", "login.nrc.nl"}
BASE = "https://www.nrc.nl"
EDITION_CODE = "NH"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


class NrcError(Exception):
    """Generic NRC operation error."""


class AuthError(NrcError):
    """Authentication failed."""


class NoEdition(NrcError):
    """No edition exists for the requested date (genuine 404)."""


# --- HTTP helpers with host validation ---


class _HostValidatingHandler(urllib.request.HTTPRedirectHandler):
    """Follow redirects but reject any host not in the allowlist."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        if parsed.hostname not in ALLOWED_HOSTS:
            raise urllib.error.URLError(
                f"redirect to unapproved host: {parsed.hostname}"
            )
        if parsed.scheme != "https":
            raise urllib.error.URLError(f"redirect to non-HTTPS: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _new_opener(cookiejar: http.cookiejar.CookieJar) -> urllib.request.OpenerDirector:
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cookiejar),
        _HostValidatingHandler(),
    )
    opener.addheaders = [
        ("User-Agent", UA),
        ("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
        ("Accept-Language", "nl,en;q=0.9"),
    ]
    return opener


def _open(opener, req, timeout=60, retries=3):
    last = None
    for i in range(retries):
        try:
            return opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            raise  # caller handles HTTP errors
        except Exception as e:
            last = e
            import time

            time.sleep(1.5 * (i + 1))
    raise NrcError(f"request failed after retries: {last}")


def _validate_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise NrcError(f"non-HTTPS URL: {url}")
    if parsed.hostname not in ALLOWED_HOSTS:
        raise NrcError(f"unapproved host: {parsed.hostname}")


# --- Login (Apereo CAS) ---


def login(
    cookiejar: http.cookiejar.CookieJar,
    username: str,
    password: str,
    return_to: str = "/krant/",
) -> None:
    """Log in via CAS at login.nrc.nl; establishes the nrcnl_session_id cookie."""
    opener = _new_opener(cookiejar)
    login_url = f"{BASE}/login/?return_to={urllib.parse.quote(return_to, safe='')}"
    _validate_url(login_url)
    resp = _open(opener, urllib.request.Request(login_url))
    html = resp.read().decode("utf-8", "replace")
    cas_url = resp.geturl()
    # Parse the CAS login form: action and execution token
    action = _parse_form_action(html)
    exec_token = _parse_execution_token(html)
    if not exec_token:
        raise AuthError("could not parse CAS execution token")
    post_url = urllib.parse.urljoin(cas_url, action) if action else cas_url
    _validate_url(post_url)
    data = urllib.parse.urlencode(
        {
            "username": username,
            "password": password,
            "execution": exec_token,
            "_eventId": "submit",
            "geolocation": "",
            "rememberMe": "true",
        }
    ).encode()
    req = urllib.request.Request(
        post_url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp2 = _open(opener, req)
    body2 = resp2.read().decode("utf-8", "replace")
    # Detect login failure
    low = body2.lower()
    if "login-error" in low or "ongeldig" in low or "mislukt" in low or "incorrect" in low:
        raise AuthError("NRC login rejected the credentials")
    # Confirm we got the session cookie
    has_session = any(c.name == "nrcnl_session_id" for c in cookiejar)
    if not has_session:
        raise AuthError("no nrcnl_session_id cookie after login")


def _parse_form_action(html: str) -> Optional[str]:
    m = re.search(r'<form[^>]*action="([^"]+)"', html)
    return m.group(1) if m else None


def parse_execution_token(html: str) -> str:
    """Extract the CAS execution hidden-field value from a login page."""
    return _parse_execution_token(html)


def _parse_execution_token(html: str) -> str:
    m = re.search(r'name="execution"\s+value="([^"]+)"', html)
    return m.group(1) if m else ""


# --- Edition manifest ---


def edition_manifest(
    cookiejar: http.cookiejar.CookieJar,
    date,
) -> Optional[dict]:
    """Fetch the edition manifest for a date; return dict or None on 404.

    Raises NrcError on other HTTP errors. `date` is a datetime.date.
    """
    opener = _new_opener(cookiejar)
    ds = date.strftime("%Y/%m/%d")
    url = f"{BASE}/de/data/{EDITION_CODE}/{ds}/"
    _validate_url(url)
    req = urllib.request.Request(url, headers={"Accept": "application/json,*/*;q=0.8"})
    try:
        resp = _open(opener, req)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise NrcError(f"manifest HTTP {e.code} for {url}")
    body = resp.read().decode("utf-8", "replace")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise NrcError(f"manifest is not JSON for {url}")


def edition_identity(manifest: dict, request_date):
    """Return the edition date (a datetime.date) for a manifest."""
    pub = manifest.get("publication_date")
    return edition_date_for_request(request_date, pub)


# --- Filename handling ---


def sanitize_filename(raw: Optional[str], fmt: str, edition_date) -> str:
    """Return a safe filename, rejecting unsafe Content-Disposition names."""
    if raw:
        # Strip quotes/whitespace
        name = raw.strip().strip('"').strip()
        # Reject path separators, absolute, dot components
        if "/" in name or "\\" in name or name.startswith("."):
            name = os.path.basename(name) or ""
        if ".." in name:
            name = ""
        # Keep only the basename
        name = os.path.basename(name)
        if name and not name.startswith(".") and name.isprintable():
            return name
    # Fallback safe name from format + edition date
    ds = edition_date.strftime("%Y%m%d")
    ds_dash = edition_date.strftime("%Y-%m-%d")
    if fmt == "pdf":
        return f"NH-{ds_dash}.pdf"
    if fmt == "epub":
        return f"nrc_{ds}.epub"
    return f"nrc_{ds}.mobi"


def parse_content_disposition(header: Optional[str]) -> Optional[str]:
    """Extract the filename from a Content-Disposition header."""
    if not header:
        return None
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', header)
    return m.group(1) if m else None


# --- Download ---


def download_edition(
    cookiejar: http.cookiejar.CookieJar,
    download_path: str,
    dest_dir: Path,
    fmt: str,
    edition_date,
) -> Path:
    """Download an edition to dest_dir with atomic completion and validation.

    Returns the final Path. Raises NrcError on any failure (temp cleaned up).
    Never follows symlinks for the destination; skips non-regular files.
    """
    opener = _new_opener(cookiejar)
    url = urllib.parse.urljoin(BASE, download_path)
    _validate_url(url)
    req = urllib.request.Request(url, headers={"Accept": "*/*"})
    try:
        resp = _open(opener, req, timeout=180)
    except urllib.error.HTTPError as e:
        raise NrcError(f"download HTTP {e.code} for {url}")
    status = resp.status
    if not (200 <= status < 300):
        raise NrcError(f"download status {status} for {url}")
    content_type = resp.headers.get("Content-Type", "")
    content_length = resp.headers.get("Content-Length")
    cd = resp.headers.get("Content-Disposition")
    filename = sanitize_filename(parse_content_disposition(cd), fmt, edition_date)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    final_path = dest_dir / filename
    # Never write through a symlink
    if final_path.is_symlink():
        raise NrcError(f"refusing to write through symlink: {final_path}")
    tmp_path = dest_dir / f".{filename}.part"
    expected_len = int(content_length) if content_length and content_length.isdigit() else None
    written = 0
    try:
        with open(tmp_path, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                written += len(chunk)
        # Validate magic bytes from the file head
        with open(tmp_path, "rb") as f:
            head = f.read(max(8, max((off + len(m) for off, m in MAGIC_OFFSET.values()), default=0)))
        if fmt in MAGIC_PREFIX:
            expected = MAGIC_PREFIX[fmt]
            if head[: len(expected)] != expected:
                raise NrcError(
                    f"downloaded body does not match {fmt} magic bytes "
                    f"(got {head[:8]!r}, expected {expected!r})"
                )
        elif fmt in MAGIC_OFFSET:
            off, expected = MAGIC_OFFSET[fmt]
            if len(head) < off + len(expected) or head[off : off + len(expected)] != expected:
                raise NrcError(
                    f"downloaded body does not match {fmt} signature "
                    f"(expected {expected!r} at offset {off})"
                )
        if expected_len is not None and written != expected_len:
            raise NrcError(
                f"download truncated: wrote {written} of {expected_len} bytes"
            )
        if written == 0:
            raise NrcError("downloaded body is empty")
        # Reject HTML/login pages defensively (PDF/epub won't start with '<')
        if head[:1] == b"<":
            raise NrcError("downloaded body looks like HTML, not the file")
        # Atomic rename
        os.replace(tmp_path, final_path)
    except BaseException:
        # Clean up temp on any failure
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    return final_path


def expected_filename(fmt: str, edition_date) -> str:
    """Return the canonical expected filename for a format+edition date."""
    return sanitize_filename(None, fmt, edition_date)
