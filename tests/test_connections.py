import json

import httpx
import pytest
from PySide6.QtTest import QTest

from koeha.app.controller import Controller
from koeha.core.events import Caption
from koeha.core.settings import Settings
from koeha.translation.connection_profiles import PROVIDERS, ConnectionProfile, base_url
from koeha.translation.connections_panel import TranslationConnections
from koeha.translation.translator import Translator, list_models
from koeha.ui.main_window import MainWindow


def test_old_settings_keep_their_connection_and_migrate_credentials(app, tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"translation_url": "http://localhost:9000/v1", "translation_model": "old-model"}))
    settings = Settings.load(path)
    credentials = {settings.translation_url: "legacy-secret"}
    monkeypatch.setattr("koeha.translation.connections_panel.get_key", lambda key: credentials.get(key, ""))
    monkeypatch.setattr("koeha.translation.connections_panel.save_key", lambda key, value: credentials.update({key: value}))
    editor = TranslationConnections(settings)
    assert editor.api_key.text() == "legacy-secret"
    updated = Settings(**editor.settings_patch())
    updated.save(path)
    editor.save_credentials()
    assert credentials["profile:default"] == "legacy-secret"
    assert "secret" not in path.read_text()
    loaded = Settings.load(path)
    assert loaded.translation_model == "old-model"
    assert loaded.translation_profiles[0].url == settings.translation_url
    editor.close()


def test_profiles_saved_by_the_ssh_preview_still_load(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "translation_profile": "lan",
        "translation_profiles": [{
            "id": "lan", "name": "LAN", "provider": "lmstudio",
            "url": "http://192.168.1.2:1234/v1", "model": "chat", "ssh_user": "",
        }],
    }))

    settings = Settings.load(path)
    assert settings.translation_profile == "lan"
    assert settings.translation_url == "http://192.168.1.2:1234/v1"
    assert settings.translation_model == "chat"
    settings.save(path)
    assert "ssh_user" not in path.read_text()


def test_profiles_save_reopen_and_switch_from_live(studio, monkeypatch):
    credentials = {}
    monkeypatch.setattr("koeha.translation.connections_panel.get_key", lambda key: credentials.get(key, ""))
    monkeypatch.setattr("koeha.translation.connections_panel.save_key", lambda key, value: credentials.update({key: value}))
    editor = studio.connections
    for provider, name, secret in [("lmstudio", "Local LM", "lm-secret"), ("ollama", "Local Ollama", "")]:
        editor.new_button.click()
        editor.provider.setCurrentIndex(editor.provider.findData(provider))
        editor.name.setText(name)
        assert editor.endpoint.text() == PROVIDERS[provider][1]
        editor.model.setEditText("chat-model")
        editor.api_key.setText(secret)
    active = editor.active_id
    editor.save_button.click()
    stored = Settings.load(studio.controller.root / "settings.json")
    assert len(stored.translation_profiles) == 3 and stored.translation_profile == active
    assert "profile:" + active not in credentials
    lm_profile = next(p for p in stored.translation_profiles if p.name == "Local LM")
    assert credentials[lm_profile.credential_id] == "lm-secret"
    assert "lm-secret" not in (studio.controller.root / "settings.json").read_text()
    reopened = MainWindow(Controller())
    try:
        assert reopened.connections.active_id == active
        reopened.provider.setCurrentIndex(reopened.provider.findData(lm_profile.id))
        assert reopened.connections.api_key.text() == "lm-secret"
        assert reopened.endpoint.text() == PROVIDERS["lmstudio"][1]
        assert reopened.save_settings()
        assert reopened.controller.settings.translation_profile == lm_profile.id
    finally:
        reopened.close()


