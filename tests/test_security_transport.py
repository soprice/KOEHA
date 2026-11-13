"""Security regression tests for translation credential transport."""

import httpx
import pytest

from koeha.core.events import Caption
from koeha.core.settings import Settings
from koeha.translation.connection_profiles import is_loopback_url, uses_remote_http
from koeha.translation.translator import Translator, _client, list_models


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:1234/v1",
    "http://localhost:11434/v1",
    "http://[::1]:8080/v1",
    "https://api.example.com/v1",
])
def test_no_http_warning_for_loopback_or_https(url):
    assert not uses_remote_http(url)


@pytest.mark.parametrize("url", [
    "http://192.168.1.2:1234/v1",
    "http://10.0.0.5:8080/v1",
    "http://api.example.com/v1",
    "http://127.0.0.2.example.com/v1",
])
def test_http_warning_for_remote_cleartext(url):
    assert uses_remote_http(url)


@pytest.mark.parametrize("url", ["http://192.168.1.2:1234/v1", "http://api.example.com/v1",
                                 "http://127.0.0.2.example.com/v1"])
def test_model_probe_sends_bearer_key_over_remote_http(url):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"data": []})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert list_models(url, "test-key", client=client) == []
    assert [str(request.url) for request in requests] == [url + "/models"]
    assert requests[0].headers["Authorization"] == "Bearer test-key"


@pytest.mark.parametrize("url", ["http://localhost:1234/v1", "http://127.0.0.1:1234/v1",
                                 "http://[::1]:1234/v1", "https://api.example.com/v1"])
def test_model_probe_sends_keys_to_https_and_loopback_http(url):
    def respond(request):
        assert str(request.url) == url + "/models"
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(200, json={"data": [{"id": "chat"}]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert list_models(url, "test-key", client=client) == ["chat"]


@pytest.mark.parametrize("url, loopback", [
    ("http://127.0.0.1:1234/v1", True),
    ("http://LOCALHOST:11434/v1", True),
    ("http://[::1]:8080/v1", True),
    ("http://192.168.1.2:1234/v1", False),
    ("http://127.0.0.2.example.com/v1", False),
    ("http://[bad/v1", False),
])
def test_loopback_detection(url, loopback):
    assert is_loopback_url(url) is loopback


def test_loopback_translation_bypasses_system_proxy(monkeypatch):
    """A configured proxy must not receive loopback requests carrying a bearer key."""
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    monkeypatch.delenv("NO_PROXY", raising=False)
    with _client("http://127.0.0.1:1234/v1", timeout=1) as local:
        assert not local._mounts
    with _client("https://api.example.com/v1", timeout=1) as remote:
        assert remote._mounts  # remote servers still honor the configured proxy


def test_unauthenticated_lan_http_model_list_sends_no_credentials():
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "lan-chat"}]})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert list_models("http://192.168.1.2:1234/v1", "", client=client) == ["lan-chat"]
    assert [str(r.url) for r in requests] == ["http://192.168.1.2:1234/v1/models"]
    assert "authorization" not in requests[0].headers


def _translate(monkeypatch, key, respond, url="http://192.168.1.2:1234/v1"):
    monkeypatch.setattr("koeha.translation.translator.get_key", lambda _: key)
    settings = Settings(target="th", translation_url=url, translation_model="lan-chat")
    output, errors = [], []
    translator = Translator(settings, output.append, errors.append)
    translator.client.close()
    translator.client = httpx.Client(transport=httpx.MockTransport(respond))
    translator.submit(Caption(1, 0, 1, "hello", True))
    translator.close()
    return output, errors


def test_unauthenticated_lan_http_translation_sends_no_credentials(monkeypatch):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "สวัสดี"}}]})
    output, errors = _translate(monkeypatch, "", respond)
    assert not errors and output[0].translation == "สวัสดี"
    assert [str(r.url) for r in requests] == ["http://192.168.1.2:1234/v1/chat/completions"]
    assert "authorization" not in requests[0].headers


def test_lan_http_translation_sends_the_stored_key(monkeypatch):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "สวัสดี"}}]})

    output, errors = _translate(monkeypatch, "lan-secret", respond)
    assert not errors and output[0].translation == "สวัสดี"
    assert [str(request.url) for request in requests] == ["http://192.168.1.2:1234/v1/chat/completions"]
    assert requests[0].headers["Authorization"] == "Bearer lan-secret"


@pytest.mark.parametrize("url", ["http://127.0.0.1:1234/v1", "https://api.example.com/v1"])
def test_translation_sends_keys_to_https_and_loopback_http(monkeypatch, url):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "สวัสดี"}}]})
    output, errors = _translate(monkeypatch, "lan-secret", respond, url=url)
    assert not errors and output[0].translation == "สวัสดี"
    assert len(requests) == 1 and requests[0].headers["Authorization"] == "Bearer lan-secret"


def test_model_probe_does_not_follow_a_redirect_to_remote_http():
    requests = []

    def respond(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://192.168.1.2:1234/v1/models"})

    with httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=True) as client:
        with pytest.raises(httpx.HTTPStatusError, match="302"):
            list_models("http://127.0.0.1:1234/v1", "test-key", client=client)
    assert requests == ["http://127.0.0.1:1234/v1/models"]


def test_translation_redirect_does_not_send_a_request_or_expose_location(monkeypatch):
    requests = []

    def respond(request):
        requests.append(str(request.url))
        return httpx.Response(307, headers={"Location": "http://192.168.1.2/v1?key=test-key"})

    output, errors = _translate(monkeypatch, "test-key", respond, url="http://127.0.0.1:1234/v1")
    assert requests == ["http://127.0.0.1:1234/v1/chat/completions"]
    assert output[0].translation_error == "HTTP 307"
    assert errors == ["Translation failed: HTTP 307"]
