from pathlib import Path
import os
import time
from openai import OpenAI

# ── Config ──────────────────────────────────────────────
OUTPUT_FILE = Path("deepseek_original_novella.md")
MODEL = "deepseek/deepseek-v4-flash"

# Put your key in Windows environment variable:
# PowerShell:
#   setx OPENROUTER_API_KEY "YOUR_KEY_HERE"
API_KEY = os.environ.get("OPENROUTER_API_KEY")

# Conservative routing: avoids OpenRouter silently falling back to expensive providers.
# If this causes endpoint errors, set SAFE_ROUTING = False.
SAFE_ROUTING = True

TEMPERATURE = 0.78
TOP_P = 0.92
MAX_TOKENS_PER_CALL = 120000
MAX_CONTINUATIONS = 5
CONTINUE_MARKER = "[STORY_CONTINUES]"

STORY_PROMPT = r"""
THE NOVELLA MANDATE: EXISTENTIAL VOID AND CONTROLLED TERROR

MISSION
Write a completely original long-form sci-fi horror novella from scratch.

The story must be a polished evening read: immersive, bleak, intelligent, atmospheric, and emotionally unsettling. It should confront the reader with cosmic indifference, failed human ambition, and the fragility of perception under extreme isolation.

This is not a rewrite.
This is not based on prior drafts.
Create a new original story.

NARRATIVE CORE
The horror must come from rational systems failing in ways the protagonist cannot fully understand:
- impossible physics,
- corrupted perception,
- machine logic,
- environmental collapse,
- recursive signals,
- memory instability,
- or a phenomenon that resists human categories.

The central threat must not be a simple monster. It should be strange, systemic, and conceptually disturbing. It may harm the body, but its real terror should be intellectual and existential.

The protagonist should not be an action hero. They are pressured by isolation, uncertainty, exhaustion, limited knowledge, and the slow realization that understanding the truth may make survival worse.

STYLE
- Second person present tense.
- Hard sci-fi horror.
- Slow-burn dread.
- Claustrophobic, lonely, intelligent atmosphere.
- Beautiful but controlled prose.
- Strong physical detail: pressure, temperature, sound, smell, light, material decay, bodily fatigue.
- Precise language over purple prose.
- Avoid repeating the same metaphors.
- Every scene must move the story forward emotionally, conceptually, or physically.

STRUCTURE
Use polished Markdown.

Include:
- a strong title,
- 8 to 12 titled sections,
- scene breaks using ---,
- in-story logs, transcripts, readouts, or fragments only when they naturally serve the mystery.

The story should progress through:
1. Establishing the protagonist’s isolated situation.
2. Introducing the first impossible irregularity.
3. Deepening the mystery through environment, data, and perception.
4. Revealing that the threat is stranger than physical danger.
5. Forcing the protagonist into a costly investigation or decision.
6. Reframing the meaning of survival.
7. Ending with bleak ambiguity or permanent uncertainty.

CREATIVE DIRECTIVES
Setting as Antagonist:
The environment must feel like an active pressure on the protagonist. The setting should shape fear, choices, and deterioration.

Discovery Through Fragments:
Reveal truth through partial evidence: logs, sensor anomalies, damaged recordings, physical traces, failed experiments, contradictory memories, or environmental behavior.

Threat Sophistication:
The threat should challenge interpretation. The protagonist should struggle to determine whether it is natural, artificial, conscious, accidental, or something outside those categories.

Psychological Pressure:
The protagonist’s mind should become part of the battlefield. Fear should emerge from observation, implication, and uncertainty, not jump scares.

Ending:
The ending must be bleak, ambiguous, or quietly devastating. Avoid clean rescue, heroic triumph, simple defeat of evil, or comforting explanation. Survival may occur, but it must cost something.

STRICT EXCLUSIONS
Do not use:
- romance,
- comedy relief,
- military power fantasy,
- fantasy elements,
- cheap monster attacks,
- clean happy rescue,
- generic chosen-one logic,
- easy villain explanation,
- simple “kill the monster” resolution.

Do not reuse any previous names, locations, timestamps, AI names, sector names, or plot mechanics from earlier drafts.

Do not write about:
- a derelict station AI waking at a repeated timestamp,
- an AI sacrificing itself to stop a data parasite,
- a standard last-survivor-on-dead-station plot unless radically transformed into something fundamentally different.

Prioritize inventing a fresh central mechanism of horror over avoiding individual forbidden examples.

LENGTH
Target 25,000 to 40,000 words if possible.
Do not summarize.
Do not rush.
If you cannot finish in one response, stop at a clean section boundary and write exactly:
[STORY_CONTINUES]

OUTPUT RULES
Output only the story.
No planning.
No explanation.
No commentary.
No preamble.
Begin immediately with the title.
""".strip()


def build_extra_body() -> dict:
    body = {"reasoning": {"enabled": False}}

    if SAFE_ROUTING:
        body["provider"] = {
            "sort": "price",
            "allow_fallbacks": False,
            "max_price": {
                "prompt": 0.14,
                "completion": 0.28,
            },
        }

    return body


