import sys
from types import SimpleNamespace

import numpy as np
import pytest

from koeha.recognition.worker import (
    WHISPER_MAX_NEW_TOKENS,
    WHISPER_PROMPT_TOKENS,
    clean_music_hallucinations,
    load_whisper,
    transcribe_whisper,
    whisper_prompt,
)


@pytest.mark.parametrize("device, precision", [("cpu", "float32"), ("cuda", "float16")])
def test_whisper_loads_only_local_assets_with_device_precision(monkeypatch, device, precision):
    calls = []
    model = SimpleNamespace(to=lambda selected: calls.append(selected) or model, eval=lambda: model)
    processor = object()
    def model_loader(folder, **kwargs):
        calls.append((folder, kwargs))
        return model
    def processor_loader(folder, **kwargs):
        calls.append((folder, kwargs))
        return processor
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(float16="float16", float32="float32"))
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(
        AutoProcessor=SimpleNamespace(from_pretrained=processor_loader),
        AutoModelForSpeechSeq2Seq=SimpleNamespace(from_pretrained=model_loader)))
    assert load_whisper("local-model", device) == (model, processor)
    assert calls == [
        ("local-model", {"local_files_only": True, "trust_remote_code": False}),
        ("local-model", {"local_files_only": True, "trust_remote_code": False, "dtype": precision}), device]


@pytest.mark.parametrize("language", ["auto", "ja", "th"])
def test_whisper_keeps_mask_and_transcribes_in_the_selected_language(language):
    audio = np.zeros(16000, dtype=np.float32)
    features = SimpleNamespace(to=lambda **kwargs: "float16 features" if kwargs["dtype"] == "float16" else None)
    mask = object()
    class Inputs(dict):
        def to(self, device):
            assert device == "cuda"
            return self
    class Processor:
        def __call__(self, samples, **kwargs):
            assert samples is audio and kwargs == {
                "sampling_rate": 16000, "return_tensors": "pt", "return_attention_mask": True}
            return Inputs(input_features=features, attention_mask=mask)

        def batch_decode(self, generated, **kwargs):
            assert generated == [10, 20] and kwargs == {"skip_special_tokens": True}
            return ["recognized dialogue"]
    def generate(**kwargs):
        assert kwargs["attention_mask"] is mask
        assert kwargs["input_features"] == "float16 features"
        assert kwargs["language"] == (None if language == "auto" else language)
        assert kwargs["task"] == "transcribe" and not kwargs["do_sample"]
        assert kwargs["max_new_tokens"] < 448
        return [10, 20]
    model = SimpleNamespace(dtype="float16", generate=generate)
    assert transcribe_whisper(model, Processor(), audio, language, "cuda") == "recognized dialogue"


class PromptProcessor:
    def __init__(self, decoded):
        self.decoded = decoded

    def __call__(self, samples, **kwargs):
        class Inputs(dict):
            def to(self, device):
                return self
        return Inputs(input_features=SimpleNamespace(to=lambda **kwargs: "features"))

    def batch_decode(self, generated, **kwargs):
        return [self.decoded]


def test_whisper_prompt_is_skipped_without_context():
    assert whisper_prompt(object(), "  \n", "cpu") is None


def test_whisper_prompt_is_capped_and_moved_to_the_device():
    class Ids(list):
        def __getitem__(self, index):
            return Ids(list.__getitem__(self, index))
        def to(self, device):
            assert device == "cuda"
            return self
    class Processor:
        def get_prompt_ids(self, text, return_tensors):
            assert len(text) == 1000 and return_tensors == "pt"
            return Ids(range(500))
    assert len(whisper_prompt(Processor(), "x" * 2000, "cuda")) == WHISPER_PROMPT_TOKENS


def test_whisper_prompt_shrinks_the_token_budget():
    seen = {}
    def generate(**kwargs):
        seen.update(kwargs)
        return [1]
    model = SimpleNamespace(dtype="float16", generate=generate)
    prompt = list(range(100))
    text = transcribe_whisper(model, PromptProcessor("live words"), np.zeros(1), "ja", "cpu", prompt, "Scene")
    assert text == "live words"
    assert seen["prompt_ids"] is prompt and seen["max_new_tokens"] == WHISPER_MAX_NEW_TOKENS - 100


def test_whisper_drops_output_that_only_repeats_the_prompt():
    model = SimpleNamespace(dtype="float16", generate=lambda **kwargs: [1])
    context = "Stream about Dragon Quest and Slime Knights"
    echo = PromptProcessor("Dragon Quest and Slime")
    assert transcribe_whisper(model, echo, np.zeros(1), "ja", "cpu", [1], context) == ""
    short = PromptProcessor("Dragon")
    assert transcribe_whisper(model, short, np.zeros(1), "ja", "cpu", [1], context) == "Dragon"


def test_clean_music_hallucinations_filters_notes_markers_and_repeat_loops():
    assert clean_music_hallucinations("♪ ♪ ♪") == ""
    assert clean_music_hallucinations("[Music]") == ""
    assert clean_music_hallucinations("（音楽）") == ""
    assert clean_music_hallucinations("♪ 君の声が聞こえる ♪") == "君の声が聞こえる"
    assert clean_music_hallucinations("ララララララララ") == "ラララ"


@pytest.mark.parametrize("text", [
    "1000000", "100000円", "2000000人", "ID 7000001", "0.00001", "10,000,000", "１００００００",
    "電話は000000番です"])
def test_repeat_filter_never_rewrites_numbers(text):
    assert clean_music_hallucinations(text) == text


@pytest.mark.parametrize("text, cleaned", [
    ("ララララララララ", "ラララ"), ("ははははは", "ははは"), ("no no no no no no", "no no no no"),
    ("la la la la la la la", "la la la la"), ("アハハハハハ", "アハハハ"),
    ("残り1000000円ララララララ", "残り1000000円ラララ")])
def test_repeat_filter_still_collapses_held_notes(text, cleaned):
    assert clean_music_hallucinations(text) == cleaned


def test_whisper_reports_token_usage_without_changing_the_limit():
    from koeha.recognition import worker
    worker.USAGE.clear()
    model = SimpleNamespace(dtype="float16", generate=lambda **kwargs: [[5, 6, 7]])
    assert transcribe_whisper(model, PromptProcessor("live words"), np.zeros(1), "ja", "cpu") == "live words"
    assert worker.USAGE == {"tokens": 3, "limit": WHISPER_MAX_NEW_TOKENS}


def test_usage_reads_tensor_shapes_and_ignores_unknown_results():
    from koeha.recognition import worker
    worker.USAGE.clear()
    worker.record_usage(SimpleNamespace(shape=(1, 37)), 512)
    assert worker.USAGE == {"tokens": 37, "limit": 512}
    worker.record_usage(None)
    assert worker.USAGE == {"limit": None}


def test_usage_does_not_count_transducer_blanks():
    from koeha.recognition import worker
    worker.USAGE.clear()
    frames = np.array([[9, 4, 9, 9, 7, 9, 9, 9]])  # two emitted tokens among blank frames
    worker.record_usage(frames, blank=9)
    assert worker.USAGE == {"tokens": 2, "limit": None}
    worker.record_usage(np.full((1, 27), 9), blank=9)
    assert worker.USAGE["tokens"] == 0
