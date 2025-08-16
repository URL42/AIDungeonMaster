# prompt_builder.py
import os
import logging
from typing import Dict, Any
from persistence import GameStateManager
from openai import OpenAI

logger = logging.getLogger(__name__)
client = OpenAI(api_key=os.getenv("DM_OPENAI_API_KEY"))

ALLOWED_ABILITIES_AND_SKILLS = [
    "Strength", "Dexterity", "Constitution",
    "Intelligence", "Wisdom", "Charisma",
    "Investigation", "Perception", "Persuasion",
    "Stealth", "Athletics", "Arcana"
]

class PromptBuilder:
    def __init__(self, gsm: GameStateManager):
        self.gsm = gsm

    # --- Prompt builders -----------------------------------------------------

    def build_intro_prompt(self, character: Dict[str, Any]) -> str:
        return (
            "You are a Dungeon Master. Create an opening scene with:\n"
            "1. Immersive environment description\n"
            "2. Immediate meaningful challenge\n"
            "3. 3 clear numbered choices with varying risk.\n\n"
            f"Character details:\n{character}"
        )

    def build_scene_prompt(self, user_input: str) -> str:
        state = self.gsm.get_state()
        return (
            "Continue the story. Format output as JSON with fields:\n"
            "{ narrative: string, choices: [ { text: string, dc: number, "
            f"ability: one of [{', '.join(ALLOWED_ABILITIES_AND_SKILLS)}], "
            "tags: string[] } ] }\n\n"
            f"Previous story: {state.get('story','')}\n"
            f"Player input: {user_input}"
        )

    def build_outcome_prompt(self, choice_text: str, roll_result=None) -> str:
        state = self.gsm.get_state()
        roll_txt = f"Roll result: {roll_result}" if roll_result else "No roll result."
        return (
            "Given the player's choice, continue the story.\n\n"
            f"Story so far: {state.get('story','')}\n"
            f"Choice: {choice_text}\n"
            f"{roll_txt}\n\n"
            "Provide a short outcome."
        )

    def build_npc_prompt(self, npc_name: str) -> str:
        state = self.gsm.get_state()
        return (
            f"Create a short dialogue with NPC {npc_name}.\n"
            f"Story so far: {state.get('story','')}"
        )

    # --- Wrapper for GPT calls ----------------------------------------------

    def call_gpt(self, prompt: str, model: str = None) -> str:
        model = model or os.getenv("DM_OPENAI_MODEL", "gpt-4o-mini")
        try:
            logger.info(f"🧠 Calling {model} with prompt:\n{prompt}")
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You are a Dungeon Master narrating a roleplaying game."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.8,
                max_tokens=800
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"GPT error: {e}")
            return "⚠️ The Dungeon Master pauses, unsure how to proceed..."
