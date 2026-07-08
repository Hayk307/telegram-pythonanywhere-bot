import json
import mimetypes
import os
import random
import tempfile
from datetime import datetime
from urllib.parse import quote

import requests

from pyexpat.errors import messages

from bot.clients import bot, BOT_INFO, store
from bot.config import (
    COMMIT_SHA,
    GOOGLE_API_KEY,
    GOOGLE_CSE_ID,
    HF_EDIT_SPACE_ID,
    HF_SPACE_ID,
    HF_TOKEN,
    HOSTING_LABEL,
    MODEL,
    RATE_LIMIT,
    SYSTEM_PROMPT,
)
from bot.ai import ask_ai
from bot.providers import generate
from bot.helpers import is_allowed, keep_typing, send_reply, should_respond
from bot.history import clear_history
from bot.preferences import get_provider, set_provider
from bot.rate_limit import is_rate_limited
from bot import fileconvert

# Verbose console logging for local dev and teaching. Enabled by
# BOT_VERBOSE_LOG=1 (run_local.py sets this automatically). Prints one
# line per inbound/outbound message so kids and teachers can see the
# conversation flow in their terminal while the bot is running.
VERBOSE_LOG = os.environ.get("BOT_VERBOSE_LOG", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)


def _log(message, direction: str, text: str) -> None:
    """Print a one-line trace of a message in verbose mode.

    direction is "in" (user → bot) or "out" (bot → user). Text is
    truncated to 500 characters so long AI replies don't flood the
    terminal. Newlines are collapsed for single-line readability.
    """
    if not VERBOSE_LOG:
        return
    user = message.from_user
    user_name = (
        f"@{user.username}" if user.username else (user.first_name or f"user:{user.id}")
    )
    bot_name = f"@{BOT_INFO.username}"
    snippet = (text or "").replace("\n", " ").replace("\r", " ")
    if len(snippet) > 500:
        snippet = snippet[:500] + "..."
    if direction == "in":
        sender, receiver = user_name, bot_name
    else:
        sender, receiver = bot_name, user_name
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {sender} → {receiver}: {snippet}", flush=True)


@bot.message_handler(commands=["start"], func=is_allowed)
def cmd_start(message):
    bot.send_message(
        message.chat.id,
        "👋 Hello! I'm your AI assistant, ready to get started!\n\n"
        "💬 Just send me a message to chat, or try one of my commands — "
        "type /help to see everything I can do.",
    )



@bot.message_handler(commands=["help"], func=is_allowed)
def cmd_help(message):
    lines = [
        "🤖 *How to use this bot*",
        "",
        "Just send me a message — a question, a task, or anything you're "
        "curious about — and I'll reply. 💬 No commands needed for normal chat.",
        "",
        "🧠 I remember our recent conversation, so you can ask follow-up "
        "questions and I'll keep the context. Use /reset to start fresh.",
        "",
        "*Commands*",
        "👋 /start — Welcome message",
        "❓ /help — Show this message",
        "🔄 /reset — Clear our conversation and start over",
        "🧹 /clear — Delete recent chat messages, keep saved notes (last 48h only)",
        "ℹ️ /about — See my personality and what powers me",
        "#️⃣ /sha — Show the live git commit SHA",
        "😂 /joke — Tell a joke",
        "💡 /fact — Tell one interesting fact",
        "🌟 /compliment — Brighten your day",
        "📜 /quote — Get a unique, one-line inspiring quote",
        "🎲 /roll — Roll a dice",
        "🔥 /roast — Get a short, playful, friendly roast for you or a friend",
        "🔍 /review — Paste code in any language and I'll point out the mistake",
        "🔀 /convert — Translate code into another language: /convert <language> <code>",
        "📎 /convertfile — Convert a file (image/audio/video/doc); send it with the target format as the caption",
        "✍️ /doc — Add comments to your code: /doc <language> <code>",
        "💱 /currency — Convert money or crypto: /currency 50$ to amd",
        "🎓 /explain — Explain a topic or term simply: /explain recursion",
        "🌀 /image — Real photo of a real subject, or a generated one: /image Eiffel Tower",
        "✏️ /edit — Edit a photo with a prompt; send/reply to a photo with /edit make it winter",
        "📝 /remember — Save a quick note for the AI to remember",
        "📖 /recall — List all the notes you've saved",
        "🗑️ /forget — Clear all your saved notes",
    ]
    if HF_SPACE_ID:
        lines.append("🔧 /model — switch which AI answers you")
    bot.send_message(message.chat.id, "\n".join(lines), parse_mode="Markdown")


@bot.message_handler(commands=["reset"], func=is_allowed)
def cmd_reset(message):
    clear_history(message.from_user.id)
    bot.send_message(message.chat.id, "🔄 Conversation cleared. Starting fresh! ✨")


# /clear walks backwards from its own message id, deleting each message in the
# private chat (Telegram message ids are sequential per chat). Telegram refuses
# to delete messages older than 48h — and already-deleted / non-existent ids —
# which raise, so we stop after a run of consecutive failures (we've reached the
# un-deletable past) and never scan more than a bounded window regardless.
CLEAR_SCAN_LIMIT = 100
CLEAR_MISS_LIMIT = 10


