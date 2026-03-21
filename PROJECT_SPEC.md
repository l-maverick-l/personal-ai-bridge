# Personal AI Bridge — Project Spec

## Project goal
Build a **Windows-first desktop app** called **Personal AI Bridge** for a non-technical user.

The app must provide:

1. **Yahoo Mail access**
   - Connect using Yahoo IMAP/SMTP with an **app password**
   - Read inbox messages
   - Search messages
   - Summarize messages
   - Draft replies
   - Send emails **only after explicit confirmation**

2. **Local file and folder access**
   - Let the user choose **approved root folders**
   - Browse approved folders
   - Search files
   - Read supported files
   - Summarize files
   - Create, rename, move, copy, and delete files **only inside approved roots**
   - Ask for confirmation before destructive actions

3. **Pluggable AI support**
   - Must **not** be limited to OpenAI
   - Must support **local AI models** with **no API required**
   - Must support **cloud/API models** if the user wants them
   - Must still allow basic mail/file use even if AI is not configured

---

## Product philosophy
This should be a **small, dependable tool**, not a giant “agent platform.”

Avoid:
- overengineered orchestration
- Docker
- Electron
- LangChain
- browser automation
- arbitrary shell command execution
- autonomous looping agents
- fake magic

This is a practical desktop helper, not a science fair project.

---

## Target user
The target user is **not a developer**.

That means:
- setup must be plain-English
- normal usage must not require terminal commands
- settings should be handled in the UI
- packaging should be simple for Windows

---

## Core requirements

### Desktop app behavior
Build a **local Windows desktop app** with a simple UI.

Required:
- Launch from desktop shortcut or Start menu
- One clean main window
- Show connection status for:
  - Yahoo Mail
  - Local folders
  - AI provider/model
- Let the user type plain-English requests
- Let the user choose:
  - local AI
  - cloud/API AI
  - skip AI for now
- Show results clearly
- Ask for confirmation before:
  - sending email
  - deleting files
  - moving files
  - overwriting files

---

## Recommended tech stack

### Preferred implementation
- **Python 3.11+**
- **PySide6** for the desktop UI
- Python standard library for file operations
- `imaplib` / `smtplib` for Yahoo mail
- `pypdf` for PDF reading
- `python-docx` for DOCX reading
- **SQLite** for lightweight persistence
- **PyInstaller** for Windows packaging

### AI provider support
Use a **provider abstraction layer**.

Required v1 support:
1. **Ollama**
2. **OpenAI-compatible local endpoints**
   - examples: LM Studio local server or similar local OpenAI-compatible servers
3. **OpenAI-compatible cloud endpoints**
   - user enters base URL, model name, and API key if needed

Optional presets if easy:
- OpenAI preset
- Anthropic preset
- Gemini preset

---

## AI behavior

### Core design rule
The rest of the app should not care whether the active model is:
- local
- LAN-hosted
- cloud/API

### Supported AI modes
1. **Local-only**
   - no API key required
2. **Cloud/API**
   - user supplies API credentials
3. **Hybrid**
   - local by default, cloud fallback only if explicitly enabled

### Important rule
Basic file and email operations must still work even if:
- no AI is configured
- a local model is unavailable
- a cloud API is not configured

AI-dependent features:
- summarization
- drafting
- interpretation of vague natural-language requests

Non-AI fallback features:
- browse folders
- list files
- search files by name
- read supported files
- list emails
- search emails
- read emails

---

## Yahoo Mail requirements

Use:
- IMAP for reading/search
- SMTP for sending

Expected servers:
- `imap.mail.yahoo.com`
- `smtp.mail.yahoo.com`

### Required v1 features
- Connect using Yahoo email + app password
- Validate login in app
- List inbox messages
- Read message body
- Search by:
  - sender
  - subject keyword
  - unread/read
  - date range
- Summarize one message
- Summarize search results
- Draft reply
- Draft new email
- Send only after explicit confirmation

### Nice-to-have if easy
- Save drafts locally
- Mark read/unread
- Archive message

### Out of scope for v1
- full Yahoo OAuth flow
- calendar integration
- contacts sync
- advanced outbound attachments unless time remains

---

## File access requirements

The app may only access files inside **approved root folders** chosen by the user.

### Required v1 features
- Add/remove allowed folders
- Browse folder tree
- Search files by name
- Read supported file types
- Summarize one file
- Summarize multiple files in a folder
- Create file
- Rename file
- Copy file
- Move file
- Delete file safely