def create_stream_with_retries(client: OpenAI, messages: list[dict], max_retries: int = 8):
    for attempt in range(1, max_retries + 1):
        try:
            return client.chat.completions.create(
                model=MODEL,
                messages=messages,
                temperature=TEMPERATURE,
                top_p=TOP_P,
                max_tokens=MAX_TOKENS_PER_CALL,
                stream=True,
                extra_body=build_extra_body(),
            )
        except Exception as exc:
            msg = str(exc)
            rate_limited = "429" in msg or "rate" in msg.lower() or "rate-limited" in msg.lower()

            if not rate_limited:
                raise

            wait = min(30 * attempt, 180)
            print(f"Rate limited on attempt {attempt}/{max_retries}. Waiting {wait}s...", flush=True)
            time.sleep(wait)

    raise RuntimeError("Too many rate-limit retries.")


def stream_call(client: OpenAI, messages: list[dict], output_file: Path, append: bool) -> tuple[str, str | None]:
    mode = "a" if append else "w"
    text_parts: list[str] = []
    output_chars = 0
    last_progress = 0
    start_time = time.time()
    finish_reason = None

    with output_file.open(mode, encoding="utf-8", newline="\n") as out:
        stream = create_stream_with_retries(client, messages)

        for chunk in stream:
            if not chunk.choices:
                continue

            choice = chunk.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason

            delta = choice.delta.content or ""
            if not delta:
                continue

            text_parts.append(delta)
            out.write(delta)
            out.flush()

            output_chars += len(delta)
            if output_chars - last_progress >= 5000:
                last_progress = output_chars
                elapsed = time.time() - start_time
                print(f"Written this call: {output_chars:,} chars ({elapsed / 60:.1f} min)", flush=True)

    return "".join(text_parts), finish_reason


def remove_continue_marker_from_file(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    cleaned = text.replace(CONTINUE_MARKER, "").rstrip() + "\n"
    path.write_text(cleaned, encoding="utf-8", newline="\n")


def main() -> None:
    if not API_KEY:
        raise RuntimeError(
            "Missing OPENROUTER_API_KEY environment variable. "
            "Set it first instead of hardcoding your key."
        )

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=API_KEY,
        timeout=7200,
    )

    print(f"Model: {MODEL}")
    print(f"Output file: {OUTPUT_FILE.resolve()}")
    print(f"Safe routing: {SAFE_ROUTING}")
    print("Starting original novella generation...\n")

    messages = [
        {
            "role": "system",
            "content": "You are a careful long-form literary horror writer. Output only polished story prose.",
        },
        {
            "role": "user",
            "content": STORY_PROMPT,
        },
    ]

    total_start = time.time()
    append = False

    for call_number in range(1, MAX_CONTINUATIONS + 2):
        print(f"Call {call_number}/{MAX_CONTINUATIONS + 1}", flush=True)

        try:
            generated, finish_reason = stream_call(client, messages, OUTPUT_FILE, append=append)
        except KeyboardInterrupt:
            print("\nStopped by user. Partial output was saved.")
            return
        except Exception as exc:
            print(f"\nError: {exc}")
            print("Partial output was saved if streaming had started.")
            return

        print(f"Call {call_number} finish reason: {finish_reason}", flush=True)

        full_output = OUTPUT_FILE.read_text(encoding="utf-8") if OUTPUT_FILE.exists() else ""
        output_words = len(full_output.split())
        print(f"Current output words: {output_words:,}\n", flush=True)

        needs_continue = CONTINUE_MARKER in generated or finish_reason == "length"
        if not needs_continue:
            break

        remove_continue_marker_from_file(OUTPUT_FILE)
        full_output = OUTPUT_FILE.read_text(encoding="utf-8")

        if call_number > MAX_CONTINUATIONS:
            print("Reached maximum continuations. Partial story saved.")
            break

        # Keep the full generated story in context so the continuation does not restart.
        messages = [
            {
                "role": "system",
                "content": "You are continuing the same original novella. Output only polished story prose.",
            },
            {
                "role": "user",
                "content": (
                    "Continue the story from the exact point where it stopped.\n"
                    "Do not restart. Do not summarize. Do not explain.\n"
                    "Preserve the same second-person present-tense style, tone, continuity, headings, and pacing.\n"
                    "Continue from the last sentence of the story below.\n"
                    "If you still cannot finish, stop at a clean section boundary and write exactly:\n"
                    f"{CONTINUE_MARKER}\n\n"
                    "STORY_SO_FAR_START\n"
                    f"{full_output}\n"
                    "STORY_SO_FAR_END"
                ),
            },
        ]

        append = True
        with OUTPUT_FILE.open("a", encoding="utf-8", newline="\n") as out:
            out.write("\n\n")

    elapsed = time.time() - total_start
    final_text = OUTPUT_FILE.read_text(encoding="utf-8")
    final_words = len(final_text.split())

    print("Done.")
    print(f"Saved to: {OUTPUT_FILE.resolve()}")
    print(f"Output words: {final_words:,}")
    print(f"Output chars: {len(final_text):,}")
    print(f"Total time: {elapsed / 60:.1f} minutes")


if __name__ == "__main__":
    main()
