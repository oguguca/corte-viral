# Corte Viral — Especificação de Design

**Data:** 2026-11-11
**Status:** aprovado

## 1. Objetivo

Aplicativo desktop para Windows que transforma vídeos longos (lives e VODs de
streamers, foco em GTA 6, idioma principal PT-BR com suporte a inglês) em clipes
verticais 9:16 prontos para TikTok, YouTube Shorts, Instagram Reels e Kwai.

Uso previsto: pessoal / pequena equipe de cortes (não é produto distribuído).

**Fora do escopo:** postagem automática, contas de usuário, pagamento, versão web.

## 2. Ambiente-alvo

| Recurso | Valor |
|---|---|
| SO | Windows 10/11 |
| GPU | NVIDIA GTX 1050 Ti, 4 GB VRAM (CUDA + NVENC) |
| RAM | 8 GB |

Essas restrições motivam: transcrição local com modelo `medium` int8 (cabe em
4 GB), análise semântica via API na nuvem (LLM local inviável), encode por NVENC.

Medição real (VOD de 27 min, `medium`, GTX 1050 Ti): ~7 min ponta a ponta
(≈ 15 min por hora de vídeo).

## 3. Decisões

| Tema | Decisão | Motivo |
|---|---|---|
| Processamento | Híbrido: transcrição local, LLM via API | Custo zero de transcrição; qualidade alta de análise |
| LLM | **Plugável** (`llm.provider`): `none` (só sinais locais), `gemini` (padrão — `gemini-3.5-flash-lite`, plano gratuito), `anthropic` (quando houver crédito) | Usuário sem orçamento para API paga (2026-11-11); o pipeline funciona inteiro sem IA |
| Interface | Electron + React (front) + Python FastAPI (backend local) | Visual moderno + player HTML5; Node já instalado |
| Facecam | Detecção automática (MediaPipe) + ajuste manual + preset por streamer | Robusto a overlays; automático a partir do 2º VOD |
| Legendas | ASS (libass) queimadas pelo FFmpeg | Destaque palavra a palavra sem renderizador extra |
| Legenda/gancho | **Opcionais**: chave global, por tarefa e por clipe | Pedido do usuário (estilo "limpo" também suportado) |
| Encode | `h264_nvenc`, fallback `libx264` | Render rápido na 1050 Ti |
| Meta de desempenho | VOD de 3h em ~60–80 min ponta a ponta | Transcrição domina (~45–60 min com `medium`) |
| Duração dos clipes | Perfis: `viral` 15–60 s, `monetizacao` 61–120 s (padrão configurável) | Creator Rewards do TikTok só paga vídeos > 1 min |
| Objetivo de negócio | Monetização nas plataformas | Ver seção 12 |

## 4. Arquitetura

```
Electron (React UI)
   │  HTTP (comandos) + WebSocket (progresso) — 127.0.0.1, porta aleatória
   ▼
Backend Python (FastAPI)  ── chama as mesmas funções expostas pela CLI
   └── corteviral/pipeline/
        1 ingest       yt-dlp (YouTube/Twitch/Kick) ou arquivo local → vídeo + audio.wav (16 kHz mono)
        2 transcribe   faster-whisper (CUDA int8; fallback CPU) → palavras com timestamp
        3 chat         chat-downloader / yt-dlp → mensagens com timestamp (quando disponível)
        4 signals      curvas por segundo: energia de áudio, taxa de chat, emotes de risada
        5 scoring      candidatos → Claude Haiku (nota 0–100 + justificativa) → Sonnet refina top-N
        6 boundaries   ajusta início/fim em pausas/fim de frase; duração 15–60 s
        7 layout       detecção de facecam + preset do streamer → filtro FFmpeg 9:16
        8 captions     gera .ass animado (opcional)
        9 metadata     gancho, título, descrição, hashtags (Claude)
       10 export       FFmpeg NVENC → MP4 1080x1920
```

