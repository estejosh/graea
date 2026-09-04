"""Demo python-telegram-bot (v21+, async) with planted, togglable bugs.

Graea's own demo target: a small bot whose behavior can be intentionally
broken via the `BUGS` env var so scenarios under `graea/demo/scenarios/`
can exercise every assertion kind against a bot that is sometimes buggy and
sometimes not.

Run:
    DEMO_BOT_TOKEN=... BUGS=raw_markdown,dead_button python -m graea.demo.buggy_bot

Env:
    DEMO_BOT_TOKEN   Telegram bot token from BotFather. Required.
    BUGS             Comma-separated list of bug names to enable. Empty/unset
                      means every flow behaves correctly.

Planted bugs (see docs/SPEC.md "Demo bot"):
    raw_markdown           /start text sent with no parse_mode; asterisks show literally.
    truncated_button       first /start button label is 60 characters long.
    edit_as_new             pressing "Order" sends a new message instead of editing.
    missing_caption         the "Status" photo is sent without a caption.
    silent_command          /help (and the inline "Help" action) never reply.
    dead_button             callback queries are never answered -> stuck spinner.
    wrong_keyboard_shape    /start keyboard is 3 rows x 1 col instead of 1 row x 3 cols.
"""
from __future__ import annotations

import io
import logging
import os
from typing import Optional

from PIL import Image
from telegram import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.helpers import escape_markdown

logger = logging.getLogger("graea.demo.buggy_bot")

ALL_BUGS: tuple[str, ...] = (
    "raw_markdown",
    "truncated_button",
    "edit_as_new",
    "missing_caption",
    "silent_command",
    "dead_button",
    "wrong_keyboard_shape",
)

HELP_TEXT = (
    "Graea demo bot commands:\n"
    "/start - show the welcome message and buttons\n"
    "/help - show this help\n"
    "/menu - show a reply keyboard with A / B / C"
)

BUTTON_LABELS = ["Order", "Status", "Help"]
BUTTON_DATA = ["order", "status", "help"]

ORDER_PLACED_TEXT = "Order placed ✅"
STATUS_CAPTION = "Current status"


def active_bugs() -> set[str]:
    """Return the set of bug names requested via the BUGS env var."""
    raw = os.environ.get("BUGS", "")
    return {b.strip() for b in raw.split(",") if b.strip()}


# ---------------------------------------------------------------------------
# Pure, Telegram-object-free logic — unit-testable without a bot connection.
# ---------------------------------------------------------------------------


def build_start_keyboard(bugs: set[str]) -> InlineKeyboardMarkup:
    """Build the /start inline keyboard.

    Normally one row of three buttons ["Order", "Status", "Help"].
    `wrong_keyboard_shape` makes it three rows of one button instead.
    `truncated_button` makes the first label 60 characters long.
    """
    labels = list(BUTTON_LABELS)
    if "truncated_button" in bugs:
        pad = "x" * (60 - len(labels[0]))
        labels[0] = labels[0] + pad
        assert len(labels[0]) == 60

    buttons = [
        InlineKeyboardButton(text=label, callback_data=data)
        for label, data in zip(labels, BUTTON_DATA)
    ]

    if "wrong_keyboard_shape" in bugs:
        rows = [[b] for b in buttons]
    else:
        rows = [buttons]
    return InlineKeyboardMarkup(rows)


def build_menu_keyboard() -> ReplyKeyboardMarkup:
    """Reply keyboard for /menu: [["A", "B"], ["C"]]."""
    return ReplyKeyboardMarkup([["A", "B"], ["C"]], resize_keyboard=True, one_time_keyboard=False)


def build_start(bugs: set[str]) -> dict:
    """Build the /start message payload.

    Returns {"text": str, "parse_mode": ParseMode|None, "reply_markup": InlineKeyboardMarkup}.
    Correctly escapes MarkdownV2 (keeping the intentional *bold* markers) unless
    `raw_markdown` is active, in which case parse_mode is None so `*bold*`
    literally shows as asterisks.
    """
    prefix = "Welcome to "
    bold = "Graea Demo Bot"
    suffix = "! Use the buttons below."

    if "raw_markdown" in bugs:
        text = f"{prefix}*{bold}*{suffix}"
        parse_mode: Optional[str] = None
    else:
        text = (
            f"{escape_markdown(prefix, version=2)}"
            f"*{escape_markdown(bold, version=2)}*"
            f"{escape_markdown(suffix, version=2)}"
        )
        parse_mode = ParseMode.MARKDOWN_V2

    return {
        "text": text,
        "parse_mode": parse_mode,
        "reply_markup": build_start_keyboard(bugs),
    }


def build_status(bugs: set[str]) -> dict:
    """Build the Status photo payload: {"caption": str|None}.

    `missing_caption` omits the caption entirely.
    """
    return {"caption": None if "missing_caption" in bugs else STATUS_CAPTION}


def generate_status_photo_bytes() -> bytes:
    """Generate a small PNG in memory (Pillow) used for the Status photo."""
    img = Image.new("RGB", (240, 120), color=(70, 130, 180))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Handlers (python-telegram-bot glue around the pure functions above)
# ---------------------------------------------------------------------------


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    bugs: set[str] = context.bot_data["bugs"]
    built = build_start(bugs)
    await update.effective_message.reply_text(
        text=built["text"],
        parse_mode=built["parse_mode"],
        reply_markup=built["reply_markup"],
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    bugs: set[str] = context.bot_data["bugs"]
    if "silent_command" in bugs:
        return
    await update.effective_message.reply_text(HELP_TEXT)


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Choose one:", reply_markup=build_menu_keyboard()
    )


async def _maybe_answer(query: CallbackQuery, bugs: set[str]) -> None:
    if "dead_button" in bugs:
        return
    await query.answer()


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    bugs: set[str] = context.bot_data["bugs"]
    data = query.data
    chat_id = query.message.chat_id

    await _maybe_answer(query, bugs)

    if data == "order":
        if "edit_as_new" in bugs:
            await context.bot.send_message(chat_id=chat_id, text=ORDER_PLACED_TEXT)
        else:
            await query.edit_message_text(ORDER_PLACED_TEXT)
    elif data == "status":
        status = build_status(bugs)
        photo_bytes: bytes = context.bot_data["status_photo"]
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=io.BytesIO(photo_bytes),
            caption=status["caption"],
        )
    elif data == "help":
        if "silent_command" in bugs:
            return
        await context.bot.send_message(chat_id=chat_id, text=HELP_TEXT)


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.effective_message.text or "").strip()
    if text in ("A", "B", "C"):
        await update.effective_message.reply_text(f"You chose {text}")
    else:
        await update.effective_message.reply_text(f"You said: {text}")


def build_application(token: str, bugs: set[str]) -> Application:
    """Assemble the Application with handlers and shared bot_data wired up."""
    application = Application.builder().token(token).build()
    application.bot_data["bugs"] = bugs
    application.bot_data["status_photo"] = generate_status_photo_bytes()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("menu", menu_command))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    return application


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    token = os.environ.get("DEMO_BOT_TOKEN")
    if not token:
        raise SystemExit("DEMO_BOT_TOKEN env var is required")

    bugs = active_bugs()
    unknown = bugs - set(ALL_BUGS)
    if unknown:
        logger.warning("Unknown bug name(s) in BUGS, ignoring: %s", ", ".join(sorted(unknown)))

    print(f"[buggy_bot] active bugs: {', '.join(sorted(bugs)) if bugs else '(none)'}")

    application = build_application(token, bugs)
    application.run_polling()


if __name__ == "__main__":
    main()
