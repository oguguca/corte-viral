"""n8n gratuito rodando neste PC (instalado em tools/n8n, dados em data/n8n — nada disso vai para o GitHub)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

PORT = 5678
BASE_URL = f"http://localhost:{PORT}"
WORKFLOW_FILE = Path(__file__).resolve().parent.parent / "n8n" / "corte-viral-postagem.json"


def n8n_bin(tools_dir: Path) -> Path | None:
    bin_dir = (Path(tools_dir) / "node_modules" / ".bin").resolve()  # absoluto: o n8n roda com outro cwd
    for name in ("n8n.cmd", "n8n"):
        if (bin_dir / name).exists():
            return bin_dir / name
    return None


def n8n_env(data_dir: Path, readable: tuple[Path, ...] = ()) -> dict[str, str]:
    user = (Path(data_dir) / "n8n").resolve()
    user.mkdir(parents=True, exist_ok=True)
    files = ";".join(str(Path(d).resolve()) for d in readable)
    return {**os.environ,
            "N8N_RESTRICT_FILE_ACCESS_TO": files,   # o n8n só lê os clipes (projetos/exportações)
            "N8N_BLOCK_FILE_ACCESS_TO_N8N_FILES": "true",
            "N8N_USER_FOLDER": str(user),          # banco, credenciais e chave de criptografia ficam aqui
            "N8N_PORT": str(PORT),
            "N8N_LISTEN_ADDRESS": "127.0.0.1",     # só este PC acessa
            "N8N_HOST": "localhost",
            "N8N_PROTOCOL": "http",
            "N8N_SECURE_COOKIE": "false",
            "WEBHOOK_URL": BASE_URL + "/",
            "N8N_DIAGNOSTICS_ENABLED": "false",
            "N8N_PERSONALIZATION_ENABLED": "false",
            "N8N_VERSION_NOTIFICATIONS_ENABLED": "false",
            "N8N_PAYLOAD_SIZE_MAX": "512",         # MB: os clipes chegam inteiros no webhook
            "N8N_FORMDATA_FILE_SIZE_MAX": "512",
            "GENERIC_TIMEZONE": "America/Sao_Paulo"}


def online(base_url: str = BASE_URL, timeout: float = 2) -> bool:
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/healthz", timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


def start(tools_dir: Path, data_dir: Path, readable: tuple[Path, ...] = ()) -> subprocess.Popen:
    """Liga o n8n em segundo plano, sem janela; o registro vai para data/n8n/n8n.log."""
    exe = n8n_bin(tools_dir)
    if exe is None:
        raise FileNotFoundError("n8n não instalado: rode `npm install n8n` em tools/n8n (veja o README)")
    env = n8n_env(data_dir, readable)
    log = open(Path(env["N8N_USER_FOLDER"]) / "n8n.log", "ab")  # noqa: SIM115 — fica com o processo
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen([str(exe), "start"], env=env, stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, creationflags=flags, cwd=str(Path(tools_dir).resolve()))


def _cli(tools_dir: Path, data_dir: Path, *args: str) -> str:
    exe = n8n_bin(tools_dir)
    if exe is None:
        raise FileNotFoundError("n8n não instalado")
    r = subprocess.run([str(exe), *args], env=n8n_env(data_dir), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300,
                       creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[-500:])
    return (r.stdout or "").strip()[-300:]


def keep_credentials(wf: dict, old: dict) -> None:
    """Leva para a versão nova as credenciais escolhidas à mão (ex.: YouTube) e religa esses nós.

    Nó novo que usa um tipo de credencial já escolhido em outro nó (ex.: "YouTube: declarar") recebe a mesma.
    """
    by_name = {n.get("name"): n for n in old.get("nodes", [])}
    by_type: dict[str, dict] = {}
    for n in old.get("nodes", []):
        for ctype, cred in (n.get("credentials") or {}).items():
            by_type.setdefault(ctype, cred)
    for n in wf.get("nodes", []):
        prev = by_name.get(n.get("name"))
        if prev and prev.get("credentials"):
            n["credentials"] = prev["credentials"]
            if not prev.get("disabled"):
                n.pop("disabled", None)
            continue
        ctype = n.get("parameters", {}).get("nodeCredentialType")
        if ctype in by_type and not n.get("credentials"):
            n["credentials"] = {ctype: by_type[ctype]}
            n.pop("disabled", None)


def ensure_workflow(tools_dir: Path, data_dir: Path, token: str, file: Path = WORKFLOW_FILE) -> bool:
    """Importa e publica o fluxo pronto quando ele é novo ou mudou (rodar com o n8n desligado).

    - grava no fluxo o token do Corte Viral (sem ele o webhook recusa tudo);
    - mantém as contas já conectadas (TikTok/Instagram ficam nos dados estáticos do fluxo).
    - mantém as credenciais escolhidas à mão nos nós (YouTube), sem precisar escolher de novo.
    """
    if not re.fullmatch(r"[0-9a-f]{32,128}", token or ""):
        raise ValueError("token do Corte Viral inválido")
    text = Path(file).read_text(encoding="utf-8").replace("__CV_TOKEN__", token)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    folder = (Path(data_dir) / "n8n").resolve()
    folder.mkdir(parents=True, exist_ok=True)
    marker = folder / "workflow.sha256"
    if marker.exists() and marker.read_text(encoding="utf-8").strip() == digest:
        return False
    wf = json.loads(text)
    if marker.exists():  # já importado antes: traz as contas conectadas da versão atual
        current = folder / "workflow-atual.json"
        try:
            _cli(tools_dir, data_dir, "export:workflow", f"--id={wf['id']}", f"--output={current}")
            old = json.loads(current.read_text(encoding="utf-8"))
            old = old[0] if isinstance(old, list) else old
            if old.get("staticData"):
                wf["staticData"] = old["staticData"]
            keep_credentials(wf, old)
        finally:
            current.unlink(missing_ok=True)
    prepared = folder / "workflow-importar.json"  # tem o token: fica em data/ (fora do git)
    prepared.write_text(json.dumps(wf, ensure_ascii=False), encoding="utf-8")
    try:
        _cli(tools_dir, data_dir, "import:workflow", f"--input={prepared}")
        _cli(tools_dir, data_dir, "publish:workflow", f"--id={wf['id']}")
    finally:
        prepared.unlink(missing_ok=True)
    marker.write_text(digest, encoding="utf-8")
    return True
