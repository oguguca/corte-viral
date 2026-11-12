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

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from corteviral.browser_session import (BrowserSessionError, edge_path, export_cookies,
                                        open_login_window, profile_in_use)
from corteviral.config import Settings
from corteviral.ffmpeg import FFmpegError
from corteviral.caption_style import load_style, save_style
from corteviral.config import AnalysisSettings, CaptionStyle
from corteviral import n8n_local
from corteviral.export import _approved as approved_moments
from corteviral.export import export_approved, export_check
from corteviral.feedback import FeedbackStore
from corteviral.publish import (NETWORKS, PublishConfig, PublishError, PublishStore, apply_callback,
                                publish_clips, setup_account, test_connection)
from corteviral.goals import GoalsError, GoalsStore, daily_counts, parse_channel, program_progress
from corteviral.metadata import generate_posts
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
    kind: Literal["ingest", "analyze", "render", "export", "posts", "publish"] = "ingest"
    source: str | None = None       # ingest
    project: str | None = None      # analyze
    n: int | None = Field(default=None, ge=1, le=50)
    min_seconds: float | None = Field(default=None, ge=5, le=180)   # analyze: duração personalizada
    max_seconds: float | None = Field(default=None, ge=5, le=180)
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
    section_start: float | None = Field(default=None, ge=0, le=7 * 86400)  # ingest: só um trecho (s)
    section_end: float | None = Field(default=None, ge=0, le=7 * 86400)
    callback_url: str | None = Field(default=None, exclude=True)  # preenchido pelo servidor (publish/export)

    @model_validator(mode="after")
    def _check(self):
        if self.kind == "ingest" and not self.source:
            raise ValueError("source é obrigatório para ingest")
        if (self.section_start is None) != (self.section_end is None) or (
                self.section_start is not None and self.section_end - self.section_start < 10):
            raise ValueError("trecho inválido: informe início e fim, com o fim ao menos 10 s depois")
        if (self.min_seconds is None) != (self.max_seconds is None) or (
                self.min_seconds is not None and self.max_seconds - self.min_seconds < 5):
            raise ValueError("duração inválida: informe mínimo e máximo, com o máximo ao menos 5 s maior")
        if self.kind in ("analyze", "render", "export", "posts", "publish") and not self.project:
            raise ValueError("project é obrigatório para analyze/render/export")
        return self


class MomentPatch(BaseModel):
    captions: bool | None = None
    caption_text: str | None = Field(default=None, max_length=2000)
    reset_caption: bool = False
    status: str | None = None
    start: float | None = None
    end: float | None = None
    hook_on: bool | None = None
    caption_pos: float | None = Field(default=None, ge=0.0, le=1.0)  # posição da legenda só neste clipe
    reset_caption_pos: bool = False
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


class PublishConfigBody(BaseModel):
    webhook_url: str
    networks: dict[str, bool]
    youtube_privacy: Literal["private", "unlisted", "public"] = "private"
    auto_send: bool = False


class YouTubeGoalBody(BaseModel):
    api_key: str = Field(default="", max_length=200)
    channel: str | None = Field(default=None, max_length=300)
    remove: bool = False


class GoalsBody(BaseModel):
    daily_videos: int | None = Field(default=None, ge=1, le=50)
    youtube: YouTubeGoalBody | None = None
    manual: dict[str, dict[str, int]] = Field(default_factory=dict)


