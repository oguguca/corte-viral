"""n8n gratuito rodando neste PC (instalado em tools/n8n, dados em data/n8n — nada disso vai para o GitHub)."""
from __future__ import annotations

import hashlib
import json
import os
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


def ensure_workflow(tools_dir: Path, data_dir: Path, file: Path = WORKFLOW_FILE) -> bool:
    """Importa e publica o fluxo pronto quando ele é novo ou mudou (rodar com o n8n desligado).

    Atenção: reimportar substitui o fluxo; credenciais escolhidas nos nós precisam ser reescolhidas.
    """
    file = Path(file)
    digest = hashlib.sha256(file.read_bytes()).hexdigest()
    marker = (Path(data_dir) / "n8n").resolve() / "workflow.sha256"
    if marker.exists() and marker.read_text(encoding="utf-8").strip() == digest:
        return False
    wf_id = json.loads(file.read_text(encoding="utf-8"))["id"]
    _cli(tools_dir, data_dir, "import:workflow", f"--input={file.resolve()}")
    _cli(tools_dir, data_dir, "publish:workflow", f"--id={wf_id}")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(digest, encoding="utf-8")
    return True
