import argparse
import sys
from pathlib import Path


def main():
    from koeha.core.settings import SPEECH_VAD, VAD_PROFILES
    from koeha.models.catalog import MODELS

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="koeha", description="Live Speech & Translation Studio")
    parser.add_argument("--prepare", choices=[model.id for model in MODELS])
    parser.add_argument("--transcribe", type=Path)
    parser.add_argument("--model", choices=[model.id for model in MODELS], default="qwen3-asr")
    parser.add_argument("--mode", choices=["sentence", "chunk"], default="sentence")
    parser.add_argument("--vad-profile", choices=VAD_PROFILES, default=SPEECH_VAD,
                        help="For --transcribe, start with speech (default) for dialogue. "
                             "Try music-vocal for singing or if speech drops out under music. "
                             "Music/vocal may caption instrumental audio.")
    parser.add_argument("--language", default="ja")
    parser.add_argument("--output", type=Path, default=Path("captions.srt"))
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    if args.prepare:
        from koeha.core.paths import data_root
        from koeha.models.catalog import by_id, prepare
        prepare(data_root(), by_id(args.prepare), lambda message: print(message, flush=True))
        return 0
    if args.transcribe:
        from koeha.audio.capture import file_audio, frames
        from koeha.audio.pipeline import LivePipeline
        from koeha.audio.vad import VoiceDetector
        from koeha.core.paths import data_root
        from koeha.core.settings import Settings
        from koeha.history.store import export
        from koeha.models.catalog import by_id
        from koeha.recognition.recognizer import CPU_NOTICE, Recognizer
        result = {}
        def receive(caption):
            result[caption.id] = caption
            print(f"{caption.start:.2f}–{caption.end:.2f} {caption.text}", flush=True)
        decoder = Recognizer(data_root(), by_id(args.model), args.language, status=print)
        try:
            if decoder.device == "cpu":
                print(CPU_NOTICE, file=sys.stderr, flush=True)
            settings = Settings(mode=args.mode, model=args.model, vad_profile=args.vad_profile)
            pipeline = LivePipeline(settings, decoder, receive, realtime=False,
                                    detector=VoiceDetector(data_root() / "vad/silero.onnx",
                                                           settings.speech_confidence, profile=settings.vad_profile))
            pipeline.run(frames(file_audio(args.transcribe)))
            export(result.values(), args.output)
        finally:
            decoder.close()
        return 0
    from koeha.ui.main_window import run_ui
    return run_ui(args.screenshot)


if __name__ == "__main__":
    sys.exit(main())
