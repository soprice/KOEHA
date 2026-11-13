"""Inference-only process. Its environment deliberately contains no desktop dependencies."""
import base64
import contextlib
import hashlib
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

# Whisper decodes at most 448 tokens: up to 4 start tokens, the prompt, then the transcript.
WHISPER_MAX_NEW_TOKENS = 440
# Whisper itself trims prompts to half its context; a shorter cap keeps room for long sentences.
WHISPER_PROMPT_TOKENS = 128
WHISPER_PROMPT_ECHO_MIN_CHARS = 2
WHISPER_ECHO_RESCUE_MAX_CHARS = 20
WHISPER_ECHO_RESCUE_MAX_WORDS = 3
# Log-probability floors for rescuing real speech that matches Scene context while dropping hallucinations.
WHISPER_ECHO_MIN_MEAN_LOGPROB = -0.6
WHISPER_ECHO_MIN_FIRST_LOGPROB = -1.1
WHISPER_ECHO_MIN_TOKEN_LOGPROB = -1.2
_KANA_FOLD = str.maketrans({chr(code): chr(code - 0x60) for code in range(0x30A1, 0x30F7)})
_PROMPT_ECHO_PUNCTUATION = str.maketrans("", "", ".,!?;:'\"()[]{}<>「」『』（）【】［］《》｜|、。！？…-—~～・")
_PROMPT_BOUNDARY_REPLACE = str.maketrans(
    {ch: " " for ch in ".,!?;:'\"()[]{}<>「」『』（）【】［］《》｜、。！？…-—~～・/\\|\n\r\t"}
)
_PROMPT_STRUCTURAL_RE = re.compile(
    r"(?<!\d):|:(?!\d)|[；;\n\r|•()\[\]{}【】《》]|^\s*[,、]|[,、][^,、]*[,、]|(?:\s|^)-\s"
)
_PROMPT_LIST_SEPARATOR_RE = re.compile(r"[,、;|\r\n]+")
WARMUP_TOKENS = 8
PARAKEET_FILENAME = "parakeet-tdt_ctc-0.6b-ja.nemo"
# LFS digest from the catalog's pinned Hugging Face revision.
PARAKEET_SHA256 = "a2ed2aab9c82cce4e9b699dca669184d7525c7b5e1eb8c3f7b789421bc3637e8"
MUSIC_NOTES = "♪♫♬♩🎵🎶"
STAGE_MARKERS = {
    "[music]", "(music)", "[音楽]", "（音楽）", "[bgm]", "(bgm)",
    "[applause]", "(applause)", "[拍手]", "（拍手）", "[singing]", "(singing)",
    "[歌]", "（歌）", "[instrumental]", "(instrumental)",
}
# Digits never form a loop unit: "1000000" is a number, not a held note.
REPEAT_LOOP_RE = re.compile(r"([^\d\n]{1,12}?)\1{4,}")
# Token usage of the latest transcription; main() reports it with each answer for decode logging.
USAGE = {}


def send(message):
    sys.__stdout__.write(json.dumps(message, ensure_ascii=True) + "\n")
    sys.__stdout__.flush()


def record_usage(sequences, limit=None, blank=None):
    """Note the length of the returned token sequence and the limit it ran with.

    The count is what the model returned, which for some models includes its start tokens.
    Transducers fill every frame without output with ``blank``; those are not counted.
    This is measurement only, so an unfamiliar result type is skipped and never fails a decode.
    """
    USAGE["limit"] = limit
    try:
        shape = getattr(sequences, "shape", None)
        if shape is not None:
            USAGE["tokens"] = int(shape[-1]) - (int((sequences == blank).sum()) if blank is not None else 0)
        else:
            first = sequences[0]
            USAGE["tokens"] = len(first) if hasattr(first, "__len__") else len(sequences)
    except Exception:
        USAGE.pop("tokens", None)


def clean_music_hallucinations(text: str) -> str:
    """Filter non-speech music markers and collapse decoder repetition loops on held notes."""
    cleaned = text.strip().strip(MUSIC_NOTES).strip()
    if not cleaned or cleaned.casefold() in STAGE_MARKERS:
        return ""
    if all(ch in MUSIC_NOTES or ch in ".…・-—~～!！?？()（）[]【】 " for ch in cleaned):
        return ""
    return REPEAT_LOOP_RE.sub(r"\1\1\1", cleaned)


