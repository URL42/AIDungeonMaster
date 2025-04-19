import logging
import json
import random
import os
from openai import OpenAI
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters
)
from dotenv import load_dotenv
from persistence import TelegramJSONPersistence
from prompt_builder import PromptBuilder
from persistence import GameStateManager

# Load environment variables
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("DM_TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("DM_OPENAI_API_KEY")

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.DEBUG
)

# Conversation states
(
    CHOOSING_CAMPAIGN,
    CHOOSING_RACE_CLASS,
    CHOOSING_NAME,
    ROLL_ABILITY,
    CHOOSING_MOTIVATION,
    GAME_LOOP,
    ADDING_ITEM,
    ADDING_QUEST
) = range(8)

# Core components
game_manager = GameStateManager()
prompt_builder = PromptBuilder(game_manager)
persistence = TelegramJSONPersistence()

async def on_startup(app: Application):
    commands = [
        BotCommand("start", "Start or resume your adventure"),
        BotCommand("sheet", "View your character sheet"),
        BotCommand("campaign", "View current campaign info"),
        BotCommand("inventory", "View your inventory"),
        BotCommand("quests", "View active quests"),
        BotCommand("additem", "Add an item to your inventory"),
        BotCommand("addquest", "Add a new quest"),
        BotCommand("roll", "Roll dice (e.g., /roll d20, /roll 2d6)"),
        BotCommand("ask", "Ask for clarifications or context"),
        BotCommand("restart", "Restart your campaign"),
        BotCommand("help", "Show available commands"),
        BotCommand("cancel", "Cancel the current operation")
    ]
    await app.bot.set_my_commands(commands)

# --- Character creation flow ---
async def choose_campaign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    genre = update.message.text.strip()
    context.user_data["genre"] = genre
    context.user_data["character"] = {}
    context.user_data["story"] = "Your adventure begins..."
    context.user_data["history"] = []
    context.user_data["inventory"] = []
    context.user_data["quests"] = []
    await update.message.reply_text("Great! Now, choose your race and class (e.g., 'Elf Wizard').")
    return CHOOSING_RACE_CLASS

async def choose_race_class(update: Update, context: ContextTypes.DEFAULT_TYPE):
    race_class = update.message.text.strip()
    parts = race_class.split()
    if len(parts) >= 2:
        race, cls = parts[0], " ".join(parts[1:])
    else:
        race = race_class
        cls = "Adventurer"
    context.user_data.setdefault("character", {})["race"] = race
    context.user_data.setdefault("character", {})["class"] = cls
    await update.message.reply_text("What is your character's name?")
    return CHOOSING_NAME

