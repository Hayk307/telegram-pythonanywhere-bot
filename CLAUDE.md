# CLAUDE.md — Project Guide for AI Agents

This file describes the architecture, conventions, and deployment process for this project so an AI agent can work on it without guessing.

---

## What this project is

A Telegram bot template built for students. It runs on PythonAnywhere's free tier, uses Cerebras (or any OpenAI-compatible API) for AI responses, and a local SQLite file on PA's persistent disk for per-user conversation memory.

**Stack:** Python 3.13 · Flask · pyTelegramBotAPI · OpenAI SDK · SQLite · PythonAnywhere

---

## Project structure

```
telegram-pythonanywhere-bot/
├── api/
│   └── index.py          # Flask entrypoint — webhook route, /api/health, secret verification
├── bot/
│   ├── __init__.py
│   ├── config.py         # All env vars and constants (edit this to configure the bot)
│   ├── clients.py        # Instantiates bot, ai, store (do not edit unless adding a client)
│   ├── store.py          # SqliteStore — KV with lazy TTL expiry, backed by sqlite3
│   ├── ai.py             # ask_ai() — history + dispatch to providers
│   ├── providers.py      # Provider dispatch: OpenAI-compatible (with retry) or HF Gradio space
│   ├── preferences.py    # Per-user provider preference stored via store
│   ├── history.py        # get/save/clear conversation history via store (graceful degradation)
│   ├── rate_limit.py     # Per-user daily message rate limiting via store (graceful degradation)
│   ├── dedupe.py         # Drops repeated update_ids when Telegram retries (graceful degradation)
│   ├── helpers.py        # send_reply(), keep_typing() context manager, should_respond() utilities
│   ├── handlers.py       # All Telegram command and message handlers — add new commands here
│   └── fileconvert.py    # /convertfile engine — image (Pillow) / media (ffmpeg) / doc (text-only); heavy libs imported lazily
├── tests/
│   ├── conftest.py       # Mocks env vars and external packages (telebot, openai, flask)
│   ├── test_ai.py        # ask_ai() orchestration
│   ├── test_providers.py # _call_main() retry, _call_hf() prompt handling, generate() dispatch
│   ├── test_preferences.py
│   ├── test_handlers.py
│   ├── test_fileconvert.py # /convertfile engine: parsing, family routing, real image/doc/media conversions
│   ├── test_helpers.py
│   ├── test_history.py
│   ├── test_rate_limit.py
│   ├── test_dedupe.py
│   ├── test_store.py     # Direct SqliteStore tests (get/set/delete/incr/expire + TTL)
│   ├── test_deploy.py    # /api/deploy auto-deploy webhook (secret verification + git pull)
│   └── test_webhook.py
├── .github/
│   └── workflows/
│       ├── ci.yml        # Runs pytest on every push and pull request
│       └── deploy.yml    # Triggers PA auto-deploy via /api/deploy on push to main
├── .env.example          # Template for required environment variables
├── run_local.py          # Run the bot locally via polling — for learning + dev
├── pythonanywhere_wsgi.py # WSGI entry exposing Flask `app` as `application` for PA
├── Makefile              # install / run / test shortcuts
├── requirements.txt
├── CLAUDE.md             # Agent-readable project guide (this file)
└── README.md             # Student-facing setup guide
```

---

## How the bot works

1. Telegram sends a POST to `https://<your-pa-username>.pythonanywhere.com/api/webhook` on every message
2. PA's WSGI loader imports `pythonanywhere_wsgi.py` at the project root, which loads `.env` then re-exports the Flask `app` as `application`
3. `api/index.py` validates the `X-Telegram-Bot-Api-Secret-Token` header (if `WEBHOOK_SECRET` is set), then deserializes the update and passes it to pyTelegramBotAPI
4. pyTelegramBotAPI routes to the correct handler in `bot/handlers.py`
5. For text messages: checks `should_respond()` → checks rate limit → enters `keep_typing()` context manager (a background thread re-sends the Telegram "typing" action every 4s so the indicator stays alive during slow generations) → calls `ask_ai()` → exits context (stops thread) → sends reply
6. `ask_ai()` loads history via the store, prepends the system prompt, dispatches to `generate()` in `bot/providers.py` which calls `_call_main()` (with retry logic) or `_call_hf()` depending on the user's provider preference, then saves updated history

