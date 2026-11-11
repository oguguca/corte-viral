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
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field, model_validator

from corteviral.browser_session import (BrowserSessionError, edge_path, export_cookies,
                                        open_login_window, profile_in_use)
from corteviral.config import Settings
from corteviral.ffmpeg import FFmpegError
from corteviral.caption_style import load_style, save_style
from corteviral.config import CaptionStyle
from corteviral.feedback import FeedbackStore
from corteviral.review import ReviewError, apply_patch
from corteviral.pipeline.analyze import analyze, load_moments
from corteviral.pipeline.ingest import IngestError, ingest, is_url
from corteviral.pipeline.transcribe import transcribe
from corteviral.pipeline.render import render_all
from corteviral.presets import LayoutPreset, detected_layout, load_preset, resolve_layout, save_preset
from corteviral.project import PROJECT_FILE, Project
from corteviral.usage import UsageTracker

STATIC = Path(__file__).parent / "static"
StageProgress = Callable[[str, float, str], None]
Pipeline = Callable[[str, Settings, StageProgress], Project]


def default_pipeline(source: str, settings: Settings, on_progress: StageProgress) -> Project:
    project = ingest(source, settings, on_progress=lambda f, m: on_progress("ingest", f, m))
    transcribe(project, settings, on_progress=lambda f, m: on_progress("transcribe", f, m))
    return project


class JobRequest(BaseModel):
    kind: Literal["ingest", "analyze", "render"] = "ingest"
    source: str | None = None       # ingest
    project: str | None = None      # analyze
    n: int | None = Field(default=None, ge=1, le=50)
    ids: list[str] | None = None    # render
    preview: bool = False           # render
    force: bool = False             # analyze: ignora o cache e reavalia
    captions: bool | None = None    # render: força legendas ligadas/desligadas
    profile: Literal["viral", "monetizacao"] | None = None
    provider: Literal["none", "gemini", "anthropic"] | None = None
    model: str | None = None
    language: Literal["auto", "pt", "en"] | None = None
    cookies_from_browser: str | None = None
    cookies_file: str | None = None

    @model_validator(mode="after")
    def _check(self):
        if self.kind == "ingest" and not self.source:
            raise ValueError("source é obrigatório para ingest")
        if self.kind in ("analyze", "render") and not self.project:
            raise ValueError("project é obrigatório para analyze/render")
        return self


class MomentPatch(BaseModel):
    captions: bool | None = None
    caption_text: str | None = Field(default=None, max_length=2000)
    reset_caption: bool = False
    status: str | None = None
    start: float | None = None
    end: float | None = None
    hook_on: bool | None = None
    meta: dict | None = None


FONT_FILES = {"Arial Black": "ariblk.ttf", "Impact": "impact.ttf", "Segoe UI Black": "seguibl.ttf",
              "Verdana": "verdanab.ttf", "Bahnschrift": "bahnschrift.ttf", "Trebuchet MS": "trebucbd.ttf",
              "Tahoma": "tahomabd.ttf"}


def available_fonts() -> list[str]:
    fonts_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    found = [name for name, f in FONT_FILES.items() if (fonts_dir / f).exists()]
    return found or ["Arial Black"]


class FeedbackBody(BaseModel):
    label: Literal["good", "bad"] | None


class StreamerBody(BaseModel):
    streamer: str = Field(min_length=1, max_length=80)


class _Jobs:
    def __init__(self):
        self.lock = threading.Lock()
        self.jobs: dict[str, dict] = {}
        self.running: str | None = None