def load_whisper(folder, device):
    import torch
    from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor

    processor = AutoProcessor.from_pretrained(folder, local_files_only=True, trust_remote_code=False)
    dtype = torch.float16 if device == "cuda" else torch.float32
    model = AutoModelForSpeechSeq2Seq.from_pretrained(
        folder, local_files_only=True, trust_remote_code=False, dtype=dtype).to(device).eval()
    return model, processor


def _prompt_1d(ids):
    ndim = getattr(ids, "ndim", None)
    if ndim is None and hasattr(ids, "shape"):
        ndim = len(ids.shape)
    if ndim is not None:
        if ndim == 0:
            return None
        if ndim > 1:
            if hasattr(ids, "reshape"):
                return ids.reshape(-1)
            if hasattr(ids, "squeeze") and getattr(ids, "shape", (None,))[0] == 1:
                return ids.squeeze(0)
            return ids[0]
        return ids
    while isinstance(ids, (list, tuple)) and len(ids) == 1 and isinstance(ids[0], (list, tuple)):
        ids = ids[0]
    return ids


def whisper_prompt(processor, context, device):
    """Scene context as rank-1 Whisper prompt tokens, or None when there is no context."""
    if not context.strip():
        return None
    ids = _prompt_1d(processor.get_prompt_ids(context[:1000], return_tensors="pt"))
    if ids is None or len(ids) == 0:
        return None
    trimmed = ids[:WHISPER_PROMPT_TOKENS]
    return trimmed.to(device) if hasattr(trimmed, "to") else trimmed


def whisper_prompt_context(processor, prompt, context):
    """Recover the actual text encoded by the prompt tokens sent to Whisper."""
    if prompt is None:
        return ""
    tokenizer = getattr(processor, "tokenizer", None)
    decoder = tokenizer if callable(getattr(tokenizer, "decode", None)) else (
        processor if callable(getattr(processor, "decode", None)) else None
    )
    if decoder is None:
        return context[:1000]
    tokens = _sequence_1d(_prompt_1d(prompt))
    if not tokens:
        return ""
    tokens = tokens[:WHISPER_PROMPT_TOKENS]
    start_of_prev = None
    if callable(getattr(decoder, "convert_tokens_to_ids", None)):
        candidate = decoder.convert_tokens_to_ids("<|startofprev|>")
        if isinstance(candidate, int) and not isinstance(candidate, bool):
            start_of_prev = candidate
    special_ids = _int_ids(getattr(tokenizer or decoder, "all_special_ids", None))
    # WhisperTokenizer.decode(..., skip_special_tokens=True) discards all tokens after <|startofprev|>
    # when <|startoftranscript|> is absent, so strip leading prompt prefix tokens first.
    while tokens and (tokens[0] == start_of_prev or tokens[0] in special_ids):
        tokens = tokens[1:]
    if not tokens:
        return ""
    return decoder.decode(tokens, skip_special_tokens=True)


def _fold_echo_chars(text: str) -> str:
    return unicodedata.normalize("NFKC", text).replace("\ufffd", "").translate(_KANA_FOLD).casefold()


def _normalize_echo_text(text: str) -> str:
    return "".join(_fold_echo_chars(text).translate(_PROMPT_ECHO_PUNCTUATION).split())


def _normalize_echo_words(text: str) -> str:
    return " ".join(_fold_echo_chars(text).translate(_PROMPT_BOUNDARY_REPLACE).split())


def _is_rescuable_prompt_echo(text: str, context: str) -> bool:
    """Check confidence for a short phrase or an exact standalone item in a prompt list."""
    folded = _fold_echo_chars(text)
    if _PROMPT_STRUCTURAL_RE.search(folded):
        return False
    if (len(_normalize_echo_text(text)) <= WHISPER_ECHO_RESCUE_MAX_CHARS
            and len(folded.split()) <= WHISPER_ECHO_RESCUE_MAX_WORDS):
        return True

    # Long matches need an entire list item. A substring of a description or a copy
    # spanning multiple items is still dropped before the scored pass.
    term = " ".join(folded.rstrip(" .!?。！？…").split())
    if _PROMPT_LIST_SEPARATOR_RE.search(term):
        return False
    items = [item.strip() for item in _PROMPT_LIST_SEPARATOR_RE.split(_fold_echo_chars(context)) if item.strip()]
    return len(items) > 1 and any(" ".join(item.rstrip(" .!?。！？…").split()) == term for item in items)


