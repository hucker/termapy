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

    def test_palette_includes_list_and_toggle_actions(self):
        # Arrange / Act -- the four "useful read/set/toggle" entries
        # dispatch /port.list, /var, /var.set, /term.line_no (Set
        # Variable opens a two-field modal first) so the palette gives
        # one-tap access without typing.
        entries = SerialTerminal.PALETTE_CMDS
        expected = [
            ("List Ports", "_palette_list_ports"),
            ("List Variables", "_palette_list_vars"),
            ("Set Variable...", "_palette_set_var"),
            ("Toggle Line Numbers", "_palette_toggle_line_numbers"),
            ("Toggle Echo", "_palette_toggle_echo"),
            ("Toggle Timestamps", "_palette_toggle_timestamps"),
            ("Toggle Hex Display", "_palette_toggle_hex"),
            ("Toggle Line Endings", "_palette_toggle_line_endings"),
            ("Show Terminal Settings", "_palette_term_info"),
        ]

        # Assert -- each (label, handler) tuple is present and the
        # handler resolves to a callable on the class.
        for label, handler in expected:
            assert (label, handler) in entries, (
                f"palette missing entry ({label!r}, {handler!r})"
            )
            assert callable(getattr(SerialTerminal, handler, None)), (
                f"SerialTerminal.{handler} is missing or not callable"
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

        # Act -- the user canceled (None) and the user submitted
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


class TestPaletteSetVar:
    """``_palette_set_var`` accepts both ``NAME`` and ``$(NAME)`` forms.

    The Name input must produce the same dispatch whether the user
    types the bare identifier or the ``$(NAME)`` reference form, so
    pasting a variable reference from elsewhere "just works."  The
    Value field is left alone (``$(OTHER)`` in the value is the
    documented way to reference another variable).
    """

    def _make_app_with_captured_callback(self):
        dispatched: list[str] = []
        captured_callback: list = []

        def fake_push_screen(screen, callback=None):
            captured_callback.append(callback)

        app = SimpleNamespace(
            _dispatch_quiet=dispatched.append,
            push_screen=fake_push_screen,
        )
        return app, dispatched, captured_callback

    def test_bare_name_and_dollar_paren_name_produce_same_dispatch(self):
        # Arrange
        app, dispatched, captured_callback = (
            self._make_app_with_captured_callback()
        )

        # Act -- call the handler twice; simulate user submitting
        # the bare form once and the $(...) form once.
        SerialTerminal._palette_set_var(app)  # type: ignore[arg-type]
        captured_callback[0](("TIMEOUT", "5000"))
        SerialTerminal._palette_set_var(app)  # type: ignore[arg-type]
        captured_callback[1](("$(TIMEOUT)", "5000"))

        # Assert -- both dispatches are identical.
        actual = dispatched
        expected = ["/var.set TIMEOUT 5000", "/var.set TIMEOUT 5000"]
        assert actual == expected, (
            f"bare and $(...) Name forms must produce identical dispatch; "
            f"got {actual!r}"
        )

    def test_value_dollar_paren_is_preserved(self):
        # Arrange -- the value field is the documented place to put
        # a variable reference; we must NOT strip $(...) there.
        app, dispatched, captured_callback = (
            self._make_app_with_captured_callback()
        )

        # Act
        SerialTerminal._palette_set_var(app)  # type: ignore[arg-type]
        captured_callback[0](("FOO", "$(BAR)"))

        # Assert
        actual = dispatched
        expected = ["/var.set FOO $(BAR)"]
        assert actual == expected, (
            f"$(...) in Value must reach the REPL untouched; "
            f"got {actual!r}"
        )

    def test_cancel_does_not_dispatch(self):
        # Arrange
        app, dispatched, captured_callback = (
            self._make_app_with_captured_callback()
        )

        # Act -- user canceled (callback fired with None).
        SerialTerminal._palette_set_var(app)  # type: ignore[arg-type]
        captured_callback[0](None)

        # Assert
        actual = dispatched
        expected: list[str] = []
        assert actual == expected, (
            f"Cancel must NOT dispatch; got {actual!r}"
        )


class TestPaletteProvider:
    """``PaletteProvider`` surfaces PALETTE_CMDS to Textual's CommandPalette.

    Three pins: discover yields every PALETTE_CMDS entry alphabetically,
    search fuzzy-filters them, and invoking a hit calls the named
    handler on the app.
    """

    def _make_provider(self, palette_cmds):
        # PaletteProvider's __init__ wants a Screen + Rich Style.  We
        # don't want to spin up a Screen here, so stub the attributes
        # that Provider's helpers reach for (the `app` property and
        # the `_Provider__match_style` name-mangled attr used by
        # ``self.matcher``).
        from rich.style import Style

        from termapy.palette_provider import PaletteProvider

        provider = object.__new__(PaletteProvider)
        # Provider.matcher reads `self.__match_style` via name-mangling
        # (-> `_Provider__match_style`).  A bare Rich Style is enough
        # for the fuzzy matcher's internal needs.
        provider._Provider__match_style = Style.null()
        # `self.app` lookup -- stub with a SimpleNamespace carrying
        # just the PALETTE_CMDS the provider needs.
        from types import SimpleNamespace
        provider._test_app = SimpleNamespace(PALETTE_CMDS=palette_cmds)
        type(provider).app = property(lambda self: self._test_app)
        return provider

    def test_discover_yields_all_palette_cmds_alphabetically(self):
        # Arrange -- input order is intentionally non-alphabetical
        # so we can assert the provider re-sorts.
        import asyncio
        cmds = [("Zeta", "_palette_z"), ("Alpha", "_palette_a")]
        provider = self._make_provider(cmds)

        # Act -- collect everything discover() yields.
        async def _collect():
            return [hit async for hit in provider.discover()]

        hits = asyncio.run(_collect())

        # Assert -- one hit per entry, in alphabetical order.
        labels = [str(h.display) for h in hits]
        actual = labels
        expected = ["Alpha", "Zeta"]
        assert actual == expected, (
            f"discover yields PALETTE_CMDS in alphabetical order; "
            f"got {actual!r}"
        )

    def test_search_fuzzy_filters(self):
        # Arrange
        import asyncio
        cmds = [
            ("Find in scrollback...", "_palette_find"),
            ("Save SVG Screenshot", "_palette_ss_svg"),
            ("Exit", "_palette_exit"),
        ]
        provider = self._make_provider(cmds)

        # Act
        async def _collect():
            return [hit async for hit in provider.search("find")]

        hits = asyncio.run(_collect())

        # Assert -- the Find entry matches, the others don't
        labels = [str(h.match_display) for h in hits]
        assert any("Find" in lbl for lbl in labels), (
            f"fuzzy 'find' matches the Find entry; got {labels!r}"
        )
        assert not any("Exit" in lbl for lbl in labels), (
            f"fuzzy 'find' does NOT match Exit; got {labels!r}"
        )

    def test_invoking_a_hit_calls_the_named_handler(self):
        # Arrange
        import asyncio
        cmds = [("Run thing", "_call_a")]
        provider = self._make_provider(cmds)
        called: list[str] = []
        provider._test_app._call_a = lambda: called.append("a")

        # Act -- search, then invoke the returned hit's callback.
        async def _flow():
            hits = [hit async for hit in provider.search("run")]
            assert len(hits) == 1, (
                f"search returned exactly one hit; got {len(hits)}"
            )
            hits[0].command()

        asyncio.run(_flow())

        # Assert -- the handler ran exactly once.
        actual = called
        expected = ["a"]
        assert actual == expected, (
            f"invoking the hit calls the named handler on app; "
            f"got {actual!r}"
        )


