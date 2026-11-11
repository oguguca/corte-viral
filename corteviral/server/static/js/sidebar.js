// Barra lateral: projetos com status e o medidor de uso da IA.
import { $, api, esc, fmt } from "./api.js";
import { go } from "./router.js";
import { state } from "./state.js";

export async function renderProjects() {
  try { state.projects = await api("/api/projects"); } catch (e) { state.projects = []; }
  const ul = $("#projects");
  if (!state.projects.length) {
    ul.innerHTML = '<li class="t-small" style="padding:8px 10px">Nenhum projeto ainda. Use “Novo vídeo”.</li>';
    return;
  }
  ul.innerHTML = state.projects.map((p) => {
    const status = p.has_moments ? "momentos prontos" : p.has_transcript ? "transcrito" : "processando";
    return `<li><button class="side-item" data-name="${esc(p.name)}" ${p.name === state.current ? 'aria-current="page"' : ""}>
      <span class="thumb" aria-hidden="true">${p.has_moments ? "★" : p.has_transcript ? "▶" : "…"}</span>
      <span class="meta"><span class="name">${esc(p.title || p.name)}</span>
        <span class="sub">${fmt(p.duration)} · ${status}</span></span></button></li>`;
  }).join("");
  ul.querySelectorAll("[data-name]").forEach((b) => b.onclick = () => go(b.dataset.name, "video"));
}

export async function renderUsageMini() {
  try {
    const u = await api("/api/usage");
    const pct = u.percent;
    const ring = $("#usageRing");
    ring.style.setProperty("--p", Math.min(100, pct));
    ring.style.setProperty("--ring-color", u.rate_limited || pct >= 90 ? "var(--bad)" : pct >= 70 ? "var(--warn)" : "var(--good)");
    $("#usagePct").textContent = (pct < 10 ? pct.toFixed(1) : Math.round(pct)) + "% do limite";
  } catch (e) { /* sem uso */ }
}
