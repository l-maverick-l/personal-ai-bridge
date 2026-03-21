# Personal AI Bridge

Personal AI Bridge is a Windows-first desktop helper for Yahoo Mail, approved local folders, and pluggable AI providers.

## Phase 1 status

This repository currently includes the Phase 1 desktop shell and scaffolding:
- PySide6 desktop window
- first-run setup wizard scaffold
- SQLite-backed settings persistence
- local logging to file and SQLite
- allowed-folder registry with path validation helpers
- AI provider selection scaffolding for Ollama and OpenAI-compatible endpoints

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m app.main
```

## Windows packaging

A Windows-oriented build script scaffold is included in `build.ps1`.