@bot.message_handler(commands=["clear"], func=is_allowed)
def cmd_clear(message):
    # In a group this would try to delete other people's messages (and needs
    # admin rights), so only run it in a private chat with the user.
    if getattr(message.chat, "type", "private") != "private":
        bot.send_message(
            message.chat.id, "🧹 /clear only works in a private chat with me."
        )
        return
    chat_id = message.chat.id
    deleted = 0
    misses = 0
    mid = message.message_id
    for _ in range(CLEAR_SCAN_LIMIT):
        if mid <= 0:
            break
        try:
            bot.delete_message(chat_id, mid)
            deleted += 1
            misses = 0
        except Exception:
            # Too old (>48h), already gone, or otherwise not deletable —
            # expected as we walk into the past. Stop once we've clearly run
            # past the deletable window.
            misses += 1
            if misses >= CLEAR_MISS_LIMIT:
                break
        mid -= 1
    # Reset the AI's conversation memory too, but leave /remember notes alone —
    # those live under a separate key and are only cleared by /forget.
    clear_history(message.from_user.id)
    if deleted:
        summary = f"🧹 Cleared {deleted} recent message(s) and reset our conversation."
    else:
        summary = "🧹 Reset our conversation. (No recent messages I could delete.)"
    bot.send_message(
        chat_id,
        f"{summary} Your saved notes are safe — use /recall to see them.",
    )


@bot.message_handler(commands=["joke"], func=is_allowed)
def cmd_joke(message):
    with keep_typing(message.chat.id):
        reply = ask_ai(message.from_user.id, "Tell one short, clean programming joke.")
    bot.send_message(message.chat.id, reply)

@bot.message_handler(commands=["compliment"], func=is_allowed)
def cmd_compliment(message):
    with keep_typing(message.chat.id):
        reply = ask_ai(message.from_user.id, "Brighten your day")
    bot.send_message(message.chat.id, reply)


@bot.message_handler(commands=["quote"], func=is_allowed)
def cmd_quote(message):
    with keep_typing(message.chat.id):
        reply = ask_ai(
            message.from_user.id,
            "Share one unique, inspirational one-line quote.",
        )
    bot.send_message(message.chat.id, reply)

@bot.message_handler(commands=["roll"], func=is_allowed)
def cmd_roll(message):
    result = random.randint(1, 6)
    bot.send_message(message.chat.id, f"🎲 You rolled a {result}!")

@bot.message_handler(commands=["roast"], func=is_allowed)
def cmd_roast(message):
    name = message.text.split(maxsplit=1)[1] if " " in message.text else "you"
    reply = ask_ai(message.from_user.id, f"Write a short, playful, friendly roast of {name}.")
    bot.send_message(message.chat.id, reply)


@bot.message_handler(commands=["review"], func=is_allowed)
def cmd_review(message):
    code = message.text.split(maxsplit=1)[1].strip() if " " in message.text else ""
    if not code:
        bot.send_message(
            message.chat.id,
            "🔍 Usage: /review <paste your code>\n\n"
            "Send code in any language and I'll tell you what's wrong with it. 🐛",
        )
        return
    prompt = (
        "You are a patient code reviewer for students. The following code may "
        "contain a bug or mistake. First detect the programming language, then "
        "point out the mistake(s) clearly and concisely: say what is wrong, why "
        "it's wrong, and how to fix it. If the code looks correct, say so. Keep "
        "the explanation short and beginner-friendly.\n\n"
        f"Code:\n{code}"
    )
    with keep_typing(message.chat.id):
        reply = ask_ai(message.from_user.id, prompt)
    send_reply(message, reply)


@bot.message_handler(commands=["convert"], func=is_allowed)
def cmd_convert(message):
    args = message.text.split(maxsplit=1)[1].strip() if " " in message.text else ""
    # First word = target language, everything after it = the code to convert.
    parts = args.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(
            message.chat.id,
            "🔀 Usage: /convert <language> <paste your code>\n\n"
            "Example: /convert python  then your code.\n"
            "I'll translate the code into the language you asked for. 🌐",
        )
        return
    target_language, code = parts[0], parts[1].strip()
    prompt = (
        f"Translate the following code into {target_language}. Keep the same "
        "behavior and logic. Output only the converted code inside a single "
        "code block, followed by a one-line note about anything that doesn't "
        "map cleanly to the target language. Detect the source language "
        "yourself.\n\n"
        f"Code:\n{code}"
    )
    with keep_typing(message.chat.id):
        reply = ask_ai(message.from_user.id, prompt)
    send_reply(message, reply)


@bot.message_handler(commands=["doc"], func=is_allowed)
def cmd_doc(message):
    args = message.text.split(maxsplit=1)[1].strip() if " " in message.text else ""
    # First word = language the user specified, everything after it = the code.
    parts = args.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(
            message.chat.id,
            "✍️ Usage: /doc <language> <paste your code>\n\n"
            "Example: /doc python  then your code.\n"
            "I'll add explanatory comments using that language's comment syntax. 💬",
        )

        return
    language, code = parts[0], parts[1].strip()
    prompt = (
        f"Add clear, beginner-friendly explanatory comments to the following "
        f"{language} code. Use the correct comment syntax for {language} "
        "(for example: # in Python, // in JavaScript/C/Java, -- in SQL/Lua, "
        "# in Ruby/Bash). Do not change the code's logic — only add comments. "
        "Return the fully commented code inside a single code block.\n\n"
        f"Code:\n{code}"
    )
    with keep_typing(message.chat.id):
        reply = ask_ai(message.from_user.id, prompt)
    send_reply(message, reply)


