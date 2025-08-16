# prompt_builder.py
import os
import json
import logging
from typing import Dict, Any, Optional, List
from openai import OpenAI

# --- OpenAI client (use your env var name exactly) ---------------------------
# Do NOT rely on OPENAI_API_KEY; we read DM_OPENAI_API_KEY directly.
_API_KEY = os.getenv("DM_OPENAI_API_KEY")
if not _API_KEY:
    raise RuntimeError("Set DM_OPENAI_API_KEY in your environment/.env")
client = OpenAI(api_key=_API_KEY)

log = logging.getLogger("dm_bot")

# --- Model resolution ---------------------------------------------------------
PRIMARY_RAW = (os.getenv("DM_OPENAI_MODEL") or "gpt-5").strip()
FALLBACK_MODEL = (os.getenv("DM_FALLBACK_MODEL") or "gpt-4o-mini").strip()

def _resolve_chat_model(name: str) -> str:
    """
    If using GPT-5 with Chat Completions, prefer the chat-optimized alias
    to avoid rare 'empty content' edge cases.
    """
    n = (name or "").lower()
    if n.startswith("gpt-5") and "chat" not in n:
        return "gpt-5-chat-latest"
    return name

PRIMARY_MODEL = _resolve_chat_model(PRIMARY_RAW)

# --- Allowed abilities/skills (for LLM hints only) ---------------------------
ALLOWED_LIST: List[str] = [
    "Strength","Dexterity","Constitution","Intelligence","Wisdom","Charisma",
    "Perception","Stealth","Athletics","Arcana","History","Insight","Investigation",
    "Medicine","Nature","Religion","Animal Handling","Deception","Intimidation",
    "Performance","Persuasion"
]

# ============================================================================

