# Tampermonkey Messenger Bot Setup

This option runs the command client inside an already logged-in **desktop** Messenger tab. It is useful when the separate local Playwright profile has expired. The script does **not** run in mobile Chrome; use a computer with desktop Chrome, Edge, Brave, or Firefox.

## Kiwi compatibility update

If Messenger showed `GM_getValue is not defined`, replace your existing userscript with version **1.0.1** from `tampermonkey/messenger-ai-bot.user.js`. This version supports both legacy Tampermonkey storage functions, Kiwi’s newer storage API, and a local browser-storage fallback.

## Installation

| Step | Action |
| --- | --- |
| 1 | Install [Tampermonkey](https://www.tampermonkey.net/) from a desktop browser. |
| 2 | Open the Tampermonkey dashboard and choose **Create a new script**. |
| 3 | Delete the default template and paste the full content of `tampermonkey/messenger-ai-bot.user.js`. |
| 4 | Save the script with **Ctrl+S**. Confirm that it is enabled in the dashboard. |
| 5 | Open Facebook Messenger on the same desktop browser and log in to the account that is in the intended group. |
| 6 | Open the target group chat. The bottom-right status notice will say that the bot is active and waiting for a fresh mention. |
| 7 | On the first command, paste the current Manus API key into the local Tampermonkey prompt. It is stored only in Tampermonkey’s local extension storage on that browser. |

> Keep the Messenger tab open while using the bot. The script only observes and acts in the tab where Messenger is open.

## Commands

| Command | Example |
| --- | --- |
| `/image` | `@Shahidulla /image cinematic rainy Dhaka street at night` |
| `/voice` | `@Shahidulla /voice সবাই কেমন আছো?` |
| `/sing` | `@Shahidulla /sing 20-second upbeat Banglish friendship jingle` |
| `/edit` | Attach a photo, then send `@Shahidulla /edit make it a watercolor portrait` |
| `/help` | `@Shahidulla /help` |
| Normal mention | `@Shahidulla Who has used English most clearly in this chat?` |

The command must be in a **new message** containing `Shahidulla Kaysar` or `@Shahidulla`. The script deliberately ignores messages that existed before it was loaded, so it does not replay historical messages or spam the group after a refresh.

## Security and operational limits

The Manus API key is not committed to this repository and is not written into the userscript. However, Tampermonkey storage is part of the local browser profile; do not share that browser profile or install untrusted extensions. If the key was ever exposed in a previous repository revision, generate and use a replacement key before setting up the userscript.

The script uses the visible Messenger page DOM. Facebook can change its interface at any time, which may require selector updates. It also needs the tab to remain open and cannot run while the computer is asleep or offline.

To remove the stored key, open the Tampermonkey editor, open the browser developer console on Messenger, and run:

```javascript
GM_setValue('shahidulla_manus_api_key', '')
```
