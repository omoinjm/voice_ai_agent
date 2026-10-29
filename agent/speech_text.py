"""Text clean-up applied right before TTS.

LLMs write typographic characters (em dashes, narrow no-break spaces, curly quotes, ellipses) that
some TTS APIs reject; Deepgram Aura closed the connection with `1008 DATA-0001` mid-call when the
reply contained "ten am". Map them to plain ASCII and drop anything else non-printable.
"""

import re

from pipecat.utils.text.base_text_filter import BaseTextFilter

_REPLACEMENTS = {
    "—": ", ",  # em dash
    "–": " to ",  # en dash, mostly in ranges ("8–5")
    "‒": "-",
    "‐": "-",
    "‑": "-",
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "…": "...",
    " ": " ",
    " ": " ",
    " ": " ",
    "​": "",
}
_TABLE = str.maketrans(_REPLACEMENTS)
_UNPRINTABLE = re.compile(r"[^\x20-\x7EÀ-ɏ]")  # keep ASCII and accented Latin letters


class SpeechTextFilter(BaseTextFilter):
    async def update_settings(self, settings):
        pass

    async def filter(self, text: str) -> str:
        return _UNPRINTABLE.sub("", text.translate(_TABLE))
