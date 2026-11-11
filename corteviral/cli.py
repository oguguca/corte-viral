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
