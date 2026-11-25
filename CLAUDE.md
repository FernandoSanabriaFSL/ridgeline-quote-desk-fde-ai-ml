# Project Instructions

## Chat history logging (permanent, silent)

This project keeps a running log of every exchange in `.chat-history/log.md`. Follow these rules in every session without asking for confirmation and without announcing that you are doing it.

### 1. At the start of each session
Read `.chat-history/log.md` for previous context before doing anything else. If it doesn't exist yet, skip the read and create it when you log the first exchange.

### 2. After each response
Append one entry to `.chat-history/log.md` using exactly this format:

```
---
- timestamp: "<ISO 8601 timestamp if available, otherwise estimate based on conversation order>"
- user_prompt: "<the user's original prompt>"
- assistant_response_summary: "<summary of what you generated or answered for this prompt>"
- files_affected: "<comma-separated list of files created or modified, or none>"
```

### 3. File handling
- Create `.chat-history/` and `log.md` if they don't exist.
- Only ever append. Never delete, rewrite, reorder or truncate previous entries.

### 4. Silent operation
Do all of this silently. Never ask for confirmation or permission to read or write the log.

### Rules
- **Files affected:** list only files explicitly created or modified during this response. Do not list files that were only read. The log itself (`.chat-history/log.md`) is not listed. If nothing was changed, write `none`.
- **Log every exchange:** never skip one. Every prompt/response pair gets an entry.
- **Summary:** keep it concise but specific. Mention function names, endpoints and key decisions made.
- **Secrets:** never write secrets to the log. Replace API keys, tokens, passwords and `.env` values with `***` in every field, including `user_prompt`.

Always run Python and pip from the project venv: .venv/bin/python and .venv/bin/pip.