@bot.message_handler(commands=["currency"], func=is_allowed)
def cmd_currency(message):
    request = message.text.split(maxsplit=1)[1].strip() if " " in message.text else ""
    if not request:
        bot.send_message(
            message.chat.id,
            "💱 Usage: /currency <amount and target>\n\n"
            "Examples:\n"
            "💵 /currency 50$ to amd\n"
            "₿ /currency 2 btc to usd\n"
            "💶 /currency 100 eur to yen",
        )
        return
    # Note: the AI has no live market feed, so rates come from its training
    # data and may be stale (crypto especially). The prompt asks it to say so.
    prompt = (
        "You are a currency and crypto conversion helper. The user wants: "
        f"'{request}'. Work out the conversion for the given amount, source, "
        "and target (which may be a fiat currency like USD/EUR/AMD or a crypto "
        "like BTC/ETH). Reply with the converted amount on the first line, then "
        "the approximate exchange rate you used. Because you don't have a live "
        "market feed, add a short note that the rate is approximate and may be "
        "out of date. If the request is unclear, ask what they meant."
    )
    with keep_typing(message.chat.id):
        reply = ask_ai(message.from_user.id, prompt)
    send_reply(message, reply)


@bot.message_handler(commands=["explain"], func=is_allowed)
def cmd_explain(message):
    topic = message.text.split(maxsplit=1)[1].strip() if " " in message.text else ""
    if not topic:
        bot.send_message(
            message.chat.id,
            "🎓 Usage: /explain <topic or term>\n\n"
            "Examples:\n"
            "/explain recursion\n"
            "/explain quantum entanglement\n"
            "/explain how does HTTPS work",
        )
        return
    prompt = (
        f"Explain '{topic}' in a simple, easy-to-understand way, as if talking "
        "to a curious beginner with no background in the subject. Use plain "
        "language, a short everyday analogy if it helps, and keep it concise. "
        "Avoid jargon; if you must use a technical term, explain it too."
    )
    with keep_typing(message.chat.id):
        reply = ask_ai(message.from_user.id, prompt)
    send_reply(message, reply)



# /image — smart image command. It first asks the AI to classify the request:
#   • REAL subject (an identifiable person, place, or thing that has actual
#     photographs — "Albert Einstein", "Eiffel Tower") → retrieve a real photo
#     from the internet and send that.
#   • CREATIVE brief (something imaginary to be made — "a dragon on a
#     skateboard", "watercolor mountains") → GENERATE it with Pollinations
#     (image.pollinations.ai), a free, no-API-key text-to-image service.
#
# Real-photo retrieval tries, in order: (1) a general web image search via
# Google Programmable Search when GOOGLE_API_KEY + GOOGLE_CSE_ID are set —
# broad coverage of arbitrary real subjects; (2) Wikipedia's lead image — no
# key needed, great for famous subjects. If neither finds a usable photo we
# fall through to generation. Every step degrades gracefully: a failed
# classification, a missing photo, or an SVG/non-raster result all fall through
# to generation, and a failed generation ends in a friendly error rather than
# an exception.
#
# NOTE for PythonAnywhere: none of image.pollinations.ai, en.wikipedia.org,
# upload.wikimedia.org, or www.googleapis.com (web search) are on the free-tier
# outbound whitelist by default — request them on the PA forum, or /image will
# time out in production while still working locally.
POLLINATIONS_ENDPOINT = "https://image.pollinations.ai/prompt/"
WIKI_API = "https://en.wikipedia.org/w/api.php"
GOOGLE_SEARCH_API = "https://www.googleapis.com/customsearch/v1"
IMAGE_TIMEOUT = 90  # seconds — Pollinations can be slow under load
WIKI_TIMEOUT = 15  # seconds — Wikipedia is fast; fail over to generation quickly
SEARCH_TIMEOUT = 15  # seconds — web image search + download
IMAGE_WIDTH = 1024
IMAGE_HEIGHT = 1024
# Telegram caps photo captions at 1024 chars; keep well under it.
IMAGE_CAPTION_LIMIT = 900
# Wikipedia's API etiquette asks for a descriptive User-Agent; reused for all
# outbound image fetches so hosts that block empty agents still serve us.
WIKI_USER_AGENT = "telegram-pythonanywhere-bot/1.0 (educational Telegram bot)"


def _classify_image_request(user_id: int, prompt: str):
    """Ask the AI whether `prompt` is a plain depiction of a real subject or a
    creative brief.

    Returns (is_real, subject): is_real True ONLY when the prompt asks for a
    real, identifiable subject exactly as it actually exists (a portrait/photo
    of a person, place, or object), so a real photo can be retrieved. A
    scenario, action, or counterfactual — even one naming real people/things
    ("a Real Madrid player playing for Barcelona") — is a creative brief and
    returns is_real False so the image is generated instead. `subject` is the
    canonical name to look up on Wikipedia. Any AI/parse failure returns
    (False, prompt) so we simply fall back to generating the image.
    """
    messages = [
        {
            "role": "system",
            "content": "You are a precise classifier that replies with only a JSON object.",
        },
        {
            "role": "user",
            "content": (
                "An image bot must decide whether to RETRIEVE a real photograph "
                "or GENERATE a new image for this request.\n\n"
                "Set real=true ONLY when the request is a plain depiction of a "
                "single real, identifiable subject exactly as it actually "
                "exists — essentially a portrait or photo of that subject with "
                "no invented action, setting, or alteration. Examples: 'Albert "
                "Einstein', 'the Eiffel Tower', 'a photo of the Toyota "
                "Corolla'.\n\n"
                "Set real=false when the request describes a SCENE, ACTION, "
                "HYPOTHETICAL, or COMBINATION that would not exist as an actual "
                "photograph — even if it names real people, places, or things. "
                "Examples: 'a Real Madrid player playing for Barcelona' (a "
                "situation that isn't real), 'Einstein riding a skateboard', "
                "'the Eiffel Tower on the moon', 'a dragon on a skateboard', "
                "'watercolor mountains at sunrise'. When unsure, prefer "
                "real=false (generate).\n\n"
                'Reply with ONLY a compact JSON object: {"real": true|false, '
                '"subject": "<if real, the subject\'s common name to search; '
                'otherwise an empty string>"}. No other text.\n\n'
                f"Request: {prompt}"
            ),
        },
    ]
    try:
        raw = generate(user_id, messages).strip()
        # Tolerate ```json fences some models wrap JSON in.
        if raw.startswith("```"):
            raw = raw.strip("`").strip()
            if raw.lower().startswith("json"):
                raw = raw[4:].strip()
        data = json.loads(raw)
        subject = (data.get("subject") or prompt).strip()
        return bool(data.get("real")), subject
    except Exception as e:
        print(f"/image classify failed: {e}")
        return False, prompt


