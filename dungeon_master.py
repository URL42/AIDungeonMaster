# dungeon_master.py
import json
import logging
import os
import random
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)

from persistence import (
    GameStateManager, TelegramJSONPersistence, setup_logger,
    ability_mod, proficiency_bonus
)
from prompt_builder import PromptBuilder

# ---------- Env & setup ----------
load_dotenv()  # load .env from working dir

TELEGRAM_BOT_TOKEN = os.getenv("DM_TELEGRAM_BOT_TOKEN")
DM_OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("DM_TELEGRAM_BOT_TOKEN not set (env or .env).")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY not set (env or .env).")

logger = setup_logger()

# Conversation states
CHOOSING_GENRE, CHOOSING_CLASS, ENTERING_NAME, ENTERING_MOTIVATION, IN_GAME = range(5)

# ---------- Helpers ----------
def user_gsm(update: Update) -> GameStateManager:
    uid = update.effective_user.id
    return GameStateManager(uid)

def make_menu() -> List[BotCommand]:
    return [
        BotCommand("start", "Start / restart"),
        BotCommand("help", "Show commands"),
        BotCommand("sheet", "Character sheet"),
        BotCommand("inventory", "Inventory"),
        BotCommand("ask", "Out-of-game question"),
        BotCommand("story", "Show last scene"),
        BotCommand("save", "Save game"),
        BotCommand("load", "Load last save"),
        BotCommand("restore", "Restore from backup"),
        BotCommand("xp", "XP & Level"),
        BotCommand("rest", "Long rest (heal)"),
        BotCommand("rollmode", "Set roll mode (normal/adv/dis)"),
        BotCommand("reset", "Hard reset"),
    ]

def abilities_block(abilities: Dict[str, int]) -> str:
    rows = []
    for k in ["STR","DEX","CON","INT","WIS","CHA"]:
        v = abilities.get(k,10)
        rows.append(f"{k}: {v} (mod {ability_mod(v):+d})")
    return "\n".join(rows)

def class_kit_for(rc_lower: str) -> List[str]:
    if "rogue" in rc_lower:
        return ["Shortsword", "Dagger", "Thieves' Tools", "Cloak"]
    if "fighter" in rc_lower or "barbarian" in rc_lower:
        return ["Longsword", "Shield", "Chain Shirt", "Traveler's Pack"]
    if "cleric" in rc_lower:
        return ["Mace", "Wooden Shield", "Holy Symbol", "Healer's Kit"]
    return ["Traveler's Cloak", "Waterskin", "Rations (3 days)"]

def rollmode_label(mode: str) -> str:
    return {"normal":"Normal", "advantage":"Advantage", "disadvantage":"Disadvantage"}.get(mode, "Normal")

# ---------- Error handler ----------
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled exception: %s", context.error)
    try:
        if hasattr(context, "bot") and update and getattr(update, "effective_chat", None):
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ Oops—something went wrong. I logged it."
            )
    except Exception:
        pass

# ---------- Command Handlers ----------
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "*Commands*\n"
        "/start — Start/restart the game\n"
        "/sheet — Show your character sheet\n"
        "/inventory — Show inventory\n"
        "/story — Show last scene\n"
        "/ask <question> — Ask out-of-game question\n"
        "/save, /load, /restore — Manage saves\n"
        "/xp — Show XP & Level\n"
        "/rest — Long rest (heal to max)\n"
        "/rollmode — Set roll mode: normal, adv, dis\n"
        "/reset — Reset game file\n"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.set_my_commands(make_menu())
    gsm = user_gsm(update)
    # Reset state to defaults but preserve file structure
    gsm.state = json.loads(json.dumps(gsm.state))
    gsm.state.update({
        "character": {
            "name":"", "race_class":"", "motivation":"",
            "abilities":{"STR":10,"DEX":10,"CON":10,"INT":10,"WIS":10,"CHA":10},
            "proficiencies": [], "hp": 10, "max_hp": 10
        },
        "inventory": [], "quests": [], "world":{"genre":""},
        "level": 1, "xp": 0, "summary":"", "last_scene": "",
        "choice_buffer": {"scene_id":"", "choices":[]},
        "roll_mode": "normal",
        "last_rest_ts": 0,
    })
    gsm.save()

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("High Fantasy", callback_data="genre|High Fantasy"),
         InlineKeyboardButton("Sci-Fantasy", callback_data="genre|Sci-Fantasy")],
        [InlineKeyboardButton("Dark Gothic", callback_data="genre|Dark Gothic"),
         InlineKeyboardButton("Sword & Sorcery", callback_data="genre|Sword & Sorcery")]
    ])
    await update.effective_chat.send_message("Welcome, adventurer! Choose a genre to begin:", reply_markup=kb)
    return CHOOSING_GENRE

