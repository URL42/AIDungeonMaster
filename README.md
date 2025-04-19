# 🐉 Dungeon Master Telegram Bot

A fully interactive, AI-powered RPG experience on Telegram. Create your character, explore a fantasy world, roll dice, and experience dynamic storytelling powered by OpenAI's GPT-4.1.

---

## ✨ Features

- 🎭 Character creation (race, class, abilities, motivation)
- 🧠 GPT-4.1 storytelling with contextual memory
- 🎲 D20 roll-based decision outcomes via Telegram buttons
- 📜 Persistent campaign, inventory, and quest tracking
- 💬 Full command interface: `/start`, `/sheet`, `/ask`, `/travel`, `/talk`, and more

---

## 🧰 Requirements

- Python 3.9+
- Telegram bot token (via @BotFather)
- OpenAI API key (GPT-4.1)

---

## 🔧 Setup Instructions

### 1. Clone the Repo

```bash
git clone https://github.com/yourusername/dungeon-master-telegram.git
cd dungeon-master-telegram
```

### 2. Create and Activate a Virtual Environment

#### On macOS/Linux:
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies

After activating your virtual environment, install the required Python packages:

```bash
pip install -r requirements.txt
```

### 4. Add Your API Keys in a `.env` File

Create a file named `.env` in the root of your project and add the following:

```dotenv
DM_TELEGRAM_BOT_TOKEN=your_telegram_bot_token
DM_OPENAI_API_KEY=your_openai_api_key
```

### 5. Set Up Your Telegram Bot

#### Step 1: Message [@BotFather](https://t.me/BotFather)

Open Telegram and search for [@BotFather](https://t.me/BotFather). Start a chat and click “Start” or type `/start`.

#### Step 2: Create a New Bot

Send the command:

```text
/newbot and follow the prompts. BotFather will give you a bot token.

Follow the prompts:

- Provide a display name (e.g., `Dungeon Master`)
- Choose a unique username ending in `bot` (e.g., `DungeonRPG_Bot`)

BotFather will reply with a message that includes your new bot’s token. It will look like this:

Done! Congratulations on your new bot. You will find it at t.me/DungeonRPG_Bot. Use this token to access the HTTP API: 123456789:ABCDefGhIJkLMnopQRStuvWXyZ

Copy this token and add it to your `.env` file like so:

```
DM_TELEGRAM_BOT_TOKEN=123456789:ABCDefGhIJkLMnopQRStuvWXyZ
```

#### Step 3: Get Your Telegram Chat ID

This step is useful if you want to send messages to yourself programmatically or log bot interactions by chat ID.

1. Start a chat with your bot in Telegram and type `/start`.

2. In your web browser, visit the following URL (replace `<YOUR_BOT_TOKEN>` with your actual token):

```
https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
```

3. You will see a JSON response that looks something like this:

```json
{
  "ok": true,
  "result": [
    {
      "update_id": 123456789,
      "message": {
        "message_id": 1,
        "from": {
          "id": 987654321,
          "is_bot": false,
          "first_name": "Alice",
          "username": "alice123",
          "language_code": "en"
        },
        "chat": {
          "id": 987654321,
          "first_name": "Alice",
          "username": "alice123",
          "type": "private"
        },
        "date": 1713456789,
        "text": "/start"
      }
    }
  ]
}

4. The number listed under "id" in the "chat" block is your **Telegram Chat ID**.

### 6. Run the Bot

After completing the setup and creating your `.env` file, you're ready to launch the bot.

Make sure your virtual environment is activated:

#### On macOS/Linux:

```bash
source venv/bin/activate
```

#### On Windows

```
venv\Scripts\activate
```

Then run your bot:

```
python3 dungeon_master.py
```

If everything is set up correctly, your bot will start polling for messages.

Open Telegram, find your bot, and type:

```
/start
```

## 🧪 Command Overview

| Command       | Description                                  |
|---------------|----------------------------------------------|
| `/start`      | Begin or resume your adventure               |
| `/sheet`      | View your character sheet                    |
| `/campaign`   | View campaign genre and story summary        |
| `/inventory`  | View items you’ve acquired                   |
| `/quests`     | View your current quests                     |
| `/additem`    | Add an item to your inventory                |
| `/addquest`   | Add a quest to your journal                  |
| `/roll d20`   | Roll a die manually                          |
| `/ask`        | Ask the LLM for contextual clarification     |
| `/talk`       | Speak to the world/NPCs                      |
| `/travel`     | Move to a new location                       |
| `/restart`    | Start over with a new character              |
| `/cancel`     | Cancel the current operation                 |
| `/help`       | Show all available commands                  |

## 📜 License

MIT License

---

## 💬 Credits

Built with ❤️ by URL42.  
Powered by GPT-4.1, Python, and pure imagination.
