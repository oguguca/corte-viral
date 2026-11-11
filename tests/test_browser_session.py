from corteviral.browser_session import logged_platforms, to_netscape


COOKIES = [
    {"name": "SAPISID", "value": "abc", "domain": ".youtube.com", "path": "/", "secure": True,
     "expires": 1900000000.5, "session": False},
    {"name": "PREF", "value": "f6=8", "domain": "www.youtube.com", "path": "/", "secure": False,
     "expires": -1, "session": True},
    {"name": "NID", "value": "x", "domain": ".google.com", "path": "/", "secure": True,
     "expires": 1900000000, "session": False},
    {"name": "auth-token", "value": "t", "domain": ".twitch.tv", "path": "/", "secure": True,
     "expires": 1900000000, "session": False},
]


def test_to_netscape_format_and_domain_filter():
    txt = to_netscape(COOKIES)
    lines = txt.splitlines()
    assert lines[0] == "# Netscape HTTP Cookie File"
    assert ".youtube.com\tTRUE\t/\tTRUE\t1900000000\tSAPISID\tabc" in lines
    assert "www.youtube.com\tFALSE\t/\tFALSE\t0\tPREF\tf6=8" in lines
    assert any(l.startswith(".google.com\t") for l in lines)
    assert any(l.startswith(".twitch.tv	") for l in lines)  # qualquer site da sessão do app


def test_logged_platforms():
    assert logged_platforms(COOKIES) == ["twitch", "youtube"]
    assert logged_platforms([c for c in COOKIES if c["name"] != "SAPISID"]) == ["twitch"]
    kick = [{"name": "session_token", "value": "k", "domain": ".kick.com", "path": "/", "secure": True,
             "expires": 1900000000, "session": False}]
    assert logged_platforms(kick) == ["kick"]
    assert logged_platforms([]) == []


def test_open_login_window_uses_absolute_profile_and_debug_port(tmp_path, monkeypatch):
    from pathlib import Path
    import corteviral.browser_session as bs
    launched = []
    monkeypatch.setattr(bs, "edge_path", lambda: Path("C:/edge/msedge.exe"))
    monkeypatch.setattr(bs.subprocess, "Popen", lambda args, **kw: launched.append(args))
    monkeypatch.chdir(tmp_path)
    bs.open_login_window(Path("data/session-profile"))
    args = launched[0]
    udd = next(a for a in args if a.startswith("--user-data-dir="))
    assert Path(udd.split("=", 1)[1]).is_absolute()
    assert Path(udd.split("=", 1)[1]) == (tmp_path / "data" / "session-profile")
    port_arg = next(a for a in args if a.startswith("--remote-debugging-port="))
    port = int(port_arg.split("=")[1])
    assert (tmp_path / "data" / "session-profile" / bs.PORT_FILE).read_text() == str(port)


def test_export_reads_from_open_login_window(tmp_path, monkeypatch):
    import corteviral.browser_session as bs
    profile = tmp_path / "p"
    profile.mkdir()
    (profile / bs.PORT_FILE).write_text("5555")
    monkeypatch.setattr(bs, "edge_path", lambda: tmp_path / "msedge.exe")
    monkeypatch.setattr(bs, "profile_in_use", lambda p: True)
    monkeypatch.setattr(bs, "_ws_url", lambda port, timeout: "ws://x" if port == 5555 else None)
    monkeypatch.setattr(bs, "_cdp_get_cookies", lambda ws: COOKIES)
    monkeypatch.setattr(bs.subprocess, "Popen",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("não deveria abrir outro Edge")))
    info = bs.export_cookies(profile, tmp_path / "c.txt")
    assert info["platforms"] == ["twitch", "youtube"]
    assert (tmp_path / "c.txt").read_text(encoding="utf-8").startswith("# Netscape")