**Critical:** `telebot.TeleBot` must be created with `threaded=False`. Without this, handlers run in threads that can be killed unexpectedly. `threaded=False` is also fine for local polling (`run_local.py`) — updates just process sequentially in the main thread.

**Local development mode:** `run_local.py` at the repo root runs the same `bot/` modules via `bot.infinity_polling()` instead of the webhook. It auto-loads `.env` with a zero-dependency inline loader, calls `bot.remove_webhook()` to release any registered production webhook, then blocks on polling. Use this for teaching, prototyping, or iterating without redeploying. Any production webhook registered against the same bot token must be re-registered via `setWebhook` after you stop polling, otherwise production will stay silent.

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | — | From @BotFather on Telegram |
| `AI_API_KEY` | Yes | — | API key for the AI provider |
| `SQLITE_PATH` | No | — | Absolute path to a SQLite DB file. When set, enables history / rate limit / preferences / dedupe. When unset, bot runs in **stateless mode**. On PA use `/home/<your-pa-username>/bot.db` |
| `AI_BASE_URL` | No | `https://api.cerebras.ai/v1` | Any OpenAI-compatible base URL |
| `AI_MODEL` | No | `gpt-oss-120b` | Model name for the provider |
| `HF_SPACE_ID` | No | — | Hugging Face Gradio space ID (e.g. `edisimon/armgpt-demo`) — enables `/model` command when set |
| `GOOGLE_API_KEY` | No | — | **Currently unused.** Was the web-image-search key for `/image`'s old real-photo path; `/image` is now pure Pollinations text-to-image generation (no real-photo lookup), so nothing reads this. Still defined in `bot/config.py` for backward compatibility — safe to leave unset |
| `GOOGLE_CSE_ID` | No | — | **Currently unused** (see `GOOGLE_API_KEY`). Paired Programmable Search engine id from the retired `/image` real-photo path |
| `HF_EDIT_SPACE_ID` | No | `black-forest-labs/FLUX.1-Kontext-Dev,Yuanshi/FLUX.1-Kontext-Turbo` | **Comma-separated fallback chain** of Hugging Face Spaces powering `/edit` (edit a photo from a text prompt). **Free, no key required** — called via `gradio_client`. Parsed into `HF_EDIT_SPACE_IDS` (a list); `_edit_image` in `bot/handlers.py` tries each in order and moves to the next when one is queue-full / down / rate-limited (free ZeroGPU Spaces are individually unreliable, so the chain is what stops the "image editor is busy" error). All default Spaces share the `/infer` signature `(input_image, prompt, seed, randomize_seed, guidance_scale, steps)` and return WebP, which the bot converts to JPEG via Pillow so Telegram renders it inline. Only add Spaces with that same signature, or adapt `_call_edit_space`. **Not** Pollinations: its free tier no longer does image-to-image (the edit models moved to the paid `enter.pollinations.ai`; free models silently ignore the input image). On PA free tier, `/edit` needs `*.hf.space` on the outbound whitelist (works locally without it) |
| `HF_TOKEN` | **Effectively required for `/edit`** | — | HF auth token (free — huggingface.co/settings/tokens). The FLUX Kontext editing Spaces run on ZeroGPU, whose **anonymous quota is now 0s** — without a token every `/edit` fails with "You have exceeded your ZeroGPU quota (Xs requested vs. 0s left)". A free token grants a daily GPU allowance and makes `/edit` work. `_edit_image` detects the quota error and, when `HF_TOKEN` is unset, tells the user the owner must set it. Also used by the optional HF chat provider when its Gradio space is private/gated |
| `WEBHOOK_SECRET` | No | _auto-generated_ | Random string Telegram echoes back in `X-Telegram-Bot-Api-Secret-Token`. Auto-bootstrapped on first run: if the env var is unset, `bot/config.py::_bootstrap_webhook_secret()` generates a 64-hex secret, persists it to `.webhook_secret` (gitignored, mode 0600), and reuses it on subsequent boots. The boot-time `register_webhook()` then ships it to Telegram. Set the env var to override / share across envs |
| `WEBHOOK_URL` | No | — | When set, the bot auto-registers this URL as the Telegram webhook on every worker boot and after every `/api/deploy`. No manual `setWebhook` step needed. Idempotent. On PA, value is `https://<your-pa-username>.pythonanywhere.com/api/webhook`. Leave unset for local polling |
| `RATE_LIMIT` | No | `250` | Max messages per user per day |
| `ALLOWED_USERS` | No | _open_ | Comma-separated whitelist of usernames (with/without `@`) or numeric user IDs. Empty = everyone allowed. Non-empty = silent drop for non-whitelisted (no rejection reply, no leak of bot existence). Implemented as `func=is_allowed` on every `@bot.message_handler` so telebot never dispatches the handler |
| `HOSTING_LABEL` | No | `PythonAnywhere` | Label shown by the `/about` command |
| `DEPLOY_SECRET` | No | — | Enables `/api/deploy` auto-deploy webhook. Fail-closed: when unset, the endpoint returns 403. Generate with `openssl rand -hex 32` and set the same value as a GitHub repo secret named `DEPLOY_SECRET` so the workflow at `.github/workflows/deploy.yml` can call the endpoint |
| `PA_WSGI_PATH` | No | _auto-detected_ | Absolute path of the PA WSGI file `/api/deploy` touches to reload the worker. Only needed when auto-detection fails (non-default PA layout / custom domain) — the deploy response says so explicitly when that happens |

