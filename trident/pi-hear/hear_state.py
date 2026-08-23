"""What pi-hear is doing, published for the map's caption strip.

The map freezes while the recogniser has the CPU, so the caption is the only
thing telling anyone the device is alive -- and, when a command goes nowhere,
whether it misheard or heard nothing at all.

The rule that matters is precedence. The capture loop comes round many times a
second and publishes the least interesting state there is ("listening"); the
worker publishes the interesting ones, rarely. Left to fight it out by
recency, the loop wins every time: recognition showed for about half a second
and the transcription never appeared. So an interesting state takes a `hold`,
and the loop's updates are dropped until it expires.
"""
import time


# The map has captions in these two. Anything else -- PI_HEAR_LANG can be
# 'auto' -- falls back rather than reaching the map as a language it cannot
# render, which would show an empty caption strip.
CAPTION_LANGS = ("ja", "en")
DEFAULT_LANG = "ja"


class StatePublisher:
    """Writes `<word>\\n<caption>\\n<lang>\\n`, newest interesting state wins.

    The third line is the language, because the captions on screen are the
    map's and the language is pi-hear's. The map could read PI_HEAR_LANG for
    itself, but then changing it means restarting both, and forgetting one
    leaves the deck listening in one language and captioning in another. An
    older map reads two lines and ignores the third.
    """

    def __init__(self, path, clock=time.monotonic, lang=None):
        self.path = path
        self.clock = clock
        self.lang = lang
        self._word = None
        self._text = None
        self._at = 0.0
        self._hold_until = 0.0

    @property
    def lang(self):
        return self._lang

    @lang.setter
    def lang(self, value):
        self._lang = value if value in CAPTION_LANGS else DEFAULT_LANG

    def publish(self, word, text="", hold=0.0, override=False):
        """Publish a state. Returns whether it was actually written.

        hold      keep this on screen for this many seconds
        override  outrank an existing hold (for the states that end one)
        """
        if not self.path:
            return False
        now = self.clock()
        if not override and hold <= 0.0 and now < self._hold_until:
            return False
        # Re-stating the same thing is pointless; the capture loop would do it
        # many times a second otherwise, and this lives on tmpfs but still.
        if (word, text) == (self._word, self._text) and now - self._at < 1.0:
            return False
        self._word = word
        self._text = text
        self._at = now
        self._hold_until = now + hold if hold > 0.0 else 0.0
        try:
            with open(self.path, "w") as f:
                f.write(word + "\n" + text + "\n" + self._lang + "\n")
        except OSError:
            return False
        return True


class LevelPublisher:
    """Writes one number, 0..1, for the map's waveform.

    Separate from StatePublisher because it changes many times a second and
    the state does not: folding it in would defeat the state file's "do not
    re-state the same thing" rule, which is what stops the caption flickering.

    Normalised against the VAD threshold so the map need know nothing about
    this particular microphone -- the threshold is a third of the way up, and
    three times it saturates. A drop to silence is published immediately
    whatever the rate limit says, because a wave left standing at full height
    after someone stops talking says the opposite of what is true.
    """

    def __init__(self, path, threshold, clock=time.monotonic, min_interval=0.05):
        self.path = path
        self.full = max(1e-6, threshold * 3.0)
        self.clock = clock
        self.min_interval = min_interval
        self._at = 0.0
        self._last = None

    def publish(self, rms):
        if not self.path:
            return False
        level = rms / self.full
        level = 0.0 if level < 0.0 else (1.0 if level > 1.0 else level)
        now = self.clock()
        silent = level == 0.0 and self._last not in (0.0, None)
        if not silent and now - self._at < self.min_interval:
            return False
        self._at = now
        self._last = level
        try:
            with open(self.path, "w") as f:
                f.write("%.4f\n" % level)
        except OSError:
            return False
        return True
