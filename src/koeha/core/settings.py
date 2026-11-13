import json
import os
import re
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from koeha.translation.connection_profiles import ConnectionProfile
from koeha.translation.prompt_profiles import PromptProfile, default_prompt_profiles

SPEECH_VAD = "speech"
MUSIC_VOCAL_VAD = "music-vocal"
VAD_PROFILES = (SPEECH_VAD, MUSIC_VOCAL_VAD)
MAX_OVERLAY_DELAY = 3600


@dataclass
class Settings:
    model: str = "qwen3-asr"
    mode: str = "sentence"
    source: str = "system"
    device: int | None = None
    device_name: str = ""
    language: str = "ja"
    target: str = "off"
    chunk_seconds: float = 4.0
    pause_seconds: float = 1.0
    preview_seconds: float = 1.0
    max_seconds: float = 15.0
    voice_threshold: float = 0.012
    # Silero VAD confidence; lower it (about 0.2-0.3) for singing over music.
    speech_confidence: float = 0.5
    translation_url: str = "http://127.0.0.1:8080/v1"
    translation_model: str = ""
    translation_profile: str = ""
    translation_profiles: list[ConnectionProfile] = field(default_factory=list)
    prompt_profile: str = "default"
    prompt_profiles: list[PromptProfile] = field(default_factory=default_prompt_profiles)
    draft_translation: bool = True
    save_history: bool = True
    ui_language: str = "en"
    subtitle_style: str = "frosted"
    typeface: str = "Segoe UI"
    text_size: int = 26
    source_text_color: str = "#c0c5cd"
    target_text_color: str = "#ffffff"
    text_outline_enabled: bool = False
    source_outline_color: str = "#d9000000"
    target_outline_color: str = "#d9000000"
    text_outline_width: float = 1.5
    background_color: str = "#161b22"
    panel_opacity: float = 0.89
    frame_color: str = "#55778391"
    frame_width: float = 1.0
    corner_radius: int = 13
    original_visible: bool = True
    text_shadow: bool = True
    sidebar_collapsed: bool = False
    transcript_layout: str = "dual"
    vad_profile: str = SPEECH_VAD
    overlay_timing: str = "immediate"
    overlay_delay_seconds: float = 2.0

    @property
    def vad_voice_threshold(self) -> float:
        return 0.008 if self.vad_profile == MUSIC_VOCAL_VAD else self.voice_threshold

    @property
    def active_prompt_profile(self) -> PromptProfile:
        for profile in self.prompt_profiles:
            if profile.id == self.prompt_profile:
                return profile
        raise ValueError("Select an existing prompt profile")

    def validate(self):
        prompt_ids, prompt_names = set(), set()
        for profile in self.prompt_profiles:
            if not isinstance(profile, PromptProfile):
                raise ValueError("Invalid prompt profile")
            profile.validate()
            if profile.id in prompt_ids or profile.name.strip().casefold() in prompt_names:
                raise ValueError("Prompt profile IDs and names must be unique")
            prompt_ids.add(profile.id)
            prompt_names.add(profile.name.strip().casefold())
        if self.prompt_profile not in prompt_ids:
            raise ValueError("Select an existing prompt profile")
        ids, names = set(), set()
        for profile in self.translation_profiles:
            if not isinstance(profile, ConnectionProfile):
                raise ValueError("Invalid connection profile")
            profile.validate()
            if profile.id in ids or profile.name.strip().casefold() in names:
                raise ValueError("Connection profile IDs and names must be unique")
            ids.add(profile.id)
            names.add(profile.name.strip().casefold())
        if (self.translation_profiles or self.translation_profile) and self.translation_profile not in ids:
            raise ValueError("Select an existing translation profile")
        if self.mode not in {"sentence", "chunk"}:
            raise ValueError("Unknown delivery mode")
        if self.vad_profile not in VAD_PROFILES:
            raise ValueError("Unknown voice detection profile")
        if self.overlay_timing not in {"immediate", "delayed"}:
            raise ValueError("Unknown floating caption timing mode")
        if not 0 <= self.overlay_delay_seconds <= MAX_OVERLAY_DELAY:
            raise ValueError("Invalid floating caption delay")
        if self.source not in {"system", "microphone"}:
            raise ValueError("Unknown audio source")
        if not 1 <= self.chunk_seconds <= 30 or not 1 <= self.max_seconds <= 30:
            raise ValueError("Audio windows must be between 1 and 30 seconds")
        if not 0.2 <= self.pause_seconds <= 3 or not 0.4 <= self.preview_seconds <= 5:
            raise ValueError("Invalid pause or preview interval")
        if not 0.0001 <= self.voice_threshold <= 0.5:
            raise ValueError("Invalid voice threshold")
        if not 0.05 <= self.speech_confidence <= 0.95:
            raise ValueError("Speech confidence must be between 0.05 and 0.95")
        if not 10 <= self.text_size <= 72:
            raise ValueError("Caption size must be between 10 and 72 pixels")
        if not 0 <= self.text_outline_width <= 6 or not 0 <= self.frame_width <= 6:
            raise ValueError("Caption outline and border must be between 0 and 6 pixels")
        if not 0 <= self.panel_opacity <= 1 or not 0 <= self.corner_radius <= 24:
            raise ValueError("Invalid caption background opacity or corner radius")
        for name in ("source_text_color", "target_text_color", "source_outline_color",
                     "target_outline_color", "background_color", "frame_color"):
            if not isinstance(getattr(self, name), str) or not re.fullmatch(
                    r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?", getattr(self, name)):
                raise ValueError(f"Invalid caption color: {name}")

    @classmethod
    def load(cls, path: Path):
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        shared = {"context": data.pop("context", ""), "glossary": data.pop("glossary", "")}
        if "prompt_profiles" in data:
            prompts = data["prompt_profiles"]
            if not isinstance(prompts, list) or any(not isinstance(p, dict) for p in prompts):
                raise ValueError("Invalid prompt profiles")
            data["prompt_profiles"] = [PromptProfile(**(shared | p)) for p in prompts]
        else:
            data["prompt_profiles"] = [replace(p, **shared) for p in default_prompt_profiles()]
        if "translation_profiles" in data:
            profiles = data["translation_profiles"]
            if not isinstance(profiles, list) or any(not isinstance(p, dict) for p in profiles):
                raise ValueError("Invalid connection profiles")
            # Profiles saved by the SSH tunnel preview still load as direct connections.
            for profile in profiles:
                profile.pop("ssh_user", None)
            data["translation_profiles"] = [ConnectionProfile(**p) for p in profiles]
        result = cls(**{key: value for key, value in data.items() if key in cls.__dataclass_fields__})
        result.validate()
        if result.translation_profile:
            active = next(p for p in result.translation_profiles if p.id == result.translation_profile)
            result.translation_url, result.translation_model = active.url, active.model
        return result

    def save(self, path: Path):
        self.validate()
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)
