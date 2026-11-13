"""Saved translation connections; credentials are kept outside the settings file."""
from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlsplit, urlunsplit

PROVIDERS = {
    "custom": ("OpenAI-compatible API", "http://127.0.0.1:8080/v1"),
    "lmstudio": ("LM Studio", "http://127.0.0.1:1234/v1"),
    "ollama": ("Ollama", "http://127.0.0.1:11434/v1"),
}


def base_url(url, provider="custom"):
    url = url.strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("Enter an HTTP or HTTPS server URL")
    if parts.username is not None or parts.password is not None or "?" in url or "#" in url:
        raise ValueError("Use a server base URL without credentials, query or fragment")
    _ = parts.port  # reject invalid port numbers
    if provider != "custom" and not parts.path:
        url = urlunsplit((parts.scheme, parts.netloc, "/v1", "", ""))
    return url


def is_loopback_url(url):
    """True only for URLs whose host is literally this machine."""
    try:
        host = (urlsplit(url.strip()).hostname or "").lower()
    except ValueError:
        return False
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


HTTP_WARNING = "HTTP warning: API keys and translation text are sent unencrypted over the network."


def uses_remote_http(url):
    try:
        normalized = base_url(url)
    except ValueError:
        return False
    return urlsplit(normalized).scheme == "http" and not is_loopback_url(normalized)


@dataclass
class ConnectionProfile:
    id: str
    name: str
    provider: str = "custom"
    url: str = "http://127.0.0.1:8080/v1"
    model: str = ""

    @property
    def credential_id(self):
        return "profile:" + self.id

    def validate(self):
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("Invalid connection profile ID")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Enter a profile name")
        if self.provider not in PROVIDERS:
            raise ValueError("Unknown translation provider")
        if not isinstance(self.model, str) or not isinstance(self.url, str):
            raise ValueError("Invalid connection profile model or URL")
        base_url(self.url, self.provider)
