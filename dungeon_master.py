import logging
import json
import random
import os
import warnings
import re
import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters
)
from telegram.warnings import PTBUserWarning
from openai import OpenAI
from dotenv import load_dotenv
from prompt_builder import PromptBuilder
from persistence import GameStateManager
from persistence import GameStateManager, setup_logger

# Setup logging path
os.makedirs("logs", exist_ok=True)
LOG_FILE = "logs/game_debug.log"
logger = setup_logger()

DEBUG_MODE = True
warnings.filterwarnings("ignore", category=UserWarning, module="telegram.ext._application")
warnings.filterwarnings("ignore", category=PTBUserWarning, message=r".*per_message=False.*")

# Load environment
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("DM_TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("DM_OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)

CHOOSING_GENRE, CHOOSING_RACE_CLASS, CHOOSING_NAME, CHOOSING_MOTIVATION, \
GAME_LOOP, HANDLING_CHOICE, AWAITING_ROLL, WAITING_ITEM = range(8)

def calculate_modifier(score: int) -> int:
    mod = (score - 10) // 2
    logger.debug(f"🧮 Modifier for score {score}: {mod}")
    return mod

async def generate_gpt_response(prompt: str) -> str:
    try:
        logger.info(f"🧠 GPT Prompt:\n{prompt}")
        resp = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a creative Dungeon Master."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.85,
            max_tokens=1000
        )
        text = resp.choices[0].message.content.strip()
        logger.info(f"📜 GPT Reply:\n{text}")
        return text
    except Exception as e:
        logger.error(f"GPT error: {e}")
        return "⚠️ The story momentarily clears..."

def init_state(update: Update, context: ContextTypes.DEFAULT_TYPE) -> GameStateManager:
    user_id = str(update.effective_user.id)
    gsm = GameStateManager(user_id)
    context.user_data["gsm"] = gsm
    context.user_data["prompt_builder"] = PromptBuilder(gsm)
    return gsm

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        logger.debug(f"/start by {update.effective_user.id}")
        gsm = init_state(update, context)
        if gsm.state.get("character", {}).get("name"):
            await update.message.reply_text("Welcome back! Use /story to continue.")
            return GAME_LOOP
        else:
            await update.message.reply_text(
                "Choose your story genre:\n(e.g: Fantasy/Sci-Fi/Noir/Steampunk)"
            )
            return CHOOSING_GENRE
    except Exception as e:
        logging.error(f"Start error: {e}")
        await update.message.reply_text("⚠️ Failed to initialize. Try /start again.")
        return ConversationHandler.END

async def handle_genre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        genre = update.message.text.strip()
        logger.debug(f"Genre: {genre}")
        context.user_data["gsm"].update_state({"character": {"genre": genre}})
        await update.message.reply_text(
            "Great! What's your race and class?\n(e.g.: 'Elf Ranger')"
        )
        return CHOOSING_RACE_CLASS
    except Exception as e:
        logging.error(f"Genre error: {e}")
        await update.message.reply_text("⚠️ Please enter a valid genre.")
        return CHOOSING_GENRE

