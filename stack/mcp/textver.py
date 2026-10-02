"""A note's version: what notes_read returns and notes_update must send back. Line endings and trailing blank lines
don't count, so Kura's copy of a note and the file in the write clone agree."""
import hashlib


def text_version(text):
    return hashlib.sha1((text.replace("\r\n", "\n").rstrip() + "\n").encode("utf-8")).hexdigest()[:12]
