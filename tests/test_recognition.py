import hashlib
import json
import queue
import threading

import httpx
import pytest

from koeha.app.controller import Controller
from koeha.core.i18n import THAI
from koeha.core.settings import Settings
from koeha.models.catalog import by_id
from koeha.recognition import worker
from koeha.recognition.recognizer import CPU_NOTICE, LLAMA_MAX_TOKENS, Recognizer


def test_parakeet_checks_the_pinned_file_before_loading(tmp_path, monkeypatch):
    payload = b"test checkpoint"
    monkeypatch.setattr(worker, "PARAKEET_SHA256", hashlib.sha256(payload).hexdigest())
    (tmp_path / "other.nemo").write_bytes(payload)
    with pytest.raises(RuntimeError, match="missing or has changed"):
        worker.verified_parakeet_checkpoint(tmp_path)
    expected = tmp_path / worker.PARAKEET_FILENAME
    expected.write_bytes(payload)
    assert worker.verified_parakeet_checkpoint(tmp_path) == expected
    expected.write_bytes(b"modified checkpoint")
    with pytest.raises(RuntimeError, match="missing or has changed"):
        worker.verified_parakeet_checkpoint(tmp_path)


class FakeWorker:
    """Stands in for the inference process: one scripted JSON answer per request line."""
    def __init__(self, ready, answers):
        self.lines = queue.Queue()
        self.lines.put(json.dumps(ready) + "\n")
        self.answers = iter(answers)
        self.requests = []
        self.stdin = self
        self.stdout = iter(self.lines.get, None)

    def write(self, line):
        self.requests.append(json.loads(line))
        self.lines.put(json.dumps(next(self.answers)) + "\n")

    def flush(self):
        pass

    def poll(self):
        return 0


def recognizer(tmp_path, monkeypatch, ready, answers):
    worker = FakeWorker(ready, answers)
    monkeypatch.setattr("koeha.recognition.recognizer.is_ready", lambda *_: True)
    monkeypatch.setattr("koeha.recognition.recognizer.external_popen", lambda *_, **__: worker)
    return Recognizer(tmp_path, by_id("whisper-large-v3"), "ja"), worker


def log_lines(tmp_path, tag):
    text = (tmp_path / "recognition.log").read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line.startswith(f"[{tag}]")]


def test_worker_device_is_kept_and_logged(tmp_path, monkeypatch):
    decoder, worker = recognizer(tmp_path, monkeypatch, {"ready": True, "device": "cpu"}, [])
    try:
        assert decoder.device == "cpu"
    finally:
        decoder.close()
        worker.lines.put(None)
    assert log_lines(tmp_path, "ready") == ["[ready] device=cpu"]


def test_each_decode_logs_latency_and_token_usage(tmp_path, monkeypatch):
    answers = [{"text": "こんにちは", "seconds": 0.25, "tokens": 12, "limit": 440},
               {"text": "ラララララララ", "seconds": 0.5, "tokens": 40, "limit": 440}]
    decoder, worker = recognizer(tmp_path, monkeypatch, {"ready": True, "device": "cuda"}, answers)
    try:
        assert decoder.decode(bytes(64000)) == "こんにちは"
        assert decoder.decode(bytes(32000)) == "ラララ"
    finally:
        decoder.close()
        worker.lines.put(None)
    first, second = log_lines(tmp_path, "decode")
    assert first.startswith("[decode] audio=2.00s latency=")
    assert first.endswith("engine=0.250s tokens=12 limit=440 tokens_per_audio_second=6.0 chars=5")
    assert second.endswith("engine=0.500s tokens=40 limit=440 tokens_per_audio_second=40.0 chars=3")
    summary, = log_lines(tmp_path, "summary")
    assert summary.startswith("[summary] decodes=2 latency_mean=")
    assert summary.endswith("tokens_max=40 tokens_per_audio_second_max=40.0")
    assert "こんにちは" not in (tmp_path / "recognition.log").read_text(encoding="utf-8")


def test_answers_without_usage_fields_still_decode(tmp_path, monkeypatch):
    decoder, worker = recognizer(tmp_path, monkeypatch, {"ready": True}, [{"text": " hello "}])
    try:
        assert decoder.device == ""
        assert decoder.decode(bytes(32000)) == "hello"
    finally:
        decoder.close()
        worker.lines.put(None)
    line, = log_lines(tmp_path, "decode")
    assert "tokens" not in line and "engine" not in line and line.endswith("chars=5")


def test_logging_after_close_does_not_raise(tmp_path, monkeypatch):
    decoder, worker = recognizer(tmp_path, monkeypatch, {"ready": True, "device": "cuda"}, [])
    decoder.close()
    worker.lines.put(None)
    decoder._write_log("late worker output\n")


def test_server_usage_reads_llama_token_counts_and_timings():
    usage = Recognizer._server_usage({"usage": {"completion_tokens": 21, "prompt_tokens": 180},
                                      "timings": {"prompt_ms": 120.0, "predicted_ms": 380.0}})
    assert usage == {"tokens": 21, "prompt_tokens": 180, "limit": LLAMA_MAX_TOKENS, "seconds": 0.5}
    assert Recognizer._server_usage({}) == {"tokens": None, "prompt_tokens": None, "limit": LLAMA_MAX_TOKENS}
    assert LLAMA_MAX_TOKENS == 512


def test_qwen_asr_sends_audio_and_language_without_translation_scene_context(tmp_path, monkeypatch):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "language Japanese<asr_text>歌う。"}}]})

    def start_server(decoder, *_):
        decoder.url = "http://127.0.0.1:1"
        decoder.client.close()
        decoder.client = httpx.Client(transport=httpx.MockTransport(respond))

    monkeypatch.setattr("koeha.recognition.recognizer.is_ready", lambda *_: True)
    monkeypatch.setattr(Recognizer, "_start_server", start_server)
    scene = "This audio comes from Japanese VTuber and VSinger MEDA."
    decoder = Recognizer(tmp_path, by_id("qwen3-asr"), "ja", scene)
    try:
        assert decoder.decode(bytes(64000)) == "歌う。"
    finally:
        decoder.close()

    assert len(requests) == 2
    for payload in requests:
        audio, language = payload["messages"]
        assert audio["role"] == "user" and audio["content"][0]["type"] == "input_audio"
        assert language == {"role": "assistant", "content": "language Japanese<asr_text>"}
        assert scene not in json.dumps(payload)


def run_session(app, tmp_path, monkeypatch, device):
    class Decoder:
        def __init__(self, *_, **__):
            self.device = device
        def decode(self, _):
            return ""
        def close(self):
            pass
    monkeypatch.setenv("KOEHA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("koeha.app.controller.Recognizer", Decoder)
    monkeypatch.setattr("koeha.app.controller.VoiceDetector", lambda *_, **__: None)
    monkeypatch.setattr("koeha.app.controller.capture", lambda *_: iter(()))
    controller = Controller()
    controller.stop_event = threading.Event()
    messages = []
    controller.error.connect(messages.append)
    controller._run(Settings(), by_id("whisper-large-v3"), None, "")
    return messages


def test_cpu_recognition_is_announced_once(app, tmp_path, monkeypatch):
    assert run_session(app, tmp_path, monkeypatch, "cpu") == [CPU_NOTICE]
    assert CPU_NOTICE in THAI


def test_gpu_and_native_runtimes_show_no_notice(app, tmp_path, monkeypatch):
    assert run_session(app, tmp_path, monkeypatch, "cuda") == []
    assert run_session(app, tmp_path, monkeypatch, "") == []
