import json
import threading
import time

import httpx

from koeha.core.events import Caption
from koeha.core.settings import Settings
from koeha.translation.prompt_profiles import SUBTITLE_REQUEST_INSTRUCTION
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


def test_live_style_switch_snapshots_requests(monkeypatch):
    monkeypatch.setattr("koeha.translation.translator.get_key", lambda _: "")
    release = threading.Event()
    first_started = threading.Event()
    payloads = {}
    output, errors = [], []

    def respond(request):
        messages = json.loads(request.content)["messages"]
        dialogue = messages[-1]["content"]
        payloads[dialogue] = messages
        if dialogue == "first subtitle":
            first_started.set()
            assert release.wait(3)
        return httpx.Response(200, json={"choices": [{"message": {"content": "translated"}}]})

    translator = Translator(Settings(target="th", translation_model="test"), output.append, errors.append)
    translator.client.close()
    translator.client = httpx.Client(transport=httpx.MockTransport(respond))
    old_style = translator.system_prompt
    try:
        translator.submit(Caption(1, 0, 1, "first subtitle", True))
        assert first_started.wait(3)
        translator.submit(Caption(2, 1, 2, "already submitted", True))
        translator.submit(Caption(3, 2, 3, "new request", True), system_prompt="New style")
    finally:
        release.set()
        translator.close()
    assert not errors and [caption.id for caption in output] == [1, 2, 3]
    for subtitle, style, earlier in (
        ("already submitted", old_style, ["first subtitle"]),
        ("new request", "New style", ["first subtitle", "already submitted"]),
    ):
        assert payloads[subtitle] == [
            {"role": "system", "content": style + "\n\n" + SUBTITLE_REQUEST_INSTRUCTION},
            {"role": "user", "content": "Earlier source subtitles (reference only, oldest first):\n"
             + json.dumps(earlier, ensure_ascii=False)},
            {"role": "assistant", "content": "I will use these as reference only."},
            {"role": "user", "content": subtitle},
        ]


def test_preview_and_final_requests_use_only_recent_final_source_as_reference(monkeypatch):
    monkeypatch.setattr("koeha.translation.translator.get_key", lambda _: "")
    preview_done = threading.Event()
    requests, errors = {}, []

    def on_caption(caption):
        if not caption.final:
            preview_done.set()

    def respond(request):
        messages = json.loads(request.content)["messages"]
        requests[messages[-1]["content"]] = messages
        return httpx.Response(200, json={"choices": [{"message": {"content": "คำแปลเก่า\nข้อความส่วนเกิน"}}]})

    translator = Translator(Settings(target="th", translation_model="test"), on_caption, errors.append)
    translator.client.close()
    translator.client = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        for id_ in range(1, 6):
            translator.submit(Caption(id_, id_ - 1, id_, f"字幕{id_}。", True))
        translator.submit(Caption(6, 5, 6, "次の", False))
        assert preview_done.wait(3)
        translator.submit(Caption(6, 5, 7, "次の字幕。", True))
        translator.submit(Caption(7, 7, 8, "最後の字幕。", True))
    finally:
        translator.close()

    assert not errors
    system = {"role": "system", "content": translator.system_prompt + "\n\n" + SUBTITLE_REQUEST_INSTRUCTION}
    assert requests["字幕1。"] == [system, {"role": "user", "content": "字幕1。"}]
    recent_source = ["字幕2。", "字幕3。", "字幕4。", "字幕5。"]
    for subtitle, earlier in (
        ("次の", recent_source),
        ("次の字幕。", recent_source),
        ("最後の字幕。", recent_source[1:] + ["次の字幕。"]),
    ):
        assert requests[subtitle] == [
            system,
            {"role": "user", "content": "Earlier source subtitles (reference only, oldest first):\n"
             + json.dumps(earlier, ensure_ascii=False)},
            {"role": "assistant", "content": "I will use these as reference only."},
            {"role": "user", "content": subtitle},
        ]


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
    for subtitle, earlier in (
        ("音量レベルは正常です。", ["音声テストを開始します。"]),
        ("このまま録音を続けます。", ["音声テストを開始します。", "音量レベルは正常です。"]),
    ):
        _, reference, acknowledgement, current = payloads[subtitle]
        assert json.loads(reference["content"].split("\n", 1)[1]) == earlier
        assert acknowledgement == {"role": "assistant", "content": "I will use these as reference only."}
        assert current == {"role": "user", "content": subtitle}


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