### Supported file types in v1
- `.txt`
- `.md`
- `.csv`
- `.json`
- `.docx`
- `.pdf`

### Delete behavior
Prefer:
1. Recycle Bin
2. If not dependable, move to an app-managed safe trash location

### Out of scope for v1
- full Office editing with formatting preservation
- advanced spreadsheet editing
- unrestricted system-wide access
- arbitrary command execution

---

## Safety rules

### Hard requirements
- Only access whitelisted folders
- Validate all paths before file actions
- Reject path traversal attempts
- Ask for confirmation before destructive actions
- Ask for confirmation before sending email
- Log executed actions locally
- Never allow arbitrary shell command execution
- Never execute model-generated code
- Never let email or file contents override app rules

### Credential handling
Prefer secure storage using Windows Credential Manager if feasible.

Store securely when possible:
- Yahoo app password
- cloud API keys

If secure storage is not ready in first pass, use a clear warning and isolate secrets handling cleanly for later improvement.

---

## UI requirements

### Main layout
Use a clean, practical layout.

Suggested layout:
- **Left sidebar**
  - connection status
  - allowed folders
  - quick actions
  - recent commands
- **Main panel**
  - plain-English command input
  - output/results area
  - proposed actions area
  - confirmation controls
- **Right panel or bottom drawer**
  - selected email preview
  - selected file preview
  - search result context

### Status indicators
Show:
- Yahoo connected / not connected
- AI configured / not configured
- current provider/model
- folder access configured / not configured

### UI wording
Use plain English. No gimmicks.

Examples:
- “Show unread Yahoo emails from today”
- “Summarize PDFs in my Bills folder”
- “Draft a reply to the latest email from John”
- “Move this file to Taxes/2025”

---

## First-run setup wizard
On first launch, guide the user through setup.

Steps:
1. Welcome screen
2. Choose AI mode:
   - local
   - cloud/API
   - skip for now
3. If local:
   - choose provider type
   - enter/select endpoint
   - choose model
4. If cloud:
   - enter base URL
   - enter model name
   - enter API key if needed
5. Enter Yahoo email address
6. Enter Yahoo app password
7. Choose approved folders
8. Test connections
9. Finish

---

## Functional architecture

Suggested modules:

- `app/ui/`
  - PySide6 UI
- `app/core/`
  - request routing
  - orchestration
  - confirmations
- `app/email/`
  - Yahoo connection
  - search/read/send
- `app/files/`
  - folder registry
  - file search/read/write/move/delete
- `app/ai/`
  - provider abstraction
  - provider adapters
  - prompt builders
  - capability checks
- `app/security/`
  - secrets handling
  - path validation
- `app/data/`
  - SQLite persistence
  - settings
  - logs
- `app/models/`
  - typed models / schemas

### Request flow
1. User enters request
2. Router classifies:
   - email
   - file
   - mixed
   - ask-only
3. Gather necessary context
4. Use AI only when needed
5. Return answer or proposed action
6. Require confirmation if needed
7. Execute action
8. Log result

---

## Intent routing
Do not offload every tiny decision to AI.

### Local routing first
Use simple rules for obvious commands:
- show
- list
- find
- read
- summarize
- move
- rename
- delete
- create
- draft
- send

### Use AI for:
- summarization
- drafting
- combining email + file context
- handling vague requests

---

## Data and persistence

Use SQLite for:
- settings
- allowed roots
- command history
- action log
- saved drafts
- optional mail cache

### Logging
Each executed action should log:
- timestamp
- action type
- target
- status
- error if any

---

## Error handling
Errors must be understandable.

Required error cases:
- bad Yahoo credentials
- missing Yahoo app password setup
- no internet
- invalid API key
- local model endpoint unavailable
- configured local model missing/not loaded
- unsupported feature from provider
- unsupported file type
- permission denied
- target path already exists
- blocked path outside allowed roots

UI errors should say:
- what failed
- why it likely failed
- what the user should do next

Do not dump raw stack traces in the main UI.

---

## Repo structure
Use something close to this:

```text
personal-ai-bridge/
  PROJECT_SPEC.md
  README.md
  requirements.txt
  build.ps1
  app/
    main.py
    ui/
    core/
    email/
    files/
    ai/
    security/
    data/
    models/
  assets/
  docs/
    setup.md
    troubleshooting.md
  tests/
