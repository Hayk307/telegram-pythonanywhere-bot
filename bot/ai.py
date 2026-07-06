from bot.config import SYSTEM_PROMPT
from bot.history import get_history, save_history
from bot.providers import generate



def ask_ai(user_id: int, user_message: str) -> str:
    history = get_history(user_id)
    history.append({"role": "user", "content": user_message})

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += history

    reply = generate(user_id, messages)

    history.append({"role": "assistant", "content": reply})
    save_history(user_id, history)
    return reply
