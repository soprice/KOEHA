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
    started = threading.Event()
    release = threading.Event()
    output = []
    translator = Translator(Settings(target="th", translation_model="test"), output.append, list().append)
    translator.client.close()
    def respond(request):
        started.set()
        release.wait(5)
        return httpx.Response(200, json={"choices": [{"message": {"content": "สวัสดี"}}]})
    translator.client = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        for index in range(1, 6):
            translator.submit(Caption(index, 0, 1, "こんにちは", True))
        assert started.wait(5)
        began = time.monotonic()
        translator.close(grace=.2)
        assert time.monotonic() - began < 2
        cancelled = [c for c in output if "cancelled" in c.translation_error]
        assert {c.id for c in cancelled} == {2, 3, 4, 5}
    finally:
        release.set()
        if not translator.closed:
            translator.close(grace=0)


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


def test_final_translations_preserve_order_and_context_under_variable_latency(monkeypatch):
    monkeypatch.setattr("koeha.translation.translator.get_key", lambda _: "")
    emitted, errors = [], []
    payloads = {}
    translator = Translator(Settings(target="en", translation_model="test"), emitted.append, errors.append)
    translator.client.close()

    def respond(request):
        messages = json.loads(request.content)["messages"]
        user_text = messages[-1]["content"]
        payloads[user_text] = messages
        if user_text == "音声テストを開始します。":
            time.sleep(0.10)
            reply = "Starting the audio test."
        elif user_text == "音量レベルは正常です。":
            time.sleep(0.01)
            reply = "The volume level is normal."
        else:
            time.sleep(0.01)
            reply = "Continuing the recording."
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})

    translator.client = httpx.Client(transport=httpx.MockTransport(respond))
    translator.submit(Caption(1, 0.0, 1.5, "音声テストを開始します。", True))
    translator.submit(Caption(2, 1.6, 3.0, "音量レベルは正常です。", True))
    translator.submit(Caption(3, 3.1, 4.5, "このまま録音を続けます。", True))
    translator.close(grace=5.0)

    assert [c.id for c in emitted] == [1, 2, 3]
    assert not errors
    assert payloads["音量レベルは正常です。"][1] == {"role": "user", "content": "音声テストを開始します。"}
    assert payloads["音量レベルは正常です。"][2] == {"role": "assistant", "content": "Starting the audio test."}
    assert payloads["このまま録音を続けます。"][3] == {"role": "user", "content": "音量レベルは正常です。"}
    assert payloads["このまま録音を続けます。"][4] == {"role": "assistant", "content": "The volume level is normal."}


def test_endpoint_outage_does_not_stall_queue_and_throttles_duplicate_notices(monkeypatch):
    monkeypatch.setattr("koeha.translation.translator.get_key", lambda _: "")
    emitted, errors = [], []
    translator = Translator(Settings(target="en", translation_model="test"), emitted.append, errors.append)
    translator.client.close()
    attempts = 0

    def respond(request):
        nonlocal attempts
        attempts += 1
        if attempts <= 5:
            return httpx.Response(503)
        return httpx.Response(200, json={"choices": [{"message": {"content": "Service recovered."}}]})

    translator.client = httpx.Client(transport=httpx.MockTransport(respond))
    for idx in range(1, 7):
        translator.submit(Caption(idx, float(idx), float(idx + 1), f"line {idx}", True))
    translator.close(grace=5.0)

    assert len(emitted) == 6
    assert all(c.translation_error == "HTTP 503" for c in emitted[:5])
    assert emitted[5].translation == "Service recovered."
    assert len(errors) == 1


def test_queued_old_style_request_keeps_old_context_before_switch_boundary(monkeypatch):
    monkeypatch.setattr("koeha.translation.translator.get_key", lambda _: "")
    emitted, errors = [], []
    payloads = {}
    first_started = threading.Event()
    release_first = threading.Event()
    translator = Translator(Settings(target="en", translation_model="test"), emitted.append, errors.append)
    translator.client.close()
    old_style = translator.system_prompt

    def respond(request):
        messages = json.loads(request.content)["messages"]
        user_text = messages[-1]["content"]
        payloads[user_text] = messages
        if user_text == "first":
            first_started.set()
            assert release_first.wait(2.0)
        return httpx.Response(200, json={"choices": [{"message": {"content": f"TX:{user_text}"}}]})

    translator.client = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        translator.submit(Caption(1, 0.0, 1.0, "first", True))
        assert first_started.wait(2.0)
        translator.submit(Caption(2, 1.0, 2.0, "second", True))
        translator.submit(Caption(3, 2.0, 3.0, "third", True), system_prompt="Updated style")
    finally:
        release_first.set()
        translator.close(grace=5.0)

    assert not errors and [c.id for c in emitted] == [1, 2, 3]
    assert payloads["second"] == [
        {"role": "system", "content": old_style},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "TX:first"},
        {"role": "user", "content": "second"},
    ]
    assert payloads["third"] == [
        {"role": "system", "content": "Updated style"},
        {"role": "user", "content": "third"},
    ]
    assert list(translator.context) == [3]
