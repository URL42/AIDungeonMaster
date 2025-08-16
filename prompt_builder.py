# prompt_builder.py
import json
import os
from typing import Dict, Any
from openai import OpenAI

MODEL = os.getenv("DM_OPENAI_MODEL", "gpt-5")

ALLOWED_ABILITIES_AND_SKILLS = (
    "Strength|Dexterity|Constitution|Intelligence|Wisdom|Charisma|"
    "Perception|Stealth|Athletics|Arcana|History|Insight|Investigation|"
    "Medicine|Nature|Religion|Animal Handling|Deception|Intimidation|"
    "Performance|Persuasion"
)

class PromptBuilder:
    def __init__(self, gsm):
        self.gsm = gsm
        api_key = os.getenv("OPENAI_API_KEY") or os.getenv("DM_OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY / DM_OPENAI_API_KEY not set.")
        self.client = OpenAI(api_key=api_key)

    # ---- GPT-5 safe chat wrapper ----
    def _chat(self, *, messages, response_format=None, temperature=None, max_tokens=1200):
        """
        Try GPT-5-style params first. On specific errors, retry with compatible params.
        """
        params = {
            "model": MODEL,
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

            # Older chat models want max_tokens, not max_completion_tokens
            if "max_completion_tokens" in msg and ("Unsupported parameter" in msg or "Unrecognized request argument" in msg):
                params.pop("max_completion_tokens", None)
                params["max_tokens"] = max_tokens
                try:
                    return do_call(params)
                except Exception as e3:
                    msg = str(e3)

            # If we started with max_tokens (older path) but model wants max_completion_tokens
            if "max_tokens" in msg and "Use 'max_completion_tokens' instead" in msg:
                params.pop("max_tokens", None)
                params["max_completion_tokens"] = max_tokens
                try:
                    return do_call(params)
                except Exception as e4:
                    msg = str(e4)

            # Some variants may not support response_format
            if "response_format" in msg and "Unsupported parameter" in msg:
                params.pop("response_format", None)
                return do_call(params)

            # If we reach here, bubble up the original error
            raise

    def _call_llm_json(self, system: str, user: str, temperature: float | None = 0.8, max_tokens: int = 1200) -> Dict[str, Any]:
        resp = self._chat(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            temperature=temperature,
            max_tokens=max_tokens,
        )
        text = resp.choices[0].message.content.strip()
        try:
            return json.loads(text)
        except Exception:
            start, end = text.find("{"), text.rfind("}")
            if start != -1 and end != -1 and end > start:
                return json.loads(text[start:end+1])
            raise

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
        # temperature will be dropped automatically if the model forbids it
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
        resp = self._chat(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.4,
            max_tokens=400,
        )
        return resp.choices[0].message.content.strip()
