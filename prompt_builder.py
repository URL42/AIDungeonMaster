# prompt_builder.py
import os
import json
import logging
from typing import Dict, Any, Optional, List

# Load .env here too, so this file can run standalone
try:
    from dotenv import load_dotenv  # pip install python-dotenv if you don't have it
    load_dotenv()
except Exception:
    pass

from openai import OpenAI

# ---- Resolve API key from either name ----
_API_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("DM_OPENAI_API_KEY")
if not _API_KEY:
    raise RuntimeError("Set OPENAI_API_KEY or DM_OPENAI_API_KEY in your environment/.env")

# Construct client explicitly with the resolved key
client = OpenAI(api_key=_API_KEY)

log = logging.getLogger("dm_bot")

PRIMARY_MODEL = os.getenv("DM_OPENAI_MODEL", "gpt-5")
FALLBACK_MODEL = os.getenv("DM_FALLBACK_MODEL", "gpt-4o-mini")  # optional

# Ability/skill allowlist (string form for prompts and a regex-ish fallback)
ALLOWED_LIST: List[str] = [
    "Strength","Dexterity","Constitution","Intelligence","Wisdom","Charisma",
    "Perception","Stealth","Athletics","Arcana","History","Insight","Investigation",
    "Medicine","Nature","Religion","Animal Handling","Deception","Intimidation",
    "Performance","Persuasion"
]
ALLOWED_PATTERN = "|".join(ALLOWED_LIST)