def _download_image(url: str):
    """Download `url` and return its bytes if it's a raster image Telegram can
    send as a photo, else None.

    Rejects non-images and SVG (logos/flags), which send_photo can't render.
    Any network/HTTP error returns None so callers fall through to the next
    source rather than raising.
    """
    try:
        resp = requests.get(
            url, headers={"User-Agent": WIKI_USER_AGENT}, timeout=SEARCH_TIMEOUT
        )
        resp.raise_for_status()
        ctype = resp.headers.get("Content-Type", "")
        if not ctype.startswith("image/") or "svg" in ctype or not resp.content:
            return None
        return resp.content
    except Exception as e:
        print(f"/image download failed for {url!r}: {e}")
        return None


def _search_web_image(subject: str):
    """Return real-photo bytes for `subject` from a general web image search
    via Google Programmable Search, or None.

    Disabled (returns None immediately) unless both GOOGLE_API_KEY and
    GOOGLE_CSE_ID are configured, so the bot keeps working with no key. Asks
    for image results, then downloads the top hit; on no result / bad key /
    network error returns None so the caller falls back to Wikipedia.
    """
    if not (GOOGLE_API_KEY and GOOGLE_CSE_ID and subject):
        return None
    try:
        params = {
            "key": GOOGLE_API_KEY,
            "cx": GOOGLE_CSE_ID,
            "q": subject,
            "searchType": "image",
            "num": 1,
            "safe": "active",
            # Prefer large, photographic results over clip art / icons.
            "imgSize": "large",
            "imgType": "photo",
        }
        resp = requests.get(
            GOOGLE_SEARCH_API,
            params=params,
            headers={"User-Agent": WIKI_USER_AGENT},
            timeout=SEARCH_TIMEOUT,
        )
        resp.raise_for_status()
        items = resp.json().get("items") or []
        if not items:
            return None
        return _download_image(items[0].get("link", ""))
    except Exception as e:
        print(f"/image web search failed: {e}")
        return None


def _wikipedia_image(subject: str):
    """Return real-photo bytes for `subject` via Wikipedia's lead image, or None.

    Uses the MediaWiki pageimages API (generator=search picks the best-
    matching page) to resolve a lead image, prefers the size-bounded
    thumbnail over the full original, then downloads it. Returns None — so
    the caller falls back to generation — when nothing suitable is found.
    """
    if not subject:
        return None
    try:
        params = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": subject,
            "gsrlimit": 1,
            "prop": "pageimages",
            "piprop": "thumbnail|original",
            "pithumbsize": IMAGE_WIDTH,
        }
        meta = requests.get(
            WIKI_API,
            params=params,
            headers={"User-Agent": WIKI_USER_AGENT},
            timeout=WIKI_TIMEOUT,
        )
        meta.raise_for_status()
        pages = (meta.json().get("query") or {}).get("pages") or {}
        image_url = None
        for page in pages.values():
            src = (page.get("thumbnail") or {}).get("source") or (
                page.get("original") or {}
            ).get("source")
            if src:
                image_url = src
                break
        if not image_url:
            return None
        return _download_image(image_url)
    except Exception as e:
        print(f"/image Wikipedia lookup failed: {e}")
        return None


def _fetch_real_photo(subject: str):
    """Return real-photo bytes for `subject`, trying the broadest source first.

    Order: general web image search (Google Programmable Search, when
    configured) → Wikipedia lead image. Returns None if neither yields a
    usable photo, so the caller generates the image instead.
    """
    return _search_web_image(subject) or _wikipedia_image(subject)


def _generate_image(prompt: str):
    """Generate an image from a free-form prompt via Pollinations.

    Returns JPEG bytes, or None on any failure. safe="" percent-encodes the
    whole prompt into the single path segment Pollinations expects.
    """
    url = POLLINATIONS_ENDPOINT + quote(prompt, safe="")
    params = {"width": IMAGE_WIDTH, "height": IMAGE_HEIGHT, "nologo": "true"}
    try:
        resp = requests.get(url, params=params, timeout=IMAGE_TIMEOUT)
        resp.raise_for_status()
        ctype = resp.headers.get("Content-Type", "")
        if not ctype.startswith("image/") or not resp.content:
            raise ValueError(f"unexpected response ({ctype or 'no content-type'})")
        return resp.content
    except Exception as e:
        print(f"/image generation failed: {e}")
        return None


def _clip_caption(text: str) -> str:
    return text if len(text) <= IMAGE_CAPTION_LIMIT else text[:IMAGE_CAPTION_LIMIT] + "…"


