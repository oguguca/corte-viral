# Corte Viral

Aplicativo desktop para Windows que transforma lives e VODs longos (YouTube,
Twitch, Kick ou arquivo local) em clipes verticais 9:16 prontos para TikTok,
YouTube Shorts, Instagram Reels e Kwai.

> **Status:** em desenvolvimento — design em revisão, Fase 1 a seguir.

Saída: MP4 9:16 (1080x1920), um único arquivo compatível com TikTok, YouTube
Shorts, Instagram Reels e Kwai, com legenda e gancho dentro da zona segura comum.

## Como funciona

1. Cole um link ou escolha um arquivo local.
2. O app baixa o vídeo e transcreve o áudio com marcação de tempo por palavra
   (faster-whisper, local na GPU).
3. Detecta os melhores momentos combinando picos de áudio, picos de chat e
   análise da transcrição por IA (Claude).
4. Cada momento recebe nota de 0 a 100 e uma justificativa.
5. Gera os N melhores clipes (15–60 s), com início e fim em pontos naturais da fala.
6. Converte para 9:16 (facecam em cima + gameplay embaixo, ou recorte seguindo o rosto).
7. Legendas animadas palavra por palavra — **opcionais**.
8. Gera gancho, título, descrição e hashtags.
9. Tela de revisão para ajustar, aprovar e exportar em lote.

## Requisitos

- Windows 10/11
- GPU NVIDIA recomendada (testado em GTX 1050 Ti 4 GB)
- Python 3.11, FFmpeg, Node.js (a partir da Fase 5)
- Chave da API Anthropic (para a análise por IA)

## Instalação

_Será preenchida ao fim da Fase 1._

## Uso

_Será preenchido conforme as fases forem concluídas._

## Documentação

- [Especificação de design](docs/superpowers/specs/2026-11-11-corte-viral-design.md)