def test_profile_drafts_and_keys_are_isolated_even_at_same_url(studio, monkeypatch):
    saved = {}
    monkeypatch.setattr("koeha.translation.connections_panel.save_key", lambda key, value: saved.update({key: value}))
    editor = studio.connections
    first = editor.active_id
    editor.model.setEditText("one")
    editor.api_key.setText("first-secret")
    editor.new_profile()
    second = editor.active_id
    editor.model.setEditText("two")
    editor.api_key.setText("second-secret")
    editor.select_profile(first)
    assert editor.model.currentText() == "one" and editor.api_key.text() == "first-secret"
    editor.select_profile(second)
    assert editor.model.currentText() == "two" and editor.api_key.text() == "second-secret"
    assert studio.save_connection_profiles()
    assert saved["profile:" + first] == "first-secret" and saved["profile:" + second] == "second-secret"
    editor.delete_profile()
    assert studio.save_connection_profiles()
    assert saved["profile:" + second] == ""
    assert studio.provider.count() == 1 and not editor.delete_button.isEnabled()


def test_invalid_profiles_do_not_replace_saved_settings(studio):
    assert studio.save_settings()
    path = studio.controller.root / "settings.json"
    before = path.read_text()
    studio.connections.name.clear()
    assert not studio.save_connection_profiles()
    assert "profile name" in studio.banner.text()
    assert path.read_text() == before
    studio.connections.name.setText("Default")
    studio.endpoint.setText("not a URL")
    assert not studio.save_settings() and path.read_text() == before


def test_credential_read_failure_cannot_clear_a_saved_key(app, monkeypatch):
    def fail(_):
        raise RuntimeError("Credential store unavailable")
    saved = []
    monkeypatch.setattr("koeha.translation.connections_panel.get_key", fail)
    monkeypatch.setattr("koeha.translation.connections_panel.save_key", lambda *args: saved.append(args))
    editor = TranslationConnections(Settings())
    with pytest.raises(ValueError, match="Re-enter"):
        editor.save_credentials()
    assert not saved
    editor.api_key.setText("replacement")
    editor.save_credentials()
    assert saved == [("profile:default", "replacement")]
    editor.close()


def test_switching_profiles_does_not_rewrite_keys_read_from_credential_store(app, monkeypatch):
    profiles = [ConnectionProfile("a", "First"), ConnectionProfile("b", "Second")]
    stored = {"profile:a": "first-secret", "profile:b": "second-secret"}
    writes = []
    monkeypatch.setattr("koeha.translation.connections_panel.get_key", stored.__getitem__)
    monkeypatch.setattr("koeha.translation.connections_panel.save_key", lambda *args: writes.append(args))
    editor = TranslationConnections(Settings(translation_profiles=profiles, translation_profile="a"))
    try:
        editor.select_profile("b")
        assert editor.api_key.text() == "second-secret"
        editor.api_key.setText("replacement")
        editor.select_profile("a")
        assert editor.api_key.text() == "first-secret"
        editor.select_profile("b")
        assert editor.api_key.text() == "replacement"
        assert editor.status.text() == "Unsaved profile changes"
        editor.save_credentials()
        editor.committed()
        editor.select_profile("a")
        editor.save_credentials()
        assert writes == [("profile:b", "replacement")]
    finally:
        editor.close()


def test_returning_to_profile_retries_a_failed_credential_read(app, monkeypatch):
    def unavailable(_):
        raise RuntimeError("Credential store unavailable")

    monkeypatch.setattr("koeha.translation.connections_panel.get_key", unavailable)
    editor = TranslationConnections(Settings())
    try:
        editor.endpoint.setText("https://changed.test/v1")
        editor.new_profile()
        original_url = Settings().translation_url
        stored = {original_url: "legacy-secret"}
        monkeypatch.setattr("koeha.translation.connections_panel.get_key", stored.__getitem__)
        editor.select_profile("default")
        assert editor.api_key.text() == "legacy-secret"
        assert editor.endpoint.text() == "https://changed.test/v1"
    finally:
        editor.close()


