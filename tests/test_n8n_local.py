import json

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


def test_ensure_workflow_imports_once_per_version(tmp_path, monkeypatch):
    calls = []

    class R:
        returncode, stdout, stderr = 0, "ok", ""

    monkeypatch.setattr(n8n_local, "n8n_bin", lambda d: tmp_path / "n8n.cmd")
    monkeypatch.setattr(n8n_local.subprocess, "run", lambda args, **kw: calls.append(args[1]) or R())
    wf = tmp_path / "wf.json"
    wf.write_text('{"id": "X1"}')
    assert n8n_local.ensure_workflow(tmp_path, tmp_path, wf) is True
    assert calls == ["import:workflow", "publish:workflow"]
    assert n8n_local.ensure_workflow(tmp_path, tmp_path, wf) is False  # mesma versão: nada a fazer
    wf.write_text('{"id": "X1", "v": 2}')
    assert n8n_local.ensure_workflow(tmp_path, tmp_path, wf) is True and len(calls) == 4
