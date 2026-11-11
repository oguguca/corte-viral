"""Linha de comando: python -m corteviral <comando>."""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from corteviral.config import load_settings
from corteviral.ffmpeg import FFmpegError
from corteviral.models import Transcript
from corteviral.pipeline.analyze import analyze, load_moments
from corteviral.pipeline.render import render_all
from corteviral.pipeline.ingest import IngestError, ingest
from corteviral.pipeline.transcribe import transcribe
from corteviral.project import Project


def _progress(stage: str):
    def report(frac: float, msg: str) -> None:
        print(f"\r[{stage}] {frac * 100:5.1f}% {msg:<30}", end="", flush=True)
    return report


def _cmd_ingest(args) -> int:
    settings = load_settings(Path(args.config) if args.config else None)
    if args.model:
        settings.whisper.model = args.model
    if args.language:
        settings.whisper.language = args.language
    if args.cookies_from_browser:
        settings.download.cookies_from_browser = args.cookies_from_browser

    if args.force:
        from corteviral.pipeline.ingest import is_url
        key = args.source if is_url(args.source) else str(Path(args.source).resolve())
        existing = Project.find_by_source(settings.paths.projects_dir, key)
        if existing:
            existing.invalidate_from("ingest")

    t0 = time.perf_counter()
    project = ingest(args.source, settings, on_progress=_progress("ingest"))
    print()
    t1 = time.perf_counter()
    transcript = transcribe(project, settings, on_progress=_progress("transcribe"))
    print()
    t2 = time.perf_counter()

    print(f"Projeto:    {project.dir}")
    print(f"Duração:    {project.data.get('duration', 0) / 60:.1f} min")
    print(f"Idioma:     {transcript.language}")
    print(f"Transcrição: {project.path('transcript.json')} ({len(transcript.words)} palavras)")
    print(f"Tempo:      ingest {t1 - t0:.0f}s · transcrição {t2 - t1:.0f}s")
    return 0


