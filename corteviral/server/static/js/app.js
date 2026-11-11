// Ponto de entrada: barra lateral, etapas do projeto e rotas.
import { $, api, esc, fmt } from "./api.js";
import { onJobDone, resumeRunning } from "./jobs.js";
import { STEPS, go, parse } from "./router.js";
import { openNewVideo } from "./sheets/newvideo.js";
import { openSettings } from "./sheets/settings.js";
import { renderProjects, renderUsageMini } from "./sidebar.js";
import { loadCapStyle, state } from "./state.js";
import { renderClips } from "./views/clips.js";
import { renderExport } from "./views/export.js";
import { renderMoments } from "./views/moments.js";
import { renderVideo } from "./views/video.js";

const VIEWS = { video: renderVideo, moments: renderMoments, clips: renderClips, export: renderExport };
let renderToken = 0;

function stepsBar(name, step, project) {
  const done = { video: project.has_transcript, moments: project.has_moments, clips: false, export: false };
  return `<nav class="steps" aria-label="Etapas do projeto">${STEPS.map(([k, label], i) => `
    ${i ? '<span class="step-sep" aria-hidden="true">›</span>' : ""}
    <button class="step ${done[k] && k !== step ? "done" : ""}" data-step="${k}" ${k === step ? 'aria-current="step"' : ""}>
      <span class="n">${done[k] && k !== step ? "✓" : i + 1}</span>${label}</button>`).join("")}</nav>`;
}

function homeView() {
  return `<div class="page"><div class="hero">
    <div class="brand-mark" style="width:72px;height:72px;font-size:28px;border-radius:20px">CV</div>
    <h1 class="t-display">Transforme lives em cortes virais</h1>
    <p>Cole o link de uma live ou VOD (YouTube, Twitch, Kick) ou escolha um arquivo. O Corte Viral encontra os melhores
      momentos e entrega os clipes verticais prontos para postar.</p>
    <button class="btn primary large" id="homeNew">＋ Novo vídeo</button>
  </div></div>`;
}

async function render() {
  const token = ++renderToken;
  const route = parse();
  const main = $("#main");
  state.current = route.view === "project" ? route.name : null;
  await renderProjects();
  if (token !== renderToken) return;
  if (route.view === "home") {
    main.innerHTML = homeView();
    $("#homeNew").onclick = (e) => openNewVideo(e.currentTarget);
    return;
  }
  const project = state.projects.find((p) => p.name === route.name);
  if (!project) { main.innerHTML = '<div class="page"><div class="empty"><div class="big">🔍</div>Projeto não encontrado.</div></div>'; return; }
  main.innerHTML = stepsBar(route.name, route.step, project) + '<div class="page" id="page"></div>';
  main.querySelectorAll("[data-step]").forEach((b) => b.onclick = () => go(route.name, b.dataset.step));
  const page = $("#page");
  page.innerHTML = `<header class="page-head"><div>
      <h1 class="t-display">${esc(project.title || project.name)}</h1>
      <div class="sub">${fmt(project.duration)} de vídeo${project.has_moments ? " · momentos prontos" : ""}</div></div></header>
    <div id="stepBody"></div>`;
  main.scrollTop = 0;
  await VIEWS[route.step]($("#stepBody"), route.name, project);
}

function init() {
  $("#newVideo").onclick = (e) => openNewVideo(e.currentTarget);
  $("#openSettings").onclick = (e) => openSettings(e.currentTarget);
  $("#usageMini").onclick = (e) => openSettings(e.currentTarget, "usage");
  addEventListener("hashchange", render);
  onJobDone(async (job) => {
    renderUsageMini();
    if (job.status !== "done") return;
    if (job.stage === "transcribe" || job.stage === "ingest") { await renderProjects(); if (job.project) go(job.project, "video"); return; }
    if (job.project === state.current) render();
  });
  loadCapStyle();
  renderUsageMini();
  setInterval(renderUsageMini, 20000);
  render();
  resumeRunning();
}

init();
