# persistence.py
import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional
from telegram.ext import BasePersistence, PersistenceInput

# ---------- Logger ----------
def setup_logger(name: str = "dm_bot", filename: str = "dm_bot.log") -> logging.Logger:
    """
    Resilient logger: tries ./logs, falls back to ~/.dm_bot on permission errors.
    """
    log_dir = Path("logs")
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        log_dir = Path.home() / ".dm_bot"
        log_dir.mkdir(parents=True, exist_ok=True)

    primary_log = log_dir / filename
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # already configured

    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    # Console handler
    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    # File handler (with fallback if permission denied)
    try:
        fh = logging.FileHandler(primary_log, encoding="utf-8")
    except PermissionError:
        fallback = Path.home() / ".dm_bot" / filename
        fallback.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(fallback, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger

logger = setup_logger()

# ---------- Defaults & helpers ----------
DEFAULT_STATE = {
    "character": {
        "name": "",
        "race_class": "",
        "motivation": "",
        "abilities": {"STR": 10, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10},
        "proficiencies": [],
        "hp": 10,
        "max_hp": 10,
    },
    "inventory": [],
    "quests": [],
    "world": {"genre": ""},
    "level": 1,
    "xp": 0,
    "summary": "",
    "last_scene": "",
    "choice_buffer": {"scene_id": "", "choices": []},
    "roll_mode": "normal",  # normal | advantage | disadvantage
    "last_rest_ts": 0,
}

XP_THRESHOLDS = [0, 300, 900, 2700, 6500, 14000, 23000, 34000, 48000, 64000]

SKILL_MAP = {
    "Strength": ("STR", []),
    "Dexterity": ("DEX", ["Acrobatics", "Stealth"]),
    "Constitution": ("CON", []),
    "Intelligence": ("INT", ["Arcana", "History", "Investigation", "Nature", "Religion"]),
    "Wisdom": ("WIS", ["Animal Handling", "Insight", "Medicine", "Perception"]),
    "Charisma": ("CHA", ["Deception", "Intimidation", "Performance", "Persuasion"]),
    # explicit skills
    "Athletics": ("STR", ["Athletics"]),
    "Stealth": ("DEX", ["Stealth"]),
    "Arcana": ("INT", ["Arcana"]),
    "History": ("INT", ["History"]),
    "Insight": ("WIS", ["Insight"]),
    "Investigation": ("INT", ["Investigation"]),
    "Medicine": ("WIS", ["Medicine"]),
    "Nature": ("INT", ["Nature"]),
    "Religion": ("INT", ["Religion"]),
    "Animal Handling": ("WIS", ["Animal Handling"]),
    "Deception": ("CHA", ["Deception"]),
    "Intimidation": ("CHA", ["Intimidation"]),
    "Performance": ("CHA", ["Performance"]),
    "Persuasion": ("CHA", ["Persuasion"]),
    "Perception": ("WIS", ["Perception"]),
}

def ability_mod(score: int) -> int:
    return (score - 10) // 2

def proficiency_bonus(level: int) -> int:
    # 1-4:+2, 5-8:+3, 9-12:+4, 13-16:+5, 17-20:+6
    return 2 + (max(1, level) - 1) // 4

# ---------- GameState ----------
@dataclass
class GameStateManager:
    user_id: int
    save_dir: Path = field(default_factory=lambda: Path("saves"))
    filename: Optional[Path] = None
    state: Dict[str, Any] = field(default_factory=lambda: json.loads(json.dumps(DEFAULT_STATE)))

    def __post_init__(self):
        self.save_dir.mkdir(parents=True, exist_ok=True)
        self.filename = self.save_dir / f"{self.user_id}.json"
        if self.filename.exists():
            try:
                self.state = json.loads(self.filename.read_text(encoding="utf-8"))
                logger.info(f"Loaded state for {self.user_id}")
            except Exception as e:
                logger.error(f"Failed to load state: {e} — using defaults")

    # ---- Core state ----
    def get_state(self) -> Dict[str, Any]:
        return self.state

    def save(self):
        assert self.filename is not None
        self.filename.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.debug(f"Saved state for {self.user_id}")

    def autosave(self):
        self.save()
        ts = int(time.time())
        backup = self.save_dir / f"{self.user_id}.{ts}.bak.json"
        try:
            shutil.copy(self.filename, backup)
        except Exception:
            pass
        backups = sorted(self.save_dir.glob(f"{self.user_id}.*.bak.json"))
        for old in backups[:-5]:
            try:
                old.unlink()
            except Exception:
                pass

    # ---- XP / Level ----
    def award_xp(self, amount: int):
        self.state["xp"] = max(0, self.state.get("xp", 0) + int(amount))
        self._maybe_level_up()

    def award_milestone(self):
        cur = self.state.get("xp", 0)
        lvl = self.state.get("level", 1)
        next_needed = XP_THRESHOLDS[min(lvl, len(XP_THRESHOLDS)-1)]
        if cur < next_needed:
            self.state["xp"] = next_needed
        self._maybe_level_up()

    def _maybe_level_up(self):
        xp = self.state.get("xp", 0)
        new_level = 1
        for i, thresh in enumerate(XP_THRESHOLDS):
            if xp >= thresh:
                new_level = i + 1
        if new_level > self.state.get("level", 1):
            self.state["level"] = new_level
            # modest HP bump
            self.state["character"]["max_hp"] += 3
            self.state["character"]["hp"] = min(
                self.state["character"]["hp"] + 3,
                self.state["character"]["max_hp"]
            )

    # ---- Rolls ----
    def compute_check(self, ability_or_skill: str, dc: int) -> Dict[str, Any]:
        """
        Computes a d20 check under roll_mode (normal/advantage/disadvantage).
        """
        char = self.state.get("character", {})
        profs = set(char.get("proficiencies", []))
        mode = self.state.get("roll_mode", "normal")
        key = (ability_or_skill or "Strength").strip().title()
        abbr, skills = SKILL_MAP.get(key, ("STR", []))

        ability_scores = char.get("abilities", {})
        score = int(ability_scores.get(abbr, 10))
        mod = ability_mod(score)

        import random
        d1 = random.randint(1, 20)
        d2 = random.randint(1, 20)
        if mode == "advantage":
            d20 = max(d1, d2)
            raw = (d1, d2)
        elif mode == "disadvantage":
            d20 = min(d1, d2)
            raw = (d1, d2)
        else:
            d20 = d1
            raw = (d1,)

        prof = proficiency_bonus(self.state.get("level", 1)) if any(s in profs for s in skills) or key in profs else 0
        total = d20 + mod + prof
        return {
            "mode": mode,
            "raw": list(raw),
            "d20": d20,
            "mod": mod,
            "prof": prof,
            "total": total,
            "dc": int(dc),
            "ability": ability_or_skill,
            "success": total >= int(dc),
        }

# ---------- Telegram Persistence (PTB v20-complete) ----------
class TelegramJSONPersistence(BasePersistence):
    """
    JSON file persistence for PTB v20+. Implements all required async methods so
    ConversationHandler state, user/chat/bot data, and callback data survive restarts.
    """
    def __init__(self, path: str = "ptb_persistence.json"):
        super().__init__()  # PTB base init
        self.path = Path(path)
        self.data: Dict[str, Any] = {
            "user_data": {}, "chat_data": {}, "bot_data": {},
            "conversations": {}, "callback_data": {}
        }
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                pass

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # --- REQUIRED: what to persist ---
    def get_persistence_input(self) -> PersistenceInput:
        return PersistenceInput(
            user_data=True, chat_data=True, bot_data=True,
            conversations=True, callback_data=True
        )

    # --- REQUIRED: getters ---
    async def get_user_data(self) -> Dict[str, Dict[str, Any]]:
        return self.data.get("user_data", {})

    async def get_chat_data(self) -> Dict[str, Dict[str, Any]]:
        return self.data.get("chat_data", {})

    async def get_bot_data(self) -> Dict[str, Any]:
        return self.data.get("bot_data", {})

    async def get_conversations(self, name: str) -> Dict[str, object]:
        return self.data.get("conversations", {}).get(name, {}).copy()

    async def get_callback_data(self) -> Dict[str, Any]:
        return self.data.get("callback_data", {})

    # --- REQUIRED: updaters ---
    async def update_user_data(self, user_id: int, data: Dict[str, Any]):
        self.data.setdefault("user_data", {})[str(user_id)] = data
        self._save()

    async def update_chat_data(self, chat_id: int, data: Dict[str, Any]):
        self.data.setdefault("chat_data", {})[str(chat_id)] = data
        self._save()

    async def update_bot_data(self, data: Dict[str, Any]):
        self.data["bot_data"] = data
        self._save()

    async def update_conversation(self, name: str, key: tuple, new_state: Optional[object]):
        convs_for_name = self.data.setdefault("conversations", {}).setdefault(name, {})
        skey = str(key)
        if new_state is None:
            convs_for_name.pop(skey, None)
        else:
            convs_for_name[skey] = new_state
        self._save()

    async def update_callback_data(self, data: Dict[str, Any]):
        self.data["callback_data"] = data
        self._save()

    # --- REQUIRED: drops ---
    async def drop_user_data(self, user_id: int):
        self.data.get("user_data", {}).pop(str(user_id), None)
        self._save()

    async def drop_chat_data(self, chat_id: int):
        self.data.get("chat_data", {}).pop(str(chat_id), None)
        self._save()

    # --- REQUIRED: refresh (sync in-memory dicts to disk) ---
    async def refresh_user_data(self, user_id: int, user_data: Dict[str, Any]):
        self.data.setdefault("user_data", {})[str(user_id)] = user_data
        self._save()

    async def refresh_chat_data(self, chat_id: int, chat_data: Dict[str, Any]):
        self.data.setdefault("chat_data", {})[str(chat_id)] = chat_data
        self._save()

    async def refresh_bot_data(self, bot_data: Dict[str, Any]):
        self.data["bot_data"] = bot_data
        self._save()

    # --- REQUIRED: flush ---
    async def flush(self):
        self._save()

