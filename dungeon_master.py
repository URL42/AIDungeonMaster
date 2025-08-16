# dungeon_master.py
import os
import random
import logging
from typing import Dict, Any, List

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

from persistence import GameStateManager, logger as app_logger
from prompt_builder import PromptBuilder

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or os.getenv("DM_TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Set TELEGRAM_BOT_TOKEN (or BOT_TOKEN) (or DM_TELEGRAM_BOT_TOKEN) in your environment/.env")

log = app_logger  # re-use same logger

# --- Dice & modifiers ---------------------------------------------------------
def proficiency_bonus_for_level(level: int) -> int:
    if level >= 17: return 6
    if level >= 13: return 5
    if level >= 9:  return 4
    if level >= 5:  return 3
    return 2

def ability_mod(score: int) -> int:
    return (score - 10) // 2

# --- Helpers to render choices ------------------------------------------------
def build_choice_keyboard(choices: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    rows = []
    for i, c in enumerate(choices):
        label = f"{i+1}. {c.get('text','')[:64]}"
        rows.append([InlineKeyboardButton(label, callback_data=f"choice:{i}")])
    return InlineKeyboardMarkup(rows) if rows else None

async def send_scene_with_choices(update: Update, context: ContextTypes.DEFAULT_TYPE, narrative: str, choices: List[Dict[str, Any]]):
    chat_id = update.effective_chat.id
    gsm: GameStateManager = context.chat_data["gsm"]
    st = gsm.get_state()
    st["last_scene"] = narrative
    st["last_choices"] = choices or []
    gsm.save_state(st)

    if narrative:
        await context.bot.send_message(chat_id, narrative)
    if choices:
        kb = build_choice_keyboard(choices)
        await context.bot.send_message(chat_id, "Your move:", reply_markup=kb)

# --- /start & /help -----------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    context.chat_data["gsm"] = GameStateManager(chat_id)
    gsm: GameStateManager = context.chat_data["gsm"]
    pb = PromptBuilder(gsm)

    st = gsm.get_state()
    # Start fresh intro scene
    opener = pb.build_opening_scene()
    await send_scene_with_choices(update, context, opener.get("narrative",""), opener.get("choices", []))
    await context.bot.send_message(chat_id, "Tip: you can also type your own action, not just tap a choice.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Commands:\n"
        "/start — begin or restart your adventure\n"
        "/ask <question> — ask an out-of-game rules/lore question\n"
        "/roll — roll the pending d20 (if you’ve picked a choice)"
    )

# --- /ask (out-of-game Q&A) ---------------------------------------------------
async def ask_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    gsm: GameStateManager = context.chat_data.get("gsm") or GameStateManager(chat_id)
    context.chat_data["gsm"] = gsm
    pb = PromptBuilder(gsm)
    q = (update.message.text or "").partition(" ")[2].strip()
    if not q:
        await update.message.reply_text("Usage: /ask <your question>")
        return
    ans = pb.build_clarification_prompt(q)
    await update.message.reply_text(ans)

# --- Freeform player input (go rogue) ----------------------------------------
async def user_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()
    if not text:
        return
    gsm: GameStateManager = context.chat_data.get("gsm") or GameStateManager(chat_id)
    context.chat_data["gsm"] = gsm
    pb = PromptBuilder(gsm)

    # continue story from freeform input
    out = pb.build_scene_prompt(text)
    await send_scene_with_choices(update, context, out.get("narrative",""), out.get("choices", []))

# --- Choice selected -> prompt to roll ---------------------------------------
async def on_choice_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    gsm: GameStateManager = context.chat_data.get("gsm") or GameStateManager(chat_id)
    context.chat_data["gsm"] = gsm

    data = query.data  # "choice:idx"
    try:
        idx = int(data.split(":")[1])
    except Exception:
        await query.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(chat_id, "I lost track of that choice—try again.")
        return

    st = gsm.get_state()
    last_choices = st.get("last_choices", [])
    if idx < 0 or idx >= len(last_choices):
        await query.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(chat_id, "That option isn’t available—pick again.")
        return

    st["pending_choice_index"] = idx
    gsm.save_state(st)

    # Replace buttons on the message the user tapped
    await query.edit_message_reply_markup(reply_markup=None)

    # Show Roll button
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🎲 Roll d20", callback_data="roll_d20")]])
    await context.bot.send_message(chat_id, "Ready to roll? Tap the button!", reply_markup=kb)

# --- Roll handler -------------------------------------------------------------
async def on_roll_d20(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    gsm: GameStateManager = context.chat_data.get("gsm") or GameStateManager(chat_id)
    context.chat_data["gsm"] = gsm
    st = gsm.get_state()

    pending_idx = st.get("pending_choice_index")
    last_choices = st.get("last_choices", [])
    if pending_idx is None or pending_idx >= len(last_choices):
        await query.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(chat_id, "No pending action to roll for—choose an option first.")
        return

    choice = last_choices[pending_idx]  # {"text","dc","ability","tags":[]}
    dc = int(choice.get("dc", 10))
    ability_name = choice.get("ability", "Strength")

    # d20 roll
    d20 = random.randint(1, 20)
    # derive modifiers
    char = st.get("character", {})
    abilities = (char.get("abilities") or {})
    score = int(abilities.get(ability_name, 10))
    mod = ability_mod(score)
    prof = proficiency_bonus_for_level(int(st.get("level", 1))) if "proficient" in (choice.get("tags") or []) else 0
    total = d20 + mod + prof
    success = total >= dc

    sign = "+" if mod >= 0 else ""
    prof_txt = f" +{prof}" if prof else ""
    roll_text = (
        f"🎲 You roll a d20… **{d20}**\n"
        f"Modifier ({ability_name} {score} → {sign}{mod}){prof_txt}\n"
        f"**Total = {total} vs DC {dc}** → {'✅ Success!' if success else '❌ Failure.'}"
    )
    await query.edit_message_reply_markup(reply_markup=None)
    await context.bot.send_message(chat_id, roll_text, parse_mode="Markdown")

    # Resolve via LLM
    pb = PromptBuilder(gsm)
    roll_payload = {"d20": d20, "mod": mod, "prof": prof, "total": total, "dc": dc, "success": success}
    outcome = pb.build_outcome_prompt(choice, roll_payload)

    # Apply consequences
    cons = (outcome.get("consequences") or {})
    st["xp"] = int(st.get("xp", 0)) + int(cons.get("xp_delta", 0))
    st["hp"] = max(0, int(st.get("hp", 10)) + int(cons.get("hp_delta", 0)))

    # TODO: add/remove items if you want to reflect inventory
    items_gained = cons.get("items_gained") or []
    items_lost = cons.get("items_lost") or []
    inv = list(st.get("inventory", []))
    if items_gained:
        inv.extend(items_gained)
    if items_lost:
        inv = [x for x in inv if x not in set(items_lost)]
    st["inventory"] = inv

    # Level up by XP (example: every 300 XP)
    if st["xp"] >= (st.get("level", 1) * 300):
        st["level"] = int(st.get("level", 1)) + 1
        await context.bot.send_message(chat_id, f"✨ You reached **Level {st['level']}**!", parse_mode="Markdown")

    st["last_scene"] = outcome.get("narrative", "")
    st["last_choices"] = outcome.get("followup_choices", [])
    st["pending_choice_index"] = None
    gsm.save_state(st)

    # Present story + next choices
    await send_scene_with_choices(update, context, st["last_scene"], st["last_choices"])

# --- Manual /roll command (optional convenience) -----------------------------
async def roll_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Behaves like pressing the roll button
    fake_cq = type("F", (), {})()
    fake_cq.data = "roll_d20"
    update.callback_query = fake_cq  # type: ignore
    return await on_roll_d20(update, context)

# --- Main ---------------------------------------------------------------------
def main():
    application = Application.builder().token(BOT_TOKEN).build()

    # basic commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("ask", ask_cmd))
    application.add_handler(CommandHandler("roll", roll_cmd))

    # choice selection & roll
    application.add_handler(CallbackQueryHandler(on_choice_selected, pattern=r"^choice:\d+$"))
    application.add_handler(CallbackQueryHandler(on_roll_d20, pattern=r"^roll_d20$"))

    # freeform text (go rogue)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, user_text))

    log.info("Bot starting…")
    application.run_polling()

if __name__ == "__main__":
    main()