async def handle_race_class(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        txt = update.message.text.strip()
        logger.debug(f"Race/Class: {txt}")
        parts = txt.split()
        race = parts[0]
        cls = " ".join(parts[1:]) or "Adventurer"
        context.user_data["gsm"].update_state({
            "character": {"race": race, "class": cls}
        })
        await update.message.reply_text("What's your character's name?")
        return CHOOSING_NAME
    except Exception as e:
        logging.error(f"Race/Class error: {e}")
        await update.message.reply_text("⚠️ Use format 'Race Class'. Try again.")
        return CHOOSING_RACE_CLASS

async def handle_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        name = update.message.text.strip()
        logger.debug(f"Name: {name}")
        context.user_data["gsm"].update_state({"character": {"name": name}})
        await update.message.reply_text("What drives your character? (e.g.: 'Find the lost crown')")
        return CHOOSING_MOTIVATION
    except Exception as e:
        logging.error(f"Name error: {e}")
        await update.message.reply_text("⚠️ Invalid name. Try again.")
        return CHOOSING_NAME

async def handle_motivation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        motivation = update.message.text.strip()
        logger.debug(f"Motivation: {motivation}")
        gsm = context.user_data["gsm"]
        gsm.update_character("motivation", motivation)

        # Roll abilities
        stats = ["Strength", "Dexterity", "Constitution", "Intelligence", "Wisdom", "Charisma"]
        rolls = [random.randint(3, 20) for _ in stats]
        random.shuffle(stats)
        abilities = {s: v for s, v in zip(stats, rolls)}
        gsm.update_character("abilities", abilities)

        await update.message.reply_text(
            "Your abilities:\n" + "\n".join(f"{k}: {v}" for k, v in abilities.items())
        )

        pb = context.user_data["prompt_builder"]
        intro = await generate_gpt_response(pb.build_intro_prompt(gsm.state["character"]))
        gsm.log_action(f"DM: {intro}")
        gsm.update_state({"story": intro})
        await context.bot.send_message(update.effective_chat.id, intro)
        return GAME_LOOP

    except Exception as e:
        logging.error(f"Motivation error: {e}")
        await update.message.reply_text("⚠️ Character setup failed. Use /start to retry.")
        return ConversationHandler.END

# ——— Main Story Loop ——————————————————————————————————————————————

async def story(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        gsm = context.user_data.get("gsm")
        if not gsm:
            target = update.callback_query or update.message
            await target.reply_text("❌ No active game. Use /start.")
            return ConversationHandler.END

        is_cb = bool(update.callback_query)
        user_input = "Continuing story..." if is_cb else update.message.text
        if not is_cb and (not user_input or user_input.strip().lower() == "/story"):
            await update.message.reply_text("What would you like to do next?")
            return GAME_LOOP
        chat_id = update.effective_chat.id
        logger.debug(f"Story input: {user_input}")

        prompt = context.user_data["prompt_builder"].build_scene_prompt(user_input)
        raw = await generate_gpt_response(prompt)
        gsm.log_action(f"Player: {user_input}")

        m = re.search(r"narrative\s*:\s*(.*?)\s*choices\s*:\s*(.*)", raw, flags=re.IGNORECASE|re.DOTALL)
        if m:
            narrative = m.group(1).strip()
            choices_block = m.group(2).strip()
            gsm.update_state({
                "story": (gsm.state.get("story", "") + "\n" + narrative).strip()
            })

            lines = [l.strip() for l in choices_block.splitlines() if re.match(r"\d+\)", l)]
            buttons, parsed = [], []
            for line in lines:
                idx = int(line.split(")")[0])
                text = line.split(")", 1)[1].split("(")[0].strip()
                dc_m = re.search(r"\(DC\s*(\d+)\s*([A-Za-z]+)\)", line, re.IGNORECASE)
                dc = int(dc_m.group(1)) if dc_m else None
                ability = dc_m.group(2) if dc_m else None
                parsed.append({"text": text, "dc": dc, "ability": ability})
                buttons.append([InlineKeyboardButton(f"{idx}. {text}", callback_data=f"choice_{idx}")])

            context.user_data["current_choices"] = parsed
            markup = InlineKeyboardMarkup(buttons)
            await context.bot.send_message(chat_id, f"{narrative}\n\nChoose your action:", reply_markup=markup)
            return HANDLING_CHOICE

        special_lines = re.findall(r"^SPECIAL\s*:\s*(.+)$", raw, flags=re.IGNORECASE | re.MULTILINE)
        if special_lines:
            narrative = re.sub(r"^SPECIAL\s*:\s*.*$", "", raw, flags=re.IGNORECASE | re.MULTILINE).strip()
            parsed, buttons = [], []
            for i, line in enumerate(special_lines, start=1):
                m2 = re.search(r"CHECK\s*:\s*([A-Za-z]+)\s*,\s*DC\s*:\s*(\d+)", line, re.IGNORECASE)
                if not m2:
                    continue
                ability = m2.group(1)
                dc = int(m2.group(2))
                parsed.append({"text": f"Check {ability}", "dc": dc, "ability": ability})
                buttons.append([InlineKeyboardButton(f"Roll {ability} (DC {dc})", callback_data=f"choice_{i}")])

            if parsed:
                gsm.update_state({
                    "story": (gsm.state.get("story", "") + "\n" + narrative).strip()
                })
                context.user_data["current_choices"] = parsed
                markup = InlineKeyboardMarkup(buttons)
                await context.bot.send_message(chat_id, f"{narrative}\n\nWhich check will you make?", reply_markup=markup)
                return HANDLING_CHOICE

        fallback = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Continue Story", callback_data="continue_story")]]
        )
        await context.bot.send_message(chat_id, raw, reply_markup=fallback)
        return GAME_LOOP

    except Exception as e:
        logging.error(f"Story error: {e}")
        target = update.callback_query or update.message
        await target.reply_text("⚠️ Story progression failed.")
        return GAME_LOOP

async def handle_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        chat_id = query.message.chat_id
        logger.debug(f"Choice callback: {query.data}")

        idx = int(query.data.split("_")[1]) - 1
        choices = context.user_data.get("current_choices", [])
        if idx < 0 or idx >= len(choices):
            await context.bot.send_message(chat_id, "Invalid choice!")
            return GAME_LOOP

        sel = choices[idx]
        gsm = context.user_data["gsm"]
        gsm.log_action(f"Choice: {sel['text']}")

        if sel["dc"] is not None:
            ability = sel["ability"]
            score = gsm.character["abilities"].get(ability, 10)
            mod = calculate_modifier(score)
            context.user_data["pending_roll"] = {
                "dc": sel["dc"], "ability": ability, "choice_text": sel["text"]
            }
            await context.bot.send_message(
                chat_id,
                f"🛡️ {ability} Check!\nYour {ability}: {score} (Mod {mod:+})\n"
                f"Target DC: {sel['dc']}\n\nChoose:",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🎲 Roll D20", callback_data="perform_roll")
                ]])
            )
            return AWAITING_ROLL

        return await resolve_choice(update, context, sel["text"])

    except Exception as e:
        logging.error(f"Choice error: {e}")
        await context.bot.send_message(update.effective_chat.id, "⚠️ Choice processing failed.")
        return GAME_LOOP

