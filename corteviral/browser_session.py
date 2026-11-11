"""Sessão de login própria do app (YouTube, Twitch, Kick, …), num perfil separado do Edge.

Por quê: Chrome/Brave/Edge recentes usam criptografia "app-bound" nos cookies — nenhum programa
externo consegue lê-los do disco. Aqui o próprio Edge descriptografa: abrimos o perfil do app em
modo invisível com depuração remota (permitida em perfis não-padrão) e pedimos os cookies via CDP.
Funciona com qualquer navegador principal do usuário aberto, porque é um perfil/processo separado.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

LOGIN_URLS = [
    "https://accounts.google.com/ServiceLogin?service=youtube&continue=https://www.youtube.com/",
    "https://www.twitch.tv/login",
    "https://kick.com/",
]
# plataforma -> (domínio, cookies que só existem com login)
_PLATFORM_LOGIN = {
    "kick": ("kick.com", {"session_token", "kick_session"}),
    "twitch": ("twitch.tv", {"auth-token"}),
    "youtube": ("youtube.com", {"SAPISID", "__Secure-3PAPISID", "LOGIN_INFO"}),
}


class BrowserSessionError(Exception):
    pass


def edge_path() -> Path | None:
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if base:
            p = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if p.exists():
                return p
    return None


def _on_domain(c: dict, dom: str) -> bool:
    d = c.get("domain", "").lstrip(".")
    return d == dom or d.endswith("." + dom)


def to_netscape(cookies: list[dict]) -> str:
    """Converte cookies do CDP para o formato cookies.txt (Netscape) que o yt-dlp lê.

    Exporta todos: o perfil é exclusivo do app e só tem os sites em que o usuário entrou.
    """
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        domain = c["domain"]
        expires = 0 if c.get("session") or c.get("expires", -1) < 0 else int(c["expires"])
        lines.append("\t".join([
            domain, "TRUE" if domain.startswith(".") else "FALSE", c.get("path", "/"),
            "TRUE" if c.get("secure") else "FALSE", str(expires), c["name"], c["value"],
        ]))
    return "\n".join(lines) + "\n"


def logged_platforms(cookies: list[dict]) -> list[str]:
    """Plataformas em que há sessão logada, em ordem alfabética."""
    return sorted(p for p, (dom, names) in _PLATFORM_LOGIN.items()
                  if any(c["name"] in names and _on_domain(c, dom) for c in cookies))


def profile_in_use(profile_dir: Path) -> bool:
    """O Edge cria 'lockfile' no perfil enquanto alguma janela dele está aberta."""
    lock = Path(profile_dir) / "lockfile"
    if not lock.exists():
        return False
    try:
        lock.unlink()  # se ninguém está usando, dá para apagar
        return False
    except OSError:
        return True


def open_login_window(profile_dir: Path) -> None:
    edge = edge_path()
    if not edge:
        raise BrowserSessionError("Microsoft Edge não encontrado")
    Path(profile_dir).mkdir(parents=True, exist_ok=True)
    subprocess.Popen([str(edge), f"--user-data-dir={profile_dir}", "--no-first-run",
                      "--no-default-browser-check", "--new-window", *LOGIN_URLS])


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _cdp_get_cookies(ws_url: str) -> list[dict]:
    import websocket  # websocket-client

    ws = websocket.create_connection(ws_url, timeout=15, suppress_origin=True)
    try:
        ws.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                if "error" in msg:
                    raise BrowserSessionError(f"CDP: {msg['error']}")
                return msg["result"]["cookies"]
    finally:
        ws.close()


def export_cookies(profile_dir: Path, out_file: Path, timeout: float = 20) -> dict:
    """Abre o perfil do app invisível, lê os cookies via CDP e grava cookies.txt."""
    edge = edge_path()
    if not edge:
        raise BrowserSessionError("Microsoft Edge não encontrado")
    if not Path(profile_dir).exists():
        raise BrowserSessionError("nenhuma conta conectada: clique em \"Conectar contas\"")
    if profile_in_use(profile_dir):
        raise BrowserSessionError("feche a janela de login (Edge) antes de continuar")

    port = _free_port()
    proc = subprocess.Popen(
        [str(edge), "--headless=new", f"--user-data-dir={profile_dir}", f"--remote-debugging-port={port}",
         "--no-first-run", "--no-default-browser-check", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        ws_url = None
        end = time.time() + timeout
        while time.time() < end and ws_url is None:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as r:
                    ws_url = json.load(r)["webSocketDebuggerUrl"]
            except OSError:
                time.sleep(0.3)
        if not ws_url:
            raise BrowserSessionError("não consegui abrir o Edge em segundo plano para ler a sessão")
        cookies = _cdp_get_cookies(ws_url)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    out_file = Path(out_file)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(to_netscape(cookies), encoding="utf-8")
    return {"count": len(cookies), "platforms": logged_platforms(cookies)}
