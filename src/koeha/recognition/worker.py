"""Inference-only process. Its environment deliberately contains no desktop dependencies."""
import base64
import contextlib
import hashlib
import json
import re
import sys
import time
from pathlib import Path

# Whisper decodes at most 448 tokens: up to 4 start tokens, the prompt, then the transcript.
WHISPER_MAX_NEW_TOKENS = 440
# Whisper itself trims prompts to half its context; a shorter cap keeps room for long sentences.
WHISPER_PROMPT_TOKENS = 128
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


def whisper_prompt(processor, context, device):
    """Scene context as Whisper prompt tokens, or None when there is no context."""
    if not context.strip():
        return None
    ids = processor.get_prompt_ids(context[:1000], return_tensors="pt")
    return ids[:WHISPER_PROMPT_TOKENS].to(device)


def echoes_prompt(text, context):
    """Whisper repeats its prompt on silence or music; such output is not speech."""
    compact = "".join(text.split())
    return len(compact) >= 8 and compact in "".join(context.split())


def transcribe_whisper(model, processor, audio, language, device, prompt=None, context="",
                       max_new_tokens=WHISPER_MAX_NEW_TOKENS):
    inputs = processor(audio, sampling_rate=16000, return_tensors="pt", return_attention_mask=True).to(device)
    inputs["input_features"] = inputs["input_features"].to(dtype=model.dtype)
    options = {}
    if prompt is not None:
        options["prompt_ids"] = prompt
        max_new_tokens = min(max_new_tokens, WHISPER_MAX_NEW_TOKENS - len(prompt))
    generated = model.generate(**inputs, language=None if language == "auto" else language,
                               task="transcribe", max_new_tokens=max_new_tokens, do_sample=False, **options)
    record_usage(generated, max_new_tokens)
    text = processor.batch_decode(generated, skip_special_tokens=True)[0]
    if prompt is not None and echoes_prompt(text, context):
        return ""
    return clean_music_hallucinations(text)


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
        def transcribe(audio, limit=None):
            return transcribe_whisper(model, processor, audio, language, device, prompt, context,
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
