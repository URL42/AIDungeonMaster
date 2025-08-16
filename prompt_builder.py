# prompt_builder.py
import json
import os
import logging
from typing import Dict, Any, Optional
from openai import OpenAI

PRIMARY_MODEL = os.getenv("DM_OPENAI_MODEL", "gpt-5")
FALLBACK_MODEL = os.getenv("DM_FALLBACK_MODEL", "gpt-4o-mini")  # used ONLY if primary fails twice

ALLOWED = [
    "Strength","Dexterity","Constitution","Intelligence","Wisdom","Charisma",
    "Perception","Stealth","Athletics","Arcana","History","Insight","Investigation",
    "Medicine","Nature","Religion","Animal Handling","Deception","Intimidation",
    "Performance","Persuasion",
]

def opening_scene_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "narrative": {"type": "string"},
            "choices": {
                "type": "array",
                "minItems": 2,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "dc": {"type": "integer", "minimum": 5, "maximum": 25},
                        "ability": {"type": "string", "enum": ALLOWED},
                        "tags": {"type": "array", "items": {"type": "string"}, "default": []},
                    },
                    "required": ["text", "dc", "ability"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["narrative", "choices"],
        "additionalProperties": False,
    }

def scene_schema() -> Dict[str, Any]:
    # same shape as opening
    return opening_scene_schema()

def outcome_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "narrative": {"type": "string"},
            "consequences": {
                "type": "object",
                "properties": {
                    "hp_delta": {"type": "integer", "default": 0},
                    "xp_delta": {"type": "integer", "default": 0},
                    "items_gained": {"type": "array", "items": {"type": "string"}, "default": []},
                    "items_lost": {"type": "array", "items": {"type": "string"}, "default": []},
                    "milestone": {"type": "boolean", "default": False},
                },
                "required": [],
                "additionalProperties": False,
            },
            "followup_choices": {
                "type": "array",
                "minItems": 0,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "dc": {"type": "integer", "minimum": 5, "maximum": 25},
                        "ability": {"type": "string", "enum": ALLOWED},
                        "tags": {"type": "array", "items": {"type": "string"}, "default": []},
                    },
                    "required": ["text", "dc", "ability"],
                    "additionalProperties": False,
                },
                "default": [],
            },
        },
        "required": ["narrative","consequences","followup_choices"],
        "additionalProperties": False,
    }

