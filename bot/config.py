import os
import secrets as _secrets_mod
import subprocess as _subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_WEBHOOK_SECRET_FILE = _PROJECT_ROOT / ".webhook_secret"


def _get_commit_sha() -> str:
    """Return the short SHA of the deployed commit, or an empty string.

    Computed once at module import — so the value reflects the worker's
    actual code, not whatever `git pull` did since boot. The auto-deploy
    flow touches the WSGI file on pull, which spawns a fresh worker on
    the next request with the new SHA. This makes /about a reliable
    "what version is live right now" probe.
    """
    try:
        result = _subprocess.run(
            ["git", "-C", str(_PROJECT_ROOT), "rev-parse", "--short=7", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (_subprocess.SubprocessError, OSError):
        pass
    return ""


COMMIT_SHA = _get_commit_sha()


def _bootstrap_webhook_secret(file_path: Path = _WEBHOOK_SECRET_FILE) -> str:
    """Return WEBHOOK_SECRET from env if set; otherwise read/generate a
    persistent random secret in `file_path`.

    This makes the webhook signed-by-default: a fresh PA deploy with no
    manual `openssl rand` step still rejects forged updates because the
    bot auto-generates and persists a 64-hex-char secret on first run,
    then registers it with Telegram via the boot-time `register_webhook()`.

    Precedence: env var > on-disk file > newly generated. Filesystem
    errors fall back to the empty string so a read-only mount can't
    crash worker boot — the webhook just stays unsigned in that case.
    """
    env_value = os.environ.get("WEBHOOK_SECRET", "").strip()
    if env_value:
        return env_value
    try:
        if file_path.exists():
            existing = file_path.read_text().strip()
            # Empty or whitespace-only file: treat as missing and regenerate,
            # otherwise we'd silently disable webhook auth.
            if existing:
                return existing
        new_secret = _secrets_mod.token_hex(32)
        file_path.write_text(new_secret)
        try:
            os.chmod(file_path, 0o600)
        except OSError:
            pass  # best-effort tightening; Windows / odd mounts can skip
        print(f"Generated webhook secret at {file_path} (auto-bootstrap)")
        return new_secret
    except OSError as e:
        print(f"Could not persist webhook secret ({e}); webhook will be unsigned")
        return ""


# Telegram
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"].strip()
WEBHOOK_SECRET = _bootstrap_webhook_secret()

# When set, the bot auto-registers this URL as the Telegram webhook on
# worker boot and after every /api/deploy. Leave unset for local
# polling (run_local.py). Example value on PA:
#   WEBHOOK_URL=https://<your-pa-username>.pythonanywhere.com/api/webhook
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").strip()

# AI provider
AI_API_KEY = os.environ["AI_API_KEY"].strip()
AI_BASE_URL = os.environ.get("AI_BASE_URL", "https://api.cerebras.ai/v1").strip()
MODEL = os.environ.get("AI_MODEL", "gpt-oss-120b").strip()

# Optional web image search (Google Programmable Search) for the /image
# command's "real photo" path. When BOTH are set, /image searches the web for
# a real photo first and only falls back to Wikipedia / generation otherwise.
# When unset, /image uses Wikipedia as before. Get an API key at
# https://developers.google.com/custom-search/v1/introduction and a search
# engine id (cx) at https://programmablesearchengine.google.com/ (turn on
# "Image search" and "Search the entire web"). Free tier: 100 queries/day.
# On PA, add www.googleapis.com to the outbound whitelist.
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "").strip()
GOOGLE_CSE_ID = os.environ.get("GOOGLE_CSE_ID", "").strip()

# Image editing (/edit) via FREE Hugging Face Spaces (no API key, no billing).
# These are Flux.1 Kontext image-editing Spaces called through gradio_client
# (same library the optional HF chat provider uses). They edit an uploaded
# photo from a text instruction ("add a hat", "make it winter").
#
# HF_EDIT_SPACE_ID is a COMMA-SEPARATED FALLBACK CHAIN: the bot tries each Space
# in order and moves to the next if one is queue-full / down / rate-limited.
# This is the fix for the "free image editor is busy or unavailable" error —
# free Spaces run on shared ZeroGPU, so a single Space alone is unreliable.
# All Spaces in the default chain expose the SAME `/infer` signature
# (input_image, prompt, seed, randomize_seed, guidance_scale, steps), so no
# per-Space adapter code is needed; only add Spaces with that same signature.
#
# Optional HF_TOKEN (free, from huggingface.co/settings/tokens) raises the
# anonymous per-IP quota. On PythonAnywhere's free tier /edit needs *.hf.space
# on the outbound whitelist (huggingface.co alone isn't enough), so it works
# locally but may be blocked on PA until that domain is requested.
HF_EDIT_SPACE_IDS = [
    s.strip()
    for s in os.environ.get(
        "HF_EDIT_SPACE_ID",
        "black-forest-labs/FLUX.1-Kontext-Dev,Yuanshi/FLUX.1-Kontext-Turbo",
    ).split(",")
    if s.strip()
]

# Hugging Face provider (optional) — when set, users can switch via /model
HF_SPACE_ID = os.environ.get("HF_SPACE_ID", "").strip()
HF_TOKEN = os.environ.get("HF_TOKEN", "").strip()  # optional, for private spaces
DEFAULT_PROVIDER = "main"

# Storage — optional. When SQLITE_PATH is unset the bot runs in
# stateless mode: history / rate limiting / preferences / dedupe all
# degrade gracefully (the consumer modules in bot/ check `store is
# None` at the top of every function and return safe defaults).
SQLITE_PATH = os.environ.get("SQLITE_PATH", "").strip()

# Label shown by the /about command. Defaults to "PythonAnywhere" since
# that is the documented deployment target. Override to suit your host.
HOSTING_LABEL = os.environ.get("HOSTING_LABEL", "PythonAnywhere").strip()

# Auto-deploy webhook secret. When set, /api/deploy accepts requests
# that present this value in the X-Deploy-Secret header and runs
# `git pull` + WSGI reload. When unset, /api/deploy returns 403 — the
# endpoint is fail-closed.
DEPLOY_SECRET = os.environ.get("DEPLOY_SECRET", "").strip()

# App
SYSTEM_PROMPT = (
    "You are an incredibly smart AI, always providing clear and precise answers to anyone who asks."
    "You are an expert AI that helps everyone by providing concise, smart solutions without the fluff."
    "Give precise answers tailored directly to the user's needs."
    " Always understand and reply in Russian, regardless of the language the user writes in."
    " Отвечай всегда на русском языке."
)
MAX_HISTORY = 20  # messages kept per user (10 conversation turns)
HISTORY_TTL = 2592000  # conversation history expires after 30 days (seconds)
RATE_LIMIT = int(os.environ.get("RATE_LIMIT", "250"))  # max messages per user per day

# Comma-separated whitelist of Telegram users. Each entry is either a
# username (with or without leading @) or a numeric user_id. Empty
# (default) means everyone can talk to the bot. When non-empty, the
# bot stays silent for anyone not in the list — silence instead of a
# rejection message so scanners don't get confirmation the bot exists.
#
# Example: ALLOWED_USERS=@alice,bob,123456789
ALLOWED_USERS = [
    u.strip().lstrip("@")
    for u in os.environ.get("ALLOWED_USERS", "").split(",")
    if u.strip()
]
MAX_MSG_LEN = 4096  # Telegram's character limit per message
# Provider call budget. Total worst case =
# AI_RETRIES * AI_REQUEST_TIMEOUT + sum of backoff sleeps. With
# retries=2 and timeout=25s plus 1s backoff: 25 + 1 + 25 = 51s.
AI_REQUEST_TIMEOUT = 25  # seconds, applied per-attempt to OpenAI-compatible calls
AI_RETRIES = 2  # total attempts (not extra retries) — 2 means one retry on failure
# HF Gradio request timeout. Without this a hung `predict()` would occupy the
# PA worker indefinitely; combined with the dedupe pre-claim, Telegram's
# retries get silently dropped for ~10 min. Tuned to give ArmGPT enough
# headroom for cold-start jitter while still freeing the worker before
# Telegram's webhook timeout (~60s).
HF_REQUEST_TIMEOUT = 50
