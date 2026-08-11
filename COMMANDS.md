# Bot Commands

The bot responds to commands only when the message also contains a mention of **Shahidulla Kaysar** (or `@Shahidulla`). This prevents accidental work requests and makes media generation explicit. Any other mention receives a normal context-aware reply.

| Command | Example | Behavior |
| --- | --- | --- |
| `/image <description>` | `@Shahidulla /image a cinematic rainy Dhaka street at night` | Generates one image from the supplied description and uploads it to the chat. |
| `/voice <text>` | `@Shahidulla /voice স্বাগতম বন্ধুরা, আজকে আমরা আড্ডা দিবো।` | Creates a natural Bengali, Banglish, or English voice note from the supplied script. |
| `/sing <brief or lyrics>` | `@Shahidulla /sing upbeat Banglish friendship song, 45 seconds` | Creates one original song or instrumental track. The bot treats `/sing` as an original-music request; it does not imitate particular artists or reproduce copyrighted lyrics. |
| `/edit <instruction>` | `@Shahidulla /edit make the attached photo look like a watercolor portrait` | Edits the most recently attached image in the same message. If no usable image is attached, the bot replies with the exact format to use. |
| Mention + normal request | `@Shahidulla Who has used English most clearly in this chat?` | Replies in the language of the request and bases conclusions only on observable recent chat context. |

> **Image-editing format:** attach the image and include the mention and `/edit` instruction in the same Messenger message. The bot preserves non-requested visual details unless the instruction says otherwise.

> **Media delivery:** the bot posts a short acknowledgement before generating. It then uploads the resulting image, audio, or song file once the generation task completes.

## Reliability rules

The bot processes each incoming message once using a message fingerprint, never treats its own sent messages as prompts, queues one media job at a time, and times out cleanly rather than blocking mention-based replies indefinitely. It does not claim a comparative judgment is supported when the visible chat context is insufficient.
