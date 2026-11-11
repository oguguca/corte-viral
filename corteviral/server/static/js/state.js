// Estado compartilhado entre as telas (cache leve; a fonte da verdade é sempre a API).
import { api } from "./api.js";

export const state = {
  projects: [],
  current: null,        // nome da pasta do projeto aberto
  capStyle: { enabled: true, hook: true, position: 0.70 },
  capFonts: [],
  selected: null,       // momento selecionado na etapa Clipes
};

export async function loadCapStyle() {
  try { const r = await api("/api/caption-style"); state.capStyle = r.style; state.capFonts = r.fonts; } catch (e) { /* padrão */ }
  return state.capStyle;
}

export async function saveCapStyle(change) {
  const r = await api("/api/caption-style", { method: "PUT", body: { ...state.capStyle, ...change } });
  state.capStyle = r.style;
  return r.style;
}
