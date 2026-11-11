# Corte Viral

Aplicativo desktop para Windows que transforma lives e VODs longos (YouTube,
Twitch, Kick ou arquivo local) em clipes verticais 9:16 prontos para TikTok,
YouTube Shorts, Instagram Reels e Kwai.

> **Status:** Fases 1–3 concluídas (download, transcrição, melhores momentos e clipes verticais 9:16).
> Próxima: Fase 4 — legendas animadas.

Saída: MP4 9:16 (1080x1920), um único arquivo compatível com TikTok, YouTube
Shorts, Instagram Reels e Kwai, com legenda e gancho dentro da zona segura comum.

## Como funciona

1. Cole um link ou escolha um arquivo local.
2. O app baixa o vídeo e transcreve o áudio com marcação de tempo por palavra
   (faster-whisper, local na GPU).
3. Detecta os melhores momentos combinando picos de áudio, picos de chat e
   análise da transcrição por IA (Claude).
4. Cada momento recebe nota de 0 a 100 e uma justificativa.
5. Gera os N melhores clipes — perfil `viral` (15–60 s) ou `monetizacao`
   (61–120 s, atende o mínimo de 1 min do TikTok Creator Rewards) — com início e
   fim em pontos naturais da fala.
6. Converte para 9:16 (facecam em cima + gameplay embaixo, ou recorte seguindo o rosto).
7. Legendas animadas palavra por palavra — **opcionais**.
8. Gera gancho, título, descrição e hashtags.
9. Tela de revisão para ajustar, aprovar e exportar em lote.

## Requisitos

- Windows 10/11
- GPU NVIDIA recomendada (testado em GTX 1050 Ti 4 GB; sem GPU funciona na CPU, mais lento)
- Python 3.11 e FFmpeg
- Node.js (a partir da Fase 5)
- Chave da API Anthropic (a partir da Fase 2)

## Instalação

```powershell
# 1. Ferramentas (uma vez só; reabra o terminal depois)
winget install -e --id Python.Python.3.11
winget install -e --id Gyan.FFmpeg

# 2. Ambiente Python do projeto (dentro da pasta do repositório)
py -3.11 -m venv .venv
.venv\Scripts\pip install -e ".[dev]"

# 3. Configuração (opcional — sem ela, valem os padrões)
copy config.example.yaml config.yaml
copy .env.example .env
```

As bibliotecas CUDA (cuBLAS/cuDNN) vêm pelo `pip`; **não** é preciso instalar o
CUDA Toolkit. Basta ter o driver NVIDIA atualizado.

Na primeira transcrição, o modelo Whisper é baixado para `models/`
(`medium` ≈ 1,5 GB).

## Uso

### Interface (recomendado)

Dê dois cliques em **`Corte Viral.bat`** (na pasta do projeto). Abre uma janela onde
você cola o link ou escolhe o arquivo, acompanha o progresso e vê a transcrição
sincronizada com o vídeo (clique numa palavra para pular até ela).

### Encontrar os melhores momentos

Abra um projeto transcrito, escolha o perfil (**Viral** 15–60 s ou **Monetização**
61–120 s), quantos momentos e a IA, e clique em **Encontrar momentos**. Cada momento
mostra nota de 0 a 100, justificativa e uma sugestão de gancho; clicar toca o trecho.

- A nota combina sinais locais (picos de volume, palavras de reação como "MEU DEUS"
  e "kkkk", ritmo de fala, picos de chat) com a avaliação da IA.
- **IA grátis:** crie uma chave em aistudio.google.com e coloque no `.env` como
  `GEMINI_API_KEY=...`. Sem chave (ou com "Sem IA"), funciona só com os sinais.
- O card **Uso da IA hoje** mostra tokens, requisições e a % do limite diário, com
  gráfico por minuto. O Google não informa a cota grátis pela API: confira em
  AI Studio → *Rate limits* e ajuste `llm.daily_request_limit` / `llm.daily_token_limit`
  no `config.yaml`.

### Gerar os clipes verticais (9:16)

