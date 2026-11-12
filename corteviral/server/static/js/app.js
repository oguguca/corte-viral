// Ponto de entrada: barra lateral, etapas do projeto e rotas.
import { $, api, esc, fmt } from "./api.js";
import { onJobDone, resumeRunning } from "./jobs.js";
import { STEPS, go, parse } from "./router.js";
import { openNewVideo } from "./sheets/newvideo.js";
import { openSettings } from "./sheets/settings.js";
import { renderProjects, renderUsageMini } from "./sidebar.js";
import { loadCapStyle, state } from "./state.js";
import { applyTheme, themeSwitcher } from "./theme.js";
import { toast } from "./ui.js";
import { renderAccounts } from "./views/accounts.js";
import { renderClips } from "./views/clips.js";
import { renderExport } from "./views/export.js";
import { renderHome } from "./views/home.js";
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

async function render({ keepFocus = false } = {}) {
  const token = ++renderToken;
  const route = parse();
  const main = $("#main");
  state.current = route.view === "project" ? route.name : null;
  await renderProjects();
  if (token !== renderToken) return;
  if (route.view === "accounts") $("#openAccounts").setAttribute("aria-current", "page");
  else $("#openAccounts").removeAttribute("aria-current");
  if (route.view === "accounts") {
    main.innerHTML = '<div class="page" id="page"></div>';
    if (!keepFocus) main.scrollTop = 0;
    await renderAccounts($("#page"));
    if (!keepFocus) { const h1 = $("#page h1"); h1?.setAttribute("tabindex", "-1"); h1?.focus({ preventScroll: true }); }
    return;
  }
  if (route.view === "home") $("#brandHome").setAttribute("aria-current", "page");
  else $("#brandHome").removeAttribute("aria-current");
  if (route.view === "home") {
    main.innerHTML = '<div class="page" id="page"></div>';
    if (!keepFocus) main.scrollTop = 0;
    await renderHome($("#page"));
    if (!keepFocus) { const h1 = $("#page h1"); h1?.setAttribute("tabindex", "-1"); h1?.focus({ preventScroll: true }); }
    return;
  }
  if (route.view === "bad") { main.innerHTML = '<div class="page"><div class="empty"><div class="big">🔍</div>Endereço inválido.</div></div>'; return; }
  const project = state.projects.find((p) => p.name === route.name);
  if (!project) { main.innerHTML = '<div class="page"><div class="empty"><div class="big">🔍</div>Projeto não encontrado.</div></div>'; return; }
  main.innerHTML = stepsBar(route.name, route.step, project) + '<div class="page" id="page"></div>';
  main.querySelectorAll("[data-step]").forEach((b) => b.onclick = () => go(route.name, b.dataset.step));
  const page = $("#page");
  page.innerHTML = `<header class="page-head"><div>
      <h1 class="t-display">${esc(project.title || project.name)}</h1>
      <div class="sub">${fmt(project.duration)} de vídeo${project.has_moments ? " · momentos prontos" : ""}</div></div></header>
    <div id="stepBody"></div>`;
  if (!keepFocus) main.scrollTop = 0;
  await VIEWS[route.step]($("#stepBody"), route.name, project, { rerender: () => render({ keepFocus: true }) });
  if (!keepFocus) {  // orientação: o foco vai para o título da tela nova (o botão clicado deixou de existir)
    const h1 = $("#page h1");
    h1?.setAttribute("tabindex", "-1");
    h1?.focus({ preventScroll: true });
  }
}

function init() {
  applyTheme();
  $("#themeSlot").append(themeSwitcher());
  $("#newVideo").onclick = (e) => openNewVideo(e.currentTarget);
  $("#openSettings").onclick = (e) => openSettings(e.currentTarget);
  $("#usageMini").onclick = (e) => openSettings(e.currentTarget, "usage");
  addEventListener("hashchange", () => render());
  document.addEventListener("capstylechange", () => { if (parse().step === "clips") render({ keepFocus: true }); });
  onJobDone(async (job) => {
    renderUsageMini();
    if (job.status !== "done") return;
    if (job.stage === "export" && job.project) state.exportResults[job.project] = job;  // a tela mostra depois de redesenhar
    if (job.stage === "publish" && job.result) {
      const r = job.result, errs = r.errors || [];
      toast(`${r.sent} clipe(s) entregues ao n8n${r.skipped?.length ? ` · ${r.skipped.length} já publicados` : ""}${
        errs.length ? ` · ${errs.length} falharam: ${errs[0].error}` : ""}`, errs.length ? "bad" : "good");
    }
    if (job.stage === "transcribe" || job.stage === "ingest") {
      await renderProjects();
      const r = parse();
      if (r.view === "project" && r.name === job.project && r.step === "video") render();  // mesma rota: hashchange não dispara
      else if (job.project) go(job.project, "video");
      return;
    }
    if (job.project === state.current) render({ keepFocus: true });
  });
  loadCapStyle();
  renderUsageMini();
  setInterval(renderUsageMini, 20000);
  render();
  resumeRunning();
}

init();
