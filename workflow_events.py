"""Queue event names shared by workflow runners and the Tkinter UI.

Events are still emitted as ``(event_name, payload)`` tuples. This module only
documents and centralizes the existing string contract; it does not introduce
dataclasses or change runtime behavior.
"""

from __future__ import annotations


# str payload: append to the log text area.
LOG = "log"

# str payload: append to the main live output/story preview text area.
PREVIEW = "preview"

# str payload: show parsed translation segment preview.
TRANSLATION_PREVIEW = "translation_preview"

# str payload: show translation source preview.
TRANSLATION_SOURCE = "translation_source"

# str payload: append/show translation output preview.
TRANSLATION_OUTPUT = "translation_output"

# str payload: show formatted validation report text.
VALIDATION_REPORT = "validation_report"

# list[str] payload: provider model IDs returned by model listing.
MODELS_LIST = "models_list"

# str payload: append enhanced prompt output.
ENHANCER_APPEND = "enhancer_append"

# str payload: final enhanced prompt output.
ENHANCER_DONE = "enhancer_done"

# str payload: set status label text.
STATUS = "status"

# str payload: show fatal/user-visible error.
ERROR = "error"

# dict payload: rewrite chunk row update compatible with update_chunk_row().
CHUNK = "chunk"

# dict payload: translation segment row update compatible with update_chunk_row().
SEGMENT = "segment"

# Any payload: worker finished; UI stops progress/running state.
DONE = "done"