Em cada momento, **Gerar clipe** cria o MP4 1080x1920 em `projects/<projeto>/clips/`;
**Gerar todos os clipes** faz a lista inteira. **Ver clipe** abre o player vertical.

Abra **Layout do vertical** para conferir o recorte:

- O app detecta sozinho as câmeras (rostos que ficam parados no mesmo lugar) e a
  janela da gameplay (a área que se mexe). Com várias câmeras, escolha qual usar.
- Arraste os retângulos (o canto branco redimensiona); a prévia 9:16 atualiza na hora.
- Layouts: **Câmera + gameplay** (câmera em cima, 30 %), **Seguir o rosto** (recorte
  vertical acompanhando o rosto) e **Vídeo com fundo desfocado** (sem câmera).
- **Salvar layout deste streamer** grava em `presets/streamers/<streamer>.json`; os
  próximos VODs do mesmo canal já saem com esse recorte.

Codificação: tenta a GPU (NVENC) e cai para a CPU (libx264) se não der. Placas da linha
Pascal (GTX 10xx) com driver 58x não rodam o NVENC do FFmpeg 9 (exige driver ≥ 610).

### Contas conectadas (vídeos com restrição de idade, privados ou de inscritos)

Clique em **"Conectar contas"**. Abre uma janela do Edge com abas do YouTube,
Twitch e Kick, num perfil separado só do app. Entre nas contas que quiser e
feche a janela. Pronto: antes de cada download o app renova essa sessão sozinho.

- Funciona com **qualquer navegador** que você usa (Brave, Chrome, Firefox…),
  mesmo aberto — o app não mexe no seu navegador principal.
- Por quê assim: Chrome/Brave/Edge recentes criptografam os cookies de um jeito
  que nenhum programa externo consegue ler. No perfil do app, o próprio Edge
  entrega a sessão ao Corte Viral.
- A sessão fica em `data/` (fora do git). Para desconectar, apague essa pasta.

Alternativas em "Outras formas de login": Firefox (lê direto do perfil) ou um
arquivo `cookies.txt` exportado com a extensão "Get cookies.txt LOCALLY".

### Linha de comando

```powershell
# Baixa/importa e transcreve
.venv\Scripts\python -m corteviral ingest "https://www.twitch.tv/videos/123456789"
.venv\Scripts\python -m corteviral ingest "C:\Videos\live.mp4"

# Opções
#   --model small        modelo mais rápido (menos preciso)
#   --language pt        força o idioma (padrão: detecção automática)
#   --force              refaz tudo, ignorando o cache
#   --cookies-from-browser firefox   usa o login do navegador (vídeos com restrição)

# Melhores momentos (perfil viral, 10 momentos, IA Gemini)
.venv\Scripts\python -m corteviral analyze projects\<pasta-do-projeto> --n 10 --profile viral --provider gemini

# Detectar layout e gerar os clipes
.venv\Scripts\python -m corteviral detect projects\<pasta-do-projeto>
.venv\Scripts\python -m corteviral render projects\<pasta-do-projeto> [--ids m00012,m01618] [--preview]

# Estado de um projeto
.venv\Scripts\python -m corteviral info projects\<pasta-do-projeto>
```

Cada vídeo vira uma pasta em `projects/` com `audio.wav`, `transcript.json`
(palavras com tempo de início/fim) e `project.json`. Rodar o mesmo link ou
arquivo de novo reaproveita o que já foi feito. Arquivos locais não são copiados
(hardlink quando estão no mesmo disco).

Lives em andamento são recusadas — aguarde virar VOD.

## Configuração

Todos os parâmetros ficam em `config.yaml` (veja `config.example.yaml`, comentado).

## Testes

```powershell
.venv\Scripts\pytest            # testes rápidos
.venv\Scripts\pytest -m slow    # inclui transcrição real com o modelo tiny
```

## Documentação

- [Especificação de design](docs/superpowers/specs/2026-11-11-corte-viral-design.md)
- [Plano da Fase 1](docs/superpowers/plans/2026-11-11-fase-1-download-transcricao.md)
