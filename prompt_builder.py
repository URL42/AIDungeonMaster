# prompt_builder.py
import os
import json
import logging
from typing import Dict, Any, Optional, List

# optional: load .env so this file can run standalone too
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from openai import OpenAI

# Map your custom key name to the standard one if needed
if not os.getenv("OPENAI_API_KEY") and os.getenv("DM_OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = os.getenv("DM_OPENAI_API_KEY")

client = OpenAI()  # uses OPENAI_API_KEY from env
log = logging.getLogger("dm_bot")

PRIMARY_MODEL = os.getenv("DM_OPENAI_MODEL", "gpt-5")
FALLBACK_MODEL = os.getenv("DM_FALLBACK_MODEL", "").strip() or None

ALLOWED_LIST: List[str] = [
    "Strength","Dexterity","Constitution","Intelligence","Wisdom","Charisma",
    "Perception","Stealth","Athletics","Arcana","History","Insight","Investigation",
    "Medicine","Nature","Religion","Animal Handling","Deception","Intimidation",
    "Performance","Persuasion"
]

class PromptBuilder:
    """
    LLM prompt/call layer for the Dungeon Master game.
    - Chat Completions (stable for openai 1.99.9)
    - GPT-5-safe param wrapper (max_completion_tokens; strips temperature if needed)
    - JSON-only prompts + robust salvage parser
    """
    def __init__(self, gsm):
        self.gsm = gsm

    # ---------- Low-level call wrapper (GPT-5 safe) ----------
    def _chat(self, *, messages, temperature: Optional[float] = 0.8, max_tokens: int = 1200, model: Optional[str] = None):
        mdl = model or PRIMARY_MODEL
        params = {
            "model": mdl,
            "messages": messages,
            "max_completion_tokens": max_tokens,  # GPT-5 wants this on chat.completions
        }
        if temperature is not None:
            params["temperature"] = temperature

        def do_call(p):
            return client.chat.completions.create(**p)

        try:
            return do_call(params)
        except Exception as e:
            msg = str(e)

            # Some 5-series only allow default temperature
            if ("temperature" in msg) and (
                "Only the default (1) value is supported" in msg
                or "Unsupported value" in msg or "Unsupported parameter" in msg
            ):
                params.pop("temperature", None)
                try:
                    return do_call(params)
                except Exception as e2:
                    msg = str(e2)

            # Some models want max_tokens instead of max_completion_tokens
            if ("max_completion_tokens" in msg) and ("Unsupported parameter" in msg or "Unrecognized request argument" in msg):
                params.pop("max_completion_tokens", None)
                params["max_tokens"] = max_tokens
                try:
                    return do_call(params)
                except Exception as e3:
                    msg = str(e3)

            # Inverse hint
            if ("max_tokens" in msg) and ("Use 'max_completion_tokens' instead" in msg):
                params.pop("max_tokens", None)
                params["max_completion_tokens"] = max_tokens
                return do_call(params)

            # Last attempt: strip temp & pick a safer model if provided
            params.pop("temperature", None)
            if "max_completion_tokens" in params:
                # keep as-is (most 5-series accept it)
                pass
            if FALLBACK_MODEL and mdl != FALLBACK_MODEL:
                params["model"] = FALLBACK_MODEL
            return do_call(params)

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
            strict_user = user + "\nIMPORTANT: Return ONLY minified JSON. No prose, no code fences."
            resp2 = self._chat(
                messages=[{"role": "system", "content": system}, {"role": "user", "content": strict_user}],
                temperature=None,
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
        return {"narrative": "The wind shifts; reality wobbles. What do you do?", "choices": []}

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
        resp = self._chat(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.4, max_tokens=400
        )
        return (resp.choices[0].message.content or "").strip()

