import os
import threading
import uuid
from dataclasses import replace

from PySide6.QtCore import QObject, Signal

from koeha.audio.capture import capture, file_audio, frames
from koeha.audio.pipeline import LivePipeline
from koeha.audio.vad import VoiceDetector
from koeha.core.paths import data_root
from koeha.core.settings import Settings
from koeha.history.store import History
from koeha.models.catalog import by_id, delete_model, is_ready, prepare
from koeha.models.engines import check_engine, delete_engine, repair_engine
from koeha.recognition.recognizer import CPU_NOTICE, Recognizer
from koeha.translation.prompt_profiles import translation_instruction
from koeha.translation.translator import Translator


class Controller(QObject):
    caption = Signal(object)
    level = Signal(float)
    status = Signal(str)
    error = Signal(str)
    running = Signal(bool)
    finished = Signal()
    models_changed = Signal()
    preparing = Signal(str, bool)
    progress = Signal(str, object)  # model id, models.Progress or a plain message
    engine_progress = Signal(str, object)
    engine_checked = Signal(str, object)
    engine_busy = Signal(str, bool)

    def __init__(self):
        super().__init__()
        self.root = data_root()
        self.settings, self.settings_warning = self._load_settings(self.root / "settings.json")
        self.history = History(self.root / "history.sqlite3")
        self.pipeline = None
        self.stop_event = threading.Event()
        self.thread = None
        self.jobs = {}
        self.session = None
        self.generation = ""
        self._session_settings = None
        self._translation_prompt = None

    @staticmethod
    def _load_settings(path):
        """Start with defaults instead of refusing to launch; keep the unreadable file for inspection."""
        try:
            return Settings.load(path), ""
        except (OSError, ValueError, TypeError, AttributeError) as error:
            backup = path.with_name("settings.invalid.json")
            try:
                os.replace(path, backup)
                kept = f" The previous file was kept as {backup.name}."
            except OSError:
                kept = ""
            return Settings(), f"Settings could not be loaded ({error}); defaults are in use.{kept}"

    def save(self, settings):
        settings.save(self.root / "settings.json")
        self.settings = settings

    def prepare(self, model_id):
        if self.jobs or (self.thread and self.thread.is_alive()):
            self.error.emit("Stop the session and wait for current setup work before changing engines or models.")
            return
        cancel = threading.Event()
        self.jobs[model_id] = cancel
        self.preparing.emit(model_id, True)
        def work():
            try:
                prepare(self.root, by_id(model_id), lambda text: self.progress.emit(model_id, text), cancel.is_set)
            except InterruptedError:
                self.progress.emit(model_id, "Setup cancelled; prepare again to resume")
            except Exception as error:
                self.error.emit(str(error))
                self.progress.emit(model_id, str(error))
            finally:
                self.jobs.pop(model_id, None)
                self.preparing.emit(model_id, False)
                self.models_changed.emit()
        threading.Thread(target=work, name="koeha-model-setup", daemon=True).start()

    def check_runtime(self, engine_id, repair=False):
        if self.jobs or (self.thread and self.thread.is_alive()):
            self.error.emit("Stop the session and wait for current setup work before changing engines or models.")
            return
        cancel = threading.Event()
        job = "engine:" + engine_id
        self.jobs[job] = cancel
        self.engine_busy.emit(engine_id, True)
        def work():
            try:
                operation = repair_engine if repair else check_engine
                result = operation(self.root, engine_id, lambda update: self.engine_progress.emit(engine_id, update),
                                   cancel.is_set)
                self.engine_checked.emit(engine_id, result)
            except InterruptedError:
                self.engine_progress.emit(engine_id, "Runtime operation cancelled")
            except Exception as error:
                self.engine_progress.emit(engine_id, str(error))
                self.error.emit(str(error))
            finally:
                self.jobs.pop(job, None)
                self.engine_busy.emit(engine_id, False)
                self.models_changed.emit()
        threading.Thread(target=work, name="koeha-engine-maintenance", daemon=True).start()

    def delete_model(self, model_id):
        if self.jobs or (self.thread and self.thread.is_alive()):
            self.error.emit("Stop the session and wait for current setup work before changing engines or models.")
            return False
        try:
            delete_model(self.root, by_id(model_id))
        except Exception as error:
            self.error.emit(str(error))
            return False
        self.models_changed.emit()
        return True

    def delete_runtime(self, engine_id):
        if self.jobs or (self.thread and self.thread.is_alive()):
            self.error.emit("Stop the session and wait for current setup work before changing engines or models.")
            return False
        try:
            delete_engine(self.root, engine_id)
        except Exception as error:
            self.error.emit(str(error))
            return False
        self.models_changed.emit()
        return True

    def start(self, file=None):
        if self.jobs:
            self.error.emit("Wait for setup or engine maintenance to finish before starting a session.")
            return
        if self.thread and self.thread.is_alive():
            return
        settings = replace(self.settings)
        settings.validate()
        model = by_id(settings.model)
        if not is_ready(self.root, model):
            self.error.emit("Download the selected recognition model in Models before starting.")
            return
        if settings.target != "off" and not settings.translation_model.strip():
            self.error.emit("Enter a translation model in Settings, or choose No translation.")
            return
        self.generation = uuid.uuid4().hex
        self.session = self.history.create(file.name if file else "Live · " + model.name) if settings.save_history else None
        self.stop_event = threading.Event()
        self._session_settings = settings
        self._translation_prompt = translation_instruction(settings)
        self.running.emit(True)
        self.thread = threading.Thread(target=self._run, args=(settings, model, file, self.generation),
                                       name="koeha-session", daemon=True)
        self.thread.start()

    def _run(self, settings, model, file, generation):
        decoder = translator = None
        session = self.session
        def emit(caption):
            if generation == self.generation:
                if session and caption.final:
                    self.history.save(session, caption)
                self.caption.emit(caption)
        try:
            decoder = Recognizer(self.root, model, settings.language, settings.active_prompt_profile.context,
                                 self.status.emit, self.stop_event.is_set)
            if self.stop_event.is_set():
                return
            if decoder.device == "cpu":
                # The session still runs; the banner explains why captions may fall behind.
                self.error.emit(CPU_NOTICE)
            translator = Translator(settings, emit, self.error.emit)
            def recognized(caption):
                emit(caption)
                translator.submit(caption, system_prompt=self._translation_prompt)
            self.pipeline = LivePipeline(settings, decoder, recognized, self.level.emit, realtime=file is None,
                                         detector=VoiceDetector(self.root / "vad/silero.onnx",
                                                                settings.speech_confidence,
                                                                profile=settings.vad_profile))
            self.pipeline.stop = self.stop_event
            if file:
                source = file_audio(file)
            else:
                source = capture(settings.source, settings.device, self.stop_event, settings.device_name)
            self.status.emit("Transcribing file" if file else "Listening")
            self.pipeline.run(frames(source))
        except InterruptedError:
            pass
        except Exception as error:
            self.error.emit(str(error))
        finally:
            self.status.emit("Finishing pending translations")
            if translator:
                translator.close()
            if decoder:
                decoder.close()
            self.pipeline = None
            self._session_settings = None
            self._translation_prompt = None
            self.level.emit(0)
            self.status.emit("Ready")
            self.running.emit(False)
            self.finished.emit()

    def select_translation_style(self, profile):
        settings = self._session_settings
        if settings is not None:
            self._translation_prompt = translation_instruction(replace(
                settings, prompt_profile=profile.id, prompt_profiles=[profile]))

    def stop(self):
        self.status.emit("Stopping; finishing the last audio window")
        self.stop_event.set()

    def cancel(self, model_id):
        if cancel := self.jobs.get(model_id):
            cancel.set()
            if model_id.startswith("engine:"):
                self.engine_progress.emit(model_id.split(":", 1)[1], "Cancelling setup…")
            else:
                self.progress.emit(model_id, "Cancelling setup…")

    def shutdown(self):
        self.stop()
        for cancel in list(self.jobs.values()):
            cancel.set()
