"""HMAC-SHA256 request signing for iFlytek Gateway Storage."""

import base64
import hashlib
import hmac
from datetime import datetime, timezone
from time import mktime
from urllib.parse import urlparse
from wsgiref.handlers import format_date_time


def build_auth_header(
    url: str,
    method: str,
    api_key: str,
    api_secret: str,
) -> dict[str, str]:
    """Build HMAC-SHA256 authentication headers for iFlytek Gateway.

    Signature string format::

        host: {host}
        date: {rfc2822_date}
        {METHOD} {path} HTTP/1.1
        digest: SHA-256={base64(sha256(""))}

    Returns:
        Dict with Method, Host, Date, Digest, Authorization headers.
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = parsed.path

    date = format_date_time(mktime(datetime.now(timezone.utc).timetuple()))

    # Digest of empty body (signing uses empty digest regardless of actual body)
    digest_hash = hashlib.sha256(b"").digest()
    digest = "SHA256=" + base64.b64encode(digest_hash).decode()

    signature_str = (
        f"host: {host}\n"
        f"date: {date}\n"
        f"{method} {path} HTTP/1.1\n"
        f"digest: {digest}"
    )

    signature = hmac.new(
        api_secret.encode(),
        signature_str.encode(),
        digestmod=hashlib.sha256,
    ).digest()
    sign = base64.b64encode(signature).decode()

    authorization = (
        f'api_key="{api_key}", algorithm="hmac-sha256", '
        f'headers="host date request-line digest", '
        f'signature="{sign}"'
    )

    return {
        "Method": method,
        "Host": host,
        "Date": date,
        "Digest": digest,
        "Authorization": authorization,
    }
