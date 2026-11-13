import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import httpx
import keyring

from koeha.translation.connection_profiles import base_url, is_loopback_url
from koeha.translation.prompt_profiles import translation_instruction

SERVICE = "KOEHA.translation"


def get_key(url):
    return keyring.get_password(SERVICE, url) or ""


def save_key(url, key):
    if key:
        keyring.set_password(SERVICE, url, key)
    elif keyring.get_password(SERVICE, url):
        keyring.delete_password(SERVICE, url)


def _client(url, timeout):
    """Loopback traffic must never be routed through a system or environment proxy,
    which would receive the request (and any bearer key) in cleartext. Remote
    servers keep honoring the configured proxy."""
    return httpx.Client(timeout=timeout, follow_redirects=False, trust_env=not is_loopback_url(url))


def _authorization_headers(key):
    if not key:
        return {}
    if not key.isascii() or not key.isprintable() or key != key.strip():
        raise ValueError("The API key contains invalid header characters.")
    return {"Authorization": "Bearer " + key}


def request_error_message(error, key):
    """Keep HTTP status details useful without displaying server-supplied URLs or credentials."""
    if isinstance(error, httpx.HTTPStatusError):
        return f"HTTP {error.response.status_code}"
    message = str(error)
    return message.replace(key, "[redacted]") if key else message


def list_models(url, key="", *, client=None):
    """Use the common models endpoint supported by LM Studio, Ollama and compatible APIs."""
    url = base_url(url)
    headers = _authorization_headers(key)
    def request(session):
        response = session.get(url + "/models", headers=headers, follow_redirects=False)
        response.raise_for_status()
        data = response.json().get("data")
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise ValueError("The server did not return a compatible model list")
        return sorted({item["id"] for item in data if isinstance(item.get("id"), str) and item["id"]})
    if client is not None:
        return request(client)
    with _client(url, timeout=8) as session:
        return request(session)


class Translator:
    def __init__(self, settings, on_caption, on_error):
        self.settings = settings
        self.system_prompt = translation_instruction(settings)
        self.prompt_revision = 0
        self.on_caption = on_caption
        self.on_error = on_error
        credential = "profile:" + settings.translation_profile if settings.translation_profile else settings.translation_url
        self.key = get_key(credential) if settings.target != "off" else ""
        self.client = _client(settings.translation_url, timeout=40)
        self.finals = ThreadPoolExecutor(max_workers=3, thread_name_prefix="koeha-translate")
        self.drafts = ThreadPoolExecutor(max_workers=1, thread_name_prefix="koeha-draft")
        self.lock = threading.Lock()
        self.last_committed = 0
        self.context = {}
        self.pending = 0
        self.draft_future = None
        self.last_draft = 0
        self.final_jobs = {}
        self.closed = False

    def submit(self, caption, *, system_prompt=None):
        if self.settings.target == "off" or not caption.text:
            return
        with self.lock:
            if system_prompt is not None and system_prompt != self.system_prompt:
                self.system_prompt = system_prompt
                self.prompt_revision += 1
                self.context.clear()
                self.last_draft = 0
            if caption.final:
                self.last_committed = max(self.last_committed, caption.id)
                if self.pending >= 64:
                    self.on_caption(replace(caption, translation_error="Translation queue is full"))
                    return
                self.pending += 1
            elif (not self.settings.draft_translation or caption.id <= self.last_committed or
                  time.monotonic() - self.last_draft < .8 or
                  (self.draft_future and not self.draft_future.done())):
                return
            else:
                self.last_draft = time.monotonic()
            context = [self.context[key] for key in sorted(self.context) if key < caption.id][-4:]
            prompt, revision = self.system_prompt, self.prompt_revision
        pool = self.finals if caption.final else self.drafts
        future = pool.submit(self._translate, caption, context, prompt, revision)
        if caption.final:
            with self.lock:
                self.final_jobs[future] = caption
            future.add_done_callback(self._forget)
        else:
            self.draft_future = future

    def _forget(self, future):
        with self.lock:
            self.final_jobs.pop(future, None)

    def _translate(self, caption, context, prompt, revision):
        try:
            messages = [{"role": "system", "content": prompt}]
            for original, translated in context:
                messages.extend([{"role": "user", "content": original},
                                 {"role": "assistant", "content": translated}])
            messages.append({"role": "user", "content": caption.text})
            url = base_url(self.settings.translation_url)
            headers = _authorization_headers(self.key)
            response = self.client.post(url + "/chat/completions",
                headers=headers, follow_redirects=False, json={"model": self.settings.translation_model, "messages": messages,
                                       "temperature": .2, "max_tokens": 1024})
            response.raise_for_status()
            text = response.json()["choices"][0]["message"]["content"].strip()
            with self.lock:
                if caption.final and revision == self.prompt_revision:
                    self.context[caption.id] = (caption.text, text)
                    # Retain bounded context even during long sessions.
                    for key in sorted(self.context)[:-8]:
                        del self.context[key]
                elif not caption.final and (caption.id <= self.last_committed or self.closed or
                                            revision != self.prompt_revision):
                    return
            self.on_caption(replace(caption, translation=text))
        except Exception as error:
            if caption.final:
                message = request_error_message(error, self.key)
                self.on_caption(replace(caption, translation_error=message))
                self.on_error("Translation failed: " + message)
        finally:
            if caption.final:
                with self.lock:
                    self.pending -= 1

    def close(self, grace=30.0):
        """Give queued final translations a bounded time to finish, then cancel the rest."""
        with self.lock:
            self.closed = True
            jobs = dict(self.final_jobs)
        self.drafts.shutdown(wait=False, cancel_futures=True)
        deadline = time.monotonic() + grace
        for future in jobs:
            try:
                future.result(timeout=max(0, deadline - time.monotonic()))
            except Exception:
                pass  # failures are reported by _translate; timeouts are cancelled below
        self.finals.shutdown(wait=False, cancel_futures=True)
        for future, caption in jobs.items():
            if future.cancelled():
                self.on_caption(replace(caption, translation_error="Translation cancelled when the session stopped"))
        self.client.close()