All env vars are read in `bot/config.py`. `.strip()` is called on every value to defend against trailing newlines / whitespace from copy-paste.

---

## AI provider

The bot uses the OpenAI Python SDK pointed at any OpenAI-compatible endpoint. Switching providers only requires changing `AI_BASE_URL` and `AI_MODEL` (via env vars — no code change needed).

**Known working providers (free tier):**

| Provider | Base URL | Notes |
|---|---|---|
| Cerebras | `https://api.cerebras.ai/v1` | Default. Confirmed working on free tier: `gpt-oss-120b`, `qwen-3-235b-a22b-instruct-2507` |
| Groq | `https://api.groq.com/openai/v1` | 14,400 req/day free. Model: `llama-3.1-8b-instant` |
| Google Gemini | `https://generativelanguage.googleapis.com/v1beta/openai/` | Model: `gemini-2.5-flash` (250 req/day) |

**Cerebras model IDs** (exact strings — wrong format causes 404):
- `gpt-oss-120b` ✓ verified working on free tier. Current default (`bot/config.py`, `.env.example`) — strong reasoning at Cerebras speed
- `qwen-3-235b-a22b-instruct-2507` ✓ verified working on free tier. Strong reasoning and multilingual, but slower per-token and more queue-pressured
- `llama3.1-8b` ✗ deprecated by Cerebras — do not use (was the previous default)

---

## Multi-provider support

The bot can dispatch requests to one of two providers per user. Provider identifiers are **`main`** and **`hf`** — both in code (`VALID_PROVIDERS`, `DEFAULT_PROVIDER`, store values) and in the user-facing `/model` command:

1. **`main`** (default) — any OpenAI-compatible endpoint via `AI_BASE_URL` / `AI_API_KEY` / `AI_MODEL`. `_call_main()` in `bot/providers.py` has retry logic (3 attempts with exponential backoff: 1s, 2s). Named "main" rather than "openai" to avoid confusing kids who might think it's tied to OpenAI Inc. — the endpoint is *OpenAI-compatible* (a protocol) but the actual provider is usually Cerebras or similar.
2. **`hf`** (optional) — a Hugging Face Gradio space set via `HF_SPACE_ID` (with optional `HF_TOKEN` for private spaces). Called via `gradio_client.Client(...).predict(prompt, length, temperature, top_k, api_name="/generate")`. No retry (HF is slow).

**When `HF_SPACE_ID` is empty, the bot works exactly as a single-provider setup** — the `/model` command is not registered and users always hit the main (OpenAI-compatible) endpoint.

