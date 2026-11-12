import json
from pathlib import Path

from corteviral import n8n_local


def test_bin_detection(tmp_path):
    assert n8n_local.n8n_bin(tmp_path) is None
    b = tmp_path / "node_modules" / ".bin"
    b.mkdir(parents=True)
    (b / "n8n.cmd").write_text("@echo off")
    assert n8n_local.n8n_bin(tmp_path).name == "n8n.cmd"


def test_bin_is_absolute_even_from_relative_dir(tmp_path, monkeypatch):
    # o n8n é iniciado com cwd=tools/n8n: um caminho relativo apontaria para tools/n8n/tools/n8n/…
    b = tmp_path / "tools" / "n8n" / "node_modules" / ".bin"
    b.mkdir(parents=True)
    (b / "n8n.cmd").write_text("@echo off")
    monkeypatch.chdir(tmp_path)
    exe = n8n_local.n8n_bin(n8n_local.Path("tools/n8n"))
    assert exe.is_absolute() and exe.exists()


def test_env_keeps_data_local_and_private(tmp_path):
    env = n8n_local.n8n_env(tmp_path, (tmp_path / "projects", tmp_path / "exports"))
    assert env["N8N_RESTRICT_FILE_ACCESS_TO"] == f"{(tmp_path / 'projects').resolve()};{(tmp_path / 'exports').resolve()}"
    assert env["N8N_USER_FOLDER"].startswith(str(tmp_path.resolve()))
    assert env["N8N_LISTEN_ADDRESS"] == "127.0.0.1"
    assert env["N8N_DIAGNOSTICS_ENABLED"] == "false"


def test_offline_when_nothing_listens():
    assert n8n_local.online("http://127.0.0.1:1") is False


def test_start_without_install_is_clear(tmp_path):
    try:
        n8n_local.start(tmp_path, tmp_path)
    except FileNotFoundError as e:
        assert "n8n" in str(e)
    else:
        raise AssertionError("deveria falhar")


def test_shipped_workflow_is_valid():
    wf = json.loads(n8n_local.WORKFLOW_FILE.read_text(encoding="utf-8"))
    names = {n["name"] for n in wf["nodes"]}
    hook = next(n for n in wf["nodes"] if n["type"] == "n8n-nodes-base.webhook")
    assert hook["parameters"]["path"] == "corte-viral"
    assert hook["parameters"]["httpMethod"] == "POST"
    for wanted in ("YouTube: enviar", "Instagram: criar envio", "TikTok: iniciar envio", "Avisar o Corte Viral"):
        assert wanted in names, wanted
    for src, conns in wf["connections"].items():  # toda ligação aponta para um nó que existe
        assert src in names
        for outs in conns["main"]:
            for c in outs:
                assert c["node"] in names, c["node"]


TOK = "ab" * 24  # formato real: secrets.token_hex(24)


def test_ensure_workflow_injects_token_keeps_accounts_and_runs_once(tmp_path, monkeypatch):
    calls, imported = [], {}

    class R:
        returncode, stdout, stderr = 0, "ok", ""

    def fake_run(args, **kw):
        calls.append(args[1])
        if args[1] == "export:workflow":  # versão que está no n8n, com as contas conectadas
            out = next(a for a in args if a.startswith("--output=")).split("=", 1)[1]
            Path(out).write_text(json.dumps([{"id": "X1", "staticData": {"global": {"tiktok": {"refresh_token": "r"}}}}]))
        if args[1] == "import:workflow":
            inp = next(a for a in args if a.startswith("--input=")).split("=", 1)[1]
            imported.update(json.loads(Path(inp).read_text(encoding="utf-8")))
        return R()

    monkeypatch.setattr(n8n_local, "n8n_bin", lambda d: tmp_path / "n8n.cmd")
    monkeypatch.setattr(n8n_local.subprocess, "run", fake_run)
    wf = tmp_path / "wf.json"
    wf.write_text(json.dumps({"id": "X1", "nodes": [{"parameters": {"jsCode": "const APP_TOKEN = '__CV_TOKEN__';"}}]}))
    assert n8n_local.ensure_workflow(tmp_path, tmp_path, TOK, wf) is True
    assert calls == ["import:workflow", "publish:workflow"]  # primeira vez: nada a preservar
    assert TOK in json.dumps(imported) and "__CV_TOKEN__" not in json.dumps(imported)
    assert n8n_local.ensure_workflow(tmp_path, tmp_path, TOK, wf) is False  # mesma versão: nada a fazer
    wf.write_text(json.dumps({**json.loads(wf.read_text()), "v": 2}))
    calls.clear()
    assert n8n_local.ensure_workflow(tmp_path, tmp_path, TOK, wf) is True
    assert calls == ["export:workflow", "import:workflow", "publish:workflow"]
    assert imported["staticData"]["global"]["tiktok"]["refresh_token"] == "r"  # contas conectadas mantidas


def test_update_keeps_chosen_credentials_and_shares_them_with_new_nodes():
    cred = {"youTubeOAuth2Api": {"id": "C1", "name": "YouTube account"}}
    old = {"nodes": [{"name": "YouTube: enviar", "credentials": cred}]}
    wf = {"nodes": [{"name": "YouTube: enviar", "disabled": True, "parameters": {}},
                    {"name": "YouTube: declarar", "disabled": True,
                     "parameters": {"nodeCredentialType": "youTubeOAuth2Api"}},
                    {"name": "Outro", "parameters": {}}]}
    n8n_local.keep_credentials(wf, old)
    up, decl, other = wf["nodes"]
    assert up["credentials"] == cred and "disabled" not in up
    assert decl["credentials"] == cred and "disabled" not in decl
    assert "credentials" not in other


def test_shipped_workflow_declares_no_synthetic_media():
    wf = json.loads(n8n_local.WORKFLOW_FILE.read_text(encoding="utf-8"))
    decl = next(n for n in wf["nodes"] if n["name"] == "YouTube: declarar")
    assert "containsSyntheticMedia: false" in decl["parameters"]["jsonBody"]
    assert "selfDeclaredMadeForKids: false" in decl["parameters"]["jsonBody"]
    assert wf["connections"]["YouTube: enviar"]["main"][0][0]["node"] == "YouTube: ver status"
    assert wf["connections"]["YouTube: ver status"]["main"][0][0]["node"] == "YouTube: declarar"
    assert "uploadId" in decl["parameters"]["jsonBody"]
    assert wf["connections"]["YouTube: declarar"]["main"][0][0]["node"] == "YouTube: idioma"
    assert wf["connections"]["YouTube: idioma"]["main"][0][0]["node"] == "YouTube: promoção paga"
    assert wf["connections"]["YouTube: conferir"]["main"][0][0]["node"] == "YouTube: resultado"