async def choose_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    context.user_data.setdefault("character", {})["name"] = name
    keyboard = [[InlineKeyboardButton("Roll Abilities", callback_data="roll_abilities")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Great! Now, roll for your abilities.", reply_markup=reply_markup)
    return ROLL_ABILITY

async def roll_abilities_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    abilities = {stat: random.randint(1, 20) for stat in ["Strength", "Dexterity", "Constitution", "Intelligence", "Wisdom", "Charisma"]}
    context.user_data.setdefault("character", {})["abilities"] = abilities
    abilities_text = "\n".join([f"{k}: {v}" for k, v in abilities.items()])
    await query.message.reply_text(f"Your abilities:\n{abilities_text}")
    await query.message.reply_text("What motivates you on your adventure?")
    return CHOOSING_MOTIVATION

async def choose_motivation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    motivation = update.message.text.strip()
    context.user_data.setdefault("character", {})["motivation"] = motivation
    intro = prompt_builder.build_intro_prompt(context.user_data)
    await update.message.reply_text(intro)
    return GAME_LOOP

# --- Game loop input ---
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message.text
    context.user_data["last_action"] = message

    keyboard = [[InlineKeyboardButton("🎲 Roll D20", callback_data="roll_d20")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "Press the button below to roll a D20 and see what happens:",
        reply_markup=reply_markup
    )

async def roll_d20_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    roll = random.randint(1, 20)
    last_action = context.user_data.get("last_action", "an unknown action")

    await query.message.reply_text(f"🎲 You rolled a {roll} on a D20.")

    prompt = (
        f"The player said: '{last_action}' and rolled a {roll} on a D20. "
        "Narrate what happens next in a classic fantasy RPG tone."
    )
    response = game_manager.handle_freeform(prompt, context.user_data)
    await query.message.reply_text(response or "Something mysterious happens...")
    return GAME_LOOP

# --- General commands ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    char = context.user_data.get("character", {})
    required_fields = ["name", "race", "class", "abilities", "motivation"]
    if all(field in char for field in required_fields):
        await update.message.reply_text("Welcome back! Type anything to explore, or use /travel and /talk to interact.")
        return GAME_LOOP
    else:
        await update.message.reply_text("Welcome to your adventure! Let's build your character.\nWhat genre would you like to play in?")
        return CHOOSING_CAMPAIGN

async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await handle_text(update, context)

async def talk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message.text
    response = game_manager.handle_talk(message, context.user_data)
    await update.message.reply_text(response)

async def travel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    response = game_manager.handle_travel(context.user_data)
    await update.message.reply_text(response)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start - Begin or continue your adventure\n"
        "/sheet - View your character sheet\n"
        "/campaign - View current campaign info\n"
        "/inventory - View your inventory\n"
        "/quests - View active quests\n"
        "/additem - Add an item to your inventory\n"
        "/addquest - Add a new quest\n"
        "/roll - Roll dice (e.g., /roll d20 or /roll 2d6)\n"
        "/ask - Ask for clarifications or context\n"
        "/restart - Restart the campaign\n"
        "/help - Show available commands\n"
        "/cancel - Cancel the current operation"
    )

async def show_character(update: Update, context: ContextTypes.DEFAULT_TYPE):
    char = context.user_data.get("character", {})
    if not char:
        return await update.message.reply_text("You haven’t created a character yet.")
    abilities = char.get("abilities", {})
    ability_text = "\n".join([f"{k}: {v}" for k, v in abilities.items()])
    await update.message.reply_text(
        f"🧙 Name: {char.get('name')}\n"
        f"Race: {char.get('race')}\n"
        f"Class: {char.get('class')}\n"
        f"Motivation: {char.get('motivation')}\n"
        f"Abilities:\n{ability_text}"
    )

async def show_campaign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    genre = context.user_data.get("genre", "Unknown")
    story = context.user_data.get("story", "")
    await update.message.reply_text(f"Genre: {genre}\n\nStory so far:\n{story}")

async def show_inventory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = context.user_data.get("inventory", [])
    text = "\n".join(f"- {i}" for i in items) if items else "Your inventory is empty."
    await update.message.reply_text(text)

async def show_quests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quests = context.user_data.get("quests", [])
    text = "\n".join(f"- {q}" for q in quests) if quests else "You have no active quests."
    await update.message.reply_text(text)

async def add_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("What item would you like to add?")
    return ADDING_ITEM

async def save_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    item = update.message.text.strip()
    context.user_data.setdefault("inventory", []).append(item)
    await update.message.reply_text(f"Added '{item}' to your inventory.")
    return GAME_LOOP

async def add_quest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("What is your new quest?")
    return ADDING_QUEST

async def save_quest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quest = update.message.text.strip()
    context.user_data.setdefault("quests", []).append(quest)
    await update.message.reply_text(f"New quest added: {quest}")
    return GAME_LOOP

def parse_dice(expr: str) -> int:
    try:
        count, sides = map(int, expr.lower().split("d"))
        return sum(random.randint(1, sides) for _ in range(count))
    except:
        return None

async def roll_dice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Usage: /roll d20 or /roll 2d6")
    total = parse_dice(context.args[0])
    if total is None:
        await update.message.reply_text("Invalid format. Try /roll d20 or /roll 2d6")
    else:
        await update.message.reply_text(f"You rolled {context.args[0]}: {total}")

async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Ask a question like: /ask What is my character’s background?")
    question = " ".join(context.args)
    prompt = prompt_builder.build_clarification_prompt(question)
    response = game_manager.handle_freeform(prompt, context.user_data)
    await update.message.reply_text(response)

async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Your character and story have been reset. Use /start to begin again.")

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operation cancelled.")
    return ConversationHandler.END

# --- Conversation Handler ---
conv_handler = ConversationHandler(
    entry_points=[CommandHandler("start", start)],
    states={
        CHOOSING_CAMPAIGN: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_campaign)],
        CHOOSING_RACE_CLASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_race_class)],
        CHOOSING_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_name)],
        ROLL_ABILITY: [CallbackQueryHandler(roll_abilities_callback, pattern="^roll_abilities$")],
        CHOOSING_MOTIVATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_motivation)],
        GAME_LOOP: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text),
            CallbackQueryHandler(roll_d20_callback, pattern="^roll_d20$")
        ],
        ADDING_ITEM: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_item)],
        ADDING_QUEST: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_quest)],
    },
    fallbacks=[MessageHandler(filters.TEXT & ~filters.COMMAND, fallback)]
)

# --- Application setup ---
app = Application.builder().token(TELEGRAM_BOT_TOKEN).persistence(persistence).build()
app.post_init = on_startup

# Register handlers
app.add_handler(conv_handler)
app.add_handler(CommandHandler("help", help_command))
app.add_handler(CommandHandler("sheet", show_character))
app.add_handler(CommandHandler("campaign", show_campaign))
app.add_handler(CommandHandler("inventory", show_inventory))
app.add_handler(CommandHandler("quests", show_quests))
app.add_handler(CommandHandler("additem", add_item))
app.add_handler(CommandHandler("addquest", add_quest))
app.add_handler(CommandHandler("roll", roll_dice))
app.add_handler(CommandHandler("ask", ask_command))
app.add_handler(CommandHandler("restart", restart_command))
app.add_handler(CommandHandler("cancel", cancel_command))
app.add_handler(CommandHandler("talk", talk))
app.add_handler(CommandHandler("travel", travel))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback))

# --- Run bot ---
if __name__ == "__main__":
    app.run_polling()
