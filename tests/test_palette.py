"""Tests for the command-palette additions that surface /find, /grep, /search.

The palette itself (the modal that renders the picker) is a TUI
concern and not easy to unit-test.  These tests verify the
structural contract instead: the new entries exist with the
expected handler names, the handlers exist with the expected
signatures, and the shared _prompt_then_dispatch helper composes
the right REPL line from user input.
"""

from __future__ import annotations

from types import SimpleNamespace

from termapy.app import SerialTerminal


_EXPECTED_PALETTE_ADDITIONS = [
    ("Find in scrollback...", "_palette_find"),
    ("Search scrollback (Grep)...", "_palette_grep"),
    ("Search command help...", "_palette_search_help"),
]


class TestPaletteEntries:
    """The palette structure exposes the three new search actions."""

    def test_palette_includes_find_grep_search(self):
        # Arrange / Act -- PALETTE_CMDS is a class attribute, no
        # instantiation needed.
        entries = SerialTerminal.PALETTE_CMDS

        # Assert -- each expected (label, handler) pair is present.
        for label, handler in _EXPECTED_PALETTE_ADDITIONS:
            assert (label, handler) in entries, (
                f"palette missing entry ({label!r}, {handler!r})"
            )

    def test_palette_handler_methods_exist(self):
        # Arrange / Act / Assert -- each handler name in the new
        # palette entries resolves to a callable on the class.
        for _label, handler in _EXPECTED_PALETTE_ADDITIONS:
            assert callable(getattr(SerialTerminal, handler, None)), (
                f"SerialTerminal.{handler} is missing or not callable"
            )

    def test_palette_search_entries_ordered_near_help(self):
        # Arrange / Act -- search entries should appear early in
        # the palette so they're easy to find while browsing.
        # Specifically: right after "Help".
        entries = SerialTerminal.PALETTE_CMDS
        labels = [label for label, _ in entries]

        # Assert
        help_idx = labels.index("Help")
        for offset, (label, _) in enumerate(
            _EXPECTED_PALETTE_ADDITIONS, start=1,
        ):
            assert labels[help_idx + offset] == label, (
                f"palette entry at slot {help_idx + offset} should be "
                f"{label!r}, got {labels[help_idx + offset]!r}"
            )

    def test_palette_includes_load_run_script(self):
        # Arrange / Act -- "Load Run Script..." reuses the existing
        # _btn_scripts handler (same as the Run title-bar button /
        # F3), so users can reach the ScriptPicker from the palette
        # alongside the Cfg ops.
        entries = SerialTerminal.PALETTE_CMDS

        # Assert -- entry is present and resolves to a callable.
        assert ("Load Run Script...", "_btn_scripts") in entries, (
            "palette missing entry ('Load Run Script...', '_btn_scripts')"
        )
        assert callable(getattr(SerialTerminal, "_btn_scripts", None)), (
            "SerialTerminal._btn_scripts is missing or not callable"
        )


class TestPromptThenDispatch:
    """The shared helper composes ``<prefix><value>`` and dispatches it quietly."""

    def test_non_empty_value_dispatches_prefixed_command(self, monkeypatch):
        # Arrange -- a stub App that records what _dispatch_quiet
        # sees, and a stub push_screen that fires the callback
        # synchronously with a fake user input.
        dispatched: list[str] = []
        captured_callback: list = []

        def fake_push_screen(screen, callback=None):
            captured_callback.append(callback)

        # Bind the real method to a SimpleNamespace + manually
        # wired stubs.  Avoids spinning up Textual.
        app = SimpleNamespace(
            _dispatch_quiet=dispatched.append,
            push_screen=fake_push_screen,
        )

        # Act -- call the real method bound to our stub.
        SerialTerminal._prompt_then_dispatch(
            app, "Test prompt:", "/foo ",  # type: ignore[arg-type]
        )
        # Simulate the modal returning the user's input.
        captured_callback[0]("bar baz")

        # Assert
        assert dispatched == ["/foo bar baz"], (
            f"non-empty value -> '<prefix><value>' dispatch; got {dispatched!r}"
        )

    def test_empty_or_none_value_does_not_dispatch(self):
        # Arrange
        dispatched: list[str] = []
        captured_callback: list = []

        def fake_push_screen(screen, callback=None):
            captured_callback.append(callback)

        app = SimpleNamespace(
            _dispatch_quiet=dispatched.append,
            push_screen=fake_push_screen,
        )

        # Act -- the user cancelled (None) and the user submitted
        # empty (""); both should be silent.
        SerialTerminal._prompt_then_dispatch(
            app, "Test:", "/foo ",  # type: ignore[arg-type]
        )
        captured_callback[0](None)

        SerialTerminal._prompt_then_dispatch(
            app, "Test:", "/foo ",  # type: ignore[arg-type]
        )
        captured_callback[1]("")

        # Assert
        assert dispatched == [], (
            f"empty / None value -> no dispatch; got {dispatched!r}"
        )


