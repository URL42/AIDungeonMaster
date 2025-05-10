# prompt_builder.py
import json

class PromptBuilder:
    def __init__(self, gsm):
        self.gsm = gsm

    def build_scene_prompt(self, user_input: str) -> str:
        st = self.gsm.get_state()
        return (
            f"You are the Dungeon Master. Game state:\n{json.dumps(st, indent=2)}\n\n"
            f"Player says: {user_input}\n\n"
            "Reply in this format:\n"
            "narrative: [describe what happens next]\n\n"
            "choices:\n"
            "1) [Choice text] (DC X Ability)\n"
            "2) [Choice text] (DC X Ability)\n"
            "3) [Choice text] (DC X Ability)\n\n"
            "Only include 'choices:' if meaningful player decisions are available.\n"
            "DO NOT describe the outcome of the roll — wait until the player rolls.\n"
            "Your tone must be immersive, dramatic, and genre-consistent."
        )

    def build_npc_prompt(self, npc_name: str) -> str:
        st = self.gsm.get_state()
        loc = st.get("current_location") or "somewhere"
        return (
            f"Roleplay NPC '{npc_name}' in {loc}.\n"
            f"Story context:\n{st.get('story', '')}\n\n"
            "Response should include:\n"
            "- Character-appropriate dialogue\n"
            "- A possible quest hook or secret\n"
            "- Optional suggestions for skill checks or reactions\n"
            "DO NOT resolve any rolls — let the player roll."
        )

    def build_clarification_prompt(self, question: str) -> str:
        st = self.gsm.get_state()
        return (
            f"Clarify this question about the game world: '{question}'\n"
            f"Game state:\n{json.dumps(st, indent=2)}\n"
            "Answer concisely and consistently with prior events."
        )

    def build_intro_prompt(self, user_data: dict) -> str:
        genre = user_data.get("genre", "Fantasy")
        char = user_data.get("character", {})
        name = char.get("name", "Unknown")
        char_class = char.get("class", "Adventurer")
        motivation = char.get("motivation", "Unknown purpose")
        return (
            f"Imagine a {genre} world with a vivid tone and rich setting.\n"
            f"The main character is {name}, a {char_class}, driven by the goal: {motivation}.\n\n"
            "Create an opening scene with:\n"
            "1. A compelling environment that fits the genre\n"
            "2. A personal and immediate challenge that ties directly to their motivation and class\n"
            "3. 3 choices, each with a (DC X Ability) format\n\n"
            "Format like:\n"
            "1) Persuade the guard (DC 15 Charisma)\n"
            "2) Sneak past (DC 12 Dexterity)\n"
            "3) Cast an illusion (DC 14 Intelligence)\n"
        )

    def build_outcome_prompt(self, action: str, check_result: dict = None) -> str:
        state = self.gsm.get_state()
        roll_txt = ""
        if check_result:
            roll_txt = (
                f"Resolve this action: {action}\n"
                f"Ability Check: {check_result['ability']}\n"
                f"Roll: {check_result['roll']} + Modifier {check_result['mod']} = {check_result['total']}\n"
                f"Target DC: {check_result['dc']}\n"
                f"Result: {'Success' if check_result['total'] >= check_result['dc'] else 'Failure'}\n\n"
            )
        return (
            f"{roll_txt}"
            "Narrate the outcome of the check. Include:\n"
            "- Impact on the player and world\n"
            "- Consequences of the action\n"
            "- New tension or open-ended situation\n\n"
            f"Game state:\n{json.dumps(state, indent=2)}"
        )

    # Optional legacy version; not currently used in your flow
    #def build_prompt(self, user_data: dict, user_input: str) -> str:
    #    st = self.gsm.get_state()
    #    loc = st.get("current_location") or "somewhere"
    #    return (
    #        f"Location: {loc}\n"
    #        f"Character: {json.dumps(user_data.get('character', {}))}\n"
    #        f"Action: {user_input}\n\n"
    #        "Respond with:\n"
    #        "1. Narrative consequences\n"
    #        "2. New challenges/choices\n"
    #        "3. [ROLL:Ability] tags when needed\n"
    #        "Maintain story continuity and game rules"
    #    )
