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

## Project rules

1. Pipeline code never reads `data/answer_key/`. Only `evaluation/evaluate.py` may.
2. No prices anywhere: never output prices, compute them or send them to the model. `list_price` is never put into a prompt.
3. The model only extracts. Ridgeline part numbers come only from `products.csv` or `cross_reference.csv`, never from the model.
4. Flag, don't guess: anything uncertain gets null, a flag and a note, and goes to review.
5. No RFQ-specific rules, customer names or part numbers hardcoded in code.
6. Outputs are drafts for a specialist. Nothing is sent anywhere.
7. Secrets live only in `.env`, which is gitignored. Never print or log a key, and never write a key-like placeholder value in any file.
8. `README.md` keeps exactly its six headings. Anything inside a section uses `###` or deeper.
9. Append the log entry for the current exchange before any commit made in that exchange.
10. Every stage ends with a commit and a push to origin main.
