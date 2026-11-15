# Transcrição na nuvem (Groq) para máquinas sem GPU — design curto

## Intenção
Em notebooks sem placa de vídeo a transcrição cai para `small` na CPU e leva dezenas de minutos por hora
de vídeo. Queremos transcrição rápida em qualquer máquina, **sem custo** (decisão do usuário).

## Objetivos
- Sem GPU + chave Groq → transcrever pela API grátis do Groq (`whisper-large-v3-turbo`), com timestamps
  por palavra (as legendas dependem disso). Meta: 1 h de áudio em ~1–2 min.
- Nunca travar: sem chave, sem internet ou cota esgotada → cai na CPU local (comportamento de hoje).
- Com GPU funcionando, nada muda.

## Não-objetivos / anti-objetivos
- Nada pago (OpenAI, Deepgram etc.). Plano grátis do Groq não pede cartão, então não há cobrança possível.
- Não usar Gemini para transcrever (timestamps por palavra não confiáveis → legendas quebram).
- Não otimizar o caminho de CPU nesta mudança.

## Abordagens consideradas
1. **Groq na nuvem + fallback local (escolhida).** Rápido, qualidade acima do `small`, grátis com cota
   diária (~2 h de áudio/hora, ~8 h/dia; conferir no console do Groq). Custo: áudio sai da máquina
   (já acontece com o Gemini), limite de 25 MB por arquivo → envio em pedaços.
2. Só acelerar local (modelo menor, inferência em lote): 2–4× mais rápido, ainda lento e pior qualidade.
3. Gemini / OpenAI: Gemini sem timestamps confiáveis por palavra; OpenAI é pago.

## Como funciona
- **Config** (`whisper` em `config.yaml`): novo `engine: auto | local | groq` (padrão `auto`) e
  `groq_model: whisper-large-v3-turbo`.
  - `auto`: GPU passa no teste prévio → local na GPU; senão, se houver `GROQ_API_KEY` → Groq; senão CPU local.
  - `groq`: sempre Groq (com fallback CPU em erro). `local`: comportamento atual.
- **Chave**: `GROQ_API_KEY` no `.env`, lida do mesmo jeito que `GEMINI_API_KEY` (`llm._key`).
  README ganha o passo a passo (console.groq.com → API Keys).
- **Envio**: ffmpeg corta `audio.wav` em pedaços de ~10 min, mono 16 kHz, comprimido (opus/mp3 ~32 kbps,
  bem abaixo de 25 MB). Cada pedaço vai para `POST /openai/v1/audio/transcriptions` com
  `response_format=verbose_json` e `timestamp_granularities[]=word` e `segment`. Os tempos recebem o
  deslocamento do pedaço e são juntados.
- **Checkpoint**: reaproveita `_Checkpoint` — cada pedaço concluído é salvo; retomar continua do próximo
  pedaço. Mesma `audio_params`.
- **Erros**: HTTP 429 (cota) ou falha de rede → salva checkpoint e continua o resto na CPU local
  (`cpu_fallback_model`), com aviso na barra de progresso ("Cota do Groq esgotada, usando CPU").
  Retentativa curta (respeitando `retry-after`) antes de desistir.
- **Resultado**: `Transcript.model = "whisper-large-v3-turbo"`, `device = "groq"`. Cache de etapa marcado
  normalmente (resultado do Groq vale como definitivo, não é tratado como fallback).
- **Progresso**: fração = pedaços concluídos / total, mensagem "transcrevendo na nuvem (Groq)".
- **Probabilidade da palavra (`p`)**: o Groq não devolve por palavra; usar `1.0` (ou derivar de
  `avg_logprob` do segmento). Conferir se algum consumidor de `Word.p` depende disso.

## Decisões minhas (confira)
- Pedaços de 10 min sem sobreposição; palavra cortada na borda é aceitável. Se der problema, cortar em
  silêncio (VAD/`silencedetect`) é melhoria futura.
- Chave só no `.env` (igual Gemini); campo na tela de Ajustes fica para depois.
- Sem nova dependência: HTTP via `urllib`/`requests` já usados no projeto.

## Para o construtor decidir
- Formato exato de compressão (opus vs mp3) conforme o que o Groq aceitar melhor.
- Testes: mock do HTTP do Groq (sucesso, 429, timeout) e da junção de deslocamentos.
