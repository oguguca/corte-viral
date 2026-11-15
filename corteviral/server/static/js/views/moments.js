// Peças da análise de momentos usadas na tela Clipes (durações, selo da IA, barras de critério).

export const DURATIONS = [["20-35", "20–35 s · ideal (YouTube)"], ["15-30", "15–30 s · curto"], ["15-60", "15–60 s · viral"], ["30-60", "30–60 s"],
                   ["61-90", "61–90 s · monetização"], ["61-120", "61–120 s · monetização"], ["custom", "Personalizado…"]];
const CRIT = [["gancho", "Gancho"], ["emocao", "Emoção"], ["visual", "Visual"], ["final", "Final"], ["contexto", "Contexto"]];

export const aiTag = (m) => m.ai === "video" ? '<span class="badge info">👁 IA assistiu</span>'
  : m.ai ? '<span class="badge">IA (texto)</span>' : '<span class="badge">sinais</span>';

export function critBars(m) {
  if (!m.criteria) return "";
  return `<div class="crit">${CRIT.map(([k, l]) => `<div><div class="lbl">${l}<b class="num">${m.criteria[k] ?? "–"}</b></div>
    <div class="bar"><i style="width:${Math.max(0, Math.min(100, m.criteria[k] || 0))}%"></i></div></div>`).join("")}</div>`;
}
