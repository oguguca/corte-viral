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
