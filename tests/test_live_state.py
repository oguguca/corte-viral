from corteviral.config import load_settings
from corteviral.live.state import LiveState, Part, Window


def _state(*parts):
    return LiveState(url="https://x", active=True, opts={}, parts=list(parts), started_at=1000.0)


def test_live_settings_defaults(tmp_path):
    lv = load_settings(tmp_path / "nao.yaml").live
    assert (lv.window_seconds, lv.overlap_seconds, lv.max_retries, lv.retry_wait) == (300, 60, 20, 10)
    assert (lv.max_clips_per_window, lv.review_per_window) == (3, 3)


def test_first_window_needs_full_window():
    s = _state(Part(file="part-000.ts", offset=0, duration=299))
    assert s.next_window(300, 60) is None
    s.parts[0].duration = 300
    w = s.next_window(300, 60)
    assert (w.n, w.part, w.start, w.end) == (0, 0, 0, 300)


def test_next_window_overlaps_previous():
    s = _state(Part(file="p0", offset=0, duration=600))
    s.windows.append(Window(n=0, part=0, start=0, end=300, state="done"))
    w = s.next_window(300, 60)
    assert (w.n, w.part, w.start, w.end) == (1, 0, 240, 600)


def test_window_never_crosses_parts():
    s = _state(Part(file="p0", offset=0, duration=450), Part(file="p1", offset=500, duration=300))
    s.windows.append(Window(n=0, part=0, start=0, end=300, state="done"))
    w = s.next_window(300, 60)  # parte 0 já terminou: fecha o que sobrou dela
    assert (w.n, w.part, w.start, w.end) == (1, 0, 240, 450)
    s.windows.append(w)
    w2 = s.next_window(300, 60)  # parte nova começa sem sobreposição com a anterior
    assert (w2.n, w2.part, w2.start, w2.end) == (2, 1, 500, 800)


def test_finished_part_with_tiny_rest_is_skipped():
    s = _state(Part(file="p0", offset=0, duration=310), Part(file="p1", offset=400, duration=10))
    s.windows.append(Window(n=0, part=0, start=0, end=300, state="done"))
    assert s.next_window(300, 60) is None  # sobra de 10 s < 20 s e a parte 1 ainda não tem 300 s


def test_state_roundtrip(tmp_path):
    s = _state(Part(file="p0", offset=0, duration=12.5))
    s.windows.append(Window(n=0, part=0, start=0, end=300, state="pending_ai", tries=2))
    s.save(tmp_path / "live.json")
    assert LiveState.load(tmp_path / "live.json") == s
    assert LiveState.load(tmp_path / "nada.json") is None
    assert s.recorded_until() == 12.5