@bot.message_handler(commands=["image"], func=is_allowed)
def cmd_image(message):
    prompt = message.text.split(maxsplit=1)[1].strip() if " " in message.text else ""
    if not prompt:
        bot.send_message(
            message.chat.id,
            "🌀 Usage: /image <a real subject or something to create>\n\n"
            "Examples:\n"
            "📷 /image Eiffel Tower — I'll send a real photo\n"
            "📷 /image Albert Einstein — a real photo\n"
            "🌀 /image a neon cyberpunk cat on a skateboard — I'll generate it\n\n"
            "Real people and things get a real photo; imaginary ideas I create. ✨",
        )
        return
    try:
        with keep_typing(message.chat.id):
            is_real, subject = _classify_image_request(message.from_user.id, prompt)
            photo = _fetch_real_photo(subject) if is_real else None
            if photo is not None:
                bot.send_photo(
                    message.chat.id, photo, caption=f"📷 {_clip_caption(subject)}"
                )
                _log(message, "out", f"[real photo] {subject}")
                return
            data = _generate_image(prompt)
        if data is None:
            bot.send_message(
                message.chat.id,
                "⚠️ Couldn't get that image right now. Please try again in a moment.",
            )
            return
        bot.send_photo(message.chat.id, data, caption=f"🌀 {_clip_caption(prompt)}")
        _log(message, "out", f"[generated image] {prompt}")
    except Exception as e:
        print(f"/image failed: {e}")
        bot.send_message(
            message.chat.id,
            "⚠️ Couldn't get that image right now. Please try again in a moment.",
        )


# /edit — true image editing, for FREE. The user supplies a photo (either by
# replying to one with `/edit <prompt>`, or by sending a photo captioned
# `/edit <prompt>`) and a text instruction ("make it winter", "add a hat"). We
# download the photo from Telegram and send it, with the instruction, to a free
# Hugging Face Space (Flux.1 Kontext by default, HF_EDIT_SPACE_ID) via
# gradio_client. The Space edits the actual photo and returns a new image.
#
# Why a HF Space and not an API: there is no free image-*editing* API left —
# Pollinations (which /image uses to *generate*) dropped its edit model to a
# paid tier and its free models ignore the input image, and Google Gemini's
# image model has a zero free-tier quota (needs billing). A community HF Space
# runs the model on donated/ZeroGPU hardware, so it costs the user nothing.
#
# Trade-offs (all handled gracefully, surfaced to the user via _EditError):
#   • Shared + GPU-queued, so it can be slow or briefly unavailable.
#   • Anonymous use has a per-IP quota; set HF_TOKEN (free) to raise it.
#   • On PythonAnywhere's free tier, *.hf.space must be on the outbound
#     whitelist (huggingface.co alone isn't enough), so /edit works locally but
#     may be blocked on PA until that domain is requested.


def _photo_file_id(msg):
    """Return the file_id of the largest photo (or image document) on `msg`,
    or None if the message carries no usable image."""
    if msg is None:
        return None
    if getattr(msg, "photo", None):
        # photo is a list of sizes; the last one is the largest.
        return msg.photo[-1].file_id
    doc = getattr(msg, "document", None)
    if doc and (getattr(doc, "mime_type", "") or "").startswith("image/"):
        return doc.file_id
    return None


class _EditError(Exception):
    """A /edit failure with a user-facing reason (shown after 'Couldn't edit
    that image — '). Raised by `_edit_image` so `_run_edit` can tell the user
    *why* (bad key, no model access, quota, etc.) instead of a vague message."""


def _normalize_for_telegram(data: bytes, mime: str):
    """Return image bytes Telegram's send_photo will render inline.

    The Flux Kontext Space returns WebP, which the photo endpoint rejects
    (it only accepts JPEG/PNG). Convert anything that isn't JPEG/PNG to JPEG
    with Pillow so it shows as a real photo. Pillow is imported lazily (like
    fileconvert) so a missing dep doesn't break import — in that case we return
    the bytes unchanged and _send_edited_image's document fallback delivers them.
    """
    if mime in ("image/jpeg", "image/png"):
        return data, mime
    try:
        from io import BytesIO

        from PIL import Image

        img = Image.open(BytesIO(data)).convert("RGB")
        out = BytesIO()
        img.save(out, format="JPEG", quality=92)
        return out.getvalue(), "image/jpeg"
    except Exception as e:
        print(f"/edit could not convert {mime} to JPEG: {e}")
        return data, mime


def _extract_result_bytes(result):
    """Pull image bytes + mime from a gradio_client `/infer` return value.

    The Space returns `(image, seed)`; `image` is a local file path (gradio
    downloads file outputs for us) or a dict carrying a `path`/`url`. Returns
    `(bytes, mime)` or None if nothing usable is present.
    """
    image = result[0] if isinstance(result, (list, tuple)) and result else result
    path = image.get("path") if isinstance(image, dict) else image
    if isinstance(path, str) and os.path.exists(path):
        with open(path, "rb") as fh:
            return fh.read(), (mimetypes.guess_type(path)[0] or "image/png")
    url = image.get("url") if isinstance(image, dict) else None
    if url:
        data = _download_image(url)
        if data:
            return data, "image/png"
    return None


