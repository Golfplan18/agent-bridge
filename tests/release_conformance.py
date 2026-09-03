"""Focused Release 1 inspection and adapter checks.

This module is test-only. It keeps no state and supplies no callable product
surface. ``inspect`` checks that the shipped command and target boundaries stay
literal and that the four package files describe only the shared courier
commands. ``adapters`` drives those same public commands through the real core
with the repository's fake peer, including one unregistered initiator label.

SPDX-License-Identifier: Unlicense
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import io
import os
import re
import sys
import tempfile
from typing import Dict, Iterable, List, Sequence, Tuple
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from bridge import BRIDGE_FORMAT, cli, connectors, session  # noqa: E402
from bridge.connectors import PeerCommand  # noqa: E402
from bridge.errors import BridgeError, Failure  # noqa: E402

TARGETS = ("codex", "claude", "zcode", "hermes")
ADAPTERS = TARGETS
FAKE_PEER = os.path.join(REPO_ROOT, "tests", "fake_peer.py")

PRODUCTION_MODULES = {
    "__init__.py",
    "__main__.py",
    "claude.py",
    "cli.py",
    "codex.py",
    "connectors.py",
    "errors.py",
    "hermes.py",
    "locking.py",
    "peer.py",
    "record.py",
    "runner.py",
    "session.py",
    "zcode.py",
}

REMOVED_ADAPTER_TEXT = (
    "--local",
    "--workflow",
    "--review-base",
    "--review-head",
    "--replace",
    "PLAN.md",
    "Programming Loop",
    "programming-loop",
    "implementation-start",
    "plan-approval",
    "technical-error",
    "user-correction",
)

REMOVED_ADAPTER_WORDS = (
    "coordinator",
    "database",
    "git",
    "planning",
    "registry",
    "review",
    "router",
    "sdk",
    "workflow",
)


class ConformanceError(Exception):
    """One concise reason a focused command cannot pass."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ConformanceError(message)


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as stream:
            return stream.read()
    except OSError as exc:
        raise ConformanceError("cannot read {0}: {1}".format(path, exc))


def _command_options(parser: argparse.ArgumentParser) -> Dict[str, set]:
    choices = None
    for action in parser._actions:
        if action.dest == "command" and hasattr(action, "choices"):
            choices = action.choices
            break
    _require(isinstance(choices, dict), "the public command set is unreadable")
    found = {}
    for name, child in choices.items():
        found[name] = {
            option
            for action in child._actions
            for option in action.option_strings
            if option not in {"-h", "--help"}
        }
    return found


def _literal_switches() -> Dict[str, str]:
    path = os.path.join(REPO_ROOT, "bridge", "connectors.py")
    tree = ast.parse(_read(path), filename=path)
    switch = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_switch"
        ),
        None,
    )
    _require(switch is not None, "bridge.connectors has no literal _switch")
    found = {}
    for node in switch.body:
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        comparison = node.test
        if (
            not isinstance(comparison.left, ast.Name)
            or comparison.left.id != "harness_id"
            or len(comparison.ops) != 1
            or not isinstance(comparison.ops[0], ast.Eq)
            or len(comparison.comparators) != 1
            or not isinstance(comparison.comparators[0], ast.Constant)
            or not isinstance(comparison.comparators[0].value, str)
            or len(node.body) != 1
            or not isinstance(node.body[0], ast.Return)
            or not isinstance(node.body[0].value, ast.Name)
        ):
            continue
        found[comparison.comparators[0].value] = node.body[0].value.id
    return found


def _adapter_path(name: str) -> str:
    return os.path.join(REPO_ROOT, "packages", name, "SKILL.md")


def _bridge_command_lines(text: str) -> List[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith("python3 -m ")
    ]


