// Tarefas em segundo plano (download, análise, clipes, textos, exportação) com a pílula de progresso.
import { $, api, fmt } from "./api.js";
import { toast } from "./ui.js";

const STAGES = {
  ingest: "Baixando o vídeo", transcribe: "Transcrevendo", analyze: "Encontrando momentos",
  render: "Gerando clipes", posts: "Escrevendo os textos", export: "Exportando",
};
const listeners = new Set();
let watching = null;

export const onJobDone = (fn) => { listeners.add(fn); return () => listeners.delete(fn); };
export const isBusy = () => watching !== null;

function showPill(job) {
  const pill = $("#pill");
  pill.classList.add("show");
  pill.classList.toggle("error", job.status === "error");
  $("#pillSpin").hidden = job.status !== "running";
  $("#pillClose").hidden = job.status === "running";
  const pct = Math.round((job.progress || 0) * 100);
  $("#pillStage").textContent = job.status === "error" ? "⚠ Não deu certo"
    : job.status === "done" ? "✓ " + (STAGES[job.stage] || "Tarefa") + " — pronto" : STAGES[job.stage] || "Trabalhando";
  $("#pillPct").textContent = job.status === "running" ? pct + "% · " + fmt(Date.now() / 1000 - job.started_at) : "";
  $("#pillMsg").textContent = job.status === "error" ? job.error : job.message || "";
  $("#pillBar").style.width = (job.status === "done" ? 100 : pct) + "%";
}

function hidePill() { $("#pill").classList.remove("show"); }

export async function startJob(body) {
  if (watching) { toast("Já existe uma tarefa em andamento — aguarde ela terminar.", "bad"); return null; }
  try {
    const job = await api("/api/jobs", { method: "POST", body });
    return watch(job.id);
  } catch (e) { toast(e.message, "bad"); return null; }
}

export async function watch(id) {
  watching = id;
  $("#pillClose").onclick = hidePill;
  let job;
  for (;;) {
    try { job = await api("/api/jobs/" + id); } catch (e) { await sleep(1500); continue; }
    showPill(job);
    if (job.status !== "running") break;
    await sleep(700);
  }
  watching = null;
  if (job.status === "done") setTimeout(hidePill, 2600);
  listeners.forEach((fn) => fn(job));
  return job;
}

export async function resumeRunning() {
  try { const j = await api("/api/jobs"); if (j) watch(j.id); } catch (e) { /* sem tarefa */ }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