**When `HF_SPACE_ID` is set**, users get a `/model` command:
- `/model` — show current provider + options
- `/model main` — switch to the OpenAI-compatible endpoint
- `/model hf` — switch to the HF space

Preferences are stored via `store` under `provider:{user_id}` (no TTL). If the store is not configured (stateless mode), the bot falls back to `DEFAULT_PROVIDER` (`"main"`).

**HF provider caveats** — the current target (`edisimon/armgpt-demo`, ArmGPT) has:
- Base completion model, not a chat model — `bot/providers.py::_last_user_message` extracts only the most recent user message and passes it as a bare prompt. Chat transcripts (`"User: ...\nAssistant: ..."`) would just confuse it since it was trained on raw Armenian text with no turn structure
- No system prompt support — the system prompt is dropped entirely for HF
- No conversation memory — only the latest user turn is sent
- Hardcoded knobs (`bot/providers.py`) — `HF_LENGTH=100`, `HF_TEMPERATURE=0.6`, `HF_TOP_K=30`. Tuned so generation finishes inside Telegram's ~60s webhook window
- Output is a `(html_output, status_text)` tuple — `_call_hf` takes index 0, strips HTML tags, and strips the echoed prompt prefix if present

To switch to a different HF space, change `HF_SPACE_ID` and confirm the target space exposes a `/generate` API with the same signature, or adapt `_call_hf` in `bot/providers.py`.

**PA outbound-whitelist caveat for HF Spaces.** `gradio_client` first fetches the space config from `huggingface.co` (whitelisted) and then routes `predict()` calls to `<space-subdomain>.hf.space` (NOT explicitly whitelisted as of last check). If `/model hf` hangs or 403s on PA but works locally, that's almost certainly the cause — verify with `curl -I https://<space>.hf.space/` from a PA Bash console, and if blocked, request `*.hf.space` on the PA forum whitelist thread. `bot/providers.py::_call_hf` passes `httpx_kwargs={"timeout": HF_REQUEST_TIMEOUT}` so a blocked subdomain fails fast instead of wedging the worker.

---

## Webhook verification

To block spoofed requests, set a random secret and pass it when registering the webhook:

```bash
# Add WEBHOOK_SECRET to PA .env, reload the web app, then:
curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook" \
  --data-urlencode "url=https://<your-pa-username>.pythonanywhere.com/api/webhook" \
  --data-urlencode "secret_token=<your secret>"
```

When `WEBHOOK_SECRET` is set, `api/index.py` checks the `X-Telegram-Bot-Api-Secret-Token` header on every request and returns 403 if it does not match. If the variable is not set, verification is skipped (backwards compatible).

---

## Storage

The bot's storage layer is a thin KV-with-TTL abstraction in `bot/store.py` exposing five operations: `get / set / delete / incr / expire`. Only one backend exists: **`SqliteStore`** — a file-backed sqlite3 with lazy TTL expiry.

- **`SQLITE_PATH` unset (stateless mode):** `bot/clients.py` sets `store = None` and prints a one-line startup notice. Each consumer (`history`, `rate_limit`, `preferences`, `dedupe`) checks for `None` at the top of every function and returns safe defaults: history is empty, rate limiting is skipped, `get_provider` returns `DEFAULT_PROVIDER`, `set_provider` returns `False`, dedupe is a no-op. This is the intended Day-1 teaching mode — kids can run the bot locally with only a Telegram token and an AI API key.
- **`SQLITE_PATH` set:** `SqliteStore` opens the DB in WAL mode with `check_same_thread=False`. The schema is a single `kv(key, value, expires_at)` table; expired rows are filtered on read and overwritten on write — no background sweeper, never affects correctness.
- **Graceful degradation under runtime failure:** every store call in the consumer modules is wrapped in try-except. On failure: same fallbacks as stateless mode, plus an error log line.
- **Performance vs. networked KV:** SQLite ops are in-process and take microseconds, vs. ~20–80ms per round-trip to a remote KV over HTTPS. The webhook reply latency for an average message is dominated by the AI call, not storage.

---

## Reliability

