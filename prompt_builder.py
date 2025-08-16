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
    - Uses `instructions` for the system prompt (per SDK docs)
    - Uses `input` with `input_text` parts (per SDK docs)
    - No response_format / schema param (avoids SDK arg mismatches)
    - Strict JSON via prompting + robust salvage parser
    - Single fallback model
    """

    def __init__(self, gsm):
        self.gsm = gsm
        self.log = logging.getLogger("dm_bot")
        api_key = os.getenv("OPENAI_API_KEY") or os.getenv("DM_OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY / DM_OPENAI_API_KEY not set.")
        self.client = OpenAI(api_key=api_key)

    # ---------- Low-level helpers ----------
    def _wrap_messages_for_responses(self, messages):
        """
        Convert chat-like messages into Responses API input.
        - First system message -> instructions
        - Remaining messages -> input with input_text parts
        """
        instructions = None
        inputs = []
        for m in messages:
            role = m["role"]
            content = m["content"]
            if role == "system" and instructions is None:
                instructions = content  # SDK supports top-level `instructions` with Responses
                continue
            inputs.append({
                "role": role,
                "content": [{"type": "input_text", "text": content}],
            })
        if instructions is None:
            instructions = "You are a helpful assistant."
        if not inputs:
            # Ensure at least one user item
            inputs.append({"role": "user", "content": [{"type": "input_text", "text": ""}]})
        return instructions, inputs

    def _responses(self, *, messages, max_tokens: int = 1200, model: Optional[str] = None):
        mdl = model or PRIMARY_MODEL
        instructions, input_payload = self._wrap_messages_for_responses(messages)
        # Keep the call shape minimal & compatible with GPT-5 on latest SDK.
        return self.client.responses.create(
            model=mdl,
            instructions=instructions,            # per SDK README
            input=input_payload,                  # list of role items with input_text parts
            max_output_tokens=max_tokens,         # correct knob for Responses API
        )

    def _resp_text(self, resp) -> str:
        """
        Extract text robustly from Responses API.
        Prefer response.output_text; otherwise walk .output/.outputs.
        """
        txt = getattr(resp, "output_text", None)
        if isinstance(txt, str) and txt.strip():
            return txt.strip()

        chunks = []
        try:
            output = getattr(resp, "output", None) or getattr(resp, "outputs", None)
            if output:
                for item in output:
                    parts = getattr(item, "content", None) or []
                    for part in parts:
                        if getattr(part, "type", None) == "output_text":
                            # Some SDKs expose .text as str; others as object with .value
                            t = getattr(part, "text", None)
                            if isinstance(t, str):
                                chunks.append(t)
                            else:
                                val = getattr(getattr(part, "text", None), "value", None)
                                if val:
                                    chunks.append(val)
        except Exception:
            pass

        return "".join(chunks).strip()

    def _extract_json(self, text: str) -> Dict[str, Any]:
        """
        Parse JSON; attempt to salvage the largest {...} block if needed.
        """
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

    def _call_llm_json_core(self, system: str, user: str, model_override: Optional[str],
                            max_tokens: int) -> Dict[str, Any]:
        """
        Single attempt: ask for STRICT JSON via prompt, then parse.
        """
        resp = self._responses(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            model=model_override,
        )
        text = self._resp_text(resp)
        return self._extract_json(text)

    def _call_llm_json(self, system: str, user: str, max_tokens: int = 1200) -> Dict[str, Any]:
        """
        Primary -> fallback. If both fail, return a safe minimal scene.
        """
        try:
            return self._call_llm_json_core(system, user, None, max_tokens)
        except Exception as e:
            self.log.warning("Primary model %s failed: %s", PRIMARY_MODEL, e)
            if FALLBACK_MODEL and FALLBACK_MODEL != PRIMARY_MODEL:
                try:
                    return self._call_llm_json_core(system, user, FALLBACK_MODEL, max_tokens)
                except Exception as e2:
                    self.log.error("Fallback %s also failed: %s", FALLBACK_MODEL, e2)

        return {"narrative": "The world blurs; reality resets. What do you do?", "choices": []}

    # ---------- Public builders ----------
    def _schema_text_for_choices(self) -> str:
        return (
            '{ "narrative": string, '
            '"choices": [ { "text": string, "dc": integer (5-25), '
            f'"ability": one of [{ALLOWED_ABILITIES_AND_SKILLS}], '
            '"tags": string[] } ] }'
        )

    def _schema_text_for_outcome(self) -> str:
        return (
            '{ "narrative": string, '
            '"consequences": { "hp_delta": int, "xp_delta": int, '
            '"items_gained": string[], "items_lost": string[], "milestone": boolean }, '
            '"followup_choices": [ { "text": string, "dc": int (5-25), '
            f'"ability": one of [{ALLOWED_ABILITIES_AND_SKILLS}], "tags": string[] } ] }}'
        )

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
            "Begin the adventure with a vivid opening (4–8 sentences). Establish tone, stakes, "
            "and end with a natural prompt to act.\n\n"
            "Return STRICT MINIFIED JSON ONLY (no prose, no code fences) matching:\n"
            f"{self._schema_text_for_choices()}\n\n"
            f"STATE:\n{json.dumps(compact)}"
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
            "Continue the story based on the player's action below. Keep continuity tight. "
            "Offer 2–4 sensible choices. Allow the player to go off-list.\n\n"
            f"PLAYER ACTION: {player_input}\n\n"
            "Return STRICT MINIFIED JSON ONLY (no prose, no code fences) matching:\n"
            f"{self._schema_text_for_choices()}\n\n"
            f"STATE:\n{json.dumps(compact)}"
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
            "Adjudicate the player's attempt given CHOICE and ROLL. "
            "Success yields greater progress and XP; failure yields setback and lesser XP "
            "(rough guide: success ≈ DC*10 XP, failure ≈ DC*5 XP). Consider milestone for major beats.\n\n"
            f"CHOICE: {json.dumps(choice)}\n"
            f"ROLL: {json.dumps(roll)}\n\n"
            "Return STRICT MINIFIED JSON ONLY (no prose, no code fences) matching:\n"
            f"{self._schema_text_for_outcome()}\n\n"
            f"STATE:\n{json.dumps(compact)}"
        )
        return self._call_llm_json(system, user, max_tokens=1000)

    def build_clarification_prompt(self, question: str) -> str:
        st = self.gsm.get_state()
        system = "You answer rules/lore questions succinctly (3–6 sentences)."
        user = (
            "Answer the player's **out-of-game** question clearly and briefly. "
            "If it's rules, be concrete; if world/lore, respect established facts.\n"
            f"QUESTION: {question}\nSTATE SUMMARY: {st.get('summary','')}\n"
        )
        try:
            resp = self._responses(
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                max_tokens=400,
            )
            text = self._resp_text(resp)
            return text or "I’ll answer as soon as I can—try again."
        except Exception:
            if FALLBACK_MODEL and FALLBACK_MODEL != PRIMARY_MODEL:
                resp2 = self._responses(
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    max_tokens=400,
                    model=FALLBACK_MODEL,
                )
                return self._resp_text(resp2) or "I’ll answer as soon as I can—try again."
            return "I’ll answer as soon as I can—try again."

