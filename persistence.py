# persistence.py
import json
import os
import shutil
import time
import logging
from telegram.ext import BasePersistence, PersistenceInput

class TelegramJSONPersistence(BasePersistence):
    """Complete persistence implementation for Telegram bot"""
    def __init__(self,
                 filepath="bot_data.json",
                 history_filepath="conversation_history.json",
                 store_data=PersistenceInput(
                     user_data=True,
                     chat_data=True,
                     bot_data=True,
                     callback_data=True
                 )):
        super().__init__(store_data=store_data)
        self.filepath = filepath
        self.history_filepath = history_filepath
        self._data = self._load_data()
        self._history = self._load_history()

    def _load_data(self):
        """Load persistent data from JSON file"""
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r") as f:
                    return json.load(f)
            except Exception as e:
                logging.error(f"Error loading persistence data: {e}")
        return {
            "user_data": {},
            "chat_data": {},
            "bot_data": {},
            "conversation_data": {},
            "callback_data": {}
        }

    def _load_history(self):
        """Load conversation history from JSON file"""
        if os.path.exists(self.history_filepath):
            try:
                with open(self.history_filepath, "r") as f:
                    return json.load(f)
            except Exception as e:
                logging.error(f"Error loading history: {e}")
        return {}

    def _save_data(self):
        """Save persistent data to JSON file"""
        try:
            with open(self.filepath, "w") as f:
                json.dump(self._data, f, indent=2)
        except Exception as e:
            logging.error(f"Error saving data: {e}")

    def _save_history(self):
        """Save conversation history to JSON file"""
        try:
            with open(self.history_filepath, "w") as f:
                json.dump(self._history, f, indent=2)
        except Exception as e:
            logging.error(f"Error saving history: {e}")

    # Required BasePersistence methods
    async def get_user_data(self):
        """Get all user data"""
        return self._data.get("user_data", {})

    async def update_user_data(self, user_id, data):
        """Update data for specific user"""
        self._data.setdefault("user_data", {})[str(user_id)] = data
        self._save_data()

    async def get_chat_data(self):
        """Get all chat data"""
        return self._data.get("chat_data", {})

    async def update_chat_data(self, chat_id, data):
        """Update data for specific chat"""
        self._data.setdefault("chat_data", {})[str(chat_id)] = data
        self._save_data()

    async def get_bot_data(self):
        """Get bot data"""
        return self._data.get("bot_data", {})

    async def update_bot_data(self, data):
        """Update bot data"""
        self._data["bot_data"] = data
        self._save_data()

    async def get_conversations(self, name):
        """Get conversations by name"""
        return self._data.get("conversation_data", {}).get(name, {})

    async def update_conversation(self, name, key, new_state):
        """Update conversation state"""
        conv_data = self._data.setdefault("conversation_data", {}).setdefault(name, {})
        if new_state:
            conv_data[str(key)] = new_state
        else:
            conv_data.pop(str(key), None)
        self._save_data()

    async def get_callback_data(self):
        """Get callback data"""
        return self._data.get("callback_data", {})

    async def update_callback_data(self, data):
        """Update callback data"""
        self._data["callback_data"] = data
        self._save_data()

    async def drop_user_data(self, user_id):
        """Remove user data"""
        self._data.get("user_data", {}).pop(str(user_id), None)
        self._save_data()

    async def drop_chat_data(self, chat_id):
        """Remove chat data"""
        self._data.get("chat_data", {}).pop(str(chat_id), None)
        self._save_data()

    async def refresh_user_data(self, user_id, user_data):
        """Refresh single user's data"""
        return user_data.get(str(user_id), {})

    async def refresh_chat_data(self, chat_id, chat_data):
        """Refresh single chat's data"""
        return chat_data.get(str(chat_id), {})

    async def refresh_bot_data(self, bot_data):
        """Refresh bot data"""
        return bot_data

    async def flush(self):
        """Flush all data to disk"""
        self._save_data()
        self._save_history()

    # Custom history methods
    def add_game_history(self, user_id, entry):
        """Add entry to game history"""
        self._history.setdefault(str(user_id), []).append(entry)
        self._save_history()

class GameStateManager:
    """Manages RPG game state persistence"""
    def __init__(self, filename="game_state.json"):
        self.filename = filename
        self.state = self._load_or_default()

    def _load_or_default(self):
        """Load game state or create default"""
        if os.path.exists(self.filename):
            try:
                with open(self.filename, "r") as f:
                    return json.load(f)
            except Exception as e:
                logging.error(f"Error loading game state: {e}")
                backup = f"{self.filename}.bak.{int(time.time())}"
                shutil.copy2(self.filename, backup)
                logging.warning(f"Created backup at {backup}")
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
        """Save game state atomically"""
        try:
            tmp = f"{self.filename}.tmp"
            with open(tmp, "w") as f:
                json.dump(self.state, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.filename)
        except Exception as e:
            logging.error(f"Error saving game state: {e}")

    def get_state(self):
        """Get current game state"""
        return self.state

    def update_state(self, updates: dict):
        """Deep merge updates into game state"""
        def _deep_merge(orig, new):
            for k, v in new.items():
                if isinstance(v, dict) and isinstance(orig.get(k), dict):
                    _deep_merge(orig[k], v)
                else:
                    orig[k] = v
            return orig
        
        _deep_merge(self.state, updates)
        self.save()
        return self.state

    def add_history(self, entry: str):
        """Add entry to conversation history"""
        self.state.setdefault("conversation_history", []).append(entry)
        self.save()

    def handle_freeform(self, prompt: str, user_data: dict) -> str:
        try:
            from openai import OpenAI
            import os
            client = OpenAI(api_key=os.getenv("DM_OPENAI_API_KEY"))
            response = client.chat.completions.create(
                model="gpt-4.1",
                messages=[
                    {"role": "system", "content": "You are a Dungeon Master crafting a rich and immersive world."},
                    {"role": "user", "content": prompt}
                ]
            )
            result = response.choices[0].message.content.strip()

            # Optional: store in history
            self.state.setdefault("conversation_history", []).append(f"DM: {result}")
            self.save()

            return result
        except Exception as e:
            print(f"[handle_freeform ERROR]: {e}")
            return "⚠️ The Dungeon Master stares blankly, as if something broke in the multiverse..."

