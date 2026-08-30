"""Agent Bridge core package.

Agent Bridge connects coding-agent harnesses through their official callable
command-line interfaces. This module holds only the two constants every other
module and every native package needs to agree on. It imports nothing, spawns
nothing, and does no work when it is imported.

SPDX-License-Identifier: Unlicense
"""

#: Version of the on-disk session record format. `SESSION.md` records it as
#: `Bridge-Format:` so a later reader can tell which layout it is looking at.
BRIDGE_FORMAT = 1

#: Version of this source tree. Nothing has been released, so this is 0.0.0.
VERSION = "0.0.0"

__version__ = VERSION

__all__ = ["BRIDGE_FORMAT", "VERSION", "__version__"]