Electron só é introduzido na Fase 5. As fases 1–4 são usadas e testadas via CLI:
`python -m corteviral <comando>`.

### 4.1 Armazenamento por projeto

`projects/<slug>-<data>/` (ignorado pelo git):

```
source.mp4   audio.wav   chat.json
transcript.json   # {language, words:[{w,start,end,p}], segments:[...]}
signals.json      # {audio:[...], chat:[...], laugh:[...]}  (1 valor/segundo)
moments.json      # [{id,start,end,score,score_parts,reason,hook,title,description,hashtags,status,captions_on}]
captions/<id>.ass
clips/<id>.mp4
project.json      # origem, duração, etapas concluídas, hash de parâmetros por etapa
```

Cache por etapa: cada etapa grava sua saída e o hash dos parâmetros que a
influenciam. Se o hash não mudou, a etapa é pulada; se mudou, ela e as seguintes
rodam de novo.

### 4.2 Configuração

- `config.yaml` — todos os parâmetros (modelo whisper, pesos, durações, layout,
  estilo de legenda, N de clipes etc.). `config.example.yaml` é versionado.
- `.env` — `ANTHROPIC_API_KEY` (não versionado; `.env.example` versionado).
- `presets/streamers/<nome>.json` — retângulo da facecam, offset da gameplay,
  layout preferido.
- `presets/styles/<nome>.yaml` — estilo de legenda/gancho.

## 5. Detecção e pontuação de momentos (Fase 2)

1. **Sinais por segundo**
   - Áudio: loudness (RMS em dB) → z-score sobre mediana móvel de 60 s.
   - Chat: mensagens/s → z-score; contagem de emotes/termos de risada
     (`KEKW`, `LUL`, `kkkk`, `HAHA`, `OMEGALUL`, configurável).
2. **Candidatos:** picos acima do limiar, agrupados (distância mínima 20 s),
   janela inicial de [pico − 25 s, pico + 15 s]. Complementado por janela
   deslizante de texto (a cada 45 s) para não perder momentos "falados" sem pico.
   Alvo: 40–80 candidatos por VOD de 3h.
3. **Triagem (Haiku):** em lotes, cada candidato com seu texto. Retorno JSON
   validado por Pydantic: `{score 0-100, reason (≤140 chars), categories[],
   standalone: bool, suggested_start, suggested_end}`.
4. **Nota final:** `0.60·llm + 0.25·áudio + 0.15·chat` (pesos no config; sem chat,
   o peso é redistribuído proporcionalmente).
5. **Refino (Sonnet):** top `N × 2` candidatos → confirma/ajusta, remove
   duplicatas, escolhe top N.
6. **Fronteiras naturais:** início snap para o início da frase/pausa ≥ 300 ms
   mais próxima antes do ponto; fim snap para fim de frase/pausa depois do
   ponto; respeita a faixa de duração do perfil ativo (`viral` 15–60 s ou
   `monetizacao` 61–120 s). No perfil `monetizacao`, o prompt do LLM prioriza
   arcos completos (setup → clímax → reação), e não só o pico.

## 5.1 Avaliação em duas etapas (Fase 2.5 — pedido do usuário em 2026-11-11)

Problemas relatados: trechos fracos escolhidos, corte começando/terminando mal, justificativa rasa.

1. **Triagem (texto):** igual à seção 5; mantém os `deep.top_k` (padrão 15) melhores.
2. **A IA assiste:** para cada um, mini-vídeo 360p, 1 qps, áudio mono (janela do momento ± 15 s,
   ~1,3 MB) enviado inline ao Gemini (`gemini-3.5-flash-lite`, `mediaResolution: LOW`; medido:
   ~6,5 mil tokens e ~7 s por trecho). Retorno estruturado:
   - critérios 0–100: `gancho`, `emocao`, `visual`, `final`, `contexto`;
   - `inicio`/`fim` sugeridos (segundos no mini-vídeo) → convertidos para o VOD e ajustados às
     pausas, dentro da duração do perfil;
   - `descricao` (o que acontece), `motivo` (por que funciona) e `risco`.
   Nota final = média ponderada dos critérios (`deep.weights`, padrão gancho 0,25, emoção 0,25,
   visual 0,15, final 0,20, contexto 0,15).