async def resolve_choice(update, context, choice_text, roll_result=None):
    try:
        gsm = context.user_data["gsm"]
        pb = context.user_data["prompt_builder"]
        pend = context.user_data.get("pending_roll", {})
        chat_id = update.effective_chat.id

        outcome = await generate_gpt_response(
            pb.build_outcome_prompt(choice_text, roll_result)
        )
        gsm.log_action(f"DM: {outcome}")
        gsm.update_state({
            "story": (gsm.state.get("story", "") + "\n" + outcome).strip()
        })

        btn = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Continue Story", callback_data="continue_story")]]
        )
        await context.bot.send_message(chat_id, outcome, reply_markup=btn)
        return GAME_LOOP

    except Exception as e:
        logging.error(f"Resolve error: {e}")
        await context.bot.send_message(update.effective_chat.id, "⚠️ Outcome generation failed.")
        return GAME_LOOP

async def handle_roll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        chat_id = query.message.chat_id
        logger.debug("Rolling d20")

        pend = context.user_data.get("pending_roll")
        if not pend:
            await context.bot.send_message(chat_id, "No active roll!")
            return GAME_LOOP

        gsm = context.user_data["gsm"]
        ability = pend["ability"]
        score = gsm.character["abilities"].get(ability, 10)
        mod = calculate_modifier(score)
        roll = random.randint(1, 20)
        total = roll + mod
        logger.info(f"🎲 Rolled {roll} + {mod} modifier = {total} vs DC {pend['dc']}")

        check_result = {
            "roll": roll,
            "mod": mod,
            "total": total,
            "dc": pend["dc"],
            "ability": ability
        }

        prompt = context.user_data["prompt_builder"].build_outcome_prompt(
            pend["choice_text"], check_result
        )
        outcome = await generate_gpt_response(prompt)

        gsm.log_action(f"Roll: {roll} + {mod} vs DC {pend['dc']} → {'Success' if total >= pend['dc'] else 'Failure'}")
        gsm.log_action(f"DM: {outcome}")
        gsm.update_state({
            "story": (gsm.state.get("story", "") + "\n" + outcome).strip()
        })

        await context.bot.send_message(
            chat_id,
            f"🎲 {ability} Check: {roll} + {mod} = {total}\nDC: {pend['dc']}\n\n{outcome}",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Continue Story", callback_data="continue_story")]]
            )
        )
        del context.user_data["pending_roll"]
        return GAME_LOOP

    except Exception as e:
        logging.error(f"Roll error: {e}")
        await context.bot.send_message(update.effective_chat.id, "⚠️ Roll failed.")
        return GAME_LOOP

# ——— Utility Commands ————————————————————————————————————————————

async def sheet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gsm = context.user_data.get("gsm")
    if not gsm:
        await update.message.reply_text("❌ No game. Use /start.")
        return ConversationHandler.END
    char = gsm.state.get("character",{})
    abil = char.get("abilities",{})
    text = (
        "🧙 Character Sheet 🧙\n"
        f"Name: {char.get('name','???')}\n"
        f"Race: {char.get('race','???')}\n"
        f"Class: {char.get('class','???')}\n"
        f"Motivation: {char.get('motivation','???')}\n\n"
        "Abilities:\n" +
        "\n".join(f"{k}: {v}" for k,v in abil.items())
    )
    await update.message.reply_text(text)
    return GAME_LOOP

