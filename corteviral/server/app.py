"""API local (127.0.0.1) usada pela interface: tarefas, projetos, transcrição e vídeo.

É o mesmo backend que o Electron vai consumir na Fase 5.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from corteviral.config import Settings
from corteviral.ffmpeg import FFmpegError
from corteviral.pipeline.ingest import IngestError, ingest
from corteviral.pipeline.transcribe import transcribe
from corteviral.project import PROJECT_FILE, Project

STATIC = Path(__file__).parent / "static"
StageProgress = Callable[[str, float, str], None]
Pipeline = Callable[[str, Settings, StageProgress], Project]


def default_pipeline(source: str, settings: Settings, on_progress: StageProgress) -> Project:
    project = ingest(source, settings, on_progress=lambda f, m: on_progress("ingest", f, m))
    transcribe(project, settings, on_progress=lambda f, m: on_progress("transcribe", f, m))
    return project


class JobRequest(BaseModel):
    source: str
    model: str | None = None
    language: Literal["auto", "pt", "en"] | None = None
    cookies_from_browser: str | None = None
    cookies_file: str | None = None


class _Jobs:
    def __init__(self):
        self.lock = threading.Lock()
        self.jobs: dict[str, dict] = {}
        self.running: str | None = None


def create_app(settings: Settings, pipeline: Pipeline | None = None) -> FastAPI:
    pipeline = pipeline or default_pipeline
    app = FastAPI(title="Corte Viral")
    jobs = _Jobs()
    projects_dir = Path(settings.paths.projects_dir)

    # --- helpers ---------------------------------------------------------
    def _project_dir(name: str) -> Path:
        root = projects_dir.resolve()
        d = (projects_dir / name).resolve()
        if d.parent != root or not (d / PROJECT_FILE).exists():
            raise HTTPException(404, "projeto não encontrado")
        return d

    def _run_job(job_id: str, req: JobRequest) -> None:
        job = jobs.jobs[job_id]
        s = settings.model_copy(deep=True)
        if req.model:
            s.whisper.model = req.model
        if req.language:
            s.whisper.language = req.language
        s.download.cookies_from_browser = req.cookies_from_browser or None
        s.download.cookies_file = Path(req.cookies_file) if req.cookies_file else None

        def progress(stage: str, frac: float, msg: str) -> None:
            job.update(stage=stage, progress=round(frac, 4), message=msg)

        try:
            project = pipeline(req.source, s, progress)
            job.update(status="done", project=project.dir.name, progress=1.0, message="concluído")
        except (IngestError, FFmpegError, FileNotFoundError) as e:
            job.update(status="error", error=str(e))
        except Exception as e:  # noqa: BLE001 — mostra qualquer falha na interface
            job.update(status="error", error=f"erro inesperado: {e!r}")
        finally:
            job["finished_at"] = time.time()
            with jobs.lock:
                jobs.running = None

    # --- rotas -----------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def index():
        return (STATIC / "index.html").read_text(encoding="utf-8")

    @app.post("/api/jobs")
    def create_job(req: JobRequest):
        with jobs.lock:
            if jobs.running:
                raise HTTPException(409, "já existe uma tarefa em andamento")
            job_id = uuid.uuid4().hex[:12]
            jobs.jobs[job_id] = {"id": job_id, "source": req.source, "status": "running",
                                 "stage": "ingest", "progress": 0.0, "message": "iniciando",
                                 "error": None, "project": None, "started_at": time.time()}
            jobs.running = job_id
        threading.Thread(target=_run_job, args=(job_id, req), daemon=True).start()
        return jobs.jobs[job_id]

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        if job_id not in jobs.jobs:
            raise HTTPException(404, "tarefa não encontrada")
        return jobs.jobs[job_id]

    @app.get("/api/jobs")
    def current_job():
        return jobs.jobs.get(jobs.running) if jobs.running else None

    @app.get("/api/projects")
    def list_projects():
        out = []
        if projects_dir.exists():
            for pj in projects_dir.glob(f"*/{PROJECT_FILE}"):
                try:
                    data = json.loads(pj.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                out.append({
                    "name": pj.parent.name, "title": data.get("title"), "source": data.get("url") or data.get("source"),
                    "duration": data.get("duration", 0), "stages": list(data.get("stages", {})),
                    "has_transcript": (pj.parent / "transcript.json").exists(),
                    "mtime": pj.stat().st_mtime,
                })
        return sorted(out, key=lambda p: p["mtime"], reverse=True)

    @app.get("/api/projects/{name}/transcript")
    def get_transcript(name: str):
        f = _project_dir(name) / "transcript.json"
        if not f.exists():
            raise HTTPException(404, "transcrição ainda não existe")
        return json.loads(f.read_text(encoding="utf-8"))

    @app.get("/api/projects/{name}/video")
    def get_video(name: str):
        project = Project.load(_project_dir(name))
        vp = project.data.get("video_path")
        if not vp or not Path(vp).exists():
            raise HTTPException(404, "vídeo não encontrado")
        return FileResponse(vp)

    @app.post("/api/projects/{name}/open-folder")
    def open_folder(name: str):
        d = _project_dir(name)
        if os.name == "nt":
            os.startfile(d)  # noqa: S606 — só abre a pasta do projeto no Explorer
        return {"ok": True}

    @app.post("/api/pick-file")
    def pick_file(kind: Literal["video", "cookies"] = "video"):
        """Abre o seletor de arquivos nativo do Windows (processo separado, não trava o servidor)."""
        types = ("[('Cookies','*.txt'),('Todos','*.*')]" if kind == "cookies"
                 else "[('Vídeos','*.mp4 *.mkv *.mov *.webm *.flv *.ts'),('Todos','*.*')]")
        title = "Escolha o cookies.txt" if kind == "cookies" else "Escolha o vídeo"
        script = (
            "import tkinter as tk; from tkinter import filedialog as fd; r=tk.Tk(); r.withdraw(); "
            "r.attributes('-topmost', True); "
            f"p=fd.askopenfilename(title={title!r}, filetypes={types}); "
            "print(p, end='')"
        )
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, encoding="utf-8",
                              env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        return {"path": proc.stdout.strip() or None}

    return app
