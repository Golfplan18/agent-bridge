"""Agent Bridge core package.

Agent Bridge connects coding-agent harnesses through their official callable
command-line interfaces. This module exposes the source release and on-disk
session-format versions. It imports nothing, spawns nothing, and does no work
when it is imported.

SPDX-License-Identifier: Unlicense
"""

#: Version of the on-disk session record format. `SESSION.md` records it as
#: `Bridge-Format:` so a later reader can tell which layout it is looking at.
BRIDGE_FORMAT = 2

#: Release version of this source tree. Release 1 is version 1.0.0.
VERSION = "1.0.0"

__version__ = VERSION

__all__ = ["BRIDGE_FORMAT", "VERSION", "__version__"]
