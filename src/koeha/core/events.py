from dataclasses import dataclass


@dataclass(frozen=True)
class Caption:
    id: int
    start: float
    end: float
    text: str
    final: bool
    translation: str = ""
    translation_error: str = ""
    translation_draft: bool = False
    translated_source: str = ""


@dataclass(frozen=True)
class AudioWindow:
    id: int
    start: float
    end: float
    pcm: bytes
    final: bool
