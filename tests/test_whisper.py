import math
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from koeha.recognition.worker import (
    WHISPER_MAX_NEW_TOKENS,
    WHISPER_PROMPT_TOKENS,
    clean_music_hallucinations,
    echoes_prompt,
    load,
    load_whisper,
    transcribe_whisper,
    whisper_prompt,
    whisper_prompt_context,
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


def test_whisper_prompt_squeezes_2d_prompt_ids_before_capping():
    class Tensor2D:
        def __init__(self, array):
            self._array = array
            self.shape = array.shape
            self.ndim = array.ndim

        def reshape(self, *shape):
            return Tensor2D(self._array.reshape(*shape))

        def __getitem__(self, index):
            return Tensor2D(self._array[index])

        def __len__(self):
            return len(self._array)

        def to(self, device):
            assert device == "cuda"
            return self

    class Processor:
        def get_prompt_ids(self, text, return_tensors):
            assert return_tensors == "pt"
            return Tensor2D(np.arange(150, dtype=np.int64).reshape(1, 150))

    result = whisper_prompt(Processor(), "Host: Dragon Quest", "cuda")
    assert result.shape == (WHISPER_PROMPT_TOKENS,)
    assert result.ndim == 1
    assert len(result) == WHISPER_PROMPT_TOKENS


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


def _logits_for_prob(token_id, probability, vocab_size=16, suppressed=()):
    logits = np.zeros((1, vocab_size), dtype=np.float32)
    for blocked in suppressed:
        logits[0, blocked] = -np.inf
    active_others = vocab_size - 1 - len(suppressed)
    logits[0, token_id] = math.log((probability / (1.0 - probability)) * active_others)
    return logits


class TokenProcessor:
    """Mock Whisper processor that exposes prompt token leaks if batch_decode ever receives them."""

    def __init__(self, vocab, special_ids=(50257, 50258, 50266, 50359, 50361, 50363)):
        self.vocab = vocab
        self.tokenizer = SimpleNamespace(all_special_ids=list(special_ids))
        self.decoded_batches = []

    def __call__(self, samples, **kwargs):
        class Inputs(dict):
            def to(self, device):
                return self

        return Inputs(input_features=SimpleNamespace(to=lambda **kwargs: "features"))

    def batch_decode(self, generated, **kwargs):
        self.decoded_batches.append([list(row) for row in generated])
        special = set(self.tokenizer.all_special_ids)
        return ["".join(self.vocab.get(token, "") for token in generated[0] if token not in special)]


class FakeScoredModel:
    """Mock Whisper model matching transformers 5.19.0 short-form return shapes."""

    def __init__(self, generated_tokens, step_probs, *, eos_id=15, no_ts_id=14, prompt_tokens=None,
                 suppressed=()):
        self.dtype = "float16"
        self.generation_config = SimpleNamespace(
            eos_token_id=eos_id, pad_token_id=eos_id, no_timestamps_token_id=no_ts_id)
        self.generated_tokens = list(generated_tokens)
        self.step_probs = list(step_probs)
        self.prompt_tokens = list(prompt_tokens or [])
        self.suppressed = tuple(suppressed)
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("return_dict_in_generate") and kwargs.get("output_scores"):
            raw_prompt = kwargs.get("prompt_ids")
            prompt_ids = (self.prompt_tokens if raw_prompt is None
                          else [int(t) for t in np.asarray(raw_prompt).reshape(-1)])
            init_tokens = [50258, 50266, 50359, 50363]
            seq = prompt_ids + init_tokens + self.generated_tokens + [self.generation_config.eos_token_id]
            vocab_size = max([16, self.generation_config.eos_token_id + 1,
                              *(t + 1 for t in self.generated_tokens), *(s + 1 for s in self.suppressed)])
            scores = tuple(
                _logits_for_prob(token, prob, vocab_size=vocab_size, suppressed=self.suppressed)
                for token, prob in zip(self.generated_tokens + [self.generation_config.eos_token_id],
                                       self.step_probs + [0.99])
            )
            return SimpleNamespace(sequences=np.asarray([seq], dtype=np.int64), scores=scores)
        return [list(self.generated_tokens)]


def test_whisper_drops_output_that_only_repeats_the_prompt():
    context = "Stream about Dragon Quest and Slime Knights"
    echo_processor = TokenProcessor({100: "PROMPT_CTX ", 10: "Dragon Quest", 11: " and Slime"})
    # Long multi-word echo is dropped directly without needing a confidence rescue pass.
    long_echo_model = FakeScoredModel([10, 11], [0.95, 0.95])
    assert transcribe_whisper(long_echo_model, echo_processor, np.zeros(1), "ja", "cpu",
                              [50361, 100], context) == ""
    assert len(long_echo_model.calls) == 1

    # Short proper nouns (< 8 chars) still undergo scored verification rather than bypassing it.
    low_short_model = FakeScoredModel([10], [0.35])
    assert transcribe_whisper(low_short_model, TokenProcessor({10: "Dragon"}), np.zeros(1), "ja", "cpu",
                              [50361, 100], context) == ""
    assert len(low_short_model.calls) == 2

    high_short_model = FakeScoredModel([10], [0.92])
    assert transcribe_whisper(high_short_model, TokenProcessor({10: "Dragon"}), np.zeros(1), "ja", "cpu",
                              [50361, 100], context) == "Dragon"
    assert len(high_short_model.calls) == 2


def test_whisper_drops_structural_and_long_prompt_echoes_even_with_high_scores():
    context = "Host: Dragon Quest and Slime Knights"
    for phrase in ("Host: Dragon Quest and Slime Knights", "Dragon Quest and Slime", "Host: Dragon", "Host:Dragon"):
        model = FakeScoredModel([4, 5, 6], [0.98, 0.98, 0.98])
        assert transcribe_whisper(model, PromptProcessor(phrase), np.zeros(1), "ja", "cpu",
                                  [50361, 1], context) == ""
        assert len(model.calls) == 1

    jp_context = "配信：兎田ぺこら、星街すいせい"
    jp_model = FakeScoredModel([4, 5], [0.98, 0.98])
    assert transcribe_whisper(jp_model, PromptProcessor("配信：兎田ぺこら、星街すいせい"),
                              np.zeros(1), "ja", "cpu", [50361, 1], jp_context) == ""
    assert len(jp_model.calls) == 1

    gloss_context = "登場人物：りん（リン）、Rin (りん)、凛［りん］、Cast: Rin, Len, Miku"
    for gloss in ("りん（リン）", "Rin (りん)", "凛［りん］", "Rin, Len, Miku"):
        gloss_model = FakeScoredModel([4, 5, 6], [0.98, 0.98, 0.98])
        assert transcribe_whisper(gloss_model, PromptProcessor(gloss),
                                  np.zeros(1), "ja", "cpu", [50361, 1], gloss_context) == ""
        assert len(gloss_model.calls) == 1

    spoken_gloss_term = FakeScoredModel([4], [0.92])
    assert transcribe_whisper(spoken_gloss_term, PromptProcessor("りん"),
                              np.zeros(1), "ja", "cpu", [50361, 1], gloss_context) == "りん"
    assert len(spoken_gloss_term.calls) == 2


@pytest.mark.parametrize("probability, expected", [(0.95, "New York Comic Con."), (0.30, "")])
def test_whisper_scores_a_long_prompt_list_item(probability, expected):
    context = "Rin, New York Comic Con, Len"
    processor = TokenProcessor({100: context, 4: "New York", 5: " Comic Con."})
    processor.tokenizer.decode = lambda ids, **kwargs: "".join(processor.vocab.get(token, "") for token in ids)
    model = FakeScoredModel([4, 5], [probability, 0.95])

    assert transcribe_whisper(model, processor, np.zeros(1), "en", "cpu", [50361, 100]) == expected
    assert [call.get("output_scores", False) for call in model.calls] == [False, True]


@pytest.mark.parametrize("context, echo", [
    ("Rin, Visit New York Comic Con tomorrow, Len", "New York Comic Con"),
    ("New York Comic Con, Len", "New York Comic Con, Len"),
    ("Rin, New York Comic Con (NYCC), Len", "New York Comic Con (NYCC)"),
    ("Host:Rin", "Host:Rin"),
])
def test_whisper_drops_long_substrings_and_structured_prompt_items(context, echo):
    model = FakeScoredModel([4], [0.99])

    assert transcribe_whisper(model, PromptProcessor(echo), np.zeros(1), "en", "cpu", [50361, 100], context) == ""
    assert len(model.calls) == 1


def test_whisper_checks_short_names_under_eight_chars_and_avoids_partial_ascii_substrings():
    jp_context = "配信：兎田ぺこら、星街すいせい、凛"
    assert transcribe_whisper(FakeScoredModel([4], [0.30]), PromptProcessor("ぺこら"),
                              np.zeros(1), "ja", "cpu", [50361, 1], jp_context) == ""
    assert transcribe_whisper(FakeScoredModel([4], [0.92]), PromptProcessor("ぺこら"),
                              np.zeros(1), "ja", "cpu", [50361, 1], jp_context) == "ぺこら"

    low_kanji_model = FakeScoredModel([4], [0.30])
    assert transcribe_whisper(low_kanji_model, PromptProcessor("凛"),
                              np.zeros(1), "ja", "cpu", [50361, 1], jp_context) == ""
    assert len(low_kanji_model.calls) == 2

    high_kanji_model = FakeScoredModel([4], [0.92])
    assert transcribe_whisper(high_kanji_model, PromptProcessor("凛"),
                              np.zeros(1), "ja", "cpu", [50361, 1], jp_context) == "凛"
    assert len(high_kanji_model.calls) == 2

    en_context = "Host: Dragon Quest and Slime Knights, a"
    assert not echoes_prompt("on", en_context)
    assert not echoes_prompt("a", en_context)
    one_pass_model = FakeScoredModel([4], [0.20])
    assert transcribe_whisper(one_pass_model, PromptProcessor("on"),
                              np.zeros(1), "ja", "cpu", [50361, 1], en_context) == "on"
    assert len(one_pass_model.calls) == 1


def test_whisper_allows_confidence_scoring_for_short_comma_phrases():
    en_context = "Scene: Thank you, Rin"
    high_model = FakeScoredModel([4, 5, 6], [0.92, 0.90, 0.91])
    assert transcribe_whisper(high_model, PromptProcessor("Thank you, Rin"),
                              np.zeros(1), "ja", "cpu", [50361, 1], en_context) == "Thank you, Rin"
    assert len(high_model.calls) == 2

    low_model = FakeScoredModel([4, 5, 6], [0.30, 0.95, 0.95])
    assert transcribe_whisper(low_model, PromptProcessor("Thank you, Rin"),
                              np.zeros(1), "ja", "cpu", [50361, 1], en_context) == ""
    assert len(low_model.calls) == 2

    jp_context = "配信：ありがとう、凛"
    jp_high_model = FakeScoredModel([4, 5], [0.92, 0.91])
    assert transcribe_whisper(jp_high_model, PromptProcessor("ありがとう、凛"),
                              np.zeros(1), "ja", "cpu", [50361, 1], jp_context) == "ありがとう、凛"
    assert len(jp_high_model.calls) == 2

    jp_low_model = FakeScoredModel([4, 5], [0.30, 0.92])
    assert transcribe_whisper(jp_low_model, PromptProcessor("ありがとう、凛"),
                              np.zeros(1), "ja", "cpu", [50361, 1], jp_context) == ""
    assert len(jp_low_model.calls) == 2

    leading_comma_model = FakeScoredModel([4], [0.98])
    assert transcribe_whisper(leading_comma_model, PromptProcessor(", Rin"),
                              np.zeros(1), "ja", "cpu", [50361, 1], en_context) == ""
    assert len(leading_comma_model.calls) == 1


def test_whisper_echo_guard_compares_only_against_truncated_prompt_tokens(monkeypatch):
    start_of_prev = 50361
    # Tokenize words onto ids 1000.. so only the first 127 content tokens survive WHISPER_PROMPT_TOKENS.
    long_context = "Host: Slime Knights " + " ".join(f"w{i:x}" for i in range(130)) + " Dragon Quest"
    assert len(long_context) <= 1000
    word_to_id = {}
    id_to_word = {}

    class TruncatingTokenizer:
        all_special_ids = [50257, 50258, start_of_prev]

        @staticmethod
        def convert_tokens_to_ids(token):
            return start_of_prev if token == "<|startofprev|>" else 50257

        @staticmethod
        def decode(token_ids, skip_special_tokens=False):
            ids = list(token_ids)
            # Replicate WhisperTokenizer._strip_prompt: <|startofprev|> without <|startoftranscript|>
            # strips the whole sequence when skip_special_tokens=True.
            if skip_special_tokens and ids and ids[0] == start_of_prev:
                return ""
            return " ".join(id_to_word[t] for t in ids if t in id_to_word)

    class TruncatingProcessor:
        tokenizer = TruncatingTokenizer()

        def __call__(self, samples, **kwargs):
            class Inputs(dict):
                def to(self, device):
                    return self
            return Inputs(input_features=SimpleNamespace(to=lambda **kwargs: "features"))

        def get_prompt_ids(self, text, return_tensors="pt"):
            tokens = [start_of_prev]
            for word in text.split():
                idx = word_to_id.setdefault(word, 1000 + len(word_to_id))
                id_to_word[idx] = word
                tokens.append(idx)
            return np.asarray([tokens], dtype=np.int64)

        def batch_decode(self, generated, **kwargs):
            return [" ".join(id_to_word.get(t, "Dragon Quest") for t in generated[0])]

    processor = TruncatingProcessor()
    prompt = whisper_prompt(processor, long_context, "cpu")
    assert prompt.shape == (WHISPER_PROMPT_TOKENS,)
    effective = whisper_prompt_context(processor, prompt, long_context)
    assert "Slime Knights" in effective
    assert "Dragon Quest" not in effective

    model = FakeScoredModel([9001], [0.20])
    monkeypatch.setattr("koeha.recognition.worker.load_whisper", lambda folder, device: (model, processor))
    transcribe = load("whisper-large-v3", "unused", "ja", long_context, "cpu")

    # Dragon Quest fell outside the 128-token prompt, so it passes in 1 call even with low scores.
    assert transcribe(np.zeros(1)) == "Dragon Quest"
    assert len(model.calls) == 1

    # Slime Knights was inside the truncated prompt, so a low-score echo is still dropped,
    # while high-confidence spoken Slime Knights is preserved on the second pass.
    slime_ids = [word_to_id["Slime"], word_to_id["Knights"]]
    slime_model = FakeScoredModel(slime_ids, [0.30, 0.30], eos_id=2001, no_ts_id=2000)
    assert transcribe_whisper(slime_model, processor, np.zeros(1), "ja", "cpu",
                              prompt, long_context) == ""
    assert len(slime_model.calls) == 2

    high_slime_model = FakeScoredModel(slime_ids, [0.95, 0.95], eos_id=2001, no_ts_id=2000)
    assert transcribe_whisper(high_slime_model, processor, np.zeros(1), "ja", "cpu",
                              prompt, long_context) == "Slime Knights"
    assert len(high_slime_model.calls) == 2


def test_whisper_keeps_confident_speech_matching_prompt_without_leaking_prompt_or_usage():
    from koeha.recognition import worker

    worker.USAGE.clear()
    context = "Stream about Dragon Quest and Slime Knights"
    prompt = [50361, 1, 2, 3]
    processor = TokenProcessor({1: "LEAKED ", 2: "PROMPT ", 3: "TEXT ", 4: "Dragon", 5: " Quest"})
    model = FakeScoredModel([4, 5], [0.92, 0.88])

    result = transcribe_whisper(model, processor, np.zeros(1), "ja", "cpu", prompt, context)

    assert result == "Dragon Quest"
    assert processor.decoded_batches == [[[4, 5]]]
    assert worker.USAGE == {"tokens": 2, "limit": WHISPER_MAX_NEW_TOKENS - len(prompt)}
    assert len(model.calls) == 2


def test_whisper_drops_prompt_copy_hallucination_with_low_onset_confidence():
    context = "Stream about Dragon Quest and Slime Knights"
    processor = TokenProcessor({4: "Dragon", 5: " Quest", 6: " and", 7: " Slime"})
    # Low onset probability followed by deterministic prompt-copy probabilities still drops.
    model = FakeScoredModel([4, 5, 6, 7], [0.25, 0.95, 0.95, 0.95])
    assert transcribe_whisper(model, processor, np.zeros(1), "ja", "cpu", [50361, 1], context) == ""


def test_whisper_ignores_eos_confidence_when_prompt_echo_content_is_uncertain():
    context = "Stream about Dragon Quest and Slime Knights"
    processor = TokenProcessor({4: "Dragon", 5: " Quest"})
    model = FakeScoredModel([4, 5], [0.45, 0.45])  # EOS gets 0.99 inside FakeScoredModel
    assert transcribe_whisper(model, processor, np.zeros(1), "ja", "cpu", [50361, 1], context) == ""


def test_whisper_fails_closed_when_scored_pass_has_no_usable_scores():
    context = "Stream about Dragon Quest and Slime Knights"
    echo = PromptProcessor("Dragon Quest and Slime")
    no_scores_model = SimpleNamespace(dtype="float16", generate=lambda **kwargs: [1])
    assert transcribe_whisper(no_scores_model, echo, np.zeros(1), "ja", "cpu", [1], context) == ""

    empty_scores_model = SimpleNamespace(
        dtype="float16",
        generate=lambda **kwargs: SimpleNamespace(sequences=[[10, 11]], scores=())
        if kwargs.get("output_scores") else [[10, 11]],
    )
    assert transcribe_whisper(empty_scores_model, echo, np.zeros(1), "ja", "cpu", [1], context) == ""

    short_seq_model = SimpleNamespace(
        dtype="float16",
        generate=lambda **kwargs: SimpleNamespace(sequences=[[10]], scores=(np.zeros((1, 16)), np.zeros((1, 16))))
        if kwargs.get("output_scores") else [[10, 11]],
    )
    assert transcribe_whisper(short_seq_model, echo, np.zeros(1), "ja", "cpu", [1], context) == ""


def test_whisper_skips_second_pass_for_normal_speech_and_pure_music():
    context = "Stream about Dragon Quest and Slime Knights"
    calls = []

    def make_model():
        calls.clear()
        def generate(**kwargs):
            calls.append(kwargs)
            return [[1]]
        return SimpleNamespace(dtype="float16", generate=generate)

    assert transcribe_whisper(make_model(), PromptProcessor("Playing Dragon Quest"),
                              np.zeros(1), "ja", "cpu", [1], context) == "Playing Dragon Quest"
    assert len(calls) == 1 and "output_scores" not in calls[0]

    assert transcribe_whisper(make_model(), PromptProcessor("♪ ♪ ♪"),
                              np.zeros(1), "ja", "cpu", [1], context) == ""
    assert len(calls) == 1 and "output_scores" not in calls[0]


def test_whisper_handles_punctuated_and_music_wrapped_prompt_echoes():
    context = "Stream about Dragon Quest and Slime Knights"
    assert echoes_prompt("Dragon Quest and Slime.", context)
    assert echoes_prompt("dragon quest", context)
    assert echoes_prompt("Dragon!", context)
    assert not echoes_prompt("a", context)

    long_music_echo = PromptProcessor("♪ Dragon Quest and Slime ♪")
    long_music_model = FakeScoredModel([4, 5, 6, 7], [0.90, 0.90, 0.90, 0.90])
    assert transcribe_whisper(long_music_model, long_music_echo, np.zeros(1), "ja", "cpu",
                              [50361, 1], context) == ""
    assert len(long_music_model.calls) == 1

    music_echo = PromptProcessor("♪ Dragon Quest ♪")
    low_model = FakeScoredModel([4, 5], [0.35, 0.35])
    assert transcribe_whisper(low_model, music_echo, np.zeros(1), "ja", "cpu", [50361, 1], context) == ""
    assert len(low_model.calls) == 2

    punctuated_speech = PromptProcessor("Dragon Quest.")
    high_model = FakeScoredModel([4, 5], [0.90, 0.90])
    assert transcribe_whisper(high_model, punctuated_speech, np.zeros(1), "ja", "cpu",
                              [50361, 1], context) == "Dragon Quest."


def test_whisper_scored_guard_handles_tensor_like_objects_suppressed_logits_and_timestamps():
    class FakeTensor:
        def __init__(self, array, floating=True):
            self._array = array
            self.dtype = SimpleNamespace(is_floating_point=floating)

        def detach(self):
            return self

        def cpu(self):
            return self

        def float(self):
            return FakeTensor(self._array.astype(np.float32), floating=True)

        def __array__(self, dtype=None):
            return np.asarray(self._array, dtype=dtype)

    context = "Stream about Dragon Quest and Slime Knights"
    processor = TokenProcessor({1: "PROMPT ", 4: "Dragon", 5: " Quest"})
    calls = []

    def generate(**kwargs):
        calls.append(kwargs)
        if kwargs.get("return_dict_in_generate"):
            # Include prompt prefix, init tokens, a leading timestamp token (15 >= no_timestamps+1),
            # content tokens [4, 5], and EOS (13).
            seq = FakeTensor(np.array([[50361, 1, 50258, 50266, 50359, 50363, 15, 4, 5, 13]], dtype=np.int64),
                             floating=False)
            scores = (
                FakeTensor(_logits_for_prob(0, 0.5, vocab_size=16).astype(np.float16)),
                FakeTensor(_logits_for_prob(4, 0.90, vocab_size=16, suppressed=(0, 1)).astype(np.float16)),
                FakeTensor(_logits_for_prob(5, 0.88, vocab_size=16, suppressed=(0, 1)).astype(np.float16)),
                FakeTensor(_logits_for_prob(13, 0.99, vocab_size=16).astype(np.float16)),
            )
            return {"sequences": seq, "scores": scores}
        return [[4, 5]]

    model = SimpleNamespace(
        dtype="float16",
        config=SimpleNamespace(eos_token_id=13, pad_token_id=13, no_timestamps_token_id=14),
        generate=generate,
    )
    assert transcribe_whisper(model, processor, np.zeros(1), "ja", "cpu", [50361, 1], context) == "Dragon Quest"
    assert len(calls) == 2


class _MockFeatures:
    def to(self, **kwargs):
        return self


class _MockInputs(dict):
    def to(self, device):
        return self


def _make_echo_harness(token_map, step_probs, context, prefix=(50361, 1)):
    calls = []
    decode_map = {1: context, **token_map}

    def decode(tokens, skip_special_tokens=True):
        return "".join(decode_map.get(int(tok), "") for tok in tokens)

    tokenizer = SimpleNamespace(
        decode=decode,
        all_special_ids=[100, 101, 50258, 50361],
        convert_tokens_to_ids=lambda tok: {
            "<|startofprev|>": 50361,
            "<|startoftranscript|>": 50258,
            "<|endoftext|>": 100,
        }.get(tok),
    )

    class Processor:
        def __init__(self):
            self.tokenizer = tokenizer

        def __call__(self, *args, **kwargs):
            return _MockInputs({"input_features": _MockFeatures(), "attention_mask": None})

        def get_decoder_prompt_ids(self, language=None, task=None):
            return []

        def decode(self, tokens, skip_special_tokens=True):
            return decode(tokens, skip_special_tokens=skip_special_tokens)

        def batch_decode(self, sequences, skip_special_tokens=True):
            out = []
            for seq in sequences:
                seq_list = [int(tok) for tok in seq]
                if 50258 in seq_list:
                    seq_list = seq_list[seq_list.index(50258) + 1 :]
                out.append(decode(seq_list, skip_special_tokens=skip_special_tokens))
            return out

    generated_ids = list(token_map.keys())
    full_seq = [*prefix, 50258, *generated_ids, 100]

    def make_logits(target_id, prob):
        logits = np.full((1, 200), -100.0, dtype=np.float32)
        logits[0, 0] = 0.0
        clamped = min(max(prob, 1e-6), 1.0 - 1e-6)
        logits[0, target_id] = math.log(clamped / (1.0 - clamped))
        return logits

    scores = tuple(
        [make_logits(tok_id, prob) for tok_id, prob in zip(generated_ids, step_probs)]
        + [make_logits(100, 0.99)]
    )

    def generate(**kwargs):
        calls.append(kwargs)
        if kwargs.get("output_scores") and kwargs.get("return_dict_in_generate"):
            return SimpleNamespace(sequences=[full_seq], scores=scores)
        return [full_seq]

    model = SimpleNamespace(
        dtype="float16",
        config=SimpleNamespace(eos_token_id=100, pad_token_id=100, no_timestamps_token_id=101),
        generate=generate,
    )
    return model, Processor(), calls


@pytest.mark.parametrize("text, context", [
    ("リン（リン）", "りん（リン）"),
    ("ﾒﾀﾞ", "メダ"),
    ("ＭＥＤＡ", "meda"),
    ("MEDA：メダ：MEDA：", "配信者：メダ（MEDA）、星街すいせい"),
    ("メダ：メダ：メダ：メダ", "配信者：メダ（MEDA）"),
    ("リン（リン（", "りん（リン）"),
])
def test_echoes_prompt_folds_kana_nfkc_and_collapsed_loops(text, context):
    assert echoes_prompt(text, context) is True


@pytest.mark.parametrize("text, context", [
    ("Playing Dragon Quest", "Dragon Quest"),
    ("はい、わかりました", "配信者：凛"),
    ("a a", "a band"),
    ("rin rin", "rink"),
])
def test_echoes_prompt_does_not_flag_non_echo_phrases_or_ascii_articles(text, context):
    assert echoes_prompt(text, context) is False


@pytest.mark.parametrize("text, context", [
    ("リン（リン）", "配信者：りん（リン）"),
    ("MEDA：メダ：MEDA：", "配信者：メダ（MEDA）、星街すいせい"),
    ("メダ：メダ：メダ：メダ", "配信者：メダ（MEDA）"),
    ("リン（リン（", "配信者：りん（リン）"),
])
def test_whisper_drops_folded_and_looped_structural_echoes_in_single_pass(text, context):
    model, processor, calls = _make_echo_harness({20: text}, [0.99], context)
    assert transcribe_whisper(model, processor, np.zeros(1), "ja", "cpu", [50361, 1], context) == ""
    assert len(calls) == 1


def test_whisper_excludes_punctuation_tokens_when_scoring_prompt_echo():
    model, processor, calls = _make_echo_harness(
        {20: "皆さん", 21: "、"},
        [math.exp(-0.21), math.exp(-1.50)],
        "皆さん",
    )
    out = transcribe_whisper(model, processor, np.zeros(1), "ja", "cpu", [50361, 1], "皆さん")
    assert out == "皆さん、"
    assert len(calls) == 2

    low_model, low_proc, low_calls = _make_echo_harness(
        {20: "皆さん", 21: "、"},
        [math.exp(-1.50), math.exp(-0.05)],
        "皆さん",
    )
    low_out = transcribe_whisper(low_model, low_proc, np.zeros(1), "ja", "cpu", [50361, 1], "皆さん")
    assert low_out == ""
    assert len(low_calls) == 2


@pytest.mark.parametrize("token_map, probs, context, expected", [
    ({20: "ありがとうございます"}, [math.exp(-0.55)], "ありがとうございます", "ありがとうございます"),
    ({20: "メ", 21: "ダ", 22: "!"}, [math.exp(-0.95), math.exp(-0.015), math.exp(-0.78)], "メダ", "メダ!"),
    ({20: "とっても", 21: "!", 22: "とっても", 23: "!"}, [0.95, 0.90, 0.95, 0.90], "とってもいい", "とっても!とっても!"),
    ({20: "ありがとうございました"}, [math.exp(-0.65)], "ありがとうございました", ""),
    ({20: "メ", 21: "ダ"}, [math.exp(-1.20), math.exp(-0.05)], "メダ", ""),
    ({20: "ありがとうございます"}, [math.exp(-1.25)], "ありがとうございます", ""),
])
def test_whisper_calibrated_logprob_thresholds_and_genuine_repeats(token_map, probs, context, expected):
    model, processor, calls = _make_echo_harness(token_map, probs, context)
    out = transcribe_whisper(model, processor, np.zeros(1), "ja", "cpu", [50361, 1], context)
    assert out == expected
    assert len(calls) == 2


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