3. **Aprender com o usuário:** 👍/👎 por momento, salvos em `data/feedback.json` (por streamer +
   global). Até 6 exemplos (3 bons, 3 ruins; prioriza o mesmo streamer) entram no prompt.
4. Falha/cota na etapa 2 → o momento fica com a nota da triagem (selo "texto").

## 6. Formato vertical (Fase 3)

Saída **9:16, 1080x1920**, 30 fps (ou fps de origem se ≤ 60), H.264 + AAC,
`+faststart`. Um único arquivo serve para TikTok, YouTube Shorts, Instagram
Reels e Kwai (mesma proporção).

**Zona segura comum:** os apps cobrem partes da tela com interface (botões à
direita, legenda/perfil embaixo, abas em cima). Legenda e gancho são sempre
posicionados dentro da interseção das zonas seguras das três plataformas
(padrão: margem superior 210 px, inferior 480 px, direita 140 px, esquerda
60 px — configurável em `safe_zone`). A revisão mostra uma sobreposição
opcional dessas áreas na pré-visualização.

**Layout `split` (padrão, baseado no exemplo do usuário):**
- Topo: facecam com `facecam_ratio = 0.30` da altura (576 px). Recorte da região
  da câmera + margem, escala para preencher a largura, corte vertical central.
- Divisória opcional (altura e cor configuráveis; padrão 6 px).
- Base: gameplay ocupando o restante. Recorte central do quadro (excluindo a
  região da facecam quando sobreposta), com `gameplay_offset_x` configurável.

**Layout `face-follow`:** recorte 9:16 que acompanha o rosto (detecção a cada
0,5 s, suavização por média móvel exponencial, zona morta para evitar tremor).

**Layout `fallback`:** gameplay centralizada com fundo do mesmo vídeo desfocado.
Usado automaticamente quando nenhum rosto é detectado.

**Detecção de facecam:** MediaPipe Face Detection em ~30 frames espalhados pelo
VOD; o retângulo de facecam é o bounding box estável (mediana) expandido até as
bordas visíveis da câmera. Proposto ao usuário, editável, salvo como preset do
streamer.

## 7. Legendas animadas (Fase 4) — opcionais

- Ligadas/desligadas em três níveis: `captions.enabled` no config (padrão),
  seletor na nova tarefa, botão por clipe na revisão.
- Agrupamento: 1–3 palavras por vez (configurável), quebrando em pausas.
- Palavra ativa destacada (cor configurável, padrão amarelo `#FFE600`), demais
  em branco, contorno preto grosso, "pop" de escala na entrada (`\t` do ASS).
- Fonte, tamanho, cor, contorno e posição vertical configuráveis por preset de
  estilo; posição padrão: terço inferior da área de gameplay.
- Texto da legenda editável na revisão (edição altera `words` do clipe e
  regenera o .ass).

## 8. Gancho, título e metadados (Fase 5)

Para cada clipe aprovado, Claude Sonnet gera:
- `hook`: texto curto (≤ 8 palavras) exibido nos primeiros 3 s (opcional, mesma
  lógica de liga/desliga das legendas), no topo da área de gameplay.
- `title` (≤ 80 chars), `description` (≤ 300 chars), `hashtags` (5–10, mistura de
  amplas e de nicho, ex.: `#gta6`, `#cortes`, nome do streamer).
Idioma segue o idioma detectado da transcrição.

## 9. Interface (Fase 5)

Telas:
1. **Nova tarefa:** colar link ou escolher arquivo; streamer (preset); N de
   clipes; layout; legendas on/off; gancho on/off.
2. **Progresso:** etapas com barra e ETA (via WebSocket); cancelar.
3. **Ajuste de facecam:** frame do vídeo com retângulo arrastável (só quando não
   há preset ou o usuário pede).