def _fmt(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


def _cmd_analyze(args) -> int:
    settings = load_settings(Path(args.config) if args.config else None)
    if args.provider:
        settings.llm.provider = args.provider
    project = Project.load(Path(args.project))
    moments = analyze(project, settings, n=args.n, profile=args.profile, on_progress=_progress("analyze"))
    print()
    for i, m in enumerate(moments, 1):
        tag = "IA" if m.get("ai") else "--"
        print(f"{i:>2}. [{m['score']:>3}] {_fmt(m['start'])}–{_fmt(m['end'])} {tag} {m['reason']}")
        if m.get("hook"):
            print(f"       gancho: {m['hook']}")
    print()
    print(f"Salvo em {project.path('moments.json')}")
    return 0


def _cmd_detect(args) -> int:
    from corteviral.presets import detected_layout, resolve_layout
    settings = load_settings(Path(args.config) if args.config else None)
    project = Project.load(Path(args.project))
    if args.force:
        project.data.pop("layout_detect", None)
    det = detected_layout(project, settings)
    print(f"Câmeras: {len(det['cams'])}")
    for i, c in enumerate(det["cams"], 1):
        print(f"  {i}. {c['rect']}  (presente em {c['share']:.0%} dos quadros)")
    print(f"Gameplay: {det['gameplay']}")
    print(f"Layout em uso: {resolve_layout(project, settings).layout}")
    return 0


def _cmd_render(args) -> int:
    settings = load_settings(Path(args.config) if args.config else None)
    project = Project.load(Path(args.project))
    saved = load_moments(project)
    if not saved:
        print("Erro: rode 'analyze' antes de renderizar.", file=sys.stderr)
        return 1
    ids = set(args.ids.split(",")) if args.ids else None
    chosen = [m for m in saved["moments"] if not ids or m["id"] in ids]
    outs = render_all(project, settings, chosen, preview=args.preview, on_progress=_progress("render"),
                      captions=args.captions)
    print()
    for o in outs:
        print(f"  {o}")
    return 0


def _cmd_info(args) -> int:
    project = Project.load(Path(args.project))
    print(f"Projeto:  {project.dir}")
    print(f"Fonte:    {project.data.get('source')}")
    print(f"Título:   {project.data.get('title')}")
    print(f"Duração:  {project.data.get('duration', 0) / 60:.1f} min")
    print(f"Etapas:   {', '.join(project.data.get('stages', {})) or '(nenhuma)'}")
    tpath = project.path("transcript.json")
    if tpath.exists():
        t = Transcript.load(tpath)
        print(f"Idioma:   {t.language} · {len(t.words)} palavras · {len(t.segments)} segmentos")
    return 0


def _open_window(url: str) -> None:
    """Abre a interface numa janela própria (Edge em modo app); senão, no navegador padrão."""
    import os
    import subprocess
    import webbrowser

    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        edge = Path(base or "") / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        if base and edge.exists():
            subprocess.Popen([str(edge), f"--app={url}", "--window-size=1400,900"])
            return
    webbrowser.open(url)


def _cmd_ui(args) -> int:
    import socket
    import threading

    import uvicorn

    from corteviral.server.app import create_app

    settings = load_settings(Path(args.config) if args.config else None)
    with socket.socket() as s:
        s.bind(("127.0.0.1", args.port or 0))
        port = s.getsockname()[1]
    url = f"http://127.0.0.1:{port}/"
    print(f"Corte Viral rodando em {url}  (feche esta janela para encerrar)")
    if not args.no_window:
        threading.Timer(1.0, _open_window, args=(url,)).start()
    uvicorn.run(create_app(settings), host="127.0.0.1", port=port, log_level="warning")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="corteviral", description="Corte Viral — VOD → clipes verticais")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest", help="baixa/importa o vídeo e transcreve")
    p.add_argument("source", help="link (YouTube/Twitch/Kick) ou caminho de arquivo")
    p.add_argument("--config", help="caminho do config.yaml")
    p.add_argument("--model", help="modelo whisper (tiny|base|small|medium|large-v3)")
    p.add_argument("--language", choices=["auto", "pt", "en"])
    p.add_argument("--force", action="store_true", help="refaz tudo, ignorando o cache")
    p.add_argument("--cookies-from-browser", metavar="NAVEGADOR",
                   help="usa o login do navegador (firefox|edge|chrome) p/ vídeos com restrição")
    p.set_defaults(func=_cmd_ingest)

    p = sub.add_parser("info", help="mostra o estado de um projeto")
    p.add_argument("project", help="pasta do projeto")
    p.set_defaults(func=_cmd_info)

    p = sub.add_parser("analyze", help="encontra e pontua os melhores momentos de um projeto")
    p.add_argument("project", help="pasta do projeto")
    p.add_argument("--config", help="caminho do config.yaml")
    p.add_argument("--n", type=int, help="quantos momentos (padrão: config)")
    p.add_argument("--profile", choices=["viral", "monetizacao"])
    p.add_argument("--provider", choices=["none", "gemini", "anthropic"], help="IA usada na nota")
    p.set_defaults(func=_cmd_analyze)

    p = sub.add_parser("detect", help="detecta câmeras e a janela da gameplay")
    p.add_argument("project", help="pasta do projeto")
    p.add_argument("--config", help="caminho do config.yaml")
    p.add_argument("--force", action="store_true", help="refaz a detecção")
    p.set_defaults(func=_cmd_detect)

    p = sub.add_parser("render", help="gera os clipes verticais 9:16 dos momentos")
    p.add_argument("project", help="pasta do projeto")
    p.add_argument("--config", help="caminho do config.yaml")
    p.add_argument("--ids", help="só estes momentos (ex.: m00012,m01618)")
    p.add_argument("--preview", action="store_true", help="prévia rápida 540x960")
    p.add_argument("--captions", dest="captions", action="store_true", default=None, help="força legendas")
    p.add_argument("--no-captions", dest="captions", action="store_false", help="sem legendas")
    p.set_defaults(func=_cmd_render)

    p = sub.add_parser("ui", help="abre a interface gráfica")
    p.add_argument("--config", help="caminho do config.yaml")
    p.add_argument("--port", type=int, help="porta fixa (padrão: livre aleatória)")
    p.add_argument("--no-window", action="store_true", help="não abre a janela automaticamente")
    p.set_defaults(func=_cmd_ui)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (IngestError, FFmpegError, FileNotFoundError) as e:
        print(f"\nErro: {e}", file=sys.stderr)
        return 1
