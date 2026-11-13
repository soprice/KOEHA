import json
import threading
import time

import httpx

from koeha.core.events import Caption
from koeha.core.settings import Settings
from koeha.translation.translator import Translator


def test_final_translation_survives_api_failure_as_caption_error(monkeypatch):
    monkeypatch.setattr("koeha.translation.translator.get_key", lambda _: "")
    output, errors = [], []
    translator = Translator(Settings(target="th", translation_model="test"), output.append, errors.append)
    translator.client.close()
    translator.client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(503)))
    translator.submit(Caption(1, 0, 1, "こんにちは", True))
    translator.close()
    assert len(output) == 1 and output[0].text == "こんにちは"
    assert output[0].translation_error and errors


def test_slow_draft_cannot_replace_final(monkeypatch):
    monkeypatch.setattr("koeha.translation.translator.get_key", lambda _: "")
    draft_started = threading.Event()
    release = threading.Event()
    output = []
    translator = Translator(Settings(target="th", translation_model="test"), output.append, list().append)
    translator.client.close()
    def respond(request):
        if threading.current_thread().name.startswith("koeha-draft"):
            draft_started.set()
            assert release.wait(2)
        return httpx.Response(200, json={"choices": [{"message": {"content": "สวัสดี"}}]})
    translator.client = httpx.Client(transport=httpx.MockTransport(respond))
    translator.submit(Caption(1, 0, 1, "こんにちは", False))
    assert draft_started.wait(2)
    translator.submit(Caption(1, 0, 2, "こんにちは。", True))
    release.set()
    translator.close()
    assert len(output) == 1 and output[0].final


def test_close_cancels_queued_translations_after_grace(monkeypatch):
    monkeypatch.setattr("koeha.translation.translator.get_key", lambda _: "")
    release = threading.Event()
    output = []
    translator = Translator(Settings(target="th", translation_model="test"), output.append, list().append)
    translator.client.close()
    def respond(request):
        release.wait(5)
        return httpx.Response(200, json={"choices": [{"message": {"content": "สวัสดี"}}]})
    translator.client = httpx.Client(transport=httpx.MockTransport(respond))
    for index in range(1, 6):
        translator.submit(Caption(index, 0, 1, "こんにちは", True))
    began = time.monotonic()
    translator.close(grace=.2)
    assert time.monotonic() - began < 2
    cancelled = [c for c in output if "cancelled" in c.translation_error]
    assert {c.id for c in cancelled} == {4, 5}
    release.set()


def test_live_style_switch_snapshots_requests_and_starts_fresh_context(monkeypatch):
    monkeypatch.setattr("koeha.translation.translator.get_key", lambda _: "")
    release = threading.Event()
    old_started = threading.Event()
    first_done = threading.Event()
    payloads = {}
    output, errors = [], []

    def on_caption(caption):
        output.append(caption)
        if caption.id == 1:
            first_done.set()

    def respond(request):
        messages = json.loads(request.content)["messages"]
        dialogue = messages[-1]["content"]
        payloads[dialogue] = messages
        if dialogue == "already submitted":
            old_started.set()
            assert release.wait(3)
        return httpx.Response(200, json={"choices": [{"message": {"content": "translated"}}]})

    translator = Translator(Settings(target="th", translation_model="test"), on_caption, errors.append)
    translator.client.close()
    translator.client = httpx.Client(transport=httpx.MockTransport(respond))
    old_style = translator.system_prompt
    try:
        translator.submit(Caption(1, 0, 1, "context", True))
        assert first_done.wait(3)
        translator.submit(Caption(2, 1, 2, "already submitted", True))
        assert old_started.wait(3)
        translator.submit(Caption(3, 2, 3, "new request", True), system_prompt="New style")
    finally:
        release.set()
        translator.close()
    assert not errors and {caption.id for caption in output} == {1, 2, 3}
    assert payloads["already submitted"][0]["content"] == old_style
    assert payloads["already submitted"][1]["content"] == "context"
    assert payloads["new request"] == [
        {"role": "system", "content": "New style"},
        {"role": "user", "content": "new request"},
    ]
    assert 2 not in translator.context
