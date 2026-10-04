"""vaultkit: the shared core of the vault services (Kura, Niwa, Konbini).

Vendored, not installed: each service copies this package in with vendor.sh at a tagged version, and
`python3 -m vaultkit.verify` fails when the copy was edited in place (fix it here, tag, re-vendor).
"""
from .front import (CONFLICT_RE, FRONT_RE, PHONE_CONFLICT, WIKILINK_RE, _str, _unlink,  # noqa: F401
                    note_front, tags_of)
from .git import Git, Mirror, auth_env, borrow, read_secret                            # noqa: F401
from .notes import (BOARD_STATUSES, CONFIDENCE, LINK_RE, NOTE_STATUSES, STAGE_MARK, STAGES, TYPES,  # noqa: F401
                    Note, created_of, e, first_paragraph, note_status, read_notes, relative, stage_of, type_of)
from .vault import CALLOUT_RE, EMBED_RE, HIDDEN, IMAGE_EXT, MDIMG_RE, Vault            # noqa: F401
from .frontmatter import EditError, edit_front, merge_note, version_of, yaml_scalar   # noqa: F401
from .gitsync import GitSync                                                          # noqa: F401

__version__ = "0.17.0"