@pytest.mark.parametrize("provider", ["lmstudio", "ollama"])
def test_local_server_model_list_and_translation(provider, monkeypatch):
    url = PROVIDERS[provider][1]
    profile = ConnectionProfile("local", provider, provider, url, "local-chat")
    requested_keys, requests = [], []
    monkeypatch.setattr("koeha.translation.translator.get_key", lambda key: requested_keys.append(key) or "")
    def respond(request):
        requests.append(request)
        assert "authorization" not in request.headers
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "local-chat"}, {"id": "local-chat"}]})
        payload = json.loads(request.content)
        assert payload["model"] == "local-chat" and payload["messages"][-1]["content"] == "こんにちは"
        return httpx.Response(200, json={"choices": [{"message": {"content": "สวัสดี"}}]})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert list_models(url, client=client) == ["local-chat"]
    settings = Settings(target="th", translation_url=url, translation_model=profile.model,
                        translation_profile=profile.id, translation_profiles=[profile])
    output, errors = [], []
    translator = Translator(settings, output.append, errors.append)
    translator.client.close()
    translator.client = httpx.Client(transport=httpx.MockTransport(respond))
    translator.submit(Caption(1, 0, 1, "こんにちは", True))
    translator.close()
    assert not errors and output[0].translation == "สวัสดี"
    assert requested_keys == [profile.credential_id]
    assert [str(r.url) for r in requests] == [url + "/models", url + "/chat/completions"]


def test_model_list_errors_and_bearer_auth():
    def respond(request):
        assert request.headers["Authorization"] == "Bearer test-secret"
        return httpx.Response(401)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            list_models("https://local.test/v1", "test-secret", client=client)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"models": []}))) as client:
        with pytest.raises(ValueError, match="model list"):
            list_models("http://local.test/v1", client=client)


@pytest.mark.parametrize("failure, expected", [
    ("auth", "Enter the server API key"), ("timeout", "timed out"), ("empty", "No models found"),
])
def test_model_discovery_recovers_from_server_failures(studio, monkeypatch, failure, expected):
    def discover(*_):
        if failure == "auth":
            response = httpx.Response(401, request=httpx.Request("GET", "http://local.test/v1/models"))
            response.raise_for_status()
        if failure == "timeout":
            raise httpx.ReadTimeout("The server timed out")
        return []
    monkeypatch.setattr("koeha.translation.connections_panel.list_models", discover)
    editor = studio.connections
    editor.model.setEditText("keep-my-model")
    editor.load_models()
    for _ in range(100):
        QTest.qWait(10)
        if editor.load_button.isEnabled():
            break
    assert editor.load_button.isEnabled() and expected in editor.status.text()
    assert studio.live_translation_load.isEnabled() and expected in studio.live_translation_status.toolTip()
    assert editor.model.currentText() == "keep-my-model"


def test_live_model_selector_loads_and_remembers_each_profiles_models(studio, monkeypatch):
    endpoints = {
        "http://192.168.1.2:1234/v1": ("first-key", ["first-chat", "first-other"]),
        "https://other.test/v1": ("second-key", ["second-chat", "second-other"]),
    }
    calls = []

    def discover(url, key):
        calls.append(url)
        expected_key, models = endpoints[url]
        assert key == expected_key
        return models

    monkeypatch.setattr("koeha.translation.connections_panel.list_models", discover)
    editor = studio.connections
    first_id = editor.active_id
    editor.endpoint.setText("http://192.168.1.2:1234/v1")
    editor.api_key.setText("first-key")
    editor.model.setEditText("first-chat")
    editor.new_profile()
    second_id = editor.active_id
    editor.endpoint.setText("https://other.test/v1")
    editor.api_key.setText("second-key")

    combo = studio.live_translation_model
    for profile_id, (_, models) in zip((first_id, second_id), endpoints.values(), strict=True):
        studio.provider.setCurrentIndex(studio.provider.findData(profile_id))
        studio.live_translation_load.click()
        for _ in range(100):
            if studio.live_translation_load.isEnabled():
                break
            QTest.qWait(10)
        assert [combo.itemData(index) for index in range(combo.count())] == models
        combo.setCurrentIndex(combo.findData(models[1]))
        assert editor.model.currentText() == models[1]

    studio.provider.setCurrentIndex(studio.provider.findData(first_id))
    assert combo.currentData() == "first-other"
    assert combo.findData("first-chat") >= 0 and combo.findData("second-chat") == -1
    assert calls == list(endpoints)

    started = []
    monkeypatch.setattr(studio.controller, "start", lambda: started.append(studio.controller.settings))
    studio.target.setCurrentIndex(studio.target.findData("th"))
    studio.toggle_live()
    assert started[0].translation_profile == first_id
    assert started[0].translation_model == "first-other"
    assert Settings.load(studio.controller.root / "settings.json").translation_model == "first-other"


