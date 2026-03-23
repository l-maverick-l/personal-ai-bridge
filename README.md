# Personal AI Bridge

Personal AI Bridge is a Windows-first desktop helper for Yahoo Mail, approved local folders, and pluggable AI providers.

## Current status

This repository now includes:
- PySide6 desktop UI with a first-run setup wizard
- SQLite-backed settings persistence and local action logging
- approved-folder registry with safe file browsing, reading, creation, rename, copy, move, and delete flows
- Yahoo Mail Phase 3 support for:
  - Yahoo settings in the UI
  - Yahoo IMAP/SMTP connection testing with app passwords
  - inbox listing and search by unread status, sender, subject keyword, and date range
  - reading email content
  - AI summaries for one email
  - AI-generated draft replies and new emails
  - SMTP send behind explicit confirmation only
- AI provider configuration for Ollama and OpenAI-compatible local or cloud endpoints

Mail listing, search, and reading still work when AI is not configured. AI is only required for summaries and draft generation.

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m app.main
```

## Windows packaging

A Windows-oriented build script scaffold is included in `build.ps1`.
