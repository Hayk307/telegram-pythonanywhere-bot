import json

import os
import random
from datetime import datetime
from bot.clients import bot, BOT_INFO, store
from bot.config import COMMIT_SHA, HF_SPACE_ID, HOSTING_LABEL, MODEL, RATE_LIMIT, SYSTEM_PROMPT
from bot.ai import ask_ai
from bot.providers import generate
from bot.helpers import is_allowed, keep_typing, send_reply, should_respond
from bot.history import clear_history
from bot.preferences import get_provider, set_provider
from bot.rate_limit import is_rate_limited

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
        "Hello! I'm your AI assistant. ready to get started. I have many commands  /help  /about /start  /reset /joke /fact /compliment /quote /roll /roast /review /convert /doc /currency /remember /recall /forget "  ,
    )
    


@bot.message_handler(commands=["help"], func=is_allowed)
def cmd_help(message):
    lines = [
        "🤖 *How to use this bot*",
        "",
        "Just send me a message — a question, a task, or anything you're "
        "curious about — and I'll reply. No commands needed for normal chat.",
        "",
        "I remember our recent conversation, so you can ask follow-up "
        "questions and I'll keep the context. Use /reset to start fresh.",
        "",
        "*Commands*",
        "/start — Welcome message",
        "/help — ~Show this message",
        "/reset — Clear our conversation and start over",
        "/about — See my personality and what powers me",
        "/joke - Tell a joke",
        "/fact - Tell a one interesting fact",
        "/compliment - Brighten your day",
        "/quote - Get a unique, one-line inspiring quote to brighten your day. ",
        "/roll - Roll a dice",
        "/roast - Get a short, playful, and friendly roast for yourself or a friend.",
        "/review - Paste code in any language and I'll point out the mistake.",
        "/convert - Translate code into another language: /convert <language> <code>.",
        "/doc - Add comments to your code: /doc <language> <code>.",
        "/currency - Convert money or crypto: /currency 50$ to amd.",
        "/remember - Save a quick note or text for the AI to remember.",
        "/recall - List all the notes you've saved.",
        "/forget - Clear all your saved notes.",
    ]
    if HF_SPACE_ID:
        lines.append("/model — switch which AI answers you")
    bot.send_message(message.chat.id, "\n".join(lines), parse_mode="Markdown")


@bot.message_handler(commands=["reset"], func=is_allowed)
def cmd_reset(message):
    clear_history(message.from_user.id)
    bot.send_message(message.chat.id, "Conversation cleared. Starting fresh!")


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
            "Usage: /review <paste your code>\n\n"
            "Send code in any language and I'll tell you what's wrong with it.",
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
            "Usage: /convert <language> <paste your code>\n\n"
            "Example: /convert python  then your code.\n"
            "I'll translate the code into the language you asked for.",
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
            "Usage: /doc <language> <paste your code>\n\n"
            "Example: /doc python  then your code.\n"
            "I'll add explanatory comments using that language's comment syntax.",
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
            "Usage: /currency <amount and target>\n\n"
            "Examples:\n"
            "/currency 50$ to amd\n"
            "/currency 2 btc to usd\n"
            "/currency 100 eur to yen",
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
        bot.send_message(message.chat.id, "Usage: /remember <something to save>")
        return
    if store is None:
        bot.send_message(
            message.chat.id,
            "I can't save notes right now — memory isn't configured.",
        )
        return
    notes = _load_notes(message.from_user.id)
    notes.append(note)
    try:
        store.set(f"note:{message.from_user.id}", json.dumps(notes))
    except Exception as e:
        print(f"/remember store.set failed: {e}")
        bot.send_message(message.chat.id, "Could not save your note. Try again later.")
        return
    bot.send_message(message.chat.id, f"Saved! You now have {len(notes)} note(s).")


@bot.message_handler(commands=["recall"], func=is_allowed)
def cmd_recall(message):
    if store is None:
        bot.send_message(
            message.chat.id,
            "I can't recall notes right now — memory isn't configured.",
        )
        return
    notes = _load_notes(message.from_user.id)
    if not notes:
        bot.send_message(
            message.chat.id, "You have no saved notes. Add one with /remember <note>."
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
            "I can't forget notes right now — memory isn't configured.",
        )
        return
    notes = _load_notes(message.from_user.id)
    if not notes:
        bot.send_message(message.chat.id, "You have no saved notes to forget.")
        return
    try:
        store.delete(f"note:{message.from_user.id}")
    except Exception as e:
        print(f"/forget store.delete failed: {e}")
        bot.send_message(
            message.chat.id, "Could not clear your notes. Try again later."
        )
        return
    bot.send_message(message.chat.id, f"Forgot all {len(notes)} note(s).")


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
        f"Model  : {model_line}",
        f"Storage: {storage_line}",
        f"Hosting: {HOSTING_LABEL}",
    ]
    if COMMIT_SHA:
        lines.append(f"Version: {COMMIT_SHA}")
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
            ),
        },
    ]
    try:
        with keep_typing(chat_id):
            return generate(user_id, messages).strip()
    except Exception as e:
        print(f"/about personality generation failed: {e}")
        return ""


if HF_SPACE_ID:

    @bot.message_handler(commands=["model"], func=is_allowed)
    def cmd_model(message):
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) == 1:
            current = get_provider(message.from_user.id)
            bot.send_message(
                message.chat.id,
                f"Current provider: {current}\n\n"
                "Options:\n"
                "/model main — Cerebras (fast, multilingual, with memory)\n"
                "/model hf — ArmGPT (Armenian only, slow, no memory)",
            )
            return
        choice = parts[1].strip().lower()
        if choice not in ("main", "hf"):
            bot.send_message(
                message.chat.id, "Invalid choice. Use: /model main or /model hf"
            )
            return
        if not set_provider(message.from_user.id, choice):
            bot.send_message(
                message.chat.id, "Could not save preference. Try again later."
            )
            return
        if choice == "hf":
            bot.send_message(
                message.chat.id,
                "Switched to hf (ArmGPT).\n\n"
                "Note: this is a tiny base completion model trained only on Armenian text. "
                "It will continue whatever you write rather than answer questions, "
                "and it does not understand English. Replies take ~30-60s and there is no memory.",
            )
        else:
            bot.send_message(message.chat.id, "Switched to Main Provider.")



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
        limit_msg = f"You've reached the daily limit of {RATE_LIMIT} messages. Try again tomorrow."
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
        bot.send_message(message.chat.id, "Something went wrong. Please try again.")
        _log(message, "out", f"[error] {e}")
