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
    """
    Builds prompts for the Dungeon Master game and calls the LLM with structured outputs.
    All story generations return strict JSON for deterministic UI/logic.
    """
    def __init__(self, gsm):
        self.gsm = gsm
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. "
                "Put it in your environment or in a .env file next to dungeon_master.py."
            )
        self.client = OpenAI(api_key=api_key)

    # ---------- LLM helper ----------
    def _call_llm_json(self, system: str, user: str, temperature: float = 0.8, max_tokens: int = 1200) -> Dict[str, Any]:
        """
        Call the model and require JSON output.
        """
        resp = self.client.chat.completions.create(
            model=MODEL,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user}
            ],
        )
        text = resp.choices[0].message.content.strip()
        try:
            return json.loads(text)
        except Exception:
            # Best-effort salvage of a JSON object
            start = text.find("{")
            end = text.rfind("}")
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
        return self._call_llm_json(system, user)

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
        return self._call_llm_json(system, user)

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
        resp = self.client.chat.completions.create(
            model=MODEL,
            temperature=0.4,
            max_tokens=400,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        return resp.choices[0].message.content.strip()

