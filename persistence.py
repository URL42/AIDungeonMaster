# persistence.py
import json
import os
import logging
from typing import Dict, Any

# Ensure dirs exist
LOG_DIR = os.getenv("DM_LOG_DIR", "./logs")
DATA_DIR = os.getenv("DM_DATA_DIR", "./data")
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

def setup_logger() -> logging.Logger:
    logger = logging.getLogger("dm_bot")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        log_path = os.path.join(LOG_DIR, "dm_bot.log")
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setLevel(logging.INFO)
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
        sh = logging.StreamHandler()
        sh.setLevel(logging.INFO)
        sh.setFormatter(fmt)
        logger.addHandler(sh)
    return logger

logger = setup_logger()

class GameStateManager:
    """
    Simple per-chat JSON persistence for game state.
    """
    def __init__(self, chat_id: int):
        self.chat_id = str(chat_id)
        self.path = os.path.join(DATA_DIR, f"{self.chat_id}.json")
        self._state = None
        self._load()

    def _default_state(self) -> Dict[str, Any]:
        return {
            "character": {
                "name": "",
                "class": "",
                "motivation": "",
                "abilities": {
                    "Strength": 10, "Dexterity": 10, "Constitution": 10,
                    "Intelligence": 10, "Wisdom": 10, "Charisma": 10
                },
            },
            "world": {},
            "level": 1,
            "xp": 0,
            "hp": 10,
            "inventory": [],
            "summary": "",
            "last_scene": "",
            "last_choices": [],
            "pending_choice_index": None,
        }

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self._state = json.load(f)
                logger.info(f"Loaded state for {self.chat_id}")
            except Exception as e:
                logger.error(f"Failed to load state for {self.chat_id}: {e}")
                self._state = self._default_state()
        else:
            self._state = self._default_state()

    def save_state(self, st: Dict[str, Any]):
        self._state = st
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save state for {self.chat_id}: {e}")

    def get_state(self) -> Dict[str, Any]:
        if self._state is None:
            self._load()
        return self._state

    def reset(self):
        self._state = self._default_state()
        self.save_state(self._state)