def _matches_prompt_piece(
    compact: str, ctx_compact: str, ctx_words: str, words: str | None = None
) -> bool:
    if compact.isascii():
        # Ignore 1-char ASCII tokens like "a"/"I", while still catching 1-char non-ASCII names (e.g. 凛).
        if len(compact) < WHISPER_PROMPT_ECHO_MIN_CHARS:
            return False
        word_text = compact if words is None else words
        if not word_text:
            return False
        if re.search(rf"(?<![a-z0-9]){re.escape(word_text)}(?![a-z0-9])", ctx_words):
            return True
        return len(compact) >= 8 and compact in ctx_compact
    return compact in ctx_compact


def _echoes_prompt_loop(text: str, ctx_compact: str, ctx_words: str) -> bool:
    """Catch collapsed hallucination loops of prompt fragments (e.g. MEDA：メダ：MEDA：)."""
    words = _normalize_echo_words(text).split()
    if len(words) < 2 or len(set(words)) >= len(words):
        return False
    return all(_matches_prompt_piece(part, ctx_compact, ctx_words, words=part) for part in words)


def echoes_prompt(text, context):
    """Whisper repeats its prompt on silence or music; such output is not speech."""
    compact = _normalize_echo_text(text)
    if not compact:
        return False
    ctx_compact = _normalize_echo_text(context)
    ctx_words = _normalize_echo_words(context)
    if _matches_prompt_piece(compact, ctx_compact, ctx_words, words=_normalize_echo_words(text)):
        return True
    return _echoes_prompt_loop(text, ctx_compact, ctx_words)


def _to_numpy(value):
    import numpy as np

    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if getattr(getattr(value, "dtype", None), "is_floating_point", False) and hasattr(value, "float"):
        value = value.float()
    return np.asarray(value)


def _sequence_1d(sequences):
    arr = _to_numpy(sequences)
    if arr.ndim == 2:
        arr = arr[0]
    elif arr.ndim != 1:
        return None
    if arr.size == 0:
        return None
    return [int(token) for token in arr.tolist()]


def _logits_1d(step_scores):
    import numpy as np

    arr = _to_numpy(step_scores)
    if arr.ndim == 2:
        arr = arr[0]
    elif arr.ndim != 1:
        return None
    arr = arr.astype(np.float64, copy=False)
    return arr if arr.size else None


def _int_ids(value):
    if isinstance(value, int) and not isinstance(value, bool):
        return {value}
    if isinstance(value, (list, tuple, set)):
        return {item for item in value if isinstance(item, int) and not isinstance(item, bool)}
    return set()


def _whisper_ignored_token_ids(model, processor):
    stop_ids = set()
    timestamp_begin = None
    for config in (getattr(model, "generation_config", None), getattr(model, "config", None)):
        if config is None:
            continue
        stop_ids.update(_int_ids(getattr(config, "eos_token_id", None)))
        stop_ids.update(_int_ids(getattr(config, "pad_token_id", None)))
        if timestamp_begin is None:
            no_ts = getattr(config, "no_timestamps_token_id", None)
            if isinstance(no_ts, int) and not isinstance(no_ts, bool):
                timestamp_begin = no_ts + 1
    tokenizer = getattr(processor, "tokenizer", None)
    special_ids = _int_ids(getattr(tokenizer, "all_special_ids", None))
    return stop_ids, special_ids, timestamp_begin


def _token_logprob(step_scores, token_id):
    import numpy as np

    logits = _logits_1d(step_scores)
    if logits is None or not (0 <= token_id < logits.shape[0]):
        return None
    chosen = float(logits[token_id])
    if not np.isfinite(chosen):
        return None
    max_logit = float(np.max(logits))
    if not np.isfinite(max_logit):
        return None
    # Subtract max_logit so suppressed -inf entries map cleanly to exp(-inf) == 0 without NaNs.
    log_z = max_logit + float(np.log(np.sum(np.exp(logits - max_logit))))
    logprob = chosen - log_z
    return logprob if np.isfinite(logprob) else None


