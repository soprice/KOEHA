"""Exercise real recognition and delivery without a desktop or external translation API."""
import argparse
import hashlib
import json
import time

from koeha.audio.capture import RATE, file_audio, frames
from koeha.audio.pipeline import LivePipeline
from koeha.audio.vad import VoiceDetector
from koeha.core.paths import data_root
from koeha.core.settings import Settings
from koeha.models.catalog import MODELS, by_id
from koeha.recognition.recognizer import Recognizer


def main():
    from pathlib import Path
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    parser.add_argument("--models", nargs="+", default=["qwen3-asr"])
    parser.add_argument("--report", type=Path, default=Path("runtime-report.json"))
    parser.add_argument("--realtime", action="store_true")
    args = parser.parse_args()
    audio = list(frames(file_audio(args.audio)))
    root = data_root()
    results = []
    model_ids = [m.id for m in MODELS] if args.models == ["all"] else args.models
    for model_id in model_ids:
        began = time.monotonic()
        decoder = Recognizer(root, by_id(model_id), "ja", status=lambda text: print(text, flush=True))
        load_seconds = time.monotonic() - began
        try:
            for mode in ["sentence", "chunk"]:
                captions = []
                began = time.monotonic()
                def replay():
                    clock = time.monotonic()
                    elapsed = 0
                    for pcm in audio:
                        if args.realtime:
                            delay = clock + elapsed - time.monotonic()
                            if delay > 0:
                                time.sleep(delay)
                        yield pcm
                        elapsed += len(pcm) / (RATE * 2)
                pipeline = LivePipeline(Settings(mode=mode), decoder, captions.append,
                    realtime=args.realtime, detector=VoiceDetector(root / "vad/silero.onnx"))
                pipeline.run(replay())
                finals = [c for c in captions if c.final]
                assert finals and any(c.text for c in finals), f"{model_id}/{mode}: no recognized text"
                assert len({c.id for c in finals}) == len(finals), "Duplicate final delivery"
                assert all(c.final for c in captions) if mode == "chunk" else True
                assert not args.realtime or mode != "sentence" or any(not c.final for c in captions)
                row = {"model": model_id, "mode": mode, "load_seconds": round(load_seconds, 3),
                       "elapsed_seconds": round(time.monotonic() - began, 3),
                       "previews": sum(not c.final for c in captions), "finals": len(finals),
                       "characters": sum(len(c.text) for c in finals), "passed": True}
                results.append(row)
                print(json.dumps(row), flush=True)
        finally:
            decoder.close()
    report = {"audio_sha256": hashlib.sha256(args.audio.read_bytes()).hexdigest(),
              "audio_seconds": sum(map(len, audio)) / (RATE * 2), "realtime": args.realtime, "results": results}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