class AccountBody(BaseModel):
    fields: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _limit(self):
        if len(self.fields) > 10 or any(len(k) > 40 or len(v) > 4000 for k, v in self.fields.items()):
            raise ValueError("dados da conta grandes demais")
        return self


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
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    jobs = _Jobs()
    projects_dir = Path(settings.paths.projects_dir)
    data_dir = Path(settings.paths.data_dir)
    yt_profile = data_dir / "session-profile"
    yt_cookies = data_dir / "session-cookies.txt"
    yt_status = data_dir / "session-status.json"
    publish_store = PublishStore(data_dir)
    goals_store = GoalsStore(data_dir)
    n8n_proc: dict = {"p": None}

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
        s.download.section_start, s.download.section_end = req.section_start, req.section_end

        if req.provider:
            s.llm.provider = req.provider
        if req.min_seconds is not None:
            s.analysis = AnalysisSettings.model_validate({
                **s.analysis.model_dump(), "min_seconds": req.min_seconds, "max_seconds": req.max_seconds,
                "profile": req.profile or ("monetizacao" if req.min_seconds >= 61 else "viral")})
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
            if req.kind == "posts":
                project = Project.load(_project_dir(req.project))
                job.update(stage="posts", project=project.dir.name, message="IA escrevendo os textos")
                ids = req.ids or [m["id"] for m in (load_moments(project) or {}).get("moments", [])]
                job["result"] = generate_posts(project, ids, s)
            elif req.kind == "export":
                project = Project.load(_project_dir(req.project))
                job.update(stage="export", project=project.dir.name)
                missing = [m["id"] for m in approved_moments(project) if not m.get("meta")]
                if missing:  # sem textos ainda: a IA escreve antes, para os .txt já saírem com o melhor título
                    job.update(stage="posts", message=f"IA escrevendo os textos de {len(missing)} clipe(s)")
                    generate_posts(project, missing, s)
                res = export_approved(project, s, on_progress=lambda f, m: progress("export", f, m))
                job["result"] = {"folder": str(res["folder"]), "duplicates": res["duplicates"],
                                 "unauthorized": res["unauthorized"], "errors": res["errors"],
                                 "files": [{"id": f["id"], "mp4": str(f["mp4"]), "dir": str(f["dir"])}
                                           for f in res["files"]]}
                cfg = publish_store.load()
                if cfg.auto_send and cfg.enabled() and res["files"] and req.callback_url:
                    try:  # envio automático para postar: falhar aqui não desfaz a exportação
                        job["result"]["publish"] = publish_clips(
                            project, s, publish_store, [f["id"] for f in res["files"]], req.callback_url,
                            on_progress=lambda f, m: progress("publish", f, m))
                    except PublishError as e:
                        job["result"]["publish"] = {"sent": 0, "errors": [{"id": "-", "error": str(e)}]}
                    job["stage"] = "export"
            elif req.kind == "publish":
                project = Project.load(_project_dir(req.project))
                job.update(stage="publish", project=project.dir.name)
                job["result"] = publish_clips(project, s, publish_store, req.ids, req.callback_url or "",
                                              force=req.force, on_progress=lambda f, m: progress("publish", f, m))
            elif req.kind == "render":
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
        except (IngestError, FFmpegError, FileNotFoundError, PublishError) as e:
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
    def create_job(req: JobRequest, request: Request):
        req.callback_url = str(request.base_url) + "api/publish/callback"  # nunca vem do cliente
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

    @app.get("/api/projects/{name}/moments/{moment_id}/preview")
    def get_preview_spec(name: str, moment_id: str):
        """Layout, legenda já agrupada, gancho e crédito para a prévia ao vivo (desenhada no navegador)."""
        from corteviral.pipeline.render import preview_spec
        project = Project.load(_project_dir(name))
        data = load_moments(project) or {}
        m = next((x for x in data.get("moments", []) + data.get("all", []) if x["id"] == moment_id), None)
        if not m:
            raise HTTPException(404, "momento não encontrado")
        return preview_spec(project, m, settings)

    @app.get("/api/projects/{name}/export-check")
    def get_export_check(name: str):
        return export_check(Project.load(_project_dir(name)), settings)

    @app.post("/api/projects/{name}/open-exports")
    def open_exports(name: str):
        d = Path(settings.paths.exports_dir) / _project_dir(name).name
        d.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(d)  # noqa: S606 — abre a pasta de exportação no Explorer
        return {"ok": True, "folder": str(d)}

    # --- contas conectadas (postagem pelo n8n) ---------------------------
    def _n8n_state() -> dict:
        cfg = publish_store.load()
        base = cfg.webhook_url.split("/webhook")[0]
        local = base.startswith(("http://127.0.0.1:5678", "http://localhost:5678"))
        return {"installed": n8n_local.n8n_bin(settings.paths.n8n_dir) is not None, "local": local,
                "online": n8n_local.online(base), "editor_url": base}

    def _public_cfg() -> dict:
        return publish_store.load().model_dump(exclude={"token"})

    @app.get("/api/goals")
    def get_goals(refresh: bool = False):
        """Painel inicial: metas de monetização de cada rede e meta diária de clipes."""
        cfg = goals_store.load()
        yt = goals_store.youtube(refresh=refresh)
        return {**goals_store.public(), "youtube": {**goals_store.public()["youtube"], **yt},
                "programs": program_progress(yt.get("stats"), cfg["manual"]),
                "daily": daily_counts(data_dir, cfg["daily_videos"])}

    @app.put("/api/goals")
    def put_goals(body: GoalsBody):
        patch = body.model_dump(exclude_none=True)
        if body.youtube and body.youtube.channel:
            try:
                parse_channel(body.youtube.channel)
            except GoalsError as e:
                raise HTTPException(422, str(e))
        goals_store.save(patch)
        return goals_store.public()

    @app.get("/api/publish")
    def get_publish():
        return {"config": _public_cfg(), "n8n": _n8n_state(), "recent": publish_store.recent(),
                "networks": {k: v[0] for k, v in NETWORKS.items()}}

    @app.put("/api/publish/config")
    def put_publish_config(body: PublishConfigBody):
        try:
            cfg = PublishConfig.model_validate(body.model_dump())
        except ValueError as e:
            raise HTTPException(422, str(e).splitlines()[-1])
        publish_store.save(cfg)
        return {"config": _public_cfg()}

    @app.post("/api/publish/test")
    def post_publish_test():
        try:
            cfg = publish_store.load()
            return {"ok": True, "reply": test_connection(cfg.webhook_url, cfg.token)}
        except PublishError as e:
            return {"ok": False, "error": str(e)}

    @app.post("/api/publish/account/{network}")
    def post_publish_account(network: str, body: AccountBody):
        try:
            cfg = publish_store.load()
            return setup_account(cfg.webhook_url, network, body.fields, cfg.token)
        except PublishError as e:
            raise HTTPException(422, str(e))

    @app.post("/api/publish/callback")
    def post_publish_callback(body: dict):
        try:
            return {"ok": True, **apply_callback(publish_store, body)}
        except PublishError as e:
            raise HTTPException(403 if "token" in str(e) else 422, str(e))

    @app.get("/api/projects/{name}/publish")
    def get_project_publish(name: str):
        return publish_store.status(_project_dir(name).name)

    @app.post("/api/n8n/start")
    def post_n8n_start():
        state = _n8n_state()
        if state["online"]:
            return {"ok": True, "already": True}
        if not state["installed"]:
            raise HTTPException(409, "o n8n ainda não foi instalado neste PC (veja o README)")
        p = n8n_proc["p"]
        if p is None or p.poll() is not None:
            try:  # primeira vez (ou fluxo atualizado): importa e publica antes de ligar
                n8n_local.ensure_workflow(settings.paths.n8n_dir, data_dir, publish_store.load().token)
            except (RuntimeError, OSError, ValueError) as e:
                raise HTTPException(500, f"não consegui preparar o fluxo no n8n: {e}")
            n8n_proc["p"] = n8n_local.start(settings.paths.n8n_dir, data_dir,
                                            (projects_dir, Path(settings.paths.exports_dir)))
        return {"ok": True, "starting": True}

    @app.post("/api/n8n/open")
    def post_n8n_open():
        url = _n8n_state()["editor_url"]
        if os.name == "nt":
            os.startfile(url)  # noqa: S606 — abre o editor do n8n no navegador padrão
        return {"ok": True, "url": url}

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