class PromptBuilder:
    """
    LLM prompt/call layer for the Dungeon Master game.
    - Chat Completions (stable with openai 1.99.9)
    - GPT-5-safe param wrapper (max_completion_tokens, strip temperature when needed)
    - Strong prompt constraints for JSON; robust JSON salvage
    """

    def __init__(self, gsm):
        self.gsm = gsm

    # ---------- Low-level call wrapper (GPT-5 safe) ----------
    def _chat(self, *, messages, temperature: Optional[float] = 0.8, max_tokens: int = 1200, model: Optional[str] = None):
        mdl = model or PRIMARY_MODEL
        params = {
            "model": mdl,
            "messages": messages,
            # GPT-5 prefers max_completion_tokens on Chat Completions
            "max_completion_tokens": max_tokens,
        }
        if temperature is not None:
            params["temperature"] = temperature

        def do_call(p):
            return client.chat.completions.create(**p)

        try:
            return do_call(params)
        except Exception as e:
            msg = str(e)

            # Some 5-series allow only default temperature
            if "Unsupported value: 'temperature'" in msg or "Only the default (1) value is supported" in msg or "Unsupported parameter: 'temperature'" in msg:
                params.pop("temperature", None)
                try:
                    return do_call(params)
                except Exception as e2:
                    msg = str(e2)

            # Older/non-5 models want max_tokens instead of max_completion_tokens
            if "Unsupported parameter: 'max_completion_tokens'" in msg or "Unrecognized request argument" in msg:
                params.pop("max_completion_tokens", None)
                params["max_tokens"] = max_tokens
                try:
                    return do_call(params)
                except Exception as e3:
                    msg = str(e3)

            # Inverse hint from server
            if "Use 'max_completion_tokens' instead" in msg and "max_tokens" in params:
                params.pop("max_tokens", None)
                params["max_completion_tokens"] = max_tokens
                return do_call(params)

            # Final attempt: strip both special args
            params.pop("temperature", None)
            if "max_completion_tokens" in params:
                params.pop("max_completion_tokens")
                params["max_tokens"] = max_tokens
            return do_call(params)

    def _extract_json(self, text: str) -> Dict[str, Any]:
        t = (text or "").strip()
        if not t:
            raise ValueError("empty content")
        try:
            return json.loads(t)
        except Exception:
            # salvage biggest {...}
            s, e = t.find("{"), t.rfind("}")
            if s != -1 and e != -1 and e > s:
                return json.loads(t[s:e+1])
            raise

    def _call_llm_json_core(self, system: str, user: str, model_override: Optional[str], max_tokens: int) -> Dict[str, Any]:
        resp = self._chat(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.8,
            max_tokens=max_tokens,
            model=model_override,
        )
        text = (resp.choices[0].message.content or "").strip()
        try:
            return self._extract_json(text)
        except Exception:
            # Retry once with even stricter instruction and no temperature
            strict_user = user + "\nIMPORTANT: Return ONLY minified JSON per the schema. No prose. No code fences."
            resp2 = self._chat(
                messages=[{"role": "system", "content": system}, {"role": "user", "content": strict_user}],
                temperature=None,  # let GPT-5 default if needed
                max_tokens=max_tokens,
                model=model_override,
            )
            text2 = (resp2.choices[0].message.content or "").strip()
            return self._extract_json(text2)

    def _call_llm_json(self, system: str, user: str, max_tokens: int = 1200) -> Dict[str, Any]:
        try:
            return self._call_llm_json_core(system, user, None, max_tokens)
        except Exception as e:
            log.warning("Primary model %s JSON failed: %s", PRIMARY_MODEL, e)
            if FALLBACK_MODEL and FALLBACK_MODEL != PRIMARY_MODEL:
                try:
                    return self._call_llm_json_core(system, user, FALLBACK_MODEL, max_tokens)
                except Exception as e2:
                    log.error("Fallback model %s JSON failed: %s", FALLBACK_MODEL, e2)
        return {"narrative": "The wind shifts; the world feels momentarily out of focus. Regain your bearings—what do you do?", "choices": []}

    # ---------- Public builders ----------
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
            '{ "narrative": string, '
            '"choices": [ { "text": string, "dc": integer (5-25), '
            f'"ability": one of [{", ".join(ALLOWED_LIST)}], "tags": string[] }} ] }}'
        )
        user = (
            "Begin the adventure with a vivid opening (4–8 sentences). Establish tone, stakes, and end with a prompt to act.\n\n"
            "Return STRICT MINIFIED JSON ONLY (no prose, no code fences) matching:\n"
            f"{schema_hint}\n\n"
            f"STATE:\n{json.dumps(compact)}"
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
            '{ "narrative": string, '
            '"choices": [ { "text": string, "dc": integer (5-25), '
            f'"ability": one of [{", ".join(ALLOWED_LIST)}], "tags": string[] }} ] }}'
        )
        user = (
            "Continue the story based on the player's action below. Offer 2–4 sensible choices. Allow off-list actions.\n\n"
            f"PLAYER ACTION: {player_input}\n\n"
            "Return STRICT MINIFIED JSON ONLY (no prose, no code fences) matching:\n"
            f"{schema_hint}\n\n"
            f"STATE:\n{json.dumps(compact)}"
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
            '{ "narrative": string, '
            '"consequences": { "hp_delta": int, "xp_delta": int, '
            '"items_gained": string[], "items_lost": string[], "milestone": boolean }, '
            '"followup_choices": [ { "text": string, "dc": int (5-25), '
            f'"ability": one of [{", ".join(ALLOWED_LIST)}], "tags": string[] }} ] }}'
        )
        user = (
            "Adjudicate the player's attempt given CHOICE and ROLL.\n"
            "Success → greater progress and XP; failure → setback and lesser XP (guide: success ≈ DC*10 XP, failure ≈ DC*5 XP). "
            "Consider milestone on major beats.\n\n"
            f"CHOICE: {json.dumps(choice)}\n"
            f"ROLL: {json.dumps(roll)}\n\n"
            "Return STRICT MINIFIED JSON ONLY (no prose, no code fences) matching:\n"
            f"{schema_hint}\n\n"
            f"STATE:\n{json.dumps(compact)}"
        )
        return self._call_llm_json(system, user, max_tokens=1000)
