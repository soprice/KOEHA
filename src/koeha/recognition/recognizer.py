import base64
import io
import json
import queue
import socket
import subprocess
import threading
import time
import wave
from contextlib import nullcontext
from pathlib import Path

import httpx

from koeha.core.runtime_tools import external_popen
from koeha.models.catalog import QWEN_FILES, is_ready, runtime_lock, worker_python
from koeha.recognition.worker import clean_music_hallucinations

CPU_NOTICE = ("Recognition is running on the CPU: the inference runtime found no CUDA GPU. "
              "Captions may lag; choose a faster model or longer chunks.")
LLAMA_MAX_TOKENS = 512


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def wav(pcm):
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(pcm)
    return output.getvalue()


class Recognizer:
    def __init__(self, root, model, language, context="", status=lambda _: None, cancel=lambda: False):
        if model.languages and language not in model.languages and language != "auto":
            raise ValueError(f"{model.name} does not support {language}")
        self.process = None
        self.log = None
        self.log_lock = threading.Lock()
        self.client = None
        self.model = model
        self.language = language
        self.context = context
        self.cancel = cancel
        # "cuda" or "cpu" as reported by a Python worker; empty when the runtime chooses for itself.
        self.device = ""
        self.decodes = 0
        self.latency_total = self.latency_max = self.rate_max = 0.0
        self.tokens_max = 0
        folder = root / "models" / model.id
        python = None if model.runtime == "llama" else worker_python(root, model.runtime)
        launch_lock = runtime_lock(python.parent.parent) if python else nullcontext()
        try:
            # Keep readiness and process creation atomic with maintenance. After Popen,
            # the Windows launcher image protects the worker, including during warmup.
            with launch_lock:
                if not is_ready(root, model):
                    raise RuntimeError("Download the selected model in Models before starting")
                self.log = (root / "recognition.log").open("w", encoding="utf-8")
                self.client = httpx.Client(timeout=45, trust_env=False)
                status("Loading recognition model")
                if python:
                    self.responses = queue.Queue()
                    command = [str(python), "-u", str(Path(__file__).with_name("worker.py")),
                               model.id, str(folder), language, context[:1000]]
                    self.process = external_popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                  stderr=self.log, text=True, encoding="utf-8",
                                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if python is None:
                self._start_server(root, folder)
                status("Warming up recognition model")
                self.decode(bytes(32000), "warmup")
            else:
                threading.Thread(target=self._read, daemon=True).start()
                answer = self._answer(240)
                if not answer.get("ready"):
                    raise RuntimeError(answer.get("error", "Worker did not become ready"))
                self.device = str(answer.get("device") or "")
                self._write_log(f"[ready] device={self.device or 'unknown'}\n")
        except BaseException:
            self.close()
            raise

    def _start_server(self, root, folder):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        names = list(QWEN_FILES)
        command = [str(root / "runtimes/llama/llama-server.exe"), "-m", str(folder / names[0]),
                   "--mmproj", str(folder / names[1]), "--host", "127.0.0.1", "--port", str(port),
                   "-ngl", "99", "-c", "4096", "--parallel", "1", "--jinja", "--no-webui"]
        self.process = external_popen(command, stdout=self.log, stderr=self.log,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if self.cancel():
                raise InterruptedError("Recognition loading cancelled")
            if self.process.poll() is not None:
                raise RuntimeError(f"Recognition runtime exited. See {self.log.name}")
            try:
                if self.client.get(self.url + "/health").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(.2)
        raise TimeoutError("Recognition model did not load within 180 seconds")

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    self.responses.put(json.loads(line))
                except ValueError:
                    self._write_log(line)
        finally:
            self.responses.put({"error": f"Recognition worker exited. See {self.log.name}"})

    def _answer(self, timeout=45):
        deadline = time.monotonic() + timeout
        while True:
            if timeout > 45 and self.cancel():
                raise InterruptedError("Recognition loading cancelled")
            try:
                result = self.responses.get(timeout=min(.2, max(.001, deadline - time.monotonic())))
                break
            except queue.Empty:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Recognition timed out. See {self.log.name}") from None
        if "error" in result:
            raise RuntimeError(result["error"])
        return result

    def _write_log(self, text):
        """The worker reader and the decode thread share this file; logging never stops a session."""
        with self.log_lock:
            try:
                self.log.write(text)
                self.log.flush()
            except (OSError, ValueError):
                pass

    def decode(self, pcm, purpose="decode"):
        began = time.perf_counter()
        text, usage = self._decode_server(pcm) if self.model.runtime == "llama" else self._decode_worker(pcm)
        self._record(purpose, len(pcm) / 32000, time.perf_counter() - began, text, usage)
        return text

    def _record(self, purpose, audio, latency, text, usage):
        """One log line per decode: audio length, wall latency, model time and token usage."""
        tokens, limit, engine, prompt = (_number(usage.get(key))
                                         for key in ("tokens", "limit", "seconds", "prompt_tokens"))
        rate = tokens / audio if tokens is not None and audio > 0 else None
        if purpose == "decode":
            self.decodes += 1
            self.latency_total += latency
            self.latency_max = max(self.latency_max, latency)
            self.tokens_max = max(self.tokens_max, tokens or 0)
            self.rate_max = max(self.rate_max, rate or 0.0)
        fields = [f"audio={audio:.2f}s", f"latency={latency:.3f}s"]
        if engine is not None:
            fields.append(f"engine={engine:.3f}s")
        if tokens is not None:
            fields.append(f"tokens={tokens}")
        if limit is not None:
            fields.append(f"limit={limit}")
        if rate is not None:
            fields.append(f"tokens_per_audio_second={rate:.1f}")
        if prompt is not None:
            fields.append(f"prompt_tokens={prompt}")
        fields.append(f"chars={len(text)}")
        self._write_log(f"[{purpose}] {' '.join(fields)}\n")

    def _decode_worker(self, pcm):
        self.process.stdin.write(json.dumps({"pcm": base64.b64encode(pcm).decode("ascii")}) + "\n")
        self.process.stdin.flush()
        answer = self._answer()
        return clean_music_hallucinations(answer["text"]), answer

    def _decode_server(self, pcm):
        messages = []
        if self.context:
            messages.append({"role": "system", "content": self.context[:1000]})
        messages.append({"role": "user", "content": [{"type": "input_audio", "input_audio": {
            "data": base64.b64encode(wav(pcm)).decode("ascii"), "format": "wav"}}]})
        names = {"ja": "Japanese", "en": "English", "th": "Thai", "zh": "Chinese", "ko": "Korean"}
        if self.language in names:
            messages.append({"role": "assistant", "content": f"language {names[self.language]}<asr_text>"})
        response = self.client.post(self.url + "/v1/chat/completions", json={
            "messages": messages, "temperature": 0, "max_tokens": LLAMA_MAX_TOKENS, "cache_prompt": False})
        response.raise_for_status()
        data = response.json()
        answer = data["choices"][0]["message"]["content"].strip()
        usage = self._server_usage(data)
        if "<asr_text>" in answer:
            head, answer = answer.split("<asr_text>", 1)
            if head.strip() == "language None":
                return "", usage
        elif answer.startswith("language "):
            return "", usage
        return clean_music_hallucinations(answer), usage

    @staticmethod
    def _server_usage(data):
        """Token counts and model time as llama-server reports them; absent fields are simply not logged."""
        counts = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        timings = data.get("timings") if isinstance(data.get("timings"), dict) else {}
        usage = {"tokens": counts.get("completion_tokens"), "prompt_tokens": counts.get("prompt_tokens"),
                 "limit": LLAMA_MAX_TOKENS}
        spent = [_number(timings.get(key)) for key in ("prompt_ms", "predicted_ms")]
        if None not in spent:
            usage["seconds"] = sum(spent) / 1000
        return usage

    def close(self):
        if self.decodes:
            self._write_log(f"[summary] decodes={self.decodes} "
                            f"latency_mean={self.latency_total / self.decodes:.3f}s "
                            f"latency_max={self.latency_max:.3f}s tokens_max={self.tokens_max} "
                            f"tokens_per_audio_second_max={self.rate_max:.1f}\n")
            self.decodes = 0
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.client is not None:
            self.client.close()
        if self.log is not None:
            self.log.close()
