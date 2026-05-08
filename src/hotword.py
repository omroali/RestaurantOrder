"""
hotword.py  –  Wake-word and interruption detection.

In the current pipeline Whisper has already transcribed the audio before
this module is called, so detection is a plain substring search — no model
needed here at all.

──────────────────────────────────────────────────────────────────────────────
### ROS INTEGRATION ###
This module is transport-agnostic.  Use it unchanged inside
voice_listener_node.py.

### UPGRADE PATH – audio-based hotword engine ###

When you want "Hey Tiago!" to trigger the robot even before Whisper runs
(lower wake-latency, always-on), subclass HotwordDetector and add a
check(audio_bytes, sample_rate) method backed by a dedicated engine:

Option A – Porcupine (pvporcupine):
    • pip install pvporcupine
    • Free AccessKey + custom keyword model from console.picovoice.ai

    class PorcupineDetector(HotwordDetector):
        def __init__(self, access_key, keyword_paths, wake_words=None):
            super().__init__(wake_words)
            import pvporcupine
            self._porcupine = pvporcupine.create(
                access_key=access_key, keyword_paths=keyword_paths)

        def check(self, audio_bytes: bytes, sample_rate: int = 16000):
            import struct
            pcm   = struct.unpack_from(f"{len(audio_bytes)//2}h", audio_bytes)
            index = self._porcupine.process(pcm)
            return self.wake_words[index] if index >= 0 else None

Option B – OpenWakeWord (open-source):
    • pip install openwakeword

    class OpenWakeWordDetector(HotwordDetector):
        def __init__(self, model_paths, wake_words=None):
            super().__init__(wake_words)
            from openwakeword.model import Model
            self._model = Model(wakeword_models=model_paths)

        def check(self, audio_bytes: bytes, sample_rate: int = 16000):
            for phrase, score in self._model.predict(audio_bytes).items():
                if score > 0.5:
                    return phrase
            return None
──────────────────────────────────────────────────────────────────────────────
"""

from typing import List, Optional


# Phrases that cancel an active ordering session mid-turn.
INTERRUPTION_PHRASES: List[str] = [
    "stop", "cancel", "abort", "never mind", "nevermind",
    "start over", "forget it", "quit",
]


class HotwordDetector:
    """
    Detect wake words and interruption phrases in already-transcribed text.

    Parameters
    ----------
    wake_words : list[str], optional
        Phrases to listen for (case-insensitive substring match).
        Defaults to a sensible set including "hey tiago".
    """

    DEFAULT_WAKE_WORDS: List[str] = [
        "hey tiago",
        "hi tiago",
        "excuse me",
        "order please",
        "hey robot",
    ]

    def __init__(self, wake_words: Optional[List[str]] = None) -> None:
        self.wake_words = [w.lower() for w in (wake_words or self.DEFAULT_WAKE_WORDS)]

    def detect_in_text(self, text: str) -> Optional[str]:
        """
        Return the first wake word found in *text*, or None.

        This normalizes the transcript by lowercasing and removing
        punctuation so phrases like "hi, tiago" match "hi tiago".
        """
        lower = text.lower()
        # keep only alphanumeric characters and whitespace; this removes
        # punctuation such as commas, periods and exclamation marks
        norm = ''.join(ch for ch in lower if ch.isalnum() or ch.isspace())
        # collapse multiple spaces that may have been created
        norm = ' '.join(norm.split())

        for phrase in self.wake_words:
            # normalize the phrase the same way we normalized the text
            phrase_norm = ''.join(ch for ch in phrase if ch.isalnum() or ch.isspace())
            phrase_norm = ' '.join(phrase_norm.split())
            if phrase_norm in norm:
                return phrase
        return None

    def is_interruption(self, text: str) -> bool:
        """Return True if *text* contains a session-cancellation phrase.

        Ignore punctuation when checking phrases (e.g. "stop!" -> "stop").
        """
        lower = text.lower()
        norm = ''.join(ch for ch in lower if ch.isalnum() or ch.isspace())
        norm = ' '.join(norm.split())
        return any(' '.join(''.join(ch for ch in phrase if ch.isalnum() or ch.isspace()).split()) in norm
                   for phrase in INTERRUPTION_PHRASES)
