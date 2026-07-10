"""Regression tests for title_bar.update_port with a read-only cfg.

Repro: load a config (e.g. using COM4), then switch ports in the UI.
``update_port`` deep-copied ``app.cfg``, which is a ``MappingProxyType``
view owned by ReplEngine -- and ``copy.deepcopy`` can't pickle a
mappingproxy ("cannot pickle 'mappingproxy' object").
"""

from __future__ import annotations

from types import MappingProxyType

from termapy.title_bar import update_port


class _FakeApp:
    """Minimal stand-in exposing exactly what update_port touches.

    Mirrors the real app: ``cfg`` is a read-only ``MappingProxyType`` view
    (the shape ReplEngine exposes), not a plain dict.
    """

    def __init__(self, cfg: dict) -> None:
        self.cfg = MappingProxyType(cfg)
        self.config_path = "proj/proj.cfg"
        self.is_connected = False
        self.switched: tuple | None = None
        self.statuses: list[tuple] = []

    def _switch_config(self, cfg, path) -> None:
        self.switched = (cfg, path)

    def _status(self, msg: str, color: str | None = None) -> None:
        self.statuses.append((msg, color))


def test_update_port_handles_readonly_cfg():
    # Arrange -- a read-only proxied cfg, as after loading a config file
    original = {"serial": {"port": "COM4", "baud_rate": 115200}, "title": "Mag3"}
    app = _FakeApp(original)

    # Act -- switch COM4 -> COM3 (raised TypeError on the mappingproxy before the fix)
    update_port(app, "COM3")

    # Assert -- the switched cfg carries the new port
    assert app.switched is not None, "update_port switched to a new cfg"
    new_cfg, path = app.switched
    assert new_cfg["serial"]["port"] == "COM3", "new port applied to the switched cfg"
    assert path == "proj/proj.cfg", "config_path preserved"


def test_update_port_does_not_mutate_the_source_cfg():
    # Arrange
    original = {"serial": {"port": "COM4"}, "title": "Mag3"}
    app = _FakeApp(original)

    # Act
    update_port(app, "COM3")

    # Assert -- the read-only source's nested dict is untouched (it's a deep copy)
    assert original["serial"]["port"] == "COM4", "source cfg's nested dict not mutated"
    new_cfg = app.switched[0]
    new_cfg["serial"]["port"] = "COMX"  # the copy is independently mutable
    assert original["serial"]["port"] == "COM4", "copy stays independent of the source"
