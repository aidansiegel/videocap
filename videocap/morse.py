"""Morse code on a light, for reading the station's status with a phone camera (or by eye) when there's no screen.

A message is a sync flash, the text, and the end-of-message sign (AR, .-.-.). The sync flash is SYNC units long,
so a reader learns the speed from it before the first letter. docs/reader/morse.js decodes exactly this.
"""
UNIT_MS = 100   # one dot: 12 words a minute, about 3 frames of a 30 fps phone camera
SYNC = 10       # the sync flash, in units
SYNC_GAP = 5    # the dark gap after it, in units

CODE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.", "G": "--.", "H": "....", "I": "..",
    "J": ".---", "K": "-.-", "L": ".-..", "M": "--", "N": "-.", "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.",
    "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-", "Y": "-.--", "Z": "--..",
    "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-", "5": ".....", "6": "-....", "7": "--...",
    "8": "---..", "9": "----.", ".": ".-.-.-", "/": "-..-.", "-": "-....-",
}
END = ".-.-."  # AR, end of message


def clean(text):
    """The text as it will be sent: capitals, digits and . / -; anything else becomes a space."""
    out = "".join(c if c in CODE else " " for c in text.upper().replace("_", " "))
    return " ".join(out.split())


def timings(text, unit=UNIT_MS):
    """(on ms, off ms) pairs for a light: sync flash, the message, the end sign, then dark."""
    seq = [(SYNC * unit, SYNC_GAP * unit)]
    words = clean(text).split(" ") if clean(text) else []
    for word in words:
        for ci, ch in enumerate(word):
            for el in CODE[ch]:
                seq.append(((1 if el == "." else 3) * unit, unit))
            gap = 3 if ci < len(word) - 1 else 7  # between letters; between words (and before the end sign)
            seq[-1] = (seq[-1][0], gap * unit)
    for el in END:
        seq.append(((1 if el == "." else 3) * unit, unit))
    seq[-1] = (seq[-1][0], 10 * unit)  # dark for a moment after, so the end is clear
    return seq


def duration(seq):
    return sum(a + b for a, b in seq) / 1000


LETTERS = {v: k for k, v in CODE.items()}


class Decoder:
    """Reads a message back from runs of the light (on or off, and for how long); the same rules as the phone
    reader's docs/reader/morse.js, so the two read alike."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.unit = 0.0      # ms per dot, once the sync flash is seen
        self.letter = ""
        self.text = ""
        self.done = False
        self.started = False

    def run(self, lit, ms):
        """One run of the light. True once the message is complete."""
        if self.done:
            return True
        if lit and 400 < ms < 3500 and (not self.started or ms > 6 * self.unit):
            self.reset()
            self.unit = ms / SYNC
            self.started = True
            return False
        if not self.started:
            return False
        u = self.unit
        if lit:
            dash = ms > 2 * u
            self.letter += "-" if dash else "."
            est = ms / 3 if dash else ms
            if 0.5 * u < est < 1.6 * u:
                self.unit = 0.85 * u + 0.15 * est
            return False
        if ms < 2 * u:
            return False
        self.end_letter()
        if self.done:
            return True
        if ms >= 5 * u and self.text and not self.text.endswith(" "):
            self.text += " "
        if ms > 25 * u and self.text:
            self.done, self.text = True, self.text.strip()
        return self.done

    def end_letter(self):
        if not self.letter:
            return
        if self.letter == END:
            self.done, self.text = True, self.text.strip()
        else:
            self.text += LETTERS.get(self.letter, "?")
        self.letter = ""

    @property
    def partial(self):
        return self.text + ("[%s]" % self.letter if self.letter else "")
