# Interface Redesenhada no Estilo Apple: Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** substituir a página única por um app com barra lateral + etapas, seguindo os princípios da
skill apple-design, mantendo 100 % das funções e sem tocar em dados nem na API.

**Architecture:** `static/index.html` (casca) + `static/css/app.css` (tokens claro/escuro, materiais,
componentes) + `static/js/` em módulos ES: `api.js`, `ui.js` (sheet, toast, pílula de progresso,
helpers), `jobs.js`, `router.js`, `sidebar.js`, `views/video.js`, `views/moments.js`, `views/clips.js`,
`views/export.js`, `sheets/newvideo.js`, `sheets/settings.js`, `sheets/layout.js`, `app.js`. FastAPI
monta `/static`.

**Spec:** `docs/superpowers/specs/2026-11-11-corte-viral-design.md` seção 14 (e 9).

## Global Constraints
- Nenhuma rota da API muda; nenhum arquivo em `projects/`, `data/`, `exports/`, `presets/` é alterado.
- Sem dependências externas (offline): CSS e JS próprios; animações com CSS/Web Animations.
- Fonte: `"Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI", system-ui`.
- Todo texto vindo de API/IA/usuário passa por `esc()` ou `textContent`.
- Tema automático (`prefers-color-scheme`) + reduced motion/transparency/contrast.

## Inventário (nada pode sumir)
Novo vídeo (link/arquivo 📁, modelo, idioma, contas conectadas + outras formas de login/cookies.txt) ·
progresso/erros de tarefa · lista de projetos · player + transcrição sincronizada + busca + abrir pasta ·
duração (presets/personalizado), quantidade, IA, Encontrar/Reavaliar (confirmação) · nota, critérios,
descrição/motivo/risco/gancho, 👍/👎, selo IA · tocar trecho até o fim · gerar clipe / ver clipe /
gerar os N melhores · legenda por clipe + editar/voltar ao original · barra rápida de legenda ·
aprovar/descartar/filtro · corte (±1 s, = player, salvar) · gancho + "no vídeo" · textos por rede
(abas, copiar, salvar, gerar com IA, confirmação ao substituir) · layout do vertical (quadro, caixas
arrastáveis, câmeras, layout, streamer, crédito, autorização, prévia) · estilo da legenda (campos +
prévia) · uso da IA (%, medidor, gráfico por minuto com dica, tabela) · exportar (checagens,
resultado, abrir pasta) · modal do clipe.

## Tasks
### Task 1: Base — servir `/static`, tokens/CSS, casca, roteador, barra lateral, pílula de progresso
- [ ] Teste: `test_static_assets_served` (GET /static/css/app.css e /static/js/app.js = 200, tipo certo); `test_js_modules_parse` (node --check em cada .js, se node existir).
- [ ] Implementar; screenshot headless do Edge (claro e escuro).
- [ ] Commit `feat(ui): base do novo design (barra lateral, etapas, tokens claro/escuro)`.
### Task 2: Etapas ① Vídeo e ② Momentos + sheet Novo vídeo
- [ ] Implementar; screenshots; commit `feat(ui): etapas Vídeo e Momentos e novo vídeo`.
### Task 3: Etapa ③ Clipes + sheet Layout do vertical + modal do clipe
- [ ] Implementar; screenshots; commit `feat(ui): etapa Clipes com revisão, textos, legenda e layout`.
### Task 4: Etapa ④ Exportar + sheet Ajustes (contas, estilo da legenda, uso da IA)
- [ ] Implementar; screenshots; commit `feat(ui): exportar e ajustes`.
### Task 5: Acessibilidade, movimento reduzido, revisão final
- [ ] Auditoria de acessibilidade (contraste, teclado, foco), revisão de código, correções, push.
