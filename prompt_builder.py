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
        genre = user_data.get("genre", "Fantasy")
        char = user_data.get("character", {})
        name = char.get("name", "Unnamed Hero")
        race = char.get("race", "Human")
        cls = char.get("class", "Adventurer")
        motivation = char.get("motivation", "to seek glory and riches")
        abilities = char.get("abilities", {})

        ability_text = "\n".join([f"{k}: {v}" for k, v in abilities.items()])

        return (
            f"You are a Dungeon Master narrating a roleplaying game.\n"
            f"The genre is: {genre}.\n\n"
            f"Here is the player’s character:\n"
            f"Name: {name}\n"
            f"Race: {race}\n"
            f"Class: {cls}\n"
            f"Motivation: {motivation}\n"
            f"Abilities:\n{ability_text}\n\n"
            f"Imagine the tone, theme, and setting that match this genre. "
            f"Define these yourself. Then begin the story with the character’s first meaningful challenge. "
            f"End your response with a clear challenge that requires the player to act.\n"
            f"If a roll is needed, instruct the player to roll using the provided D20 button — but do not roll for them. "
            f"Wait for the roll result before describing the outcome."
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
            f"The player says: {user_input}\n"
            f"Respond with narrative and possible consequences. If an ability check is needed, "
            f"instruct the player to roll. Do not roll the dice or resolve the result yourself — wait for the roll."
        )

