"""Let the package be run directly: `python3 -m bridge <command> ...`.

Native packages start Agent Bridge exactly this way - as a fixed argument
vector, with no shell and no installed console script to depend on.

SPDX-License-Identifier: Unlicense
"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
