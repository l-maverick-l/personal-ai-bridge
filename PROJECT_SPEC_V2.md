# Personal AI Bridge — V2 Product Specification

## 1) Product vision

Personal AI Bridge V2 is a **desktop copilot**: a floating AI assistant that helps users complete real work across files and desktop applications while keeping a strong, user-visible safety boundary.

V2 should feel like:
- “A simple chat assistant that follows my instructions.”
- “It can see enough desktop context to be useful.”
- “It asks me before destructive or high-risk actions.”

## 2) North-star user experience

### Primary UX goals
1. **Always-available floating assistant**
   - Small, movable, always-on-top chat panel.
   - Keyboard shortcut to show/hide.
2. **Context-aware assistance**
   - Understand current app/window context.
   - Optional visual context capture (screenshot/OCR).
3. **Actionable agent behavior**
   - Can browse, read, create, move, copy, and delete files.
   - Can execute desktop actions via explicit tool APIs.
4. **Safety-first automation**
   - Read-only tasks may auto-run.
   - Destructive/system/external actions require confirmation.

### Non-goals for V2
- Fully autonomous background operation without user oversight.
- Arbitrary shell execution from model output.
- Silent destructive actions.

## 3) Core principles

1. **Human in control**
   - User can review and confirm risky actions.
2. **Progressive autonomy**
   - Policy levels determine what can auto-execute.
3. **Transparent execution**
   - Every action is logged and explainable.
4. **Grounded responses**
   - Assistant should use tool evidence for factual file/system claims.
5. **Least privilege by default**
   - Start safe, allow user opt-in to broader access.

## 4) User stories

1. As a user, I can ask the assistant to organize files in a folder tree and preview planned changes before execution.
2. As a user, I can ask “clean Downloads” and approve bulk moves/deletes with one confirmation dialog that summarizes impact.
3. As a user, I can ask questions about files I have not manually opened and the assistant can browse/search approved scopes.
4. As a user, I can enable broader desktop context (window/title/screenshot OCR) so the assistant can reference what I’m viewing.
5. As a user, I can see a complete timeline of what the assistant did and undo where possible.

## 5) Functional requirements

### A. Floating assistant shell
- Always-on-top mini window with:
  - conversation thread,
  - pending actions list,
  - policy indicator,
  - quick cancel button.
- Global hotkey to open/close.
- Dock/undock mode (compact + expanded).

### B. Desktop context ingestion
- Context sources (user-configurable):
  1. active window metadata (title/process),
  2. selected text (if available),
  3. on-demand screenshot,
  4. OCR extraction from screenshot.
- User can disable any source.
- Context payload shown in “what I used” inspector.

### C. Filesystem operations
- Keep existing file tools and add bulk/planning variants:
  - list/search/read/summarize,
  - create/rename/copy/move/delete,
  - batch operations with dry-run preview.
- Support browsing un-opened folders **within allowed scope**.
- Optional “expanded access mode” for broader filesystem access with heightened confirmations.

### D. Desktop action tools (new)
- Tool layer for common desktop actions (phased):
  - open app,
  - focus window,
  - open file/folder,
  - clipboard operations.
- Strictly disallow raw shell pass-through from model text.

### E. Plan → preview → execute flow
- For non-trivial tasks, assistant returns:
  1. intent summary,
  2. execution plan,
  3. affected resources,
  4. risk level,
  5. required confirmation.
- User can approve all, approve step-by-step, or reject.

### F. Safety and policy controls
- Policy presets:
  1. **Read-only auto**
  2. **Trusted write auto (non-destructive)**
  3. **Confirm destructive/external**
  4. **Advanced full auto**
- Hard stops (never auto without explicit confirmation):
  - delete operations,
  - external sends,
  - credential changes,
  - system-level settings changes.

### G. Auditability
- Structured action log entries include:
  - timestamp,
  - user request,
  - tool call,
  - arguments,
  - result,
  - confirmation source,
  - rollback hint (if available).
- Export log to JSON/CSV.

## 6) Safety model

### Risk tiers
- **Tier 0**: read-only local actions (list/read/search).
- **Tier 1**: reversible writes (create/rename/copy/move with safe destination).
- **Tier 2**: destructive writes (delete/overwrite).
- **Tier 3**: external/system actions (email send, app/system operations).

### Confirmation matrix
- Tier 0: auto allowed.
- Tier 1: auto depends on policy.
- Tier 2: always confirm.
- Tier 3: always confirm (+ optional typed confirmation for critical operations).

### Guardrails
- Path traversal prevention.
- Scope checks before action execution.
- Action simulation (dry-run) for batch operations.
- Max step/operation limits to prevent runaway execution.

## 7) Technical architecture (target)

1. **Assistant Orchestrator**
   - Maintains tool loop, grounding checks, and policy checks.
2. **Capability Registry**
   - Typed tool contracts with risk metadata.
3. **Policy Engine**
   - Decides auto-run vs proposal-required.
4. **Desktop Context Service**
   - Active-window adapter + screenshot/OCR pipeline.
5. **Execution Engine**
   - Runs approved actions with transactional logging.
6. **UI Layer**
   - Floating shell + full settings panel + action inspector.

## 8) Data model updates

Add/extend persistent entities:
- `assistant_policies`
- `action_proposals`
- `action_executions`
- `context_snapshots` (metadata only by default; screenshot retention configurable)
- `undo_journal` (where reversible)

## 9) Observability and quality

### Required telemetry (local-first)
- Success/failure rate by tool.
- Confirmation acceptance/rejection rate.
- Time-to-completion for multi-step tasks.
- Top failure reasons (permissions/path/context missing).

### Quality gates
- Tool-schema validation tests.
- Policy-engine unit tests.
- Integration tests for proposal/approval execution.
- Regression suite for destructive-action confirmation guarantees.

## 10) Rollout plan

### Milestone 1 — UX and safety foundation
- Floating assistant window.
- Unified plan/proposal panel.
- Policy engine refactor + risk tiers.

### Milestone 2 — Better file automation
- Batch file plans + dry-run previews.
- Undo journal for move/rename/delete (where possible).

### Milestone 3 — Desktop context
- Active window context + optional screenshot/OCR.
- Context transparency inspector.

### Milestone 4 — Desktop actions
- Controlled app/window automation tools.
- Expanded confirmation UX for system/external tasks.

## 11) Acceptance criteria

V2 is complete when:
1. User can perform multi-step file organization from chat with clear preview and confirmations.
2. Assistant can consume optional desktop context and cite it in responses.
3. Destructive/system actions never execute without explicit user approval.
4. Every action is audit-logged and visible in UI.
5. Non-technical user can understand and control automation level from policy presets.

## 12) Open decisions

1. Default scope model: strict approved roots only vs optional broader filesystem mode.
2. Screenshot retention policy and privacy defaults.
3. Exact desktop automation backend per OS.
4. Whether “full auto” should still force confirmation for some Tier 3 actions.