class PromptBuilder:
    """
    LLM prompt/call layer for the Dungeon Master game using the Responses API + JSON Schema.
    Guarantees a text output matching the schema so we don't get empty content.
    """
    def __init__(self, gsm):
        self.gsm = gsm
        self.log = logging.getLogger("dm_bot")
        api_key = os.getenv("OPENAI_API_KEY") or os.getenv("DM_OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY / DM_OPENAI_API_KEY not set.")
        self.client = OpenAI(api_key=api_key)

    # ---------- Responses API helpers ----------
    def _responses(self, *, input_payload, json_schema: Optional[Dict[str, Any]] = None,
                   max_tokens: int = 1200, model: Optional[str] = None):
        """
        Call OpenAI Responses API with canonical 'input', JSON Schema (strict) for structured text output,
        and GPT-5-friendly knobs for less invisible reasoning.
        """
        mdl = model or PRIMARY_MODEL
        params = {
            "model": mdl,
            "input": input_payload,               # canonical key for Responses API
            "max_output_tokens": max_tokens,      # correct knob for Responses
            "verbosity": "low",                   # GPT-5: keep it tight
            "reasoning": {"effort": "minimal"},   # GPT-5: avoid long hidden reasoning-only outputs
        }
        if json_schema:
            params["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "dm_struct",
                    "schema": json_schema,
                    "strict": True,
                }
            }
        # Do NOT send temperature unless you truly need it; some 5-series force default only
        return self.client.responses.create(**params)

    def _resp_text(self, resp) -> str:
        """
        Extract text robustly. Prefer response.output_text; otherwise walk outputs.
        """
        txt = getattr(resp, "output_text", None)
        if isinstance(txt, str) and txt.strip():
            return txt.strip()
        chunks = []
        try:
            output = getattr(resp, "output", None) or getattr(resp, "outputs", None)
            if output:
                for item in output:
                    parts = getattr(item, "content", None)
                    if not parts:
                        continue
                    for part in parts:
                        if getattr(part, "type", None) == "output_text":
                            val = getattr(getattr(part, "text", None), "value", None)
                            if val:
                                chunks.append(val)
                        else:
                            # Some SDKs expose part.text as a raw string
                            t2 = getattr(part, "text", None)
                            if isinstance(t2, str) and t2:
                                chunks.append(t2)
        except Exception:
            pass
        return "".join(chunks).strip()

    # ---------- JSON call with retry + fallback ----------
    def _extract_json(self, text: str) -> Dict[str, Any]:
        t = (text or "").strip()
        if not t:
            raise ValueError("empty content")
        try:
            return json.loads(t)
        except Exception:
            # salvage {...}
            s, e = t.find("{"), t.rfind("}")
            if s != -1 and e != -1 and e > s:
                return json.loads(t[s:e+1])
            raise

    def _call_llm_json_core(self, system: str, user: str, model_override: Optional[str],
                            schema: Dict[str, Any], max_tokens: int) -> Dict[str, Any]:
        # Attempt 1: strict JSON Schema
        resp = self._responses(
            input_payload=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            json_schema=schema,
            max_tokens=max_tokens,
            model=model_override,
        )
        text = self._resp_text(resp)
        try:
            return self._extract_json(text)
        except Exception:
            self.log.warning("JSON parse failed (attempt 1, model=%s). Raw (first 500): %r",
                             model_override or PRIMARY_MODEL, (text or "")[:500])

        # Attempt 2: re-ask with an explicit reminder (schema still enforced)
        strict_user = user + " Return ONLY minified JSON adhering to the schema. No prose."
        resp2 = self._responses(
            input_payload=[
                {"role": "system", "content": system},
                {"role": "user", "content": strict_user},
            ],
            json_schema=schema,
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

    def _call_llm_json(self, system: str, user: str, schema: Dict[str, Any], max_tokens: int = 1200) -> Dict[str, Any]:
        try:
            return self._call_llm_json_core(system, user, None, schema, max_tokens)
        except Exception:
            if FALLBACK_MODEL and FALLBACK_MODEL != PRIMARY_MODEL:
                self.log.warning("Falling back to %s after primary model returned empty/unparseable output.",
                                 FALLBACK_MODEL)
                try:
                    return self._call_llm_json_core(system, user, FALLBACK_MODEL, schema, max_tokens)
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
            "Return JSON with:\n"
            '{ "narrative": string, "choices": [ { "text": string, "dc": int, "ability": enum, "tags": [] } ] }\n'
            "2–4 choices; each includes a relevant ability or skill."
            f"\n\nSTATE:\n{json.dumps(compact)}"
        )
        return self._call_llm_json(system, user, opening_scene_schema(), max_tokens=1200)

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
            "Continue the story based on the player's action:\n"
            f"{player_input}\n\n"
            "Return JSON with:\n"
            '{ "narrative": string, "choices": [ { "text": string, "dc": int, "ability": enum, "tags": [] } ] }'
            "\nAllow off-list actions; remain coherent and responsive."
            f"\n\nSTATE:\n{json.dumps(compact)}"
        )
        return self._call_llm_json(system, user, scene_schema(), max_tokens=1200)

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
            "Return JSON with:\n"
            '{ "narrative": string, "consequences": {"hp_delta": int, "xp_delta": int, "items_gained": [], '
            '"items_lost": [], "milestone": bool}, "followup_choices": [ { "text": string, "dc": int, "ability": enum, "tags": [] } ] }\n'
            "XP guidance: success ≈ DC*10, failure ≈ DC*5. Consider milestone for major beats."
            f"\n\nSTATE:\n{json.dumps(compact)}"
        )
        return self._call_llm_json(system, user, outcome_schema(), max_tokens=1000)

    def build_clarification_prompt(self, question: str) -> str:
        st = self.gsm.get_state()
        system = "You answer rules/lore questions succinctly (3–6 sentences)."
        user = (
            "Answer the player's out-of-game question clearly and briefly. "
            "If it's rules, be concrete; if world/lore, respect established facts.\n"
            f"QUESTION: {question}\nSTATE SUMMARY: {st.get('summary','')}\n"
        )
        # No JSON schema here—plain text
        resp = self._responses(
            input_payload=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            json_schema=None,
            max_tokens=400,
            model=None,
        )
        text = self._resp_text(resp)
        if text:
            return text
        # fallback
        if FALLBACK_MODEL and FALLBACK_MODEL != PRIMARY_MODEL:
            self.log.warning("Falling back to %s for /ask.", FALLBACK_MODEL)
            resp2 = self._responses(
                input_payload=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                json_schema=None, max_tokens=400, model=FALLBACK_MODEL,
            )
            return self._resp_text(resp2) or "I’ll answer as soon as I can—try again."
        return "I’ll answer as soon as I can—try again."