def _scored_outputs(result):
    if isinstance(result, dict):
        return result.get("sequences"), result.get("scores")
    return getattr(result, "sequences", None), getattr(result, "scores", None)


def _is_punctuation_token(decoder, token_id: int) -> bool:
    decode = getattr(decoder, "decode", None)
    if not callable(decode):
        return False
    try:
        piece = decode([int(token_id)], skip_special_tokens=True)
    except Exception:
        return False
    if not isinstance(piece, str) or not piece or "\ufffd" in piece:
        return False
    return not _normalize_echo_text(piece)


def _scored_echo_is_confident(scored, model, processor):
    """Verify via per-token logprobs that a prompt-matching output reflects confident speech."""
    sequences, scores = _scored_outputs(scored)
    if sequences is None or scores is None:
        return False
    scores = tuple(scores)
    if not scores:
        return False
    seq = _sequence_1d(sequences)
    if seq is None or len(seq) < len(scores):
        return False
    # With return_dict_in_generate=True on Whisper, sequences retains prompt_ids and init_tokens,
    # whereas scores only covers the trailing newly generated steps.
    tail = seq[-len(scores):]
    stop_ids, special_ids, timestamp_begin = _whisper_ignored_token_ids(model, processor)
    decoder = getattr(processor, "tokenizer", None)
    logprobs = []
    lexical_logprobs = []
    for token_id, step_scores in zip(tail, scores):
        if token_id in stop_ids:
            break
        if token_id in special_ids or (timestamp_begin is not None and token_id >= timestamp_begin):
            continue
        logprob = _token_logprob(step_scores, token_id)
        if logprob is None:
            return False
        logprobs.append(logprob)
        if not _is_punctuation_token(decoder, token_id):
            lexical_logprobs.append(logprob)
    if not logprobs:
        return False
    scored_logprobs = lexical_logprobs or logprobs
    mean_logprob = sum(scored_logprobs) / len(scored_logprobs)
    return (
        mean_logprob >= WHISPER_ECHO_MIN_MEAN_LOGPROB
        and scored_logprobs[0] >= WHISPER_ECHO_MIN_FIRST_LOGPROB
        and min(scored_logprobs) >= WHISPER_ECHO_MIN_TOKEN_LOGPROB
    )


def transcribe_whisper(model, processor, audio, language, device, prompt=None, context="",
                       max_new_tokens=WHISPER_MAX_NEW_TOKENS):
    inputs = processor(audio, sampling_rate=16000, return_tensors="pt", return_attention_mask=True).to(device)
    inputs["input_features"] = inputs["input_features"].to(dtype=model.dtype)
    options = {}
    if prompt is not None:
        options["prompt_ids"] = prompt
        max_new_tokens = min(max_new_tokens, WHISPER_MAX_NEW_TOKENS - len(prompt))
    generate_kwargs = {
        **inputs,
        "language": None if language == "auto" else language,
        "task": "transcribe",
        "max_new_tokens": max_new_tokens,
        "do_sample": False,
        **options,
    }
    generated = model.generate(**generate_kwargs)
    record_usage(generated, max_new_tokens)
    text = processor.batch_decode(generated, skip_special_tokens=True)[0]
    cleaned = clean_music_hallucinations(text)
    if not cleaned:
        return ""
    if prompt is not None:
        prompt_context = whisper_prompt_context(processor, prompt, context)
        if echoes_prompt(cleaned, prompt_context):
            if not _is_rescuable_prompt_echo(cleaned, prompt_context):
                return ""
            # Re-decode with scores only for eligible prompt matches so normal speech pays
            # no extra pass and USAGE stays strictly tied to the first-pass generated tokens.
            scored = model.generate(**generate_kwargs, return_dict_in_generate=True, output_scores=True)
            if not _scored_echo_is_confident(scored, model, processor):
                return ""
    return cleaned