def test_model_discovery_is_async_and_ignores_stale_results(studio, monkeypatch):
    import threading

    release = threading.Event()
    started = threading.Event()
    def discover(url, key):
        started.set()
        assert release.wait(3)
        return ["stale-model"]
    monkeypatch.setattr("koeha.translation.connections_panel.list_models", discover)
    editor = studio.connections
    editor.load_models()
    try:
        assert started.wait(1) and not editor.load_button.isEnabled()
        editor.new_profile()
        editor.model.setEditText("keep-this")
    finally:
        release.set()
    for _ in range(100):
        QTest.qWait(10)
        if editor.load_button.isEnabled():
            break
    assert editor.model.currentText() == "keep-this" and editor.load_button.isEnabled()
    monkeypatch.setattr("koeha.translation.connections_panel.list_models", lambda *_: ["new-model"])
    editor.model.setEditText("")
    editor.load_models()
    for _ in range(100):
        QTest.qWait(10)
        if editor.load_button.isEnabled():
            break
    assert editor.model.currentText() == "new-model" and "1" in editor.status.text()


@pytest.mark.parametrize("url", ["ftp://host", "http://", "http://host:bad/v1", "http://user:secret@host/v1"])
def test_invalid_base_urls_are_rejected(url):
    with pytest.raises(ValueError):
        base_url(url)


def test_local_base_urls_get_the_compatible_prefix():
    assert base_url(" http://localhost:11434/ ", "ollama") == "http://localhost:11434/v1"
    assert base_url("http://localhost:1234", "lmstudio") == "http://localhost:1234/v1"
    assert base_url("https://api.test/custom/v1/", "custom") == "https://api.test/custom/v1"


@pytest.mark.parametrize("save", ["save_connection_profiles", "save_settings"])
def test_saving_a_key_for_remote_http_shows_a_warning(studio, monkeypatch, save):
    saved = []
    monkeypatch.setattr("koeha.translation.connections_panel.save_key", lambda *args: saved.append(args))
    editor = studio.connections
    assert "Translation API" in studio.settings_sections.titles
    editor.endpoint.setText("http://192.168.1.20:1234/v1")
    editor.api_key.setText("lan-secret")
    assert not editor.http_warning.isHidden()
    assert studio.translation_http_warning.isHidden()
    studio.target.setCurrentIndex(studio.target.findData("th"))
    assert not studio.translation_http_warning.isHidden()
    assert getattr(studio, save)()
    assert not editor.http_warning.isHidden()
    assert saved == [("profile:default", "lan-secret")]
    assert "lan-secret" not in (studio.controller.root / "settings.json").read_text()
    assert studio.controller.settings.translation_url == "http://192.168.1.20:1234/v1"


@pytest.mark.parametrize("url", ["https://api.example.com/v1", "http://127.0.0.1:1234/v1", "http://localhost:11434/v1"])
def test_saving_a_key_for_https_or_loopback_is_allowed(studio, url):
    studio.connections.endpoint.setText(url)
    studio.connections.api_key.setText("secret")
    assert studio.connections.http_warning.isHidden()
    assert studio.save_connection_profiles()
