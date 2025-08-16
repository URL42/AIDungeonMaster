# prompt_builder.py
import os
import json
import logging
from typing import Dict, Any, Optional, List
from openai import OpenAI

# --- Load API key from your custom env var name ------------------------------
# (We map DM_OPENAI_API_KEY -> OPENAI_API_KEY before constructing the client)
if not os.getenv("OPENAI_API_KEY") and os.getenv("DM_OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = os.getenv("DM_OPENAI_API_KEY")

client = OpenAI()  # uses OPENAI_API_KEY (mapped above)
log = logging.getLogger("dm_bot")

PRIMARY_RAW = os.getenv("DM_OPENAI_MODEL", "gpt-5").strip() or "gpt-5"
FALLBACK_MODEL = (os.getenv("DM_FALLBACK_MODEL", "").strip() or "gpt-4o-mini")

def resolve_chat_model(name: str) -> str:
    """
    If you set DM_OPENAI_MODEL=gpt-5 (or gpt-5-large, etc) but you're using Chat Completions,
    route to the chat-optimized alias to avoid empty content.
    """
    n = (name or "").lower()
    if n.startswith("gpt-5") and "chat" not in n:
        return os.getenv("DM_CHAT_MODEL_OVERRIDE", "gpt-5-chat-latest")
    return name

PRIMARY_MODEL = resolve_chat_model(PRIMARY_RAW)

ALLOWED_LIST: List[str] = [
    "Strength","Dexterity","Constitution","Intelligence","Wisdom","Charisma",
    "Perception","Stealth","Athletics","Arcana","History","Insight","Investigation",
    "Medicine","Nature","Religion","Animal Handling","Deception","Intimidation",
    "Performance","Persuasion"
]

class PromptBuilder:
    """
    Chat Completions wrapper with GPT-5-safe params:
      - prefers max_completion_tokens
      - drops temperature if the model rejects it
      - if primary returns empty/unparseable, retries with stricter prompt, then swaps model:
          1) try PRIMARY_MODEL again with no temperature
          2) try 'gpt-5-chat-latest' if PRIMARY_RAW was a 5-series but not chat
          3) try FALLBACK_MODEL (default gpt-4o-mini)
    """
    def __init__(self, gsm):
        self.gsm = gsm

    # ---- Core chat wrapper with guardrails -----------------------------------
    def _chat(self, *, messages, temperature: Optional[float] = 0.8, max_tokens: int = 1200, model: Optional[str] = None):
        mdl = (model or PRIMARY_MODEL)
        params = {
            "model": mdl,
            "messages": messages,
            "max_completion_tokens": max_tokens,
        }
        if temperature is not None:
            params["temperature"] = temperature

        def call(p):
            return client.chat.completions.create(**p)

        try:
            return call(params)
        except Exception as e:
            msg = str(e)

            # GPT-5 can require default temp; strip it
            if "temperature" in msg and ("Only the default" in msg or "Unsupported" in msg):
                params.pop("temperature", None)
                try:
                    return call(params)
                except Exception as e2:
                    msg = str(e2)

            # Some models want max_tokens instead
            if "max_completion_tokens" in msg and ("Unsupported parameter" in msg or "Unrecognized request" in msg):
                params.pop("max_completion_tokens", None)
                params["max_tokens"] = max_tokens
                try:
                    return call(params)
                except Exception as e3:
                    msg = str(e3)

            # or the inverse hint
            if "Use 'max_completion_tokens' instead" in msg and "max_tokens" in params:
                params.pop("max_tokens", None)
                params["max_completion_tokens"] = max_tokens
                return call(params)

            # Last try as-is (lets server normalize)
            params.pop("temperature", None)
            return call(params)

    # ---- JSON helpers --------------------------------------------------------
    def _extract_json(self, text: str) -> Dict[str, Any]:
        t = (text or "").strip()
        if not t:
            raise ValueError("empty content")
        try:
            return json.loads(t)
        except Exception:
            s, e = t.find("{"), t.rfind("}")
            if s != -1 and e != -1 and e > s:
                return json.loads(t[s:e+1])
            raise

    def _call_llm_json_attempt(self, system: str, user: str, model: Optional[str], max_tokens: int, temp: Optional[float]) -> Dict[str, Any]:
        resp = self._chat(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=temp,
            max_tokens=max_tokens,
            model=model,
        )
        text = (resp.choices[0].message.content or "").strip()
        return self._extract_json(text)

    def _call_llm_json(self, system: str, user: str, max_tokens: int = 1200) -> Dict[str, Any]:
        # 1) primary, normal temp
        try:
            return self._call_llm_json_attempt(system, user, PRIMARY_MODEL, max_tokens, 0.8)
        except Exception as e1:
            log.warning("Primary model %s JSON failed: %s", PRIMARY_MODEL, e1)

        # 2) primary, default temp (None)
        try:
            return self._call_llm_json_attempt(system, user, PRIMARY_MODEL, max_tokens, None)
        except Exception as e2:
            log.warning("Primary model %s JSON failed again: %s", PRIMARY_MODEL, e2)

        # 3) if your raw was a 5-series but not chat, try the chat alias explicitly
        if PRIMARY_RAW.startswith("gpt-5") and "chat" not in PRIMARY_MODEL:
            try:
                return self._call_llm_json_attempt(system, user, "gpt-5-chat-latest", max_tokens, None)
            except Exception as e3:
                log.warning("gpt-5-chat-latest JSON failed: %s", e3)

        # 4) fallback model (gpt-4o-mini by default) as a last resort
        if FALLBACK_MODEL and FALLBACK_MODEL != PRIMARY_MODEL:
            try:
                return self._call_llm_json_attempt(system, user, FALLBACK_MODEL, max_tokens, 0.8)
            except Exception as e4:
                log.error("Fallback model %s JSON failed: %s", FALLBACK_MODEL, e4)

        # 5) safety payload so the loop doesn't stall
        return {"narrative": "The wind shifts; reality steadies. What do you do?", "choices": []}

    # ---- Builders ------------------------------------------------------------
    def build_opening_scene(self) -> Dict[str, Any]:
        st = self.gsm.get_state()
        system = "You are a masterful, fair Dungeon Master for a Telegram text adventure. Be vivid, fair, and concise."
        compact = {
            "character": st.get("character", {}),
            "world": st.get("world", {}),
            "level": st.get("level", 1),
            "xp": st.get("xp", 0),
            "summary": st.get("summary", ""),
        }
        schema_hint = (
            'Return JSON with keys: narrative (string), '
            'choices (array of objects with: text (string), dc (int 5-25), '
            f'ability (one of {ALLOWED_LIST}), tags (array of strings)).'
        )
        user = (
            "Begin the adventure with a vivid opening (4–8 sentences). "
            "Establish tone and stakes. End with a natural prompt to act.\n\n"
            f"{schema_hint}\n\nSTATE:\n{json.dumps(compact)}"
        )
        return self._call_llm_json(system, user, max_tokens=1200)

    def build_scene_prompt(self, player_input: str) -> Dict[str, Any]:
        st = self.gsm.get_state()
        system = "You are a masterful, fair Dungeon Master for a Telegram text adventure. Keep continuity tight."
        compact = {
            "character": st.get("character", {}),
            "level": st.get("level", 1),
            "xp": st.get("xp", 0),
            "summary": st.get("summary", ""),
            "last_scene": st.get("last_scene", ""),
        }
        schema_hint = (
            'Return JSON with keys: narrative (string), '
            'choices (array of objects with: text (string), dc (int 5-25), '
            f'ability (one of {ALLOWED_LIST}), tags (array of strings)).'
        )
        user = (
            "Continue the story based on the player's action below. "
            "Offer 2–4 sensible choices. Allow the player to go off-list.\n\n"
            f"PLAYER ACTION: {player_input}\n\n"
            f"{schema_hint}\n\nSTATE:\n{json.dumps(compact)}"
        )
        return self._call_llm_json(system, user, max_tokens=1200)

    def build_outcome_prompt(self, choice: Dict[str, Any], roll: Dict[str, Any]) -> Dict[str, Any]:
        st = self.gsm.get_state()
        system = "You are a fair DM adjudicating d20 checks; reward success, narrate setbacks on failure."
        compact = {
            "character": st.get("character", {}),
            "level": st.get("level", 1),
            "xp": st.get("xp", 0),
            "summary": st.get("summary", ""),
            "last_scene": st.get("last_scene", ""),
        }
        schema_hint = (
            'Return JSON with keys: narrative (string), '
            'consequences (object: hp_delta (int), xp_delta (int), '
            'items_gained (string[]), items_lost (string[]), milestone (bool)), '
            'followup_choices (array of objects with: text (string), dc (int 5-25), '
            f'ability (one of {ALLOWED_LIST}), tags (string[])).'
        )
        user = (
            "Adjudicate the player's attempt given CHOICE and ROLL. "
            "Success → greater progress and XP; failure → setback and lesser XP. "
            "XP guidance: success ≈ DC*10, failure ≈ DC*5. Consider milestone for major beats.\n\n"
            f"CHOICE: {json.dumps(choice)}\n"
            f"ROLL: {json.dumps(roll)}\n\n"
            f"{schema_hint}\n\nSTATE:\n{json.dumps(compact)}"
        )
        return self._call_llm_json(system, user, max_tokens=1000)

    def build_clarification_prompt(self, question: str) -> str:
        st = self.gsm.get_state()
        system = "You answer rules/lore questions succinctly (3–6 sentences)."
        user = (
            "Answer the player's out-of-game question clearly and briefly. "
            "If it's rules, be concrete; if world/lore, respect established facts.\n"
            f"QUESTION: {question}\nSTATE SUMMARY: {st.get('summary','')}\n"
        )
        # Use primary; if it errors, the JSON path's fallback isn't needed for plain text
        resp = self._chat(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.4, max_tokens=400
        )
        return (resp.choices[0].message.content or "").strip()
