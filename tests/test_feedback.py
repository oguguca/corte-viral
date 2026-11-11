from corteviral.feedback import FeedbackStore


def _m(i, desc="trecho"):
    return {"id": f"m{i}", "description": f"{desc} {i}", "reason": "porque sim", "text": "fala fala",
            "criteria": {"gancho": 50}, "score": 60}


def test_add_and_examples_prefer_same_streamer(tmp_path):
    fb = FeedbackStore(tmp_path / "feedback.json")
    fb.add("proj-a", "Outro", _m(1), "good")
    fb.add("proj-b", "Flow", _m(2), "good")
    ex = fb.examples("Flow", k=2)
    assert ex[0]["streamer"] == "Flow" and ex[0]["label"] == "good"


def test_examples_balanced_good_bad(tmp_path):
    fb = FeedbackStore(tmp_path / "feedback.json")
    for i in range(5):
        fb.add("p", "Flow", _m(i), "good")
    for i in range(5, 7):
        fb.add("p", "Flow", _m(i), "bad")
    ex = fb.examples("Flow", k=6)
    assert sum(e["label"] == "good" for e in ex) == 3 and sum(e["label"] == "bad" for e in ex) == 2
    assert ex[0]["description"] == "trecho 4"  # mais recente primeiro


def test_relabel_replaces(tmp_path):
    fb = FeedbackStore(tmp_path / "feedback.json")
    fb.add("p", "Flow", _m(1), "good")
    fb.add("p", "Flow", _m(1), "bad")
    assert fb.label_for("p", "m1") == "bad"
    assert len(fb.examples("Flow", k=6)) == 1
    fb.add("p", "Flow", _m(1), None)  # desmarcar
    assert fb.label_for("p", "m1") is None


def test_no_feedback_empty_examples(tmp_path):
    assert FeedbackStore(tmp_path / "nada.json").examples("Flow") == []


def test_analyze_uses_saved_feedback_as_examples(settings_tmp, monkeypatch, sample_video):
    from tests.test_analyze import make_project
    from corteviral.pipeline.analyze import analyze
    monkeypatch.setattr("corteviral.pipeline.signals.fetch_chat", lambda project, settings: None)
    seen = {}

    def fake_deep(project, moments, transcript, settings, client, examples=None, on_progress=None):
        seen["examples"] = examples
        return moments, {"video": 0, "rate_limited": False, "errors": 0, "skipped": True}

    monkeypatch.setattr("corteviral.pipeline.analyze.deep_review", fake_deep)
    settings_tmp.llm.provider = "none"
    p = make_project(settings_tmp)
    p.data["streamer"] = "Flow"
    p.save()
    FeedbackStore(settings_tmp.paths.data_dir / "feedback.json").add("x", "Flow", _m(1), "good")
    analyze(p, settings_tmp, n=3)
    assert seen["examples"] and seen["examples"][0]["streamer"] == "Flow"
