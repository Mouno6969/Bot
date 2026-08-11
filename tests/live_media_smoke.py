import asyncio
import sys

from messenger_bot.config import Settings
from messenger_bot.media import ManusMediaClient
from messenger_bot.router import RequestKind


REQUESTS = {
    "image": (RequestKind.IMAGE, "A small friendly robot waving, clean flat illustration, no text."),
    "voice": (RequestKind.VOICE, "Hello everyone. আশা করি সবাই ভালো আছো।"),
    "sing": (RequestKind.SING, "A 20-second upbeat original Banglish friendship jingle with acoustic guitar and light claps."),
    "edit": (RequestKind.EDIT, "Change the robot's blue accent color to green. Preserve everything else."),
}


async def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "image"
    kind, prompt = REQUESTS[command]
    settings = Settings.from_env()
    source_image = next(settings.output_dir.glob("*.png"), None) if kind == RequestKind.EDIT else None
    if kind == RequestKind.EDIT and source_image is None:
        raise RuntimeError("Run the image smoke test first to create an edit source.")
    asset = await ManusMediaClient(settings).generate(kind, prompt, source_image)
    print(f"{asset.media_type}:{asset.local_path}")


if __name__ == "__main__":
    asyncio.run(main())
