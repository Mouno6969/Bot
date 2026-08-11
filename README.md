# Shahidulla Kaysar — Messenger AI Bot

A mention-driven Facebook Messenger group bot that responds in **Bengali, Banglish, or English**. The bot provides normal context-aware replies when mentioned and uses explicit commands for media work, so a conversation cannot accidentally trigger a long image, voice, song, or edit job.

> **Security first:** this repository intentionally contains **no Facebook cookies, browser profile, encryption PIN, or Manus API key**. Keep `.env` and `bot_data_dir/` on the machine that runs the bot. Do not commit or share them.

## Commands

| Command | Example | Result |
| --- | --- | --- |
| `/image` | `@Shahidulla /image cinematic rainy Dhaka street at night` | Generates and uploads one image. |
| `/voice` | `@Shahidulla /voice সবাই কেমন আছো?` | Creates and uploads one natural voice note. |
| `/sing` | `@Shahidulla /sing 45-second upbeat Banglish friendship song` | Creates and uploads one original song or instrumental audio file. |
| `/edit` | Attach an image and send `@Shahidulla /edit make it watercolor` | Edits the attached image while preserving all details not named in the request. |
| Normal mention | `@Shahidulla Who communicated most clearly?` | Returns a regular context-aware reply. Comparative answers only use evidence visible in the recent chat; when evidence is inadequate, the bot says so. |
| `/help` | `@Shahidulla /help` | Posts the command guide in the chat. |

For `/edit`, attach the image and write the mention plus edit instruction **in the same Messenger message**. The bot checks the latest attached image and does not use profile pictures as source images.

## Project layout

| Path | Purpose |
| --- | --- |
| `fb_bot.py` | Small application entry point. |
| `messenger_bot/config.py` | Environment-based configuration and validation. |
| `messenger_bot/router.py` | Deterministic mention and slash-command parser. |
| `messenger_bot/media.py` | Manus task creation, local-image upload, result polling, and attachment download. |
| `messenger_bot/messenger.py` | Messenger monitoring, idempotency, prompts, and file delivery. |
| `COMMANDS.md` | Short command reference for group users. |
| `tests/` | Command routing and anti-spam regression tests. |

## Local setup

Install the Python packages and Chromium once:

```bash
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
```

Copy the template and fill in local values that must never be committed:

```bash
cp .env.example .env
```

Set these values in `.env`:

```dotenv
MESSENGER_GROUP_URL=https://www.facebook.com/messages/t/YOUR_GROUP_ID
BOT_USER_DATA_DIR=/absolute/path/to/local/bot_data_dir
MANUS_API_KEY=your_current_manus_api_key
MESSENGER_ENCRYPTION_PIN=your_pin_if_required
```

Log in to the intended Facebook account once in the local Chromium profile at `BOT_USER_DATA_DIR`; this profile remains local to the host. On a **headless server** (no display), import the session instead of a manual login — copy `c_user` and `xs` (optionally `datr`, `sb`, `fr`) from a logged-in browser and run:

```bash
FB_C_USER=... FB_XS=... python3 tools/import_session.py
```

The script injects the cookies into `BOT_USER_DATA_DIR`, opens the messages page, and verifies the login with a screenshot. Cookie values are never printed or committed. Start the bot with:

```bash
chmod +x start_bot.sh
./start_bot.sh
```

Use `./start_bot.sh --foreground` while debugging. Runtime logs are written to `bot_logs.txt` and the process ID to `bot_pid.txt`.

## Reliability behavior

The bot checkpoints the conversation when it starts, then considers only newly appended message text. It keeps a small persistent fingerprint history, rejects its own sent text, and processes each new mention once. Media tasks send an acknowledgement first, wait for a generated attachment, and fail cleanly rather than holding the monitor loop indefinitely.

## Security checklist

The previous revision embedded an API key and authenticated browser session in Git history. Those materials are being removed from the repository in this refactor. You should create a **new API key** and refresh the Facebook sign-in session on the host before relying on the bot, because any prior credential that was committed to a public repository must be treated as exposed.
