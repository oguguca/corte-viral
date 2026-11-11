// Acesso à API local e utilitários de texto (tudo que vem de fora passa por esc()).

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export const fmt = (s) => {
  s = Math.max(0, Math.floor(Number(s) || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(sec).padStart(2, "0");
};

export const nf = new Intl.NumberFormat("pt-BR");
export const enc = encodeURIComponent;

export async function api(path, opts = {}) {
  const init = { headers: { "Content-Type": "application/json" }, ...opts };
  if (init.body && typeof init.body !== "string") init.body = JSON.stringify(init.body);
  const r = await fetch(path, init);
  const body = await r.json().catch(() => null);
  if (!r.ok) {
    const d = body?.detail;
    const msg = Array.isArray(d) ? d.map((x) => x.msg?.replace(/^Value error, /, "")).join("; ") : d;
    throw new Error(msg || r.statusText);
  }
  return body;
}

export const projectApi = (name) => (path) => `/api/projects/${enc(name)}${path}`;
