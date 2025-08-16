# prompt_builder.py
import json
import os
import logging
from typing import Dict, Any, Optional
from openai import OpenAI

PRIMARY_MODEL = os.getenv("DM_OPENAI_MODEL", "gpt-5")
FALLBACK_MODEL = os.getenv("DM_FALLBACK_MODEL", "gpt-4o-mini")  # used ONLY if primary returns empty twice

ALLOWED_ABILITIES_AND_SKILLS = (
    "Strength|Dexterity|Constitution|Intelligence|Wisdom|Charisma|"
    "Perception|Stealth|Athletics|Arcana|History|Insight|Investigation|"
    "Medicine|Nature|Religion|Animal Handling|Deception|Intimidation|"
    "Performance|Persuasion"
)

class PromptBuilder:
    def __init__(self, gsm):
        self.gsm = gsm
        self.log = logging.getLogger("dm_bot")
        api_key = os.getenv("OPENAI_API_KEY") or os.getenv("DM_OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY / DM_OPENAI_API_KEY not set.")
        self.client = OpenAI(api_key=api_key)

    # ---- GPT-5-safe chat wrapper with param fallbacks ----
    def _chat(self, *, messages, response_format=None, temperature: Optional[float]=None,
              max_tokens=1200, model: Optional[str]=None):
        """
        Try GPT-5-style params first; on specific errors, retry with compatible params.
        Works with both 5-series (max_completion_tokens) and older models (max_tokens).
        """
        mdl = model or PRIMARY_MODEL
        params = {
            "model": mdl,
            "messages": messages,
            "max_completion_tokens": max_tokens,  # GPT-5 & reasoning variants
        }
        if response_format:
            params["response_format"] = response_format
        if temperature is not None:
            params["temperature"] = temperature

        def do_call(p):
            return self.client.chat.completions.create(**p)

        try:
            return do_call(params)
        except Exception as e:
            msg = str(e)

            # Some 5-series reject any non-default temperature
            if "temperature" in msg and ("Unsupported parameter" in msg or "Unsupported value" in msg or "Only the default" in msg):
                params.pop("temperature", None)
                try:
                    return do_call(params)
                except Exception as e2:
                    msg = str(e2)

            # Older chat models want max_tokens instead
            if "max_completion_tokens" in msg and ("Unsupported parameter" in msg or "Unrecognized request argument" in msg):
                params.pop("max_completion_tokens", None)
                params["max_tokens"] = max_tokens
                try:
                    return do_call(params)
                except Exception as e3:
                    msg = str(e3)

            # Some variants may not support response_format
            if "response_format" in msg and "Unsupported parameter" in msg:
                params.pop("response_format", None)
                return do_call(params)

            raise

    # ---- Robust JSON helpers ----
    def _extract_json(self, text: str) -> Dict[str, Any]:
        text = (text or "").strip()
        if not text:
            raise ValueError("empty content")
        try:
            return json.loads(text)
        except Exception:
            start, end = text.find("{"), text.rfind("}")
            if start != -1 and end != -1 and end > start:
                return json.loads(text[start:end + 1])
            raise

    def _call_llm_json_core(self, system: str, user: str, model_override: Optional[str],
                            temperature: Optional[float], max_tokens: int) -> Dict[str, Any]:
        # Attempt 1: JSON mode
        resp = self._chat(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            temperature=temperature,
            max_tokens=max_tokens,
            model=model_override,
        )
        text = (resp.choices[0].message.content or "").strip()
        try:
            return self._extract_json(text)
        except Exception:
            self.log.warning("JSON parse failed (attempt 1, model=%s). Raw (first 500): %r",
                             model_override or PRIMARY_MODEL, text[:500])

        # Attempt 2: stricter instruction, no response_format, default temperature
        strict_user = user + "\nIMPORTANT: Return ONLY valid minified JSON per the schema. No commentary, no code fences."
        resp2 = self._chat(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": strict_user}],
            temperature=None,  # drop temp to satisfy models that only support default
            max_tokens=max_tokens,
            model=model_override,
        )
        text2 = (resp2.choices[0].message.content or "").strip()
        try:
            return self._extract_json(text2)
        except Exception:
            self.log.error("JSON parse failed (attempt 2, model=%s). Raw (first 500): %r",
                           model_override or PRIMARY_MODEL, text2[:500])
            raise ValueError("unparseable")

    def _call_llm_json(self, system: str, user: str, temperature: Optional[float] = 0.8, max_tokens: int = 1200) -> Dict[str, Any]:
        """
        Try primary model; if both passes fail (empty/non-JSON), try fallback model once.
        If everything fails, return a safe minimal scene.
        """
        try:
            return self._call_llm_json_core(system, user, None, temperature, max_tokens)
        except Exception:
            # Fallback model (only if configured and different)
            if FALLBACK_MODEL and FALLBACK_MODEL != PRIMARY_MODEL:
                self.log.warning("Falling back to %s after primary model returned empty/unparseable output.",
                                 FALLBACK_MODEL)
                try:
                    return self._call_llm_json_core(system, user, FALLBACK_MODEL, temperature, max_tokens)
                except Exception:
                    pass

        # Final safety: keep the game moving
        return {
            "narrative": "The wind shifts; the world feels momentarily out of focus. Regain your bearings—what do you do?",
            "choices": []
        }

    # ---------- Prompts ----------
    def build_opening_scene(self) -> Dict[str, Any]:
        st = self.gsm.get_state()
        system = "You are a masterful, fair Dungeon Master for a Telegram text adventure."
        compact = {
            "character": st.get("character", {}),
            "world": st.get("world", {}),
            "level": st.get("level", 1),
            "xp": st.get("xp", 0),
            "summary": st.get("summary", ""),
        }
        user = (
            "Begin the adventure with a vivid opening (4–8 sentences), establish tone, stakes, and a prompt to act.\n"
            "Return STRICT JSON (no code fences):\n"
            "{\n"
            '  "narrative": "string",\n'
            f'  "choices": [ {{"text":"string","dc": int, "ability": "{ALLOWED_ABILITIES_AND_SKILLS}","tags":[] }} ]\n'
            "}\n"
            "2–4 choices. Each must include a relevant ability or skill."
            f"\n\nSTATE:\n{json.dumps(compact)}"
        )
        return self._call_llm_json(system, user, temperature=0.8)

    def build_scene_prompt(self, player_input: str) -> Dict[str, Any]:
        st = self.gsm.get_state()
        system = "You are a masterful, fair Dungeon Master for a Telegram text adventure."
        compact = {
            "character": st.get("character", {}),
            "level": st.get("level", 1),
            "xp": st.get("xp", 0),
            "summary": st.get("summary", ""),
            "last_scene": st.get("last_scene", ""),
        }
        user = (
            "Continue the story. The player acted/said:\n"
            f"{player_input}\n\n"
            "Return STRICT JSON (no code fences):\n"
            "{\n"
            '  "narrative": "string",\n'
            f'  "choices": [ {{"text":"string","dc": int, "ability": "{ALLOWED_ABILITIES_AND_SKILLS}","tags":[] }} ]\n'
            "}\n"
            "Allow the player to go off-list; remain coherent and responsive."
            f"\n\nSTATE:\n{json.dumps(compact)}"
        )
        return self._call_llm_json(system, user, temperature=0.8)

    def build_outcome_prompt(self, choice: Dict[str, Any], roll: Dict[str, Any]) -> Dict[str, Any]:
        st = self.gsm.get_state()
        system = "You are a fair DM adjudicating checks; reward success, narrate setbacks on failure."
        compact = {
            "character": st.get("character", {}),
            "level": st.get("level", 1),
            "xp": st.get("xp", 0),
            "summary": st.get("summary", ""),
            "last_scene": st.get("last_scene", ""),
        }
        user = (
            "Adjudicate the player's attempt.\n"
            f"CHOICE: {json.dumps(choice)}\n"
            f"ROLL: {json.dumps(roll)}\n\n"
            "Return STRICT JSON (no code fences):\n"
            "{\n"
            '  "narrative": "string",\n'
            '  "consequences": {"hp_delta": 0, "xp_delta": 0, "items_gained": [], "items_lost": [], "milestone": false},\n'
            f'  "followup_choices": [ {{"text":"string","dc": int, "ability":"{ALLOWED_ABILITIES_AND_SKILLS}","tags":[] }} ]\n'
            "}\n"
            "XP guidance: success ≈ DC*10, failure ≈ DC*5. Consider milestone for major beats."
            f"\n\nSTATE:\n{json.dumps(compact)}"
        )
        return self._call_llm_json(system, user, temperature=0.7)

    def build_clarification_prompt(self, question: str) -> str:
        st = self.gsm.get_state()
        system = "You answer rules/lore questions succinctly (3–6 sentences)."
        user = (
            "Answer the player's out-of-game question clearly and briefly. "
            "If it's rules, be concrete; if world/lore, respect established facts.\n"
            f"QUESTION: {question}\nSTATE SUMMARY: {st.get('summary','')}\n"
        )
        # Text path (with same guardrails and fallback if needed)
        try:
            resp = self._chat(
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.4,
                max_tokens=400,
            )
            text = (resp.choices[0].message.content or "").strip()
            if not text:
                raise ValueError("empty")
            return text
        except Exception:
            if FALLBACK_MODEL and FALLBACK_MODEL != PRIMARY_MODEL:
                self.log.warning("Falling back to %s for /ask.", FALLBACK_MODEL)
                resp2 = self._chat(
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    temperature=None,
                    max_tokens=400,
                    model=FALLBACK_MODEL,
                )
                return (resp2.choices[0].message.content or "").strip()
            return "I'll answer briefly once I get my bearings—try again in a moment."
