@echo off
title Corte Viral
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Ambiente nao encontrado. Rode a instalacao descrita no README.md primeiro.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m corteviral ui
if errorlevel 1 pause
