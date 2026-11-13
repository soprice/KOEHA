from datetime import datetime, timezone

from koeha.core.events import Caption
from koeha.history.store import History, export, local_time


def test_history_upserts_translation_and_orders_parallel_results(tmp_path):
    history = History(tmp_path / "history.db")
    session = history.create("test")
    history.save(session, Caption(2, 2, 4, "二", True, "สอง"))
    history.save(session, Caption(1, 0, 2, "一", False))
    assert len(history.captions(session)) == 1
    history.save(session, Caption(1, 0, 2, "一", True))
    history.save(session, Caption(1, 0, 2, "一", True, "หนึ่ง"))
    assert [c.translation for c in history.captions(session)] == ["หนึ่ง", "สอง"]
    history.delete(session)
    assert history.captions(session) == []
    assert history.sessions() == []


def test_unicode_subtitle_exports_exclude_previews(tmp_path):
    captions = [Caption(2, 61.234, 62.567, "日本語", True, "ไทย"), Caption(1, 0, 1, "draft", False)]
    output = tmp_path / "captions.srt"
    export(captions, output)
    assert output.read_text(encoding="utf-8") == "1\n00:01:01,234 --> 00:01:02,567\n日本語\nไทย\n"
    output = tmp_path / "captions.vtt"
    export(captions, output)
    assert "00:01:01.234" in output.read_text(encoding="utf-8")


def test_cue_text_cannot_break_subtitle_structure(tmp_path):
    captions = [Caption(1, 0, 1, "line --> one", True, "first\n\n  second  \n"), Caption(2, 1, 2, "two", True)]
    output = tmp_path / "captions.srt"
    export(captions, output)
    blocks = output.read_text(encoding="utf-8").split("\n\n")
    assert len(blocks) == 2
    assert blocks[0] == "1\n00:00:00,000 --> 00:00:01,000\nline -> one\nfirst\nsecond"


def test_session_times_are_shown_in_local_time():
    moment = datetime(2026, 11, 6, 3, 4, 5, tzinfo=timezone.utc)
    expected = moment.astimezone().strftime("%Y-%m-%d  %H:%M:%S")
    assert local_time(moment.isoformat(timespec="seconds")) == expected
    assert local_time("not a date") == "not a date"
