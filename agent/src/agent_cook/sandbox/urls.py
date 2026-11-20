import re
from urllib.parse import urlsplit

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def public_https_origin(url: str, *, origin_only: bool = False) -> str:
    """Return a canonical HTTPS origin for a DNS-named site."""
    error = "Provide an HTTPS URL with a public DNS hostname and no credentials."
    if "\\" in url or any(char.isspace() or ord(char) < 32 for char in url):
        raise ValueError(error)
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower()
        port = parsed.port
    except (ValueError, UnicodeError) as cause:
        raise ValueError(error) from cause
    labels = host.split(".")
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or len(labels) < 2
        or len(host) > 253
        or labels[-1].isdigit()
        or any(not _LABEL.fullmatch(label) for label in labels)
        or port == 0
    ):
        raise ValueError(error)
    if origin_only and (parsed.path not in ("", "/") or "?" in url or "#" in url):
        raise ValueError("A browser origin must contain only the HTTPS scheme, hostname and optional port.")
    return f"https://{host}" if port in (None, 443) else f"https://{host}:{port}"