4. **Revisão:** lista de clipes ordenada por nota (nota + justificativa);
   player com pré-visualização; timeline com alças de início/fim; edição de
   legenda, gancho, título, descrição, hashtags; legendas on/off; aprovar /
   descartar; exportar aprovados em lote.
5. **Configurações:** edita `config.yaml` e chave de API.

Pré-visualização: render rápido de baixa resolução (540x960, preset rápido)
gerado sob demanda; export final em 1080x1920.

## 10. Tratamento de erros

| Situação | Comportamento |
|---|---|
| Falha de download / VOD restrito | Mensagem do yt-dlp exibida; tarefa marcada como falha |
| CUDA indisponível / falta de VRAM | Fallback automático para CPU (modelo `small`) com aviso |
| API Claude com erro transitório | Retry com backoff exponencial (3 tentativas) |
| Resposta do LLM inválida | Revalida; após falhas, candidato fica só com nota de sinais, marcado "sem análise de IA" |
| Sem chave de API | Pipeline roda só com sinais; aviso na UI |
| Nenhum rosto detectado | Layout `fallback` |
| NVENC indisponível | Fallback `libx264` |

## 11. Testes

- **Unitários (pytest):** detecção de picos, geração de candidatos, snap de
  fronteiras, fórmula de nota, parsing/validação de JSON do LLM (Claude
  mockado), geração de ASS, montagem do filtro FFmpeg, cache por hash.
- **Integração:** vídeo sintético curto gerado por FFmpeg (`testsrc` + tom de
  áudio) atravessando ingest → render.
- **Manual por fase:** um VOD real do usuário ao fim de cada fase.

## 12. Monetização

Objetivo do usuário: monetizar os clipes. Requisitos derivados:

- **Duração:** perfil `monetizacao` (61–120 s) atende o mínimo do TikTok Creator
  Rewards (> 1 min) e cabe no limite do YouTube Shorts (≤ 3 min). Perfil `viral`
  (15–60 s) para alcance. Regras das plataformas mudam; os limites ficam no config.
- **Conteúdo transformador** (reduz risco de "conteúdo reaproveitado"): no
  perfil `monetizacao`, legendas, gancho, zoom de ênfase em picos (punch-in
  de 1,15x por ~0,6 s, ativável) e crédito ao streamer (texto discreto
  "@streamer", ativável) vêm ligados por padrão — todos desligáveis.
- **Crédito/autorização:** cada preset de streamer tem `credit_handle` e
  `authorized: bool`; a revisão mostra aviso quando o streamer não está marcado
  como autorizado.
- **Sem duplicatas:** `history.json` global registra trechos exportados
  (origem + intervalo); o app avisa ao exportar trecho com sobreposição > 50%
  de um já exportado.
- **Metadados por plataforma:** título/descrição/hashtags gerados com variação
  por plataforma (TikTok, Shorts, Reels, Kwai), exportados num `.txt` ao lado
  de cada MP4.

## 13. Fases

| Fase | Entrega | Instalação necessária |
|---|---|---|
| 1 | Download + transcrição (CLI) | Python 3.11, FFmpeg (winget), venv: `faster-whisper`, `yt-dlp`, libs CUDA via pip |
| 2 | Sinais + pontuação + fronteiras (CLI) | Chave Anthropic no `.env`; `anthropic`, `chat-downloader` |
| 3 | Corte + 9:16 + detecção de facecam (CLI) | `mediapipe`, `opencv-python` |
| 4 | Legendas animadas (CLI) | — (libass vem no FFmpeg) |
| 5 | Metadados + API FastAPI + Electron/React (revisão e export) | `fastapi`, `uvicorn`; `npm install` no app |
| Extra | Detecção/abafamento de música com direitos autorais (opcional) | classificador de áudio leve (YAMNet/PANNs) |

Cada fase termina com testes passando, commit (Conventional Commits, PT-BR, sem
linhas de atribuição) e push para `origin/main`.
