from datetime import datetime

from corteviral.usage import UsageTracker


class Clock:
    def __init__(self, dt):
        self.dt = dt

    def __call__(self):
        return self.dt


def test_record_accumulates_today(tmp_path):
    clock = Clock(datetime(2026, 11, 11, 10, 0))
    u = UsageTracker(tmp_path / "usage.json", now=clock)
    u.record("gemini", "m", 100, 20, ok=True)
    u.record("gemini", "m", 50, 10, ok=True)
    t = u.today()
    assert t["requests"] == 2 and t["input_tokens"] == 150 and t["output_tokens"] == 30
    assert t["total_tokens"] == 180 and t["rate_limited"] is False
    # persiste entre instâncias
    assert UsageTracker(tmp_path / "usage.json", now=clock).today()["total_tokens"] == 180


def test_new_day_resets(tmp_path):
    clock = Clock(datetime(2026, 11, 11, 23, 59))
    u = UsageTracker(tmp_path / "usage.json", now=clock)
    u.record("gemini", "m", 100, 0, ok=True)
    clock.dt = datetime(2026, 11, 12, 0, 1)
    assert u.today()["total_tokens"] == 0


def test_per_minute_buckets_last_hour(tmp_path):
    clock = Clock(datetime(2026, 11, 11, 10, 0))
    u = UsageTracker(tmp_path / "usage.json", now=clock)
    u.record("gemini", "m", 10, 0, ok=True)
    clock.dt = datetime(2026, 11, 11, 10, 5)
    u.record("gemini", "m", 30, 5, ok=True)
    pm = u.today()["per_minute"]
    assert len(pm) == 60
    assert pm[-1] == {"t": "10:05", "tokens": 35}
    assert pm[-6] == {"t": "10:00", "tokens": 10}
    assert sum(b["tokens"] for b in pm) == 45


def test_rate_limited_flag(tmp_path):
    u = UsageTracker(tmp_path / "usage.json", now=Clock(datetime(2026, 11, 11, 10, 0)))
    u.record("gemini", "m", 0, 0, ok=False, rate_limited=True)
    t = u.today()
    assert t["rate_limited"] is True and t["requests"] == 1 and t["errors"] == 1


def test_record_never_raises_on_io_error(tmp_path, monkeypatch):
    u = UsageTracker(tmp_path / "usage.json", now=Clock(datetime(2026, 11, 11, 10, 0)))

    def locked(*a, **k):
        raise PermissionError("arquivo em uso")
    monkeypatch.setattr("corteviral.usage.write_json_atomic", locked)
    u.record("gemini", "m", 1, 1, ok=True)  # não pode levantar


def test_trackers_on_same_file_share_lock(tmp_path):
    a = UsageTracker(tmp_path / "usage.json")
    b = UsageTracker(tmp_path / "usage.json")
    assert a._lock is b._lock
