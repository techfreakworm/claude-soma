"""Exit 0 if stdin (a capture-pane dump) contains a REPLY line that is just the token.

The user's own message also contains the token, so a plain grep would match the echo; a reply
line is the token alone, optionally wrapped in backticks/quotes/period, behind TUI border glyphs.
"""
import re
import sys

tok = re.escape(sys.argv[1])
pat = re.compile(r"^[\s│┃║|]*[`'\"*]*" + tok + r"[`'\".*]*\s*$")
sys.exit(0 if any(pat.match(ln) for ln in sys.stdin.read().splitlines()) else 1)