def _inspect_adapter(name: str) -> None:
    path = _adapter_path(name)
    text = _read(path)
    lowered = text.lower()

    _require(
        "initiator label is always `{0}`".format(name) in lowered,
        "{0} does not declare its fixed initiator label".format(path),
    )
    _require(
        "--kind session-create --initiator {0} --peer".format(name) in text,
        "{0} does not create Format 2 sessions as {1}".format(path, name),
    )
    _require(
        "--kind note" in text,
        "{0} does not expose the neutral note operation".format(path),
    )
    _require(
        "python3 -m bridge check --peer" in text
        and "python3 -m bridge run --session" in text,
        "{0} lacks readiness or courier commands".format(path),
    )
    _require(
        "Bridge-Format: 2" in text,
        "{0} does not require Format 2 when reusing a session".format(path),
    )
    _require(
        "Never hide, rewrite, or turn a Bridge failure into success" in text,
        "{0} does not preserve Bridge failures".format(path),
    )

    for removed in REMOVED_ADAPTER_TEXT:
        _require(removed not in text, "{0} retains {1!r}".format(path, removed))
    for word in REMOVED_ADAPTER_WORDS:
        _require(
            re.search(r"\b{0}\b".format(re.escape(word)), lowered) is None,
            "{0} retains the removed {1} surface".format(path, word),
        )

    commands = _bridge_command_lines(text)
    _require(commands, "{0} has no shared Bridge invocation".format(path))
    for command in commands:
        _require(
            command.startswith("python3 -m bridge "),
            "{0} invokes an alternate Python module".format(path),
        )
        operation = command[len("python3 -m bridge ") :].split(None, 1)[0]
        _require(
            operation in {"check", "record", "run"},
            "{0} uses unsupported operation {1}".format(path, operation),
        )
        for option in (
            "--local",
            "--workflow",
            "--review-base",
            "--review-head",
        ):
            _require(
                option not in command,
                "{0} uses removed option {1}".format(path, option),
            )
    kinds = set(re.findall(r"--kind\s+([a-z-]+)", "\n".join(commands)))
    _require(
        kinds == {"session-create", "note"},
        "{0} uses record kinds {1}".format(path, sorted(kinds)),
    )


def inspect() -> None:
    """Prove the public surface and package sources stay irreducible."""
    _require(BRIDGE_FORMAT == 2, "the runtime does not declare Format 2")
    _require(
        connectors.HARNESS_IDS == TARGETS,
        "the target tuple is not exactly {0}".format(", ".join(TARGETS)),
    )
    _require(
        _literal_switches() == dict((name, name) for name in TARGETS),
        "the connector resolver is not one literal branch per fixed target",
    )

    bridge_dir = os.path.join(REPO_ROOT, "bridge")
    modules = {name for name in os.listdir(bridge_dir) if name.endswith(".py")}
    _require(
        modules == PRODUCTION_MODULES,
        "the production module set changed: {0}".format(
            ", ".join(sorted(modules ^ PRODUCTION_MODULES))
        ),
    )

    options = _command_options(cli.build_parser())
    _require(
        options
        == {
            "check": {"--peer"},
            "run": {"--session", "--timeout"},
            "record": {"--session", "--kind", "--initiator", "--peer", "--project"},
        },
        "the public command or option surface differs from Format 2",
    )

    package_root = os.path.join(REPO_ROOT, "packages")
    package_names = {
        name
        for name in os.listdir(package_root)
        if os.path.isdir(os.path.join(package_root, name))
    }
    _require(
        package_names == set(ADAPTERS),
        "the adapter set is not exactly {0}".format(", ".join(ADAPTERS)),
    )
    for name in ADAPTERS:
        _inspect_adapter(name)


class _FakeConnector:
    COURIER_ONLY = False

    @staticmethod
    def check(deadline, cwd):
        return "test target is ready without a model call"

    @staticmethod
    def build_command(deadline, cwd):
        return PeerCommand(
            argv=(sys.executable, FAKE_PEER, "plain"),
            cwd=cwd,
            env=tuple(os.environ.items()),
        )


class _FakeCourierConnector(_FakeConnector):
    COURIER_ONLY = True


def _fake_resolve(peer: str):
    _require(peer in TARGETS, "the adapter named an unknown target")
    return _FakeCourierConnector if peer == "hermes" else _FakeConnector