def _edit_image(prompt: str, image_bytes: bytes, mime_type: str = "image/jpeg"):
    """Edit `image_bytes` per `prompt` via the free Hugging Face editing Space.

    Returns `(edited_bytes, out_mime)` on success, or None when the Space
    returned no usable image. Raises `_EditError` with a user-facing reason on
    any failure (library missing, Space busy / down / over quota) so the real
    cause reaches the user instead of a generic "try again".
    """
    try:
        from gradio_client import Client, handle_file
    except Exception as e:
        print(f"/edit gradio_client import failed: {e}")
        raise _EditError("image editing isn't installed on this bot.") from e

    ext = mimetypes.guess_extension(mime_type) or ".jpg"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, f"input{ext}")
            with open(src, "wb") as fh:
                fh.write(image_bytes)
            client = Client(HF_EDIT_SPACE_ID, hf_token=HF_TOKEN or None)
            result = client.predict(
                input_image=handle_file(src),
                prompt=prompt,
                seed=0,
                randomize_seed=True,
                guidance_scale=2.5,
                steps=28,
                api_name="/infer",
            )
    except Exception as e:
        print(f"/edit HF Space call failed: {e}")
        raise _EditError(
            "the free image editor is busy or unavailable right now — "
            "please try again in a minute."
        ) from e

    extracted = _extract_result_bytes(result)
    if extracted is None:
        print(f"/edit HF Space returned no usable image: {result!r}")
        return None
    return _normalize_for_telegram(*extracted)


def _send_edited_image(message, data: bytes, out_mime: str, prompt: str) -> None:
    """Send the edited image back, guaranteeing delivery.

    Tries `send_photo` first (renders inline). If Telegram rejects the bytes
    as a photo — some formats/dimensions aren't accepted on the photo
    endpoint — we fall back to `send_document`, which accepts any bytes, so
    the user still gets their edited image instead of an error.
    """
    caption = f"✏️ {_clip_caption(prompt)}"
    try:
        bot.send_photo(message.chat.id, data, caption=caption)
    except Exception as e:
        print(f"/edit send_photo failed ({e}); retrying as a document")
        ext = mimetypes.guess_extension(out_mime) or ".png"
        bot.send_document(
            message.chat.id, data, visible_file_name=f"edited{ext}", caption=caption
        )
    _log(message, "out", f"[edited image] {prompt}")


def _run_edit(message, file_id: str, prompt: str) -> None:
    """Shared /edit worker: download the source photo, edit it, reply."""
    # 1. Download the source photo from Telegram.
    try:
        info = bot.get_file(file_id)
        image_bytes = bot.download_file(info.file_path)
        mime = mimetypes.guess_type(info.file_path or "")[0] or "image/jpeg"
        if not mime.startswith("image/"):
            mime = "image/jpeg"
    except Exception as e:
        print(f"/edit download failed: {e}")
        bot.send_message(
            message.chat.id,
            "⚠️ I couldn't fetch that photo from Telegram. Please resend it and try again.",
        )
        return

    # 2. Edit it (the slow call — keep the typing indicator alive).
    try:
        with keep_typing(message.chat.id):
            result = _edit_image(prompt, image_bytes, mime)
    except _EditError as e:
        bot.send_message(message.chat.id, f"⚠️ Couldn't edit that image — {e}")
        return
    except Exception as e:
        print(f"/edit unexpected error during edit: {e}")
        bot.send_message(
            message.chat.id,
            "⚠️ Couldn't edit that image right now. Please try again in a moment.",
        )
        return

    if result is None:
        bot.send_message(
            message.chat.id,
            "⚠️ The model didn't return an image — your request may have been "
            "blocked. Try rewording the edit or using a different photo.",
        )
        return

    # 3. Send the edited image back (guaranteed delivery via document fallback).
    edited_bytes, out_mime = result
    try:
        _send_edited_image(message, edited_bytes, out_mime, prompt)
    except Exception as e:
        print(f"/edit send failed: {e}")
        bot.send_message(
            message.chat.id,
            "⚠️ I edited your image but couldn't send it back. Please try again.",
        )


_EDIT_USAGE = (
    "✏️ Usage: send me a photo and edit it with a prompt.\n\n"
    "Two ways:\n"
    "1️⃣ Send a photo with the caption: /edit make it look like winter\n"
    "2️⃣ Reply to a photo with: /edit turn it into a watercolor painting\n\n"
    "I'll apply your change and send the edited image back. 🎨"
)


@bot.message_handler(commands=["edit"], func=is_allowed)
def cmd_edit(message):
    prompt = message.text.split(maxsplit=1)[1].strip() if " " in message.text else ""
    # The photo must come from the message being replied to (a bare /edit
    # command has no image of its own — the caption path is handled in
    # handle_file, where the photo and its /edit caption arrive together).
    file_id = _photo_file_id(getattr(message, "reply_to_message", None))
    if file_id is None:
        bot.send_message(
            message.chat.id,
            "✏️ I need a photo to edit. Reply to one with /edit <prompt>, or "
            "send a photo captioned /edit <prompt>.\n\n" + _EDIT_USAGE,
        )
        return
    if not prompt:
        bot.send_message(message.chat.id, _EDIT_USAGE)
        return
    _run_edit(message, file_id, prompt)


def _load_notes(user_id: int) -> list:
    """Return the user's saved notes as a list, or [] on any failure.

    Notes are stored under note:{user_id} as a JSON array of strings.
    Tolerates a legacy plain-string value (pre-list format) by wrapping it.
    """
    if store is None:
        return []
    try:
        raw = store.get(f"note:{user_id}")
    except Exception as e:
        print(f"notes load failed: {e}")
        return []
    if not raw:
        return []
    try:
        notes = json.loads(raw)
        return notes if isinstance(notes, list) else [str(notes)]
    except (ValueError, TypeError):
        # Legacy value written before the list format — treat as one note.
        return [raw]


