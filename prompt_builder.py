# prompt_builder.py
import json
import os
import logging
from typing import Dict, Any, Optional
from openai import OpenAI

PRIMARY_MODEL = os.getenv("DM_OPENAI_MODEL", "gpt-5")
FALLBACK_MODEL = os.getenv("DM_FALLBACK_MODEL", "gpt-4o-mini")  # used ONLY if primary fails twice

ALLOWED_ABILITIES_AND_SKILLS = (
    "Strength|Dexterity|Constitution|Intelligence|Wisdom|Charisma|"
    "Perception|Stealth|Athletics|Arcana|History|Insight|Investigation|"
    "Medicine|Nature|Religion|Animal Handling|Deception|Intimidation|"
    "Performance|Persuasion"
)

class PromptBuilder:
    """
    LLM prompt/call layer for the Dungeon Master game using the Responses API.
    Enforces strict JSON for scene/outcomes and has robust fallbacks.
    """
    def __init__(self, gsm):
        self.gsm = gsm
        self.log = logging.getLogger("dm_bot")
        api_key = os.getenv("OPENAI_API_KEY") or os.getenv("DM_OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY / DM_OPENAI_API_KEY not set.")
        self.client = OpenAI(api_key=api_key)

    # ---------- Low-level Responses API helpers ----------
    def _responses(self, *, messages, response_format: Optional[dict] = None,
                   max_tokens: int = 1200, model: Optional[str] = None):
        """
        Call OpenAI Responses API. We avoid custom temperatures because some GPT-5
        variants only allow the default. Use max_output_tokens per API.
        """
        mdl = model or PRIMARY_MODEL
        params = {
            "model": mdl,
            # Responses API accepts "input" for simple strings OR chat-style "messages".
            # We send messages to preserve roles/system prompts.
            "messages": messages,
            "max_output_tokens": max_tokens,
        }
        if response_format:
            params["response_format"] = response_format
        return self.client.responses.create(**params)

    def _resp_text(self, resp) -> str:
        """
        Extract text from various Responses API SDK shapes.
        Prefer resp.output_text when available; otherwise walk .output[].content[].
        """
        # Newer SDKs provide a convenience
        text = getattr(resp, "output_text", None)
        if isinstance(text, str) and text.strip():
            return text.strip()

        chunks = []
        try:
            output = getattr(resp, "output", None) or getattr(resp, "outputs", None)
            if output:
                for item in output:
                    # Each item typically has .content (list of parts)
                    parts = getattr(item, "content", None)
                    if not parts:
                        continue
                    for part in parts:
                        # part could have .type == "output_text" and .text.value
                        ptype = getattr(part, "type", None)
                        if ptype == "output_text":
                            t = getattr(getattr(part, "text", None), "value", None)
                            if t:
                                chunks.append(t)
                        # fallback: some SDKs expose .text directly as str
                        t2 = getattr(part, "text", None)
                        if isinstance(t2, str) and t2:
                            chunks.append(t2)
        except Exception:
            pass

        combined = "".join(chunks).strip()
        return combined

    # ---------- JSON call with salvage, retry, and model fallback ----------
    def _extract_json(self, text: str) -> Dict[str, Any]:
        text = (text or "").strip()
        if not text:
            raise ValueError("empty content")
        try:
            return json.loads(text)
        except Exception:
            # salvage {...}
            start, end = text.find("{"), text.rfind("}")
            if start != -1 and end != -1 and end > start:
                return json.loads(text[start:end+1])
            raise

    def _call_llm_json_core(self, system: str, user: str, model_override: Optional[str],
                            max_tokens: int) -> Dict[str, Any]:
        # Attempt 1: JSON mode
        resp = self._responses(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            max_tokens=max_tokens,
            model=model_override,
        )
        text = self._resp_text(resp)
        try:
            return self._extract_json(text)
        except Exception:
            self.log.warning("JSON parse failed (attempt 1, model=%s). Raw (first 500): %r",
                             model_override or PRIMARY_MODEL, (text or "")[:500])

        # Attempt 2: stricter instruction, no response_format (some variants can be picky)
        strict_user = user + "\nIMPORTANT: Return ONLY valid minified JSON per the schema. No commentary, no code fences."
        resp2 = self._responses(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": strict_user}],
            response_format=None,
            max_tokens=max_tokens,
            model=model_override,
        )
        text2 = self._resp_text(resp2)
        try:
            return self._extract_json(text2)
        except Exception:
            self.log.error("JSON parse failed (attempt 2, model=%s). Raw (first 500): %r",
                           model_override or PRIMARY_MODEL, (text2 or "")[:500])
            raise ValueError("unparseable")

    def _call_llm_json(self, system: str, user: str, max_tokens: int = 1200) -> Dict[str, Any]:
        """
        Try primary model first; if both passes fail (empty/non-JSON) switch to fallback model once.
        If everything fails, return a safe minimal scene so the game continues.
        """
        try:
            return self._call_llm_json_core(system, user, None, max_tokens)
        except Exception:
            if FALLBACK_MODEL and FALLBACK_MODEL != PRIMARY_MODEL:
                self.log.warning("Falling back to %s after primary model returned empty/unparseable output.",
                                 FALLBACK_MODEL)
                try:
                    return self._call_llm_json_core(system, user, FALLBACK_MODEL, max_tokens)
                except Exception:
                    pass

        # Final safety
        return {
            "narrative": "The wind shifts; the world feels momentarily out of focus. Regain your bearings—what do you do?",
            "choices": []
        }

    # ---------- Public builders ----------
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
        return self._call_llm_json(system, user, max_tokens=1200)

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
        return self._call_llm_json(system, user, max_tokens=1200)

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
        return self._call_llm_json(system, user, max_tokens=1000)

    def build_clarification_prompt(self, question: str) -> str:
        st = self.gsm.get_state()
        system = "You answer rules/lore questions succinctly (3–6 sentences)."
        user = (
            "Answer the player's out-of-game question clearly and briefly. "
            "If it's rules, be concrete; if world/lore, respect established facts.\n"
            f"QUESTION: {question}\nSTATE SUMMARY: {st.get('summary','')}\n"
        )
        # No response_format here—plain text answer
        try:
            resp = self._responses(
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format=None,
                max_tokens=400,
                model=None,
            )
            text = self._resp_text(resp)
            if text:
                return text
            raise ValueError("empty")
        except Exception:
            if FALLBACK_MODEL and FALLBACK_MODEL != PRIMARY_MODEL:
                self.log.warning("Falling back to %s for /ask.", FALLBACK_MODEL)
                resp2 = self._responses(
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    response_format=None,
                    max_tokens=400,
                    model=FALLBACK_MODEL,
                )
                return self._resp_text(resp2) or "I’ll answer as soon as I can—try again."
            return "I’ll answer as soon as I can—try again."