async def inventory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gsm = context.user_data.get("gsm")
    if not gsm:
        await update.message.reply_text("❌ No game. Use /start.")
        return ConversationHandler.END
    items = gsm.state.get("inventory",[])
    txt = "Inventory:\n" + "\n".join(f"- {i}" for i in items) if items else "Empty."
    await update.message.reply_text(txt)
    return GAME_LOOP

async def add_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gsm = context.user_data.get("gsm")
    if not gsm:
        await update.message.reply_text("❌ No game. Use /start.")
        return ConversationHandler.END

    if context.args:
        item = " ".join(context.args).strip()
        inv = gsm.state.get("inventory",[])
        inv.append(item)
        gsm.update_state({"inventory":inv})
        await update.message.reply_text(f"Added **{item}** to your inventory!")
        return GAME_LOOP

    await update.message.reply_text("What item would you like to add?")
    return WAITING_ITEM

async def save_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gsm = context.user_data.get("gsm")
    if not gsm:
        await update.message.reply_text("❌ No game. Use /start.")
        return ConversationHandler.END
    item = update.message.text.strip()
    inv = gsm.state.get("inventory",[])
    inv.append(item)
    gsm.update_state({"inventory":inv})
    await update.message.reply_text(f"Added **{item}** to your inventory!")
    return GAME_LOOP

async def interact_npc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gsm = context.user_data.get("gsm")
    if not gsm:
        await update.message.reply_text("❌ No game. Use /start.")
        return ConversationHandler.END
    npc = context.args[0] if context.args else "Stranger"
    resp = await generate_gpt_response(
        context.user_data["prompt_builder"].build_npc_prompt(npc)
    )
    await update.message.reply_text(resp)
    return GAME_LOOP

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    save_dir = os.path.join("saves", user_id)
    if "gsm" in context.user_data:
        del context.user_data["gsm"]
    if os.path.exists(save_dir):
        import shutil
        shutil.rmtree(save_dir)
    await update.message.reply_text("🗑️ Game reset! Use /start.")
    return ConversationHandler.END

async def boost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gsm = context.user_data.get("gsm")
    if not gsm:
        await update.message.reply_text("No game loaded. Use /start.")
        return ConversationHandler.END

    try:
        ability = context.args[0].capitalize()
        amount = int(context.args[1])
        current = gsm.state["character"]["abilities"].get(ability, 10)
        gsm.state["character"]["abilities"][ability] = current + amount
        gsm.save()
        await update.message.reply_text(
            f"📈 {ability} boosted from {current} to {current + amount}"
        )
    except Exception as e:
        logger.error(f"Boost error: {e}")
        await update.message.reply_text("Usage: /boost [Ability] [Amount]")

    return GAME_LOOP

async def set_commands(app):
    commands = [
        BotCommand("start","Begin/continue adventure"),
        BotCommand("story","Progress narrative"),
        BotCommand("sheet","View character sheet"),
        BotCommand("inventory","Check inventory"),
        BotCommand("npc","Interact NPC"),
        BotCommand("additem","Add item"),
        BotCommand("reset","Reset game"),
        BotCommand("boost", "Increase an ability score (e.g., /boost Strength 2)")
    ]
    await app.bot.set_my_commands(commands)

def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSING_GENRE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_genre)],
            CHOOSING_RACE_CLASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_race_class)],
            CHOOSING_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_name)],
            CHOOSING_MOTIVATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_motivation)],
            GAME_LOOP: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, story),
                CommandHandler("story", story),
                CallbackQueryHandler(story, pattern="^continue_story$")
            ],
            HANDLING_CHOICE: [
                CallbackQueryHandler(handle_choice, pattern=r"^choice_\d+$")
            ],
            AWAITING_ROLL: [
                CallbackQueryHandler(handle_roll, pattern="^perform_roll$")
            ],
            WAITING_ITEM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, save_item)
            ],
        },
        fallbacks=[CommandHandler("reset", reset)],
        per_message=False,
        per_chat=True,
        per_user=True
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("sheet", sheet))
    app.add_handler(CommandHandler("inventory", inventory))
    app.add_handler(CommandHandler("npc", interact_npc))
    app.add_handler(CommandHandler("additem", add_item))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("boost", boost))

    app.post_init = set_commands
    app.run_polling()

if __name__ == "__main__":
    main()
