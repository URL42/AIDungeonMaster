# prompt_builder.py
import json
import os
import logging
from typing import Dict, Any, Optional
from openai import OpenAI

PRIMARY_MODEL = os.getenv("DM_OPENAI_MODEL", "gpt-5")
FALLBACK_MODEL = os.getenv("DM_FALLBACK_MODEL", "gpt-4o-mini")

ALLOWED_ABILITIES_AND_SKILLS = (
    "Strength|Dexterity|Constitution|Intelligence|Wisdom|Charisma|"
    "Perception|Stealth|Athletics|Arcana|History|Insight|Investigation|"
    "Medicine|Nature|Religion|Animal Handling|Deception|Intimidation|"
    "Performance|Persuasion"
)

class PromptBuilder:
    """
    LLM prompt/call layer for the Dungeon Master game using the Responses API.
    Strict JSON schema + retries + fallback model.
    """

    def __init__(self, gsm):
        self.gsm = gsm
        self.log = logging.getLogger("dm_bot")
        api_key = os.getenv("OPENAI_API_KEY") or os.getenv("DM_OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY / DM_OPENAI_API_KEY not set.")
        self.client = OpenAI(api_key=api_key)

    # ---------- Helpers ----------
    def _wrap_messages(self, messages):
        """Wraps content into the Responses API format: list of parts."""
        wrapped = []
        for m in messages:
            wrapped.append({
                "role": m["role"],
                "content": [{"type": "text", "text": m["content"]}],
            })
        return wrapped

    def _responses(self, *, messages, json_schema: Optional[dict] = None,
                   max_tokens: int = 1200, model: Optional[str] = None):
        mdl = model or PRIMARY_MODEL
        params = {
            "model": mdl,
            "input": self._wrap_messages(messages),
            "max_output_tokens": max_tokens,
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
        return self.client.responses.create(**params)

    def _resp_text(self, resp) -> str:
        # Try .output_text if available
        text = getattr(resp, "output_text", None)
        if isinstance(text, str) and text.strip():
            return text.strip()

        # Otherwise, manually extract
        chunks = []
        try:
            output = getattr(resp, "output", None) or getattr(resp, "outputs", None)
            if output:
                for item in output:
                    for part in getattr(item, "content", []):
                        if getattr(part, "type", None) == "output_text":
                            val = getattr(getattr(part, "text", None), "value", None)
                            if val:
                                chunks.append(val)
                        elif isinstance(getattr(part, "text", None), str):
                            chunks.append(part.text)
        except Exception:
            pass

        return "".join(chunks).strip()

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
                            max_tokens: int, schema: dict) -> Dict[str, Any]:
        resp = self._responses(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            json_schema=schema,
            max_tokens=max_tokens,
            model=model_override,
        )
        text = self._resp_text(resp)
        return self._extract_json(text)

    def _call_llm_json(self, system: str, user: str, max_tokens: int, schema: dict) -> Dict[str, Any]:
        try:
            return self._call_llm_json_core(system, user, None, max_tokens, schema)
        except Exception as e:
            self.log.warning("Primary model %s failed: %s", PRIMARY_MODEL, e)
            if FALLBACK_MODEL and FALLBACK_MODEL != PRIMARY_MODEL:
                try:
                    return self._call_llm_json_core(system, user, FALLBACK_MODEL, max_tokens, schema)
                except Exception as e2:
                    self.log.error("Fallback %s also failed: %s", FALLBACK_MODEL, e2)

        # safety
        return {"narrative": "The world blurs; reality resets. What do you do?", "choices": []}

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
        schema = {
            "type": "object",
            "properties": {
                "narrative": {"type": "string"},
                "choices": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "dc": {"type": "integer"},
                            "ability": {"type": "string", "pattern": ALLOWED_ABILITIES_AND_SKILLS},
                            "tags": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["text", "dc", "ability", "tags"],
                    },
                },
            },
            "required": ["narrative", "choices"],
        }
        user = (
            "Begin the adventure with a vivid opening (4–8 sentences), establish tone, stakes, and a prompt to act.\n"
            "Return STRICT JSON."
            f"\n\nSTATE:\n{json.dumps(compact)}"
        )
        return self._call_llm_json(system, user, 1200, schema)

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
        schema = {
            "type": "object",
            "properties": {
                "narrative": {"type": "string"},
                "choices": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "dc": {"type": "integer"},
                            "ability": {"type": "string", "pattern": ALLOWED_ABILITIES_AND_SKILLS},
                            "tags": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["text", "dc", "ability", "tags"],
                    },
                },
            },
            "required": ["narrative", "choices"],
        }
        user = (
            f"Continue the story. The player acted/said:\n{player_input}\n\n"
            "Return STRICT JSON.\n"
            f"STATE:\n{json.dumps(compact)}"
        )
        return self._call_llm_json(system, user, 1200, schema)

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
        schema = {
            "type": "object",
            "properties": {
                "narrative": {"type": "string"},
                "consequences": {
                    "type": "object",
                    "properties": {
                        "hp_delta": {"type": "integer"},
                        "xp_delta": {"type": "integer"},
                        "items_gained": {"type": "array", "items": {"type": "string"}},
                        "items_lost": {"type": "array", "items": {"type": "string"}},
                        "milestone": {"type": "boolean"},
                    },
                    "required": ["hp_delta", "xp_delta", "items_gained", "items_lost", "milestone"],
                },
                "followup_choices": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "dc": {"type": "integer"},
                            "ability": {"type": "string", "pattern": ALLOWED_ABILITIES_AND_SKILLS},
                            "tags": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["text", "dc", "ability", "tags"],
                    },
                },
            },
            "required": ["narrative", "consequences", "followup_choices"],
        }
        user = (
            f"Adjudicate the player's attempt.\nCHOICE: {json.dumps(choice)}\nROLL: {json.dumps(roll)}\n\n"
            "Return STRICT JSON.\n"
            f"STATE:\n{json.dumps(compact)}"
        )
        return self._call_llm_json(system, user, 1000, schema)

    def build_clarification_prompt(self, question: str) -> str:
        st = self.gsm.get_state()
        system = "You answer rules/lore questions succinctly (3–6 sentences)."
        user = f"QUESTION: {question}\nSTATE SUMMARY: {st.get('summary','')}"
        try:
            resp = self._responses(
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                max_tokens=400,
            )
            return self._resp_text(resp) or "I’ll answer as soon as I can—try again."
        except Exception:
            if FALLBACK_MODEL and FALLBACK_MODEL != PRIMARY_MODEL:
                resp2 = self._responses(
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    max_tokens=400,
                    model=FALLBACK_MODEL,
                )
                return self._resp_text(resp2) or "I’ll answer as soon as I can—try again."
            return "I’ll answer as soon as I can—try again."
