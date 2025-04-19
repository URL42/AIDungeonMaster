import json

class PromptBuilder:
    """Constructs LLM prompts for scenes, NPCs, clarifications, etc."""
    def __init__(self, gsm):
        self.gsm = gsm

    def build_scene_prompt(self, user_input: str) -> str:
        st = self.gsm.get_state()
        loc = st.get("current_location") or "an unknown place"
        hist = st.get("conversation_history", [])[-5:]
        return (
            f"You are the Dungeon Master in {loc}.\n"
            f"Story so far: {st.get('story','')}\n"
            f"Recent lines: {json.dumps(hist)}\n"
            f"Player says: {user_input}\n"
            "Continue the narrative."
        )

    def build_npc_prompt(self, npc_name: str) -> str:
        st = self.gsm.get_state()
        loc = st.get("current_location") or "somewhere"
        return (
            f"You are roleplaying NPC '{npc_name}' in {loc}.\n"
            f"Story so far: {st.get('story','')}\n"
            "Reply in character, giving flavor and optionally a question."
        )

    def build_clarification_prompt(self, question: str) -> str:
        st = self.gsm.get_state()
        return (
            f"Clarify this question about the game: '{question}'.\n"
            f"Full state: {json.dumps(st,indent=2)}\n"
        )

    def build_intro_prompt(self, user_data: dict) -> str:
        char = user_data.get("character", {})
        name = char.get("name", "Unknown Hero")
        race = char.get("race", "Human")
        cls = char.get("class", "Adventurer")
        abilities = char.get("abilities", {})
        motivation = char.get("motivation", "glory and treasure")
        ability_text = "\n".join([f"{k}: {v}" for k, v in abilities.items()])

        return (
            f"Create the opening scene of a fantasy adventure.\n"
            f"Character:\n"
            f"Name: {name}\n"
            f"Race: {race}\n"
            f"Class: {cls}\n"
            f"Abilities:\n{ability_text}\n"
            f"Motivation: {motivation}\n\n"
            f"Now, where do we start?..."
        )

    def build_prompt(self, user_data: dict, user_input: str) -> str:
        st = self.gsm.get_state()
        loc = st.get("current_location") or "somewhere"
        hist = st.get("conversation_history", [])[-5:]
        char = user_data.get("character", "an adventurer")

        return (
            f"Location: {loc}\n"
            f"Character: {char}\n"
            f"History: {json.dumps(hist)}\n"
            f"User says: {user_input}\n"
            f"Continue the scene."
        )
