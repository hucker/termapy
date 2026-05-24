"""Textual command-provider that surfaces termapy's PALETTE_CMDS.

The provider feeds Textual's built-in CommandPalette.  Two methods
matter:

- ``discover()`` -- yields the default suggestions shown when the
  palette opens with an empty query.  We yield every PALETTE_CMDS
  entry alphabetically so the cold-open list is browsable.
- ``search(query)`` -- yields fuzzy-matched results as the user types.
"""

from __future__ import annotations

from textual.command import DiscoveryHit, Hit, Hits, Provider


class PaletteProvider(Provider):
    """Surface SerialTerminal.PALETTE_CMDS via Textual's CommandPalette."""

    def _palette_cmds(self) -> list[tuple[str, str]]:
        """Read ``app.PALETTE_CMDS`` and sort alphabetically by label.

        Source order in PALETTE_CMDS is thematic (grouped by phase of
        use), but the palette renders flat without visible separators,
        so the thematic grouping doesn't read as structure.  Sort here
        so the rendered list is browsable; "I know roughly what I
        want" is handled by the fuzzy ``search`` path.
        """
        cmds = list(getattr(self.app, "PALETTE_CMDS", []))
        return sorted(cmds, key=lambda lh: lh[0].lower())

    async def discover(self) -> Hits:
        """Default suggestions when the palette opens with an empty query."""
        for label, handler_name in self._palette_cmds():
            yield DiscoveryHit(
                label,
                self._invoke_factory(handler_name),
            )

    async def search(self, query: str) -> Hits:
        """Yield Hits matching ``query`` against the curated palette list."""
        matcher = self.matcher(query)
        for label, handler_name in self._palette_cmds():
            score = matcher.match(label)
            if score:
                yield Hit(
                    score,
                    matcher.highlight(label),
                    self._invoke_factory(handler_name),
                )

    def _invoke_factory(self, handler_name: str):
        """Build a no-arg callable that invokes the named handler on the app."""

        def _invoke() -> None:
            handler = getattr(self.app, handler_name, None)
            if callable(handler):
                handler()

        return _invoke