async def on_genre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    _, genre = query.data.split("|",1)
    gsm = user_gsm(update)
    gsm.state["world"]["genre"] = genre
    gsm.save()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Human Fighter", callback_data="class|Human Fighter"),
         InlineKeyboardButton("Elf Rogue", callback_data="class|Elf Rogue")],
        [InlineKeyboardButton("Dwarf Cleric", callback_data="class|Dwarf Cleric"),
         InlineKeyboardButton("Half-Orc Barbarian", callback_data="class|Half-Orc Barbarian")]
    ])
    await query.message.chat.send_message(f"Genre set to *{genre}*.\nChoose race/class:", parse_mode="Markdown", reply_markup=kb)
    return CHOOSING_CLASS

async def on_class(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    _, rc = query.data.split("|",1)
    gsm = user_gsm(update)
    gsm.state["character"]["race_class"] = rc
    gsm.save()
    await query.message.chat.send_message(f"Great choice: *{rc}*.\nWhat's your character's name?", parse_mode="Markdown")
    return ENTERING_NAME

async def on_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gsm = user_gsm(update)
    name = update.message.text.strip()
    gsm.state["character"]["name"] = name
    gsm.save()
    await update.message.reply_text(f"Nice to meet you, *{name}*.\nWhat's your core motivation?", parse_mode="Markdown")
    return ENTERING_MOTIVATION

async def on_motivation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gsm = user_gsm(update)
    mot = update.message.text.strip()
    gsm.state["character"]["motivation"] = mot

    # Seed proficiencies by class
    rc = gsm.state["character"]["race_class"].lower()
    profs = []
    if "rogue" in rc:
        profs = ["Stealth","Perception"]
    elif "fighter" in rc or "barbarian" in rc:
        profs = ["Athletics","Intimidation"]
    elif "cleric" in rc:
        profs = ["Medicine","Religion"]
    gsm.state["character"]["proficiencies"] = profs

    # Starting gear kit
    kit = class_kit_for(rc)
    gsm.state["inventory"] = list({*gsm.state.get("inventory", []), *kit})

    # Ability score generation per your preference: d20 for each stat (3–20 cap)
    rolls = {k: random.randint(3,20) for k in ["STR","DEX","CON","INT","WIS","CHA"]}
    gsm.state["character"]["abilities"] = rolls
    # HP = 8 + CON mod
    gsm.state["character"]["max_hp"] = 8 + max(-5, (rolls["CON"]-10)//2)
    gsm.state["character"]["hp"] = gsm.state["character"]["max_hp"]

    gsm.autosave()
    msg = (
        f"Motivation set: *{mot}*.\n\n"
        "Your starting gear: " + ", ".join(kit) + "\n\n"
        "Ability rolls (d20 each):\n"
        f"{abilities_block(rolls)}\n\n"
        "Type anything to begin your adventure!"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")
    return IN_GAME

# ---------- Game Loop ----------
async def story_or_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gsm = user_gsm(update)
    pb = PromptBuilder(gsm)
    text = update.message.text.strip()

    # Opening
    if not gsm.state.get("last_scene"):
        opener = pb.build_opening_scene()
        await send_scene(update, context, opener, gsm)
        return IN_GAME

    # Continue scene from freeform input
    res = pb.build_scene_prompt(text)
    await send_scene(update, context, res, gsm)
    return IN_GAME

async def send_scene(update: Update, context: ContextTypes.DEFAULT_TYPE, scene: Dict[str, Any], gsm: GameStateManager):
    # Save narrative + rolling summary for context
    gsm.state["last_scene"] = scene.get("narrative", "")
    gsm.state["summary"] = (gsm.state.get("summary","") + " " + gsm.state["last_scene"]).strip()[-2000:]

    # Choice buffer with scene_id for callback integrity
    scene_id = secrets.token_hex(4)
    choices = scene.get("choices", []) or []
    gsm.state["choice_buffer"] = {"scene_id": scene_id, "choices": choices}
    gsm.autosave()

    # Render narrative
    await update.effective_chat.send_message(scene.get("narrative","(no narrative)"))

    # Render choices, if any
    if choices:
        rows = []
        for i, c in enumerate(choices):
            label = f"{i+1}) {c.get('text','')}"
            rows.append([InlineKeyboardButton(label, callback_data=f"choose|{scene_id}|{i}")])
        kb = InlineKeyboardMarkup(rows)
        await update.effective_chat.send_message("What do you do?", reply_markup=kb)

async def on_choose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    _, seen_scene_id, idx_s = query.data.split("|",2)
    idx = int(idx_s)

    gsm = user_gsm(update)
    pb = PromptBuilder(gsm)

    buf = gsm.state.get("choice_buffer", {"scene_id":"", "choices":[]})
    if seen_scene_id != buf.get("scene_id"):
        await query.message.chat.send_message("Those choices have expired. Say anything to continue.")
        return IN_GAME

    last_choices = buf.get("choices", [])
    if not last_choices or idx < 0 or idx >= len(last_choices):
        await query.message.chat.send_message("That choice is no longer valid. Please type your action.")
        return IN_GAME

    choice = last_choices[idx]
    # roll check (respects roll_mode + proficiency)
    roll = gsm.compute_check(choice.get("ability","Strength"), int(choice.get("dc", 10)))
    raw = ", ".join(str(r) for r in roll["raw"])
    roll_txt = (
        f"🎲 {rollmode_label(roll['mode'])} d20 ({raw})  "
        f"mod {roll['mod']:+d}  prof {roll['prof']:+d}  "
        f"= *{roll['total']}* vs DC *{roll['dc']}* → "
        f"{'**SUCCESS**' if roll['success'] else '**FAIL**'}"
    )
    await query.message.chat.send_message(roll_txt, parse_mode="Markdown")

    # LLM outcome
    outcome = pb.build_outcome_prompt(choice, roll)
    await apply_consequences(update, gsm, outcome.get("consequences", {}))

    # present outcome + next options (new scene_id)
    await update.effective_chat.send_message(outcome.get("narrative",""))
    follow = outcome.get("followup_choices", []) or []
    new_scene_id = secrets.token_hex(4)
    gsm.state["choice_buffer"] = {"scene_id": new_scene_id, "choices": follow}
    gsm.autosave()

    if follow:
        rows = [[InlineKeyboardButton(f"{i+1}) {c.get('text','')}", callback_data=f"choose|{new_scene_id}|{i}")]
                for i,c in enumerate(follow)]
        kb = InlineKeyboardMarkup(rows)
        await update.effective_chat.send_message("Next step:", reply_markup=kb)

    return IN_GAME

async def apply_consequences(update: Update, gsm: GameStateManager, cons: Dict[str, Any]):
    hp_delta = int(cons.get("hp_delta", 0))
    xp_delta = int(cons.get("xp_delta", 0))
    items_gained = cons.get("items_gained", [])
    items_lost = cons.get("items_lost", [])
    milestone = bool(cons.get("milestone", False))

    ch = gsm.state["character"]
    ch["hp"] = max(0, min(ch["max_hp"], ch.get("hp", 10) + hp_delta))
    # inventory update
    inv = [it for it in gsm.state.get("inventory", []) if it not in items_lost]
    inv.extend(items_gained)
    gsm.state["inventory"] = inv

    if milestone: gsm.award_milestone()
    if xp_delta: gsm.award_xp(xp_delta)

    gsm.autosave()

    lines = []
    if hp_delta: lines.append(f"HP {'+' if hp_delta>0 else ''}{hp_delta} → {ch['hp']}/{ch['max_hp']}")
    if xp_delta: lines.append(f"XP +{xp_delta} (Total {gsm.state['xp']})")
    if milestone: lines.append("Milestone reached!")
    if items_gained: lines.append(f"Gained: {', '.join(items_gained)}")
    if items_lost: lines.append(f"Lost: {', '.join(items_lost)}")
    if lines:
        await update.effective_chat.send_message("• " + "\n• ".join(lines))

# ---------- Out-of-game ----------
async def ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gsm = user_gsm(update)
    pb = PromptBuilder(gsm)
    # allow "/ask something" or reply after /ask
    content = update.message.text
    q = content.split(" ", 1)[1].strip() if " " in content else "Explain checks and leveling briefly."
    answer = pb.build_clarification_prompt(q)
    await update.message.reply_text(answer)

# ---------- Display ----------
async def sheet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gsm = user_gsm(update)
    ch = gsm.state["character"]
    level = gsm.state.get("level",1)
    xp = gsm.state.get("xp",0)
    mode = gsm.state.get("roll_mode","normal")
    msg = (
        f"*{ch.get('name','Unnamed')}* — {ch.get('race_class','')}\n"
        f"Motivation: {ch.get('motivation','')}\n"
        f"Level {level} | XP {xp} | HP {ch.get('hp',0)}/{ch.get('max_hp',0)} | Prof +{proficiency_bonus(level)}\n"
        f"Roll Mode: {rollmode_label(mode)}\n\n"
        f"{abilities_block(ch.get('abilities',{}))}\n\n"
        f"Proficiencies: {', '.join(ch.get('proficiencies', [])) or 'None'}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def inventory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gsm = user_gsm(update)
    inv = gsm.state.get("inventory", [])
    if not inv:
        await update.message.reply_text("Your pack is empty.")
    else:
        await update.message.reply_text("Inventory:\n- " + "\n- ".join(inv))

async def story(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gsm = user_gsm(update)
    txt = gsm.state.get("last_scene") or "No story yet—say anything to begin."
    await update.message.reply_text(txt)

async def xp_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gsm = user_gsm(update)
    await update.message.reply_text(f"Level {gsm.state.get('level',1)} — XP {gsm.state.get('xp',0)}")

async def rest_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gsm = user_gsm(update)
    now = int(time.time())
    # Optional cooldown: 60s to avoid spam; tweak/remove as desired
    if gsm.state.get("last_rest_ts", 0) and now - gsm.state["last_rest_ts"] < 60:
        await update.message.reply_text("You need a little more time before another long rest.")
        return
    ch = gsm.state["character"]
    ch["hp"] = ch["max_hp"]
    gsm.state["last_rest_ts"] = now
    gsm.autosave()
    await update.message.reply_text(f"You rest and recover to full: {ch['hp']}/{ch['max_hp']} HP.")

async def rollmode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gsm = user_gsm(update)
    args = (update.message.text or "").split()
    if len(args) == 1:
        await update.message.reply_text("Usage: /rollmode normal | adv | dis")
        return
    val = args[1].lower()
    mode = "normal"
    if val in ("adv","advantage"): mode = "advantage"
    elif val in ("dis","disadvantage"): mode = "disadvantage"
    gsm.state["roll_mode"] = mode
    gsm.autosave()
    await update.message.reply_text(f"Roll mode set to: {rollmode_label(mode)}")

async def save_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gsm = user_gsm(update)
    gsm.autosave()
    await update.message.reply_text("Game saved. (Auto-rotating backups kept.)")

async def load_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gsm = user_gsm(update)
    gsm.__post_init__()  # reload from file
    await update.message.reply_text("Game loaded from last save.")

async def restore_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gsm = user_gsm(update)
    import glob
    backups = sorted(glob.glob(f"saves/{gsm.user_id}.*.bak.json"))
    if not backups:
        await update.message.reply_text("No backups found.")
        return
    newest = backups[-1]
    Path(f"saves/{gsm.user_id}.json").write_text(Path(newest).read_text(encoding="utf-8"), encoding="utf-8")
    gsm.__post_init__()
    await update.message.reply_text(f"Restored from {os.path.basename(newest)}.")

async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gsm = user_gsm(update)
    f = gsm.filename
    try:
        if f and f.exists():
            f.unlink()
    except Exception:
        pass
    gsm.__post_init__()
    await update.message.reply_text("Reset complete. Use /start to begin anew.")

# ---------- Main ----------
def main():
    persistence = TelegramJSONPersistence()
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).persistence(persistence).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSING_GENRE: [CallbackQueryHandler(on_genre, pattern=r"^genre\|")],
            CHOOSING_CLASS: [CallbackQueryHandler(on_class, pattern=r"^class\|")],
            ENTERING_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_name)],
            ENTERING_MOTIVATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_motivation)],
            IN_GAME: [
                CallbackQueryHandler(on_choose, pattern=r"^choose\|"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, story_or_input),
            ],
        },
        fallbacks=[CommandHandler("start", start)],
        per_chat=True,
        name="dm_conversation",
        persistent=True,
    )

    # Commands
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("sheet", sheet))
    app.add_handler(CommandHandler("inventory", inventory))
    app.add_handler(CommandHandler("ask", ask))
    app.add_handler(CommandHandler("story", story))
    app.add_handler(CommandHandler("xp", xp_cmd))
    app.add_handler(CommandHandler("rest", rest_cmd))
    app.add_handler(CommandHandler("rollmode", rollmode_cmd))
    app.add_handler(CommandHandler("save", save_cmd))
    app.add_handler(CommandHandler("load", load_cmd))
    app.add_handler(CommandHandler("restore", restore_cmd))
    app.add_handler(CommandHandler("reset", reset_cmd))

    app.add_handler(conv)

    # Error handler
    app.add_error_handler(on_error)

    logger.info("Bot starting…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