@bot.message_handler(commands=["remember"], func=is_allowed)
def cmd_remember(message):
    note = message.text.split(maxsplit=1)[1].strip() if " " in message.text else ""
    if not note:
        bot.send_message(message.chat.id, "📝 Usage: /remember <something to save>")
        return
    if store is None:
        bot.send_message(
            message.chat.id,
            "⚠️ I can't save notes right now — memory isn't configured.",
        )
        return
    notes = _load_notes(message.from_user.id)
    notes.append(note)
    try:
        store.set(f"note:{message.from_user.id}", json.dumps(notes))
    except Exception as e:
        print(f"/remember store.set failed: {e}")
        bot.send_message(message.chat.id, "❌ Could not save your note. Try again later.")
        return
    bot.send_message(message.chat.id, f"✅ Saved! You now have {len(notes)} note(s).")


@bot.message_handler(commands=["recall"], func=is_allowed)
def cmd_recall(message):
    if store is None:
        bot.send_message(
            message.chat.id,
            "⚠️ I can't recall notes right now — memory isn't configured.",
        )
        return
    notes = _load_notes(message.from_user.id)
    if not notes:
        bot.send_message(
            message.chat.id, "📭 You have no saved notes. Add one with /remember <note>."
        )
        return
    lines = ["📝 Your saved notes:"]
    lines += [f"{i}. {note}" for i, note in enumerate(notes, 1)]
    send_reply(message, "\n".join(lines))


@bot.message_handler(commands=["forget"], func=is_allowed)
def cmd_forget(message):
    if store is None:
        bot.send_message(
            message.chat.id,
            "⚠️ I can't forget notes right now — memory isn't configured.",
        )
        return
    notes = _load_notes(message.from_user.id)
    if not notes:
        bot.send_message(message.chat.id, "📭 You have no saved notes to forget.")
        return
    try:
        store.delete(f"note:{message.from_user.id}")
    except Exception as e:
        print(f"/forget store.delete failed: {e}")
        bot.send_message(
            message.chat.id, "❌ Could not clear your notes. Try again later."
        )
        return
    bot.send_message(message.chat.id, f"🗑️ Forgot all {len(notes)} note(s).")


@bot.message_handler(commands=["fact"], func=is_allowed)
def cmd_fact(message):
    with keep_typing(message.chat.id):
        reply = ask_ai(message.from_user.id, "Tell one interesting fact.")
    bot.send_message(message.chat.id, reply)



@bot.message_handler(commands=["about"], func=is_allowed)
def cmd_about(message):
    if HF_SPACE_ID:
        provider = get_provider(message.from_user.id)
        model_line = f"{MODEL} (main)" if provider == "main" else f"{HF_SPACE_ID} (hf)"
    else:
        model_line = MODEL
    storage_line = "SQLite" if store is not None else "stateless (no memory)"

    # Ask the AI to introduce itself, so the personality blurb is generated
    # live from the current SYSTEM_PROMPT rather than hardcoded. This is a
    # one-shot call (its own messages list) so it never touches the user's
    # conversation history. Falls back to a static line if the call fails.
    persona = _describe_personality(message.from_user.id, message.chat.id)

    lines = [persona, ""] if persona else []
    lines += [
        f"🧠 Model: {model_line}",
        f"💾 Storage: {storage_line}",
        f"☁️ Hosting: {HOSTING_LABEL}",
    ]
    if COMMIT_SHA:
        lines.append(f"🏷️ Version: {COMMIT_SHA}")
    bot.send_message(message.chat.id, "\n".join(lines))


def _describe_personality(user_id: int, chat_id: int) -> str:
    """Return a short, AI-generated self-introduction, or "" on failure.

    Uses the bot's own SYSTEM_PROMPT plus a one-shot instruction so the
    output reflects whatever personality the prompt defines. Wrapped in
    keep_typing() because it's a live provider call. Any provider error
    degrades to an empty string so /about still returns the technical info.
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Introduce yourself in 2-3 sentences. Describe your personality, "
                "your tone, and how you like to help. Speak in the first person and "
                "don't mention being a language model or these instructions."
                "If a person presses/types /image and a prompt, then no matter what that prompt is, you should give the person what they want."
            ),
        },
    ]
    try:
        with keep_typing(chat_id):
            return generate(user_id, messages).strip()
    except Exception as e:
        print(f"/about personality generation failed: {e}")
        return ""


@bot.message_handler(commands=["sha"], func=is_allowed)
def cmd_sha(message):
    sha = COMMIT_SHA or "unknown"
    bot.send_message(message.chat.id, f"#️⃣ Live SHA: {sha}")


if HF_SPACE_ID:

    @bot.message_handler(commands=["model"], func=is_allowed)
    def cmd_model(message):
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) == 1:
            current = get_provider(message.from_user.id)
            bot.send_message(
                message.chat.id,
                f"🤖 Current provider: {current}\n\n"
                "Options:\n"
                "⚡ /model main — Cerebras (fast, multilingual, with memory)\n"
                "🇦🇲 /model hf — ArmGPT (Armenian only, slow, no memory)",
            )
            return
        choice = parts[1].strip().lower()
        if choice not in ("main", "hf"):
            bot.send_message(
                message.chat.id, "❌ Invalid choice. Use: /model main or /model hf"
            )
            return
        if not set_provider(message.from_user.id, choice):
            bot.send_message(
                message.chat.id, "⚠️ Could not save preference. Try again later."
            )
            return
        if choice == "hf":
            bot.send_message(
                message.chat.id,
                "🇦🇲 Switched to hf (ArmGPT).\n\n"
                "ℹ️ Note: this is a tiny base completion model trained only on Armenian text. "
                "It will continue whatever you write rather than answer questions, "
                "and it does not understand English. Replies take ~30-60s and there is no memory.",
            )
        else:
            bot.send_message(message.chat.id, "⚡ Switched to Main Provider.")



FILE_CONTENT_TYPES = ["document", "audio", "video", "voice", "photo"]
# Telegram bots can only download files up to 20 MB via getFile.
MAX_CONVERT_BYTES = 20 * 1024 * 1024


@bot.message_handler(commands=["convertfile"], func=is_allowed)
def cmd_convertfile(message):
    bot.send_message(
        message.chat.id,
        "📎 *Convert a file*\n\n"
        "Send me a file — document, image, audio, or video — and put the "
        "format you want as the *caption*.\n\n"
        "Examples (write this as the file's caption):\n"
        "🎬 `mp3` — pull the audio out of a video\n"
        "🖼️ `png` — convert an image (jpg/png/webp/gif…)\n"
        "📄 `pdf` — convert a text or Word doc (text only)\n\n"
        "I convert *within a family*: image↔image, audio/video↔audio/video, "
        "document↔document — so things like PDF→MP3 aren't possible. Max 20 MB.",
        parse_mode="Markdown",
    )


def _incoming_file(message):
    """Return (file_id, file_name, file_size) for whichever kind of file the
    message carries, or (None, None, None) if there isn't one."""
    if message.content_type == "document" and message.document:
        d = message.document
        return d.file_id, d.file_name, d.file_size
    if message.content_type == "photo" and message.photo:
        # photo is a list of sizes; the last one is the largest.
        p = message.photo[-1]
        return p.file_id, "image.jpg", getattr(p, "file_size", None)
    if message.content_type == "audio" and message.audio:
        a = message.audio
        return a.file_id, a.file_name or "audio.mp3", a.file_size
    if message.content_type == "video" and message.video:
        v = message.video
        return v.file_id, getattr(v, "file_name", None) or "video.mp4", v.file_size
    if message.content_type == "voice" and message.voice:
        vo = message.voice
        return vo.file_id, "voice.ogg", vo.file_size
    return None, None, None