def verified_parakeet_checkpoint(folder):
    path = Path(folder) / PARAKEET_FILENAME
    if path.is_file():
        with path.open("rb") as file:
            if hashlib.file_digest(file, "sha256").hexdigest() == PARAKEET_SHA256:
                return path
    raise RuntimeError("The Parakeet model file is missing or has changed. Download it again in Models.")


def load(model_id, folder, language, context, device):
    """Load a model and return transcribe(audio, limit=None) -> text."""
    if model_id == "parakeet-ja":
        checkpoint = verified_parakeet_checkpoint(folder)
        from nemo.collections.asr.models import ASRModel
        model = ASRModel.restore_from(str(checkpoint), map_location=device).eval()
        def transcribe(audio, limit=None):
            result = model.transcribe([audio], batch_size=1, verbose=False)[0]
            record_usage(getattr(result, "y_sequence", None))
            raw = result.text if hasattr(result, "text") else str(result)
            return clean_music_hallucinations(raw)
        return transcribe
    if model_id.startswith("whisper-"):
        model, processor = load_whisper(folder, device)
        prompt = whisper_prompt(processor, context, device)
        prompt_context = whisper_prompt_context(processor, prompt, context)
        def transcribe(audio, limit=None):
            return transcribe_whisper(model, processor, audio, language, device, prompt, prompt_context,
                                      limit or WHISPER_MAX_NEW_TOKENS)
        return transcribe
    from transformers import AutoModelForRNNT, AutoModelForSpeechSeq2Seq, AutoProcessor
    processor = AutoProcessor.from_pretrained(folder, local_files_only=True, trust_remote_code=False)
    model_class = AutoModelForRNNT if model_id == "nemotron" else AutoModelForSpeechSeq2Seq
    model = model_class.from_pretrained(folder, local_files_only=True, trust_remote_code=False).to(device).eval()
    if model_id == "fun-asr":
        def transcribe(audio, limit=None):
            inputs = processor.apply_transcription_request(audio=audio,
                language=None if language == "auto" else language,
                processor_kwargs={"return_tensors": "pt", "audio_kwargs": {"sampling_rate": 16000}})
            inputs = inputs.to(device)
            generated = model.generate(**inputs, max_new_tokens=limit or 512, do_sample=False)
            answer = generated[:, inputs.input_ids.shape[1]:]
            record_usage(answer, limit or 512)
            raw = processor.batch_decode(answer, skip_special_tokens=True)[0]
            return clean_music_hallucinations(raw)
        return transcribe
    locales = {"ja": "ja-JP", "en": "en-US", "th": "th-TH", "zh": "zh-CN", "ko": "ko-KR"}
    def transcribe(audio, limit=None):
        inputs = processor(audio, sampling_rate=16000, language=locales.get(language, "auto"),
                           return_tensors="pt").to(device)
        generated = model.generate(**inputs, return_dict_in_generate=True)
        record_usage(generated.sequences, blank=getattr(model.config, "blank_token_id", None))
        text = processor.decode(generated.sequences, skip_special_tokens=True)
        raw = text[0] if isinstance(text, list) else text
        return clean_music_hallucinations(raw)
    return transcribe


def main():
    import numpy as np
    import torch
    model_id, folder, language = sys.argv[1:4]
    context = sys.argv[4] if len(sys.argv) > 4 else ""
    torch.set_num_threads(4)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    transcribe = load(model_id, folder, language, context, device)
    # One silent pass makes CUDA prepare its kernels now, so the first sentence is not delayed.
    with torch.inference_mode():
        transcribe(np.zeros(16000, dtype=np.float32), WARMUP_TOKENS)
    send({"ready": True, "device": device})
    for line in sys.stdin:
        try:
            pcm = base64.b64decode(json.loads(line)["pcm"])
            audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
            USAGE.clear()
            began = time.perf_counter()
            with torch.inference_mode():
                text = transcribe(audio)
            send({"text": text.strip(), "seconds": round(time.perf_counter() - began, 4), **USAGE})
        except Exception as error:
            send({"error": f"{type(error).__name__}: {error}"})


if __name__ == "__main__":
    try:
        with contextlib.redirect_stdout(sys.stderr):
            main()
    except Exception as error:
        send({"error": f"{type(error).__name__}: {error}"})
        raise
