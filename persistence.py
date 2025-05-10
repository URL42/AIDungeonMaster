# persistence.py
import os
import json
import shutil
import logging
import time
from telegram.ext import BasePersistence, PersistenceInput
from pathlib import Path

    # === Logger Handler ===

def setup_logger(name='dungeon_master', filename='dungeon_master.log'):
    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / filename
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        fh = logging.FileHandler(log_path)
        fh.setLevel(logging.DEBUG)
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
        fh.setFormatter(formatter)
        ch.setFormatter(formatter)
        logger.addHandler(fh)
        logger.addHandler(ch)

    return logger

class TelegramJSONPersistence(BasePersistence):
    """Persistence layer for Telegram bot state (user/chat/bot/convo/callback)."""
    def __init__(
        self,
        filepath="bot_data.json",
        history_filepath="conversation_history.json",
        store_data=PersistenceInput(
            user_data=True,
            chat_data=True,
            bot_data=True,
            callback_data=True
        )
    ):
        super().__init__(store_data=store_data)
        self.filepath = filepath
        self.history_filepath = history_filepath
        self._data = self._load_data()
        self._history = self._load_history()

    def _load_data(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r") as f:
                    return json.load(f)
            except Exception as e:
                logging.error(f"Failed loading bot data: {e}")
        return {
            "user_data": {},
            "chat_data": {},
            "bot_data": {},
            "conversation_data": {},
            "callback_data": {}
        }

    def _load_history(self):
        if os.path.exists(self.history_filepath):
            try:
                with open(self.history_filepath, "r") as f:
                    return json.load(f)
            except Exception as e:
                logging.error(f"Failed loading history: {e}")
        return {}

    def _save_data(self):
        try:
            with open(self.filepath, "w") as f:
                json.dump(self._data, f, indent=2)
        except Exception as e:
            logging.error(f"Failed saving bot data: {e}")

    def _save_history(self):
        try:
            with open(self.history_filepath, "w") as f:
                json.dump(self._history, f, indent=2)
        except Exception as e:
            logging.error(f"Failed saving history: {e}")

    # Telegram bot state handlers
    async def get_user_data(self): return self._data.get("user_data", {})
    async def update_user_data(self, user_id, data):
        self._data.setdefault("user_data", {})[str(user_id)] = data
        self._save_data()

    async def get_chat_data(self): return self._data.get("chat_data", {})
    async def update_chat_data(self, chat_id, data):
        self._data.setdefault("chat_data", {})[str(chat_id)] = data
        self._save_data()

    async def get_bot_data(self): return self._data.get("bot_data", {})
    async def update_bot_data(self, data):
        self._data["bot_data"] = data
        self._save_data()

    async def get_conversations(self, name):
        return self._data.get("conversation_data", {}).get(name, {})
    async def update_conversation(self, name, key, new_state):
        conv = self._data.setdefault("conversation_data", {}).setdefault(name, {})
        if new_state:
            conv[str(key)] = new_state
        else:
            conv.pop(str(key), None)
        self._save_data()

    async def get_callback_data(self): return self._data.get("callback_data", {})
    async def update_callback_data(self, data):
        self._data["callback_data"] = data
        self._save_data()

    async def drop_user_data(self, user_id):
        self._data.get("user_data", {}).pop(str(user_id), None)
        self._save_data()

    async def drop_chat_data(self, chat_id):
        self._data.get("chat_data", {}).pop(str(chat_id), None)
        self._save_data()

    async def flush(self):
        self._save_data()
        self._save_history()

    # History utilities
    def add_game_history(self, user_id, entry):
        self._history.setdefault(str(user_id), []).append(entry)
        self._save_history()

class GameStateManager:
    """Manages RPG game state with auto-saving and rotating backups."""
    def __init__(self, user_id: str, save_dir="saves", slot_count=3):
        self.user_id = user_id
        self.slot_count = slot_count
        self.save_dir = os.path.join(save_dir, str(user_id))
        os.makedirs(self.save_dir, exist_ok=True)
        self.current_slot = 0
        self.state = self._load_latest_or_default()

    def _slot_filename(self, slot_index):
        return os.path.join(self.save_dir, f"save_slot_{slot_index+1}.json")

    def update_character(self, key: str, value):
        self.state.setdefault("character", {})[key] = value
        self.save()

    def _load_latest_or_default(self):
        """Load the newest save slot, or return default state."""
        latest = None
        latest_time = 0
        for i in range(self.slot_count):
            path = self._slot_filename(i)
            if os.path.exists(path):
                mtime = os.path.getmtime(path)
                if mtime > latest_time:
                    latest_time = mtime
                    latest = path
        if latest:
            try:
                with open(latest, "r") as f:
                    logging.info(f"Loaded game state from {latest}")
                    return json.load(f)
            except Exception as e:
                logging.error(f"Failed loading slot {latest}: {e}")
        return {
            "current_location": None,
            "story": "",
            "conversation_history": [],
            "character": {},
            "inventory": [],
            "quests": [],
            "world_data": {}
        }

    def save(self):
        """Save current state to next rotating slot."""
        self.current_slot = (self.current_slot + 1) % self.slot_count
        path = self._slot_filename(self.current_slot)
        try:
            tmp = f"{path}.{int(time.time())}.tmp"
            with open(tmp, "w") as f:
                json.dump(self.state, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            logging.info(f"Saved game state to slot {self.current_slot + 1}")
        except Exception as e:
            logging.error(f"Error saving game slot: {e}")

    def restore_latest(self):
        """Restore the most recent save slot."""
        self.state = self._load_latest_or_default()

    def get_state(self):
        return self.state

    def update_state(self, updates: dict):
        """Apply updates to the game state and save."""
        def _deep_merge(old, new):
            for k, v in new.items():
                if isinstance(v, dict) and isinstance(old.get(k), dict):
                    _deep_merge(old[k], v)
                else:
                    old[k] = v
            return old
        _deep_merge(self.state, updates)
        self.save()
        return self.state

    def log_action(self, entry: str):
        """Append entry to conversation history and save."""
        self.state.setdefault("conversation_history", []).append(entry)
        self.save()

    def handle_freeform(self, prompt: str, user_data: dict) -> str:
        """Generate response via GPT and log it."""
        try:
            from openai import OpenAI
            client = OpenAI(api_key=os.getenv("DM_OPENAI_API_KEY"))
            response = client.chat.completions.create(
                model="gpt-4.1",
                messages=[
                    {"role": "system", "content": "You are a Dungeon Master crafting a rich and immersive world."},
                    {"role": "user", "content": prompt}
                ]
            )
            result = response.choices[0].message.content.strip()
            self.state.setdefault("conversation_history", []).append(f"DM: {result}")
            self.save()
            return result
        except Exception as e:
            logging.error(f"[handle_freeform ERROR]: {e}")
            return "⚠️ The Dungeon Master stares blankly, as if something broke in the multiverse..."

    # === Property Helpers ===

    @property
    def character(self) -> dict:
        return self.state.setdefault("character", {})

    @property
    def inventory(self) -> list:
        return self.state.setdefault("inventory", [])

    @property
    def abilities(self) -> dict:
        return self.character.setdefault("abilities", {})


        except Exception as e:
            print(f"[handle_freeform ERROR]: {e}")
            return "⚠️ The Dungeon Master stares blankly, as if something broke in the multiverse..."

