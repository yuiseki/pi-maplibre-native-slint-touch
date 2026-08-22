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


class StatePublisher:
    """Writes `<word>\\n<caption>\\n`, newest interesting state wins."""

    def __init__(self, path, clock=time.monotonic):
        self.path = path
        self.clock = clock
        self._word = None
        self._text = None
        self._at = 0.0
        self._hold_until = 0.0

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
                f.write(word + "\n" + text + "\n")
        except OSError:
            return False
        return True
