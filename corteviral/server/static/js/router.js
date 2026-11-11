// Rotas por hash: #/  ·  #/p/<projeto>/<etapa>. Voltar/avançar do navegador funcionam.
export const STEPS = [
  ["video", "Vídeo"], ["moments", "Momentos"], ["clips", "Clipes"], ["export", "Exportar"],
];

export function parse() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  if (parts[0] === "p" && parts[1]) {
    const step = STEPS.some(([k]) => k === parts[2]) ? parts[2] : "video";
    return { view: "project", name: parts[1], step };
  }
  return { view: "home" };
}

export const go = (name, step = "video") => { location.hash = `#/p/${encodeURIComponent(name)}/${step}`; };
export const goHome = () => { location.hash = "#/"; };