class PromptBuilder:
    """
    Chat Completions wrapper with GPT-5-safe params:
      - prefers max_completion_tokens (falls back to max_tokens if needed)
      - drops temperature if the model rejects it
      - retries with stricter prompt; then tries gpt-5-chat-latest; then fallback
    Returns strict JSON objects (we enforce via prompt + parsing salvage).
    """

    def __init__(self, gsm):
        self.gsm = gsm

    # ---------- Low-level call wrapper (defensive for GPT-5) ----------
    def _chat(
        self, *,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = 0.8,
        max_tokens: int = 1200,
        model: Optional[str] = None
    ):
        mdl = model or PRIMARY_MODEL
        params = {
            "model": mdl,
            "messages": messages,
            "max_completion_tokens": max_tokens,  # GPT-5 prefers this on chat.completions
        }
        if temperature is not None:
            params["temperature"] = temperature

        def do_call(p):
            return client.chat.completions.create(**p)

        try:
            return do_call(params)
        except Exception as e:
            msg = str(e)

            # Some 5-series only allow default temperature (omit it)
            if "temperature" in msg and ("Only the default" in msg or "Unsupported" in msg):
                params.pop("temperature", None)
                try:
                    return do_call(params)
                except Exception as e2:
                    msg = str(e2)

            # Some models want max_tokens instead of max_completion_tokens
            if "max_completion_tokens" in msg and ("Unsupported parameter" in msg or "Unrecognized request" in msg):
                params.pop("max_completion_tokens", None)
                params["max_tokens"] = max_tokens
                try:
                    return do_call(params)
                except Exception as e3:
                    msg = str(e3)

            # Or the inverse hint (use max_completion_tokens)
            if "Use 'max_completion_tokens' instead" in msg and "max_tokens" in params:
                params.pop("max_tokens", None)
                params["max_completion_tokens"] = max_tokens
                return do_call(params)

            # Final attempt: strip temp and let server normalize
            params.pop("temperature", None)
            return do_call(params)

    # ---------- JSON helpers ----------
    def _extract_json(self, text: str) -> Dict[str, Any]:
        t = (text or "").strip()
        if not t:
            raise ValueError("empty content")
        try:
            return json.loads(t)
        except Exception:
            # Salvage the largest {...} block if extra prose sneaks in
            s, e = t.find("{"), t.rfind("}")
            if s != -1 and e != -1 and e > s:
                return json.loads(t[s:e+1])
            raise

    def _call_llm_json_attempt(
        self, system: str, user: str, model: Optional[str], max_tokens: int, temp: Optional[float]
    ) -> Dict[str, Any]:
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

        # 2) primary, default temp (omit)
        try:
            return self._call_llm_json_attempt(system, user, PRIMARY_MODEL, max_tokens, None)
        except Exception as e2:
            log.warning("Primary model %s JSON failed again: %s", PRIMARY_MODEL, e2)

        # 3) if raw was 5-series but not chat alias, try the alias explicitly
        if PRIMARY_RAW.startswith("gpt-5") and "chat" not in PRIMARY_MODEL:
            try:
                return self._call_llm_json_attempt(system, user, "gpt-5-chat-latest", max_tokens, None)
            except Exception as e3:
                log.warning("gpt-5-chat-latest JSON failed: %s", e3)

        # 4) fallback model last
        if FALLBACK_MODEL and FALLBACK_MODEL != PRIMARY_MODEL:
            try:
                return self._call_llm_json_attempt(system, user, FALLBACK_MODEL, max_tokens, 0.8)
            except Exception as e4:
                log.error("Fallback model %s JSON failed: %s", FALLBACK_MODEL, e4)

        # 5) safety payload so the loop doesn’t stall
        return {
            "narrative": "The wind shifts; reality steadies. What do you do?",
            "choices": []
        }

    # =================== Public builders used by your bot =====================

    def build_opening_scene(self) -> Dict[str, Any]:
        st = self.gsm.get_state()
        system = (
            "You are a masterful, fair Dungeon Master for a Telegram text adventure. "
            "Be vivid, responsive, and concise."
        )
        compact = {
            "character": st.get("character", {}),
            "world": st.get("world", {}),
            "level": st.get("level", 1),
            "xp": st.get("xp", 0),
            "summary": st.get("summary", ""),
        }
        # Keep schema instructions as plain English to avoid f-string brace issues
        schema_hint = (
            "Return ONLY minified JSON with keys:\n"
            "- narrative: string (4–8 sentences, end with a prompt to act)\n"
            "- choices: array of 2–4 objects, each with:\n"
            "    text: string (a concrete action)\n"
            "    dc: integer between 5 and 25\n"
            f"    ability: string from this set {ALLOWED_LIST}\n"
            "    tags: array of strings (may be empty)\n"
        )
        user = (
            "Begin the adventure with a vivid opening. Establish tone and stakes. "
            "Offer sensible actions.\n\n"
            f"{schema_hint}\nSTATE:\n{json.dumps(compact)}"
        )
        return self._call_llm_json(system, user, max_tokens=1200)

    def build_scene_prompt(self, player_input: str) -> Dict[str, Any]:
        st = self.gsm.get_state()
        system = (
            "You are a masterful, fair Dungeon Master for a Telegram text adventure. "
            "Maintain continuity with prior scenes."
        )
        compact = {
            "character": st.get("character", {}),
            "level": st.get("level", 1),
            "xp": st.get("xp", 0),
            "summary": st.get("summary", ""),
            "last_scene": st.get("last_scene", ""),
        }
        schema_hint = (
            "Return ONLY minified JSON with keys:\n"
            "- narrative: string\n"
            "- choices: array of 2–4 objects with fields text (string), dc (int 5–25), "
            f"ability (one of {ALLOWED_LIST}), tags (string[])\n"
        )
        user = (
            "Continue the story based on the player's action below. "
            "Offer 2–4 sensible choices. The player may also go off-list.\n\n"
            f"PLAYER ACTION: {player_input}\n\n"
            f"{schema_hint}\nSTATE:\n{json.dumps(compact)}"
        )
        return self._call_llm_json(system, user, max_tokens=1200)

    def build_outcome_prompt(self, choice: Dict[str, Any], roll: Dict[str, Any]) -> Dict[str, Any]:
        st = self.gsm.get_state()
        system = (
            "You are a fair DM adjudicating d20 checks; reward success, narrate setbacks on failure."
        )
        compact = {
            "character": st.get("character", {}),
            "level": st.get("level", 1),
            "xp": st.get("xp", 0),
            "summary": st.get("summary", ""),
            "last_scene": st.get("last_scene", ""),
        }
        schema_hint = (
            "Return ONLY minified JSON with keys:\n"
            "- narrative: string\n"
            "- consequences: object with hp_delta (int), xp_delta (int), "
            "items_gained (string[]), items_lost (string[]), milestone (boolean)\n"
            "- followup_choices: array of 0–4 objects with text (string), dc (int 5–25), "
            f"ability (one of {ALLOWED_LIST}), tags (string[])\n"
        )
        user = (
            "Adjudicate the player's attempt given CHOICE and ROLL. "
            "Success → greater progress and XP; failure → setback and lesser XP. "
            "XP guidance: success ≈ DC*10, failure ≈ DC*5. Consider milestone for major beats.\n\n"
            f"CHOICE: {json.dumps(choice)}\n"
            f"ROLL: {json.dumps(roll)}\n\n"
            f"{schema_hint}\nSTATE:\n{json.dumps(compact)}"
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
        # Plain text answer; reuse the same guarded chat wrapper
        resp = self._chat(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.4, max_tokens=400, model=PRIMARY_MODEL
        )
        return (resp.choices[0].message.content or "").strip()

