// Faixa da live ao vivo na tela Clipes: status, Parar/Retomar e os clipes novos aparecendo sozinhos.
import { $, api, esc } from "../api.js";
import { startJob } from "../jobs.js";
import { confirmAction, toast } from "../ui.js";

const hms = (s) => { s = Math.max(0, Math.round(s || 0)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return `${h}:${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`; };
let timer = null;

export async function mountLiveBar(root, name, project, rerender) {
  clearInterval(timer);
  if (!project.live && !project.live_ended) return;
  const bar = document.createElement("section");
  bar.className = "card pad live-bar";
  bar.setAttribute("aria-live", "polite");
  root.prepend(bar);
  let lastClips = null;

  const paint = async () => {
    if (!bar.isConnected) { clearInterval(timer); return; }
    let s;
    try { s = await api("/api/live"); } catch (e) { return; }
    const mine = s.project === name;
    if (s.active && mine) {
      const next = s.next_in == null ? "" : s.next_in > 30 ? ` · próxima análise em ${Math.ceil(s.next_in / 60)} min` : " · analisando…";
      bar.innerHTML = `<div class="row"><span class="live-dot" aria-hidden="true"></span>
        <b>Ao vivo</b><span class="num">· gravando ${hms(s.recorded)} · ${s.clips} ${s.clips === 1 ? "clipe" : "clipes"}${esc(next)}</span>
        ${s.pending_ai ? `<span class="badge warn" title="A IA atingiu o limite; esses trechos serão avaliados de novo">${s.pending_ai} esperando a IA</span>` : ""}
        <span class="spacer"></span>${s.stopping ? '<span class="t-small">encerrando…</span>' : '<button class="btn" id="liveStop">■ Parar</button>'}</div>`;
      $("#liveStop", bar)?.addEventListener("click", async () => {
        if (!confirmAction("Parar de acompanhar a live? Os clipes já feitos ficam, e o vídeo gravado vira um arquivo só.")) return;
        try { await api("/api/live/stop", { method: "POST" }); toast("Encerrando a live…", "good"); paint(); } catch (e) { toast(e.message, "bad"); }
      });
      if (lastClips !== null && s.clips > lastClips) { toast(`Novo clipe da live pronto (${s.clips} no total).`, "good"); rerender(); }
      lastClips = s.clips;
    } else if (s.can_resume && mine) {
      bar.innerHTML = `<div class="row"><b>Live interrompida</b><span class="t-small">o app fechou enquanto acompanhava esta live.</span>
        <span class="spacer"></span><button class="btn primary" id="liveResume">Retomar</button></div>`;
      $("#liveResume", bar).onclick = async () => {
        try { await api(`/api/live/resume?project=${encodeURIComponent(name)}`, { method: "POST" }); toast("Live retomada.", "good"); paint(); }
        catch (e) { toast(e.message, "bad"); }
      };
    } else {
      clearInterval(timer);
      bar.innerHTML = `<div class="row"><b>Live encerrada</b><span class="t-small">o vídeo completo foi gravado.</span>
        <span class="spacer"></span><button class="btn" id="liveFull" title="Análise normal sobre o vídeo inteiro; mantém os clipes que você já tem">Analisar a live inteira</button></div>`;
      $("#liveFull", bar).onclick = () => startJob({ kind: "analyze", project: name, merge: true, auto: true });
    }
  };
  await paint();
  timer = setInterval(paint, 20000);
}