- **AI retry logic:** `_call_main()` in `bot/providers.py` retries up to 3 attempts (`AI_RETRIES=2` extra retries) with exponential backoff (1s, 2s) before raising. Handles transient network errors and rate-limit spikes. HF is not retried (it's too slow — a retry would blow the per-request budget).
- **Typing indicator during slow calls:** `keep_typing()` in `bot/helpers.py` spawns a daemon thread that re-sends `send_chat_action(chat_id, "typing")` every 4 seconds (Telegram's typing action expires after ~5s). On context exit the thread is signalled and joined with a 2s timeout so the request shuts down cleanly. Proxy 503s from PA's outbound proxy are caught and logged; the thread keeps looping.

---

## File conversion (`/convertfile`)

`/convertfile` lets a user send a file with the desired output format as the **caption** (e.g. attach a video, caption `mp3`). `cmd_convertfile` prints usage; the content-type handler `handle_file` (registered for `document/audio/video/voice/photo`) does the work: parse target format → size-check (≤20 MB, the Bot API download cap) → download via `get_file`/`download_file` → convert inside a `TemporaryDirectory` → return the result via `send_document`.

The engine lives in `bot/fileconvert.py` and converts **within a family** only:

- **image ↔ image** (png/jpg/webp/bmp/gif/tiff) via **Pillow**
- **media ↔ media** (audio + video are one family, so mp4→mp3 works) via **ffmpeg**
- **doc ↔ doc** (pdf/docx/txt/md → pdf/docx/txt) — **text only**: extract text, then re-emit. Layout, images, and fonts are lost. PDF output uses fpdf2's Latin-1 core font, so non-Latin text (e.g. Armenian) is replaced with `?` unless a Unicode TTF is bundled via `add_font()`.

Cross-family requests (e.g. `pdf`→`mp3`) raise `ConversionError` with a clear message. `convert()` validates family / target / source **before** importing any heavy lib, so those checks are testable without the optional deps installed.

**Crash-safety:** Pillow / pypdf / python-docx / fpdf / ffmpeg are imported **lazily inside** the conversion functions, never at module top level. `bot/fileconvert.py` imports only stdlib at import time, so `bot.handlers` (which does `from bot import fileconvert`) still boots and every other command keeps working even if the conversion deps aren't installed — a conversion attempt just replies "not available (missing X)".

**ffmpeg via pip:** the media path uses `imageio_ffmpeg.get_ffmpeg_exe()` (a static ffmpeg binary shipped inside the pip wheel), falling back to `ffmpeg` on PATH. This avoids depending on a system ffmpeg or adding an entry to PA's outbound whitelist. `subprocess.run([...])` is always called with a list (never `shell=True`) and a `FFMPEG_TIMEOUT` so a pathological file can't wedge the worker.

**Deploy note:** these deps are in `requirements.txt`, but `/api/deploy` only runs `git pull` + touch — it does **not** `pip install`. After pulling this on PA, run `pip install -r requirements.txt` in the virtualenv and reload the web app, or `/convertfile` will report the libraries as missing.

---

## PythonAnywhere deployment

The deployment target is `https://<your-pa-username>.pythonanywhere.com`. The same Flask app at `api/index.py` runs via a long-lived WSGI worker — no serverless cold-start considerations, no function timeout caps.

**PA wiring** (manual one-time setup, no CLI equivalent):
- PA's WSGI file at `/var/www/<your-pa-username>_pythonanywhere_com_wsgi.py` adds the project to `sys.path` and does `from pythonanywhere_wsgi import application`
- `.env` is uploaded to the PA project directory (read by `pythonanywhere_wsgi.py` at worker startup using the same minimal loader as `run_local.py`)
- Webhook registration is a one-off `curl setWebhook` against `https://<your-pa-username>.pythonanywhere.com/api/webhook`

**Re-deploying after a `git pull`:** PA workers don't auto-reload. Either click "Reload" on the Web tab, or `touch /var/www/<your-pa-username>_pythonanywhere_com_wsgi.py` in a Bash console (changing the WSGI file's mtime triggers a worker reload).

**First-time deploy automation.** `scripts/pa_deploy.sh` (run via `make deploy-pa`) drives the full first-time setup from the local terminal: creates the web app via `POST /api/v0/user/<u>/webapps/`, finds or creates a bash console (the only step requiring a one-time browser visit — PA initializes new consoles only after they're loaded in the browser), then `send_input`s `git clone`, `python3.13 -m venv`, and `pip install -r requirements.txt`. It then uploads `.env` to `<PROJECT_DIR>/.env` and the WSGI shim to `/var/www/<u>_pythonanywhere_com_wsgi.py` via the Files API, `PATCH`es `source_directory` + `virtualenv_path` on the web app, and reloads. Required `.env` vars: `PA_USERNAME`, `PA_API_TOKEN` (in addition to the regular bot vars). Idempotent — re-running heals partial state. For ongoing updates the GitHub Actions workflow (`.github/workflows/deploy.yml` → `/api/deploy`) is still preferred; the script is for first-time setup + recovery.

**Console output polling.** `pa_deploy.sh::run_remote` wraps every command it sends as `{ cmd; } && echo <marker>_'OK' || echo <marker>_'FAIL'`, then polls `GET /consoles/<id>/get_latest_output/` every 3s until either marker appears (or it times out). The quoted `'OK'`/`'FAIL'` suffixes keep the echoed *input* line from matching the grep — only the executed echo produces the contiguous marker — so success isn't declared early or on a failed command. Cloning uses an HTTPS URL derived from the origin remote (PA consoles have no SSH key for GitHub).

**Auto-deploy on push to main.** When `DEPLOY_SECRET` is set in PA's `.env`, the `/api/deploy` endpoint accepts authenticated POSTs that converge the checkout to origin and reload the worker: `git fetch origin` + `git reset --hard origin/<branch>` (NOT `git pull --ff-only` — a pull wedges permanently once the server worktree diverges via a hand-edited file or a force-push, and every later deploy 500s while the bot keeps running old code; reproduced live 2026-07-02). Untracked files (`.env`, `.webhook_secret`, `.deploy.lock`, `bot.db`) survive the reset; there is deliberately no `git clean`. Consequence: edits to TRACKED files made directly on PA are discarded by the next deploy — the PA checkout is a deploy target, not a workspace. If the deploy changed `requirements.txt`, the endpoint runs `<venv>/bin/pip install -r requirements.txt` (venv found via `sys.prefix`) before reloading, and refuses to reload (500, old worker keeps serving) if pip fails. The WSGI-touch outcome is always reported in the response body — a missing WSGI file yields a loud "worker was NOT restarted" warning instead of the old silent skip; `_pa_wsgi_path()` resolves via `PA_WSGI_PATH` env → `$USER`/`$LOGNAME` → `pwd.getpwuid` → `/home/<user>/` prefix of the checkout → unambiguous `/var/www/*_pythonanywhere_com_wsgi.py` glob. `.github/workflows/deploy.yml` triggers on push to `main` using two repo secrets (`DEPLOY_SECRET`, `PA_DEPLOY_URL`), retries the curl through PA proxy blips (idempotent server side makes retries safe), then polls `/api/health` until the pushed commit's SHA is actually being served — a green run means the new code is LIVE, not merely that the server said OK. The endpoint fails-closed (403) when `DEPLOY_SECRET` is unset and uses `hmac.compare_digest` for secret comparison. The workflow skips with a warning when its secrets aren't set, so this is fully optional. `/api/health` returns `OK <short-sha>`, with the SHA captured at worker boot — it identifies the code the worker is *running*, which is what makes the verification step truthful.

**Auto webhook registration.** When `WEBHOOK_URL` is set, `pythonanywhere_wsgi.py` calls `bot.clients.register_webhook()` at worker boot, and `/api/deploy` calls it again after every deploy. Both call `bot.set_webhook(url=WEBHOOK_URL, secret_token=WEBHOOK_SECRET)` with up to 3 attempts (1s/2s backoff) because PA's outbound proxy 503-blips transiently (a boot-time registration was seen failing on such a blip on 2026-06-29). Failures are caught and logged — never crash the worker. This eliminates the manual `curl setWebhook` step from the deploy guide.

**Auto webhook-secret bootstrap.** If `WEBHOOK_SECRET` is unset, `bot/config.py::_bootstrap_webhook_secret()` generates a 64-hex-character random secret and persists it in `.webhook_secret` at the project root (gitignored, chmod 0600). Subsequent boots read it back. The auto-registration above then passes it to Telegram via `secret_token`, so the bot is signed-by-default with zero manual setup. A read-only mount or other FS error falls back to an empty secret (unsigned webhook) rather than crashing the worker. To rotate: delete `.webhook_secret` and reload — boot generates a new one and re-registers. Tests must set `WEBHOOK_SECRET` in env (conftest.py does this) so the bootstrap doesn't litter the working tree.

**Critical PA-specific constraints:**
- **Free-tier outbound HTTPS whitelist.** `api.telegram.org`, `api.cerebras.ai`, `huggingface.co` are all on it. Most other domains aren't — if you add a feature that calls a new service, check `https://www.pythonanywhere.com/whitelist/` first. To request a new domain be added, post on the PA forums.
- **Monthly renewal.** Free-tier web apps expire roughly every month. PA emails a week before. The user must click "Run until N days from today" in the Web tab to extend. There is no API endpoint for this on free tier — it must be done in the browser (or via paid plan upgrade).
- **No SSH, no scheduled tasks on free tier.** Automation against PA is limited to the HTTP API for files/webapps/consoles, and consoles require a one-time browser visit before the API can send_input. Don't promise full hands-off automation.
- **One webhook per bot token.** If you ever run `make run` locally, the production webhook is removed. Re-register it after by running `setWebhook` again — see README Step 12.

---

## Known gotchas

- **`threaded=False` is required** — see "How the bot works" above
- **Cerebras model names** — exact ID strings are required (e.g. `gpt-oss-120b`); a wrong format causes a 404. Check https://inference-docs.cerebras.ai/models for current IDs
- **Telegram 4096 char limit** — `send_reply()` in `bot/helpers.py` handles splitting automatically
- **Group chats** — `should_respond()` returns `True` for all messages, so the bot replies to every message in any chat it's in. If you need mention-gated or reply-gated behavior in groups, reintroduce it in `bot/helpers.py::should_respond`. The handler still strips `@<bot_username>` from text before sending to the AI
- **Webhook secret must match** — if `WEBHOOK_SECRET` is set, the same value must be passed as `secret_token` in `setWebhook`. Mismatch causes all updates to return 403 and the bot goes silent
- **Don't hand-edit tracked files on PA** — every `/api/deploy` runs `git reset --hard origin/<branch>`, so server-side edits to tracked files are silently discarded on the next push. Untracked files (`.env`, `.webhook_secret`, `bot.db`) are safe. Change code via git, always
- **`/api/health` body is `OK <short-sha>`** — the deploy workflow string-matches this prefix to verify a deploy went live. Scripts should check the HTTP status or the `OK ` prefix, never exact body equality
- **PA expects WSGI to expose `application`** — `pythonanywhere_wsgi.py` does `from api.index import app as application`. Renaming the Flask app variable would break this
- **Formatter strips unused imports between Edit calls** — if you do a two-step rewrite (add an import in one Edit, use it in the next), the formatter may remove the "unused" import between calls. Combine them into one Edit, or re-add the import after the second Edit
- **`fcntl` is POSIX-only** — `api/index.py` guards `import fcntl` with `try/except ImportError` and routes its `/api/deploy` flock through `_lock_deploy_nb`/`_unlock_deploy` (no-ops without fcntl). A bare `import fcntl` breaks every test that imports `api.index` on Windows. Don't reintroduce one
- **Windows `make.ps1 install` + the Microsoft Store Python stub** — typing `py`/`python` on Windows can hit a Store "app execution alias": a 0-byte stub under `%LOCALAPPDATA%\Microsoft\WindowsApps` that exits 0 and creates nothing. So `Get-Command py` succeeding (or `py -m venv` returning 0) proves nothing. `make.ps1`'s `New-RepoVenv` tries `py`→`python`→`python3` and keeps the first whose run actually produces `.venv\Scripts\python.exe`; don't "simplify" it back to a single `Get-Command` check. A student whose `python --version` works can still hit the old failure because the script tried `py` (the stub) first
