"""Turn a raw /video or /musicvideo prompt into a structured creative plan.

The pipeline the user asked for: take the prompt, summarize it into a tight idea,
expand that into an extended voiceover script (or song brief), and describe 2-3
visual scenes plus an overall mood. An LLM does the creative writing; THIS module
only builds the instruction prompt and then safely parses the model's reply.

Safety boundary: the model returns ONLY structured data — an idea, scene
descriptions, a script, and a mood label from a FIXED set. It never emits ffmpeg
syntax. The compositor maps the mood label to concrete filter parameters in code,
so nothing the model says can reach a shell command. Parsing always yields a valid
plan (falling back to the user's own prompt) so the feature never hard-fails here.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re

# The only moods the compositor knows how to render. The model is asked to pick
# one of these; anything else is replaced with a per-command default.
ALLOWED_MOODS = ("calm", "cinematic", "energetic")

MIN_SCENES = 2
MAX_SCENES = 3


@dataclass(frozen=True)
class VideoPlan:
    idea: str          # one-line creative summary of what the video is about
    scenes: list[str]  # 2-3 vivid image prompts, one per visual scene
    script: str        # extended voiceover text (/video) or song brief+lyrics (/musicvideo)
    mood: str          # one of ALLOWED_MOODS


def _default_mood(is_music: bool) -> str:
    return "energetic" if is_music else "cinematic"


def build_planner_prompt(user_prompt: str, is_music: bool) -> str:
    """Instruction prompt asking the LLM for a strict-JSON creative plan."""
    if is_music:
        script_line = (
            '"script": a short original song brief for a singer — theme, feel, and 4-8 '
            "original lyric lines (no copyrighted lyrics, do not imitate a real artist). "
            "Match the language of the user idea (Bengali, Banglish, or English)."
        )
    else:
        script_line = (
            '"script": an extended, natural spoken VOICEOVER (about 3-6 sentences) that '
            "narrates the idea warmly. Write ONLY the words to be spoken, in the same "
            "language/script as the user idea (Bengali, Banglish, or English)."
        )
    moods = ", ".join(f'"{m}"' for m in ALLOWED_MOODS)
    return (
        "You are a creative director planning a short social-media video. "
        f"Take the user's idea and design the video.\n\nUser idea: {user_prompt}\n\n"
        "Respond with a SINGLE JSON object and nothing else — no markdown, no commentary. "
        "The JSON must have exactly these keys:\n"
        '"idea": one vivid sentence summarizing the concept.\n'
        f'"scenes": an array of {MIN_SCENES} to {MAX_SCENES} strings, each a short vivid '
        "visual description of one shot (setting, subject, lighting, colors) suitable for "
        "an image generator. Do not put any text or captions in the images.\n"
        f"{script_line}\n"
        f'"mood": exactly one of {moods} — pick the one that best fits the feel.\n\n'
        "Return only the JSON object."
    )


def _coerce_scenes(value: object, user_prompt: str) -> list[str]:
    scenes: list[str] = []
    if isinstance(value, list):
        for item in value:
            text = str(item).strip()
            if text:
                scenes.append(text)
    elif isinstance(value, str) and value.strip():
        scenes.append(value.strip())

    if not scenes:
        # No usable scenes from the model: build two from the user's own prompt so the
        # multi-scene compositor still has something to cross-fade.
        scenes = [user_prompt.strip() or "an abstract cinematic background"] * MIN_SCENES
    if len(scenes) < MIN_SCENES:
        scenes = (scenes * MIN_SCENES)[:MIN_SCENES]
    return scenes[:MAX_SCENES]


def _extract_json_object(raw_text: str) -> dict | None:
    """Best-effort pull of the first JSON object out of a model reply.

    Tolerates ```json fences and leading/trailing prose by scanning for the first
    balanced {...} block, then falling back to a plain json.loads.
    """
    if not raw_text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_text, re.DOTALL)
    candidates = []
    if fenced:
        candidates.append(fenced.group(1))
    # First balanced-looking object: from the first "{" to the last "}".
    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(raw_text[start : end + 1])
    candidates.append(raw_text)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def parse_plan(raw_text: str, user_prompt: str, is_music: bool) -> VideoPlan:
    """Validate and clamp a model reply into a VideoPlan; always returns a valid plan."""
    data = _extract_json_object(raw_text) or {}

    idea = str(data.get("idea") or "").strip() or user_prompt.strip()
    scenes = _coerce_scenes(data.get("scenes"), user_prompt)

    script = str(data.get("script") or "").strip()
    if not script:
        # No script from the model: narrate/sing the user's own words rather than fail.
        script = user_prompt.strip()

    mood = str(data.get("mood") or "").strip().lower()
    if mood not in ALLOWED_MOODS:
        mood = _default_mood(is_music)

    return VideoPlan(idea=idea, scenes=scenes, script=script, mood=mood)
