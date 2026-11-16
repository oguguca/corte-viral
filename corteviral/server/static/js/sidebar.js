// Barra lateral: projetos com status e o medidor de uso da IA.
import { $, api, esc, fmt } from "./api.js";
import { go } from "./router.js";
import { state } from "./state.js";

export async function renderProjects() {
  try { state.projects = await api("/api/projects"); } catch (e) { state.projects = []; }
  const ul = $("#projects"), search = $("#projSearch");
  $("#projCount").textContent = state.projects.length ? `· ${state.projects.length}` : "";
  search.hidden = state.projects.length <= 5;  // busca só aparece quando a lista cresce
  search.oninput = () => {
    const q = search.value.trim().toLowerCase();
    ul.querySelectorAll("li").forEach((li) => { li.hidden = !!q && !li.textContent.toLowerCase().includes(q); });
  };
  if (!state.projects.length) {
    ul.innerHTML = '<li class="t-small" style="padding:8px 10px">Nenhum projeto ainda. Use “Novo vídeo”.</li>';
    return;
  }
  ul.innerHTML = state.projects.map((p) => {
    const [kind, status] = p.clips ? ["ok", `${p.clips} clipe${p.clips > 1 ? "s" : ""} pronto${p.clips > 1 ? "s" : ""}`]
      : p.has_moments ? ["mid", "sem clipes ainda"] : p.has_transcript ? ["mid", "transcrito"] : ["wait", "processando"];
    return `<li><button class="side-item" data-name="${esc(p.name)}" ${p.name === state.current ? 'aria-current="page"' : ""}
        title="${esc(p.title || p.name)}">
      <span class="thumb num" aria-hidden="true">${p.clips || (p.has_transcript ? "▶" : "…")}</span>
      <span class="meta"><span class="name">${esc(p.title || p.name)}</span>
        <span class="sub"><span class="dot ${kind}" aria-hidden="true"></span>${fmt(p.duration)} · ${status}</span></span></button></li>`;
  }).join("");
  ul.querySelectorAll("[data-name]").forEach((b) => b.onclick = () => go(b.dataset.name, "clips"));
  if (search.value) search.oninput();  // mantém o filtro depois de redesenhar
}

export async function renderUsageMini() {
  try {
    const u = await api("/api/usage");
    const pctTxt = (p) => (p < 10 ? p.toFixed(1) : Math.round(p)) + "%";
    const color = (p, limited) => limited || p >= 90 ? "var(--bad)" : p >= 70 ? "var(--warn)" : "var(--good)";
    const paint = (bar, label, p, limited, title) => {
      Object.assign($(bar).style, { width: Math.min(100, p) + "%", background: color(p, limited) });
      $(label).textContent = pctTxt(p);
      $(label).title = title;
    };
    paint("#usageBarG", "#usagePct", u.percent, u.rate_limited, `${u.requests} requisições hoje`);
    const g = u.groq || { percent: 0, seconds: 0, rate_limited: false };  // transcrição na nuvem: minutos de áudio
    paint("#usageBarQ", "#usageGroq", g.percent, g.rate_limited, `${Math.round(g.seconds / 60)} min de áudio hoje`);
  } catch (e) { /* sem uso */ }
}