@bot.message_handler(content_types=FILE_CONTENT_TYPES, func=is_allowed)
def handle_file(message):
    # A photo captioned `/edit <prompt>` is an image edit request, not a file
    # conversion. Command handlers only match message.text, and a photo's text
    # lives in message.caption, so we route it here before the convert flow.
    caption = (message.caption or "").strip()
    if caption.startswith("/edit"):
        photo_id = _photo_file_id(message)
        if photo_id is not None:
            prompt = caption.split(maxsplit=1)[1].strip() if " " in caption else ""
            if not prompt:
                bot.send_message(message.chat.id, _EDIT_USAGE)
                return
            _run_edit(message, photo_id, prompt)
            return

    file_id, file_name, file_size = _incoming_file(message)
    if file_id is None:
        return
    target = fileconvert.parse_target_format(message.caption)
    if not target:
        bot.send_message(
            message.chat.id,
            "📎 Add the format you want as the file's caption, e.g. `mp3` or "
            "`pdf`. See /convertfile for details.",
            parse_mode="Markdown",
        )
        return
    if target not in fileconvert.SUPPORTED_TARGETS:
        bot.send_message(
            message.chat.id,
            f"❌ I can't produce .{target} files. I support images "
            "(png/jpg/webp/gif…), audio/video (mp3/mp4/wav…), and documents "
            "(pdf/docx/txt).",
        )
        return
    if file_size and file_size > MAX_CONVERT_BYTES:
        bot.send_message(
            message.chat.id, "❌ That file is too big — I can only handle up to 20 MB."
        )
        return
    source_ext = fileconvert.normalize_ext(file_name)
    try:
        with keep_typing(message.chat.id):
            info = bot.get_file(file_id)
            data = bot.download_file(info.file_path)
            with tempfile.TemporaryDirectory() as tmp:
                in_path = os.path.join(tmp, f"input.{source_ext or 'bin'}")
                with open(in_path, "wb") as handle:
                    handle.write(data)
                out_path = fileconvert.convert(in_path, source_ext, target)
                stem = os.path.splitext(file_name or "converted")[0] or "converted"
                out_name = f"{stem}.{target}"
                with open(out_path, "rb") as result:
                    bot.send_document(
                        message.chat.id, result, visible_file_name=out_name
                    )
    except fileconvert.ConversionError as e:
        bot.send_message(message.chat.id, f"❌ {e}")
    except Exception as e:
        print(f"/convertfile failed: {e}")
        bot.send_message(
            message.chat.id, "❌ Something went wrong converting that file."
        )


@bot.message_handler(content_types=["text"], func=is_allowed)
def handle_message(message):
    if not should_respond(message):
        return
    text = (message.text or "").replace(f"@{BOT_INFO.username}", "").strip()
    if not text:
        # Edited messages, forwards, or stickers-with-empty-caption can
        # arrive with no usable text. Don't burn rate-limit / AI calls on them.
        return
    _log(message, "in", text)
    if is_rate_limited(message.from_user.id):
        limit_msg = f"🚫 You've reached the daily limit of {RATE_LIMIT} messages. Try again tomorrow."
        bot.send_message(message.chat.id, limit_msg)
        _log(message, "out", f"[rate limited] {limit_msg}")
        return
    try:
        with keep_typing(message.chat.id):
            reply = ask_ai(message.from_user.id, text)
        send_reply(message, reply)
        _log(message, "out", reply)
    except Exception as e:
        print(f"Error in handle_message: {e}")
        bot.send_message(message.chat.id, "⚠️ Something went wrong. Please try again.")
        _log(message, "out", f"[error] {e}")