def _invoke(argv: Sequence[str], body: str) -> Tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with mock.patch.object(connectors, "resolve", _fake_resolve):
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with mock.patch("sys.stdin", io.StringIO(body)):
                status = cli.main(argv)
    return status, stdout.getvalue(), stderr.getvalue()


def _success(argv: Sequence[str], body: str) -> str:
    status, stdout, stderr = _invoke(argv, body)
    _require(
        status == 0,
        "{0} failed: {1}".format(" ".join(argv), stderr.strip()),
    )
    _require(not stderr, "a successful command wrote an error: {0}".format(stderr))
    return stdout.strip()


def _exercise_caller(
    root: str, initiator: str, target: str, with_project: bool
) -> None:
    session_dir = os.path.join(root, "session-{0}".format(initiator))
    project = os.path.join(root, "project-{0}".format(initiator))
    os.mkdir(project)

    ready = _success(["check", "--peer", target], "")
    _require("ready without a model call" in ready, "readiness output was lost")

    create = [
        "record",
        "--session",
        session_dir,
        "--kind",
        "session-create",
        "--initiator",
        initiator,
        "--peer",
        target,
    ]
    if with_project:
        create.extend(["--project", project])
    session_path = _success(create, "Courier session for {0}.\n".format(initiator))
    _require(session_path == session.session_file(session_dir), "session path changed")

    request = "Distinctive message from {0} to {1}.\n".format(initiator, target)
    response_path = _success(
        ["run", "--session", session_dir, "--timeout", "30"], request
    )
    response = _read(response_path)
    _require(response.endswith(request), "the response body was not preserved")
    _require(
        "From: {0}\nTo: {1}\n".format(target, initiator) in response,
        "the response envelope does not match the immutable session",
    )

    note_path = _success(
        ["record", "--session", session_dir, "--kind", "note"],
        "Neutral note from {0}.\n".format(initiator),
    )
    _require(
        os.path.basename(note_path) == "0003-initiator-record.md",
        "the neutral note did not follow the request and response",
    )
    record = session.read_session(session_dir)
    _require(record.initiator == initiator, "the initiator changed")
    _require(record.peer == target, "the target changed")
    _require(
        record.project == (project if with_project else None),
        "the project changed",
    )


def adapters() -> None:
    """Exercise every harness label and one open application label."""
    inspect()
    target_for = {
        "codex": "claude",
        "claude": "zcode",
        "zcode": "hermes",
        "hermes": "codex",
    }
    with tempfile.TemporaryDirectory(prefix="agent-bridge-adapters-") as root:
        for initiator in ADAPTERS:
            target = target_for[initiator]
            _exercise_caller(
                root,
                initiator,
                target,
                with_project=target != "hermes",
            )
        _exercise_caller(
            root,
            "saffron.tools-7",
            "codex",
            with_project=True,
        )

        class FailingConnector:
            @staticmethod
            def check(deadline, cwd):
                raise BridgeError(
                    Failure.RESTRICTIONS_UNAVAILABLE,
                    detail="focused adapter failure",
                )

        def failing_resolve(peer):
            return FailingConnector

        stderr = io.StringIO()
        stdout = io.StringIO()
        with mock.patch.object(connectors, "resolve", failing_resolve):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                status = cli.main(["check", "--peer", "codex"])
        _require(status == 1, "a Bridge failure became success")
        _require(not stdout.getvalue(), "a Bridge failure printed success output")
        _require(
            "focused adapter failure" in stderr.getvalue()
            and "Next action:" in stderr.getvalue(),
            "the complete Bridge failure was not surfaced",
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m tests.release_conformance",
        description="Run one focused Agent Bridge release check.",
    )
    parser.add_argument("command", choices=("inspect", "adapters"))
    return parser


def main(argv: Sequence[str] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "inspect":
            inspect()
        else:
            adapters()
    except ConformanceError as exc:
        sys.stderr.write("{0}: FAIL - {1}\n".format(args.command, exc))
        return 1
    sys.stdout.write("{0}: PASS\n".format(args.command))
    return 0


if __name__ == "__main__":
    sys.exit(main())
