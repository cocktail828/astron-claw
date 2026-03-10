"""HMAC-SHA256 request signing for iFlytek Gateway Storage."""

import base64
import hashlib
import hmac
from datetime import datetime, timezone
from time import mktime
from typing import Dict
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
    u = urlparse(url)
    host = u.hostname
    path = u.path
    now = datetime.now()
    date = format_date_time(mktime(now.timetuple()))
    m = hashlib.sha256(bytes("".encode(encoding="utf-8"))).digest()
    digest = "SHA256=" + base64.b64encode(m).decode(encoding="utf-8")
    signatureStr = "host: " + str(host) + "\n"
    signatureStr += "date: " + date + "\n"
    signatureStr += method + " " + path + " " + "HTTP/1.1" + "\n"
    signatureStr += "digest: " + digest

    signature = hmac.new(
        bytes(api_secret, encoding="UTF-8"),
        bytes(signatureStr, encoding="UTF-8"),
        digestmod=hashlib.sha256,
    ).digest()
    sign = base64.b64encode(signature).decode(encoding="utf-8")

    authHeader = (
        'api_key="%s", algorithm="%s", '
        'headers="host date request-line digest", '
        'signature="%s"' % (api_key, "hmac-sha256", sign)
    )

    headers: Dict[str, str] = {
        "Method": method,
        "Host": host or "",
        "Date": date,
        "Digest": digest,
        "Authorization": authHeader,
    }
    return headers
