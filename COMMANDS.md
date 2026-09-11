# Bot Commands

The bot responds to commands only when the message also contains a mention of **Shahidulla Kaysar** (or `@Shahidulla`). This prevents accidental work requests and makes media generation explicit. Any other mention receives a normal context-aware reply.

| Command | Example | Behavior |
| --- | --- | --- |
| `/image <description>` | `@Shahidulla /image a cinematic rainy Dhaka street at night` | Generates one image from the supplied description and uploads it to the chat. |
| `/voice <text>` | `@Shahidulla /voice স্বাগতম বন্ধুরা, আজকে আমরা আড্ডা দিবো।` | Creates a natural Bengali, Banglish, or English voice note from the supplied script. |
| `/sing <brief or lyrics>` | `@Shahidulla /sing upbeat Banglish friendship song, 45 seconds` | Creates one original song or instrumental track. The bot treats `/sing` as an original-music request; it does not imitate particular artists or reproduce copyrighted lyrics. |
| `/edit <instruction>` | `@Shahidulla /edit make the attached photo look like a watercolor portrait` | Edits the most recently attached image in the same message. If no usable image is attached, the bot replies with the exact format to use. |
| `/link <Facebook URL> <option path> [quantity <number>] [submit]` | `@Shahidulla /link https://www.facebook.com/username 2 4 1 quantity 3 submit` | Follows the existing option path. `quantity 3` repeats the same flow three times after confirmation. The final `submit` remains explicit, and `/link confirm` executes the pending quantity. |
| Mention + normal request | `@Shahidulla Who has used English most clearly in this chat?` | Replies in the language of the request and bases conclusions only on observable recent chat context. |

## Games, credits, and ranking

Every player starts with **100 free credits**. Winners earn credits, losers keep playing — game commands are instant and never use the media APIs.

| Command | Example | Behavior |
| --- | --- | --- |
| `/games` | `@Shahidulla /games` | Shows the full game menu. |
| `/quiz [category]` | `@Shahidulla /quiz sports` | Posts a multiple-choice question (categories: general, science, bangladesh, sports, fun). The first correct answer within the time limit wins **40 credits**. |
| `/answer <A\|B\|C\|D>` | `@Shahidulla /answer B` | Answers the active quiz. Numbers 1–4 also work. |
| `/race [lane]` | `@Shahidulla /race 3` | Opens/joins a horse-race lobby (minimum 2 players). The race track message is **edited live** as the horses run; empty lanes race as NPCs. Winner takes the pot (players × 20 credits). The lobby creator can type `/race go` to start early. |
| `/guess [number]` | `@Shahidulla /guess 27` | `/guess` alone starts a round (bot thinks of 1–50); `/guess 27` tries that number. The bot hints higher/lower; the correct guess wins **30 credits**. |
| `/flip <heads\|tails> <amount>` | `@Shahidulla /flip heads 50` | Coin-flip bet: win doubles the stake, losing costs it. Minimum bet 10 credits. |
| `/daily` | `@Shahidulla /daily` | Free **60 credits** once every 20 hours. |
| `/balance` (or `/credits`, `/wallet`) | `@Shahidulla /balance` | Shows your credits, games played, wins, and leaderboard rank. |
| `/ranking` (or `/leaderboard`, `/rank`) | `@Shahidulla /ranking` | Top 10 credit holders with win counts. |

> **Image-editing format:** attach the image and include the mention and `/edit` instruction in the same Messenger message. The bot preserves non-requested visual details unless the instruction says otherwise.

> **Media delivery:** the bot posts a short acknowledgement before generating. It then uploads the resulting image, audio, or song file once the generation task completes.

## Reliability rules

The bot processes each incoming message once using a message fingerprint, never treats its own sent messages as prompts, queues one media job at a time, and times out cleanly rather than blocking mention-based replies indefinitely. Link quantity accepts any positive integer, and repeated link submissions run sequentially. Option-path numbers remain limited to 1–10. It does not claim a comparative judgment is supported when the visible chat context is insufficient.