def create_app(settings: Settings, pipeline: Pipeline | None = None, analyzer=None) -> FastAPI:
    pipeline = pipeline or default_pipeline
    analyzer = analyzer or analyze
    usage = UsageTracker(Path(settings.paths.data_dir) / "usage.json")
    feedback = FeedbackStore(Path(settings.paths.data_dir) / "feedback.json")
    app = FastAPI(title="Corte Viral")
    jobs = _Jobs()
    projects_dir = Path(settings.paths.projects_dir)
    data_dir = Path(settings.paths.data_dir)
    yt_profile = data_dir / "session-profile"
    yt_cookies = data_dir / "session-cookies.txt"
    yt_status = data_dir / "session-status.json"

    def _yt_sync() -> dict:
        info = export_cookies(yt_profile, yt_cookies)
        info["synced_at"] = time.time()
        data_dir.mkdir(parents=True, exist_ok=True)
        yt_status.write_text(json.dumps(info), encoding="utf-8")
        return info

    def _yt_state() -> dict:
        state = {"connected": False, "platforms": [], "synced_at": None,
                 "profile": yt_profile.exists(), "edge": edge_path() is not None}
        if yt_status.exists():
            try:
                state.update(json.loads(yt_status.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                pass
        state["connected"] = bool(state.get("platforms")) and yt_cookies.exists()
        return state

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

        if req.provider:
            s.llm.provider = req.provider
        # Sem escolha manual: usa a sessão de login do app (renovada agora; se falhar, a última salva)
        needs_web = is_url(req.source) if req.kind == "ingest" else req.kind == "analyze"
        if needs_web and not req.cookies_from_browser and not req.cookies_file and yt_profile.exists():
            job.update(message="lendo sessão de login")
            try:
                _yt_sync()
            except BrowserSessionError:
                pass
            if yt_cookies.exists():
                s.download.cookies_file = yt_cookies

        def progress(stage: str, frac: float, msg: str) -> None:
            job.update(stage=stage, progress=round(frac, 4), message=msg)

        try:
            if req.kind == "render":
                project = Project.load(_project_dir(req.project))
                job.update(stage="render", project=project.dir.name)
                saved = load_moments(project) or {"moments": []}
                chosen = [m for m in saved["moments"] if not req.ids or m["id"] in req.ids]
                if not chosen:
                    raise FileNotFoundError("nenhum momento para renderizar: rode \"Encontrar momentos\" antes")
                render_all(project, s, chosen, preview=req.preview, captions=req.captions,
                           on_progress=lambda f, m: progress("render", f, m))
            elif req.kind == "analyze":
                project = Project.load(_project_dir(req.project))
                if req.force:
                    project.invalidate_from("scoring")
                job.update(stage="analyze", project=project.dir.name)
                analyzer(project, s, n=req.n, profile=req.profile,
                         on_progress=lambda f, m: progress("analyze", f, m))
            else:
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
                    "has_moments": (pj.parent / "moments.json").exists(),
                    "mtime": pj.stat().st_mtime,
                })
        return sorted(out, key=lambda p: p["mtime"], reverse=True)

    @app.get("/api/projects/{name}/transcript")
    def get_transcript(name: str):
        f = _project_dir(name) / "transcript.json"
        if not f.exists():
            raise HTTPException(404, "transcrição ainda não existe")
        return json.loads(f.read_text(encoding="utf-8"))

    @app.get("/api/projects/{name}/moments")
    def get_moments(name: str):
        d = _project_dir(name)
        data = load_moments(Project.load(d))
        if not data:
            raise HTTPException(404, "momentos ainda não calculados")
        data.pop("all", None)
        clips = {}
        for m in data["moments"]:
            final, prev = d / "clips" / f"{m['id']}.mp4", d / "clips" / f"{m['id']}.preview.mp4"
            if final.exists() or prev.exists():
                clips[m["id"]] = {"final": final.exists(), "preview": prev.exists()}
        data["clips"] = clips
        data["feedback"] = feedback.labels(name)
        for m in data["moments"]:
            cw = m.get("caption_words")
            m["caption_text"] = " ".join(w["w"] for w in cw) if cw is not None else m.get("text", "")
        return data

    @app.put("/api/projects/{name}/moments/{moment_id}")
    def patch_moment(name: str, moment_id: str, body: MomentPatch):
        project = Project.load(_project_dir(name))
        try:
            moment = apply_patch(project, moment_id, body.model_dump(), settings)
        except KeyError:
            raise HTTPException(404, "momento não encontrado")
        except ReviewError as e:
            raise HTTPException(422, str(e))
        return {"ok": True, "moment": moment}

    @app.get("/api/caption-style")
    def get_caption_style():
        return {"style": load_style(settings).model_dump(), "fonts": available_fonts()}

    @app.put("/api/caption-style")
    def put_caption_style(style: CaptionStyle):
        save_style(settings, style)
        return {"ok": True, "style": style.model_dump()}

    @app.post("/api/projects/{name}/moments/{moment_id}/feedback")
    def post_feedback(name: str, moment_id: str, body: FeedbackBody):
        project = Project.load(_project_dir(name))
        data = load_moments(project) or {}
        moment = next((m for m in data.get("moments", []) + data.get("all", []) if m["id"] == moment_id), None)
        if moment is None:
            raise HTTPException(404, "momento não encontrado")
        feedback.add(name, project.data.get("streamer"), moment, body.label)
        return {"ok": True, "label": body.label}

    @app.get("/api/projects/{name}/clips/{clip_id}")
    def get_clip(name: str, clip_id: str, preview: bool = False):
        if not clip_id.replace("_", "").isalnum():
            raise HTTPException(404, "clipe não encontrado")
        f = _project_dir(name) / "clips" / f"{clip_id}{'.preview' if preview else ''}.mp4"
        if not f.exists():
            raise HTTPException(404, "clipe não encontrado")
        return FileResponse(f, media_type="video/mp4")

    @app.get("/api/projects/{name}/frame")
    def get_frame(name: str, t: float = 0.0):
        import cv2

        from corteviral.vision.frames import grab
        project = Project.load(_project_dir(name))
        frames = grab(Path(project.data["video_path"]), [max(0.0, t)], width=960)
        if not frames:
            raise HTTPException(404, "quadro indisponível")
        ok, buf = cv2.imencode(".jpg", frames[0], [cv2.IMWRITE_JPEG_QUALITY, 85])
        return Response(content=buf.tobytes(), media_type="image/jpeg")

    @app.get("/api/projects/{name}/layout")
    def get_layout(name: str):
        project = Project.load(_project_dir(name))
        streamer = project.data.get("streamer")
        has_preset = load_preset(settings.render.presets_dir, streamer) is not None
        det = detected_layout(project, settings)
        preset = resolve_layout(project, settings)
        origin = "preset" if has_preset else ("detected" if det.get("cams") or det.get("gameplay") else "fallback")
        return {"preset": preset.model_dump(), "origin": origin, "detected": det, "streamer": streamer,
                "duration": project.data.get("duration", 0)}

    @app.put("/api/projects/{name}/layout")
    def put_layout(name: str, preset: LayoutPreset):
        project = Project.load(_project_dir(name))
        streamer = project.data.get("streamer") or project.data.get("title") or name
        if not project.data.get("streamer"):
            project.data["streamer"] = streamer
            project.save()
        save_preset(settings.render.presets_dir, streamer, preset)
        return {"ok": True, "streamer": streamer, "preset": preset.model_dump()}

    @app.put("/api/projects/{name}/streamer")
    def put_streamer(name: str, body: StreamerBody):
        project = Project.load(_project_dir(name))
        project.data["streamer"] = body.streamer.strip()
        project.save()
        return {"ok": True, "streamer": project.data["streamer"]}

    @app.get("/api/usage")
    def get_usage():
        u = usage.today()
        lim = {"requests": settings.llm.daily_request_limit, "tokens": settings.llm.daily_token_limit}
        pct = max(u["requests"] / max(1, lim["requests"]), u["total_tokens"] / max(1, lim["tokens"])) * 100
        provider = settings.llm.provider
        return {**u, "limits": lim, "percent": round(min(pct, 100.0), 1), "provider": provider}

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

    @app.get("/api/session/status")
    def session_status():
        return _yt_state()

    @app.post("/api/session/connect")
    def session_connect():
        try:
            open_login_window(yt_profile)
        except BrowserSessionError as e:
            raise HTTPException(400, str(e))
        return {"ok": True}

    @app.post("/api/session/sync")
    def session_sync():
        try:
            return _yt_sync()
        except BrowserSessionError as e:
            raise HTTPException(409, str(e))

    @app.get("/api/session/window-open")
    def session_window_open():
        return {"open": yt_profile.exists() and profile_in_use(yt_profile)}

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
