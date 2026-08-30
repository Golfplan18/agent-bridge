"""Reading the one line that decides an external review.

An external reviewer's answer is ordinary Markdown prose. Its last line, and
only its last line, may carry a decision. The rule is deliberately unforgiving:
the final line that is not blank must be character-for-character one of three
strings. Anything else - a code fence after it, different capitalisation, one
trailing space, an unknown word, prose, nothing at all - is a technical failure,
never an acceptance.

That strictness is the safety property. A reviewing peer writes the body, and a
body is not allowed to become authority by accident. Text that merely looks like
a verdict earlier in the response is inert, because the parser reads only the
final nonblank line.

SPDX-License-Identifier: Unlicense
"""

from __future__ import annotations

from typing import NamedTuple, Optional, Tuple

from .errors import Failure

#: The three decisions an external review may return.
ACCEPT = "ACCEPT"
REJECT = "REJECT"
ASK_USER = "ASK_USER"

VERDICT_VALUES: Tuple[str, ...] = (ACCEPT, REJECT, ASK_USER)

#: Everything before the decision on a verdict line. Spacing is exact.
VERDICT_PREFIX = "Agent-Bridge-Verdict: "

#: The three lines that are valid in full, for documentation and fixtures.
VERDICT_LINES: Tuple[str, ...] = tuple(
    VERDICT_PREFIX + value for value in VERDICT_VALUES
)


class VerdictResult(NamedTuple):
    """What reading a response produced.

    Exactly one of the two fields is set. `verdict` holds ACCEPT, REJECT or
    ASK_USER when the response ended correctly. Otherwise `failure` says which
    of the two failing states holds: EMPTY_RESPONSE when the peer produced no
    text at all, INVALID_VERDICT when it produced text whose final nonblank line
    is not one of the three exact verdict lines.
    """

    verdict: Optional[str]
    failure: Optional[Failure]

    @property
    def ok(self) -> bool:
        """True when a valid verdict was read."""
        return self.verdict is not None


def read_verdict(response: str) -> VerdictResult:
    """Read the decision, if any, from a reviewing peer's whole response.

    Line endings are normalised first, so a response written on Windows is
    judged the same as one written on macOS. Blank lines at the end are then
    dropped, because trailing newlines are an artefact of how a program writes
    its output and say nothing about the decision. What remains is the final
    nonblank line, compared exactly.

    Ordinary invalid input is never an exception: unreadable answers are the
    normal case this function exists to report.
    """
    normalised = response.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalised.split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return VerdictResult(verdict=None, failure=Failure.EMPTY_RESPONSE)
    final_line = lines[-1]
    for value in VERDICT_VALUES:
        if final_line == VERDICT_PREFIX + value:
            return VerdictResult(verdict=value, failure=None)
    return VerdictResult(verdict=None, failure=Failure.INVALID_VERDICT)
