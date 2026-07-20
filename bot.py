import os
import logging
import random
import urllib.parse

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
import httpx

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Style presets -> extra keywords appended to the user's prompt
STYLES = {
    "realistic": "photorealistic, 8k, highly detailed, sharp focus",
    "anime": "anime style, studio ghibli, vibrant colors, cel shaded",
    "3d": "3d render, octane render, unreal engine, cinematic lighting",
    "painting": "oil painting, fine art, brush strokes, canvas texture",
    "cyberpunk": "cyberpunk, neon lights, futuristic city, blade runner style",
    "none": "",
}

STYLE_LABELS = {
    "realistic": "📷 Realistic",
    "anime": "🎨 Anime",
    "3d": "🧊 3D Render",
    "painting": "🖌️ Painting",
    "cyberpunk": "🌆 Cyberpunk",
    "none": "🚫 No Style",
}

# Simple in-memory storage: {user_id: last_prompt}
LAST_PROMPT = {}


# ---------------------------------------------------------------------------
# Image generation (Pollinations.ai - free, no API key required)
# ---------------------------------------------------------------------------
async def generate_image(prompt: str, seed: int | None = None) -> bytes:
    encoded_prompt = urllib.parse.quote(prompt)
    seed = seed if seed is not None else random.randint(0, 999_999)
    url = (
        f"https://image.pollinations.ai/prompt/{encoded_prompt}"
        f"?width=1024&height=1024&seed={seed}&nologo=true"
    )
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.content


def style_keyboard(prefix: str = "style") -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(label, callback_data=f"{prefix}:{key}")
        for key, label in STYLE_LABELS.items()
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(rows)


def result_keyboard(seed: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔁 Regenerate", callback_data=f"regen:{seed}"),
                InlineKeyboardButton("✨ Variation", callback_data=f"vary:{seed}"),
            ]
        ]
    )


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to *AwwaluBot*!\n\n"
        "I generate AI images from your text prompts.\n\n"
        "Usage:\n"
        "`/imagine a dragon flying over a futuristic city`\n\n"
        "I'll then ask you to pick a style, and generate your image ✨",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*Commands:*\n"
        "/imagine <prompt> — generate an image\n"
        "/help — show this message",
        parse_mode="Markdown",
    )


async def imagine(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Please add a prompt.\nExample: `/imagine a cat astronaut in space`",
            parse_mode="Markdown",
        )
        return

    prompt = " ".join(context.args)
    user_id = update.effective_user.id
    LAST_PROMPT[user_id] = prompt

    await update.message.reply_text(
        f"Prompt: _{prompt}_\n\nPick a style:",
        parse_mode="Markdown",
        reply_markup=style_keyboard(),
    )


async def on_style_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    style_key = query.data.split(":", 1)[1]
    base_prompt = LAST_PROMPT.get(user_id)

    if not base_prompt:
        await query.edit_message_text("Prompt expired — please run /imagine again.")
        return

    full_prompt = base_prompt
    if STYLES.get(style_key):
        full_prompt = f"{base_prompt}, {STYLES[style_key]}"

    await query.edit_message_text(f"🎨 Generating: _{base_prompt}_", parse_mode="Markdown")
    await context.bot.send_chat_action(chat_id=query.message.chat_id, action=ChatAction.UPLOAD_PHOTO)

    seed = random.randint(0, 999_999)
    try:
        image_bytes = await generate_image(full_prompt, seed=seed)
    except Exception as e:
        logger.exception("Image generation failed")
        await query.message.reply_text(f"❌ Generation failed: {e}")
        return

    # store full prompt for regenerate/variation buttons
    context.bot_data[f"prompt:{seed}"] = full_prompt

    await query.message.reply_photo(
        photo=image_bytes,
        caption=f"✅ Done!\nPrompt: {base_prompt}",
        reply_markup=result_keyboard(seed),
    )


async def on_regen_or_vary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action, seed_str = query.data.split(":", 1)
    old_seed = int(seed_str)
    full_prompt = context.bot_data.get(f"prompt:{old_seed}")

    if not full_prompt:
        await query.message.reply_text("This prompt has expired — please run /imagine again.")
        return

    await context.bot.send_chat_action(chat_id=query.message.chat_id, action=ChatAction.UPLOAD_PHOTO)

    # regenerate = same seed feel (new random anyway on this free API), vary = definitely new seed
    new_seed = old_seed if action == "regen" else random.randint(0, 999_999)
    try:
        image_bytes = await generate_image(full_prompt, seed=new_seed + (0 if action == "regen" else 1))
    except Exception as e:
        logger.exception("Image generation failed")
        await query.message.reply_text(f"❌ Generation failed: {e}")
        return

    context.bot_data[f"prompt:{new_seed}"] = full_prompt

    await query.message.reply_photo(
        photo=image_bytes,
        caption="✅ Here's another one!",
        reply_markup=result_keyboard(new_seed),
    )


async def fallback_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Treat any plain text message as a prompt too, for convenience
    context.args = update.message.text.split()
    await imagine(update, context)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is not set.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("imagine", imagine))
    app.add_handler(CallbackQueryHandler(on_style_chosen, pattern=r"^style:"))
    app.add_handler(CallbackQueryHandler(on_regen_or_vary, pattern=r"^(regen|vary):"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_text))

    logger.info("AwwaluBot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
