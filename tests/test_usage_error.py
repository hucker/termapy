"""Dispatch-level tests for the UsageError mechanism.

Every ``Usage:`` line the dispatcher emits is rendered by ONE owner
(``format_usage``) from the registered declaration -- the command's dotted
name, its ``args=`` string (or synthesized params synopsis), and the ACTIVE
REPL prefix.  Handlers raise ``UsageError`` instead of hand-writing usage
strings, so the error, ``/help``, and a re-configured prefix can never
disagree.
"""

import json

import pytest

from termapy.plugins import Command, UsageError, format_usage
from termapy.plugins.params import ParamSpec
from termapy.repl import ReplEngine


@pytest.fixture
def engine(tmp_path):
    """Basic ReplEngine with a temp config (builtins loaded)."""
    cfg = {"port": "COM4", "baud_rate": 115200, "eol": "\r"}
    config_path = tmp_path / "sub" / "test.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    for sub in ("plugin", "ss", "run"):
        (config_path.parent / sub).mkdir(exist_ok=True)
    output = []
    eng = ReplEngine(cfg, str(config_path), lambda t, c=None: output.append((t, c)))
    flags = eng.ctx.ns("flags")
    flags["echo"] = True
    flags["output_level"] = "verbose"
    flags["hex"] = False
    # cfg is the SAME object as eng._cfg_data, so tests can simulate a
    # runtime prefix change by mutating it (the engine prefix property
    # derives live from this dict).
    return eng, output, cfg


# -- format_usage (pure unit) ------------------------------------------------


class TestFormatUsage:
    def test_basic_line(self):
        # Arrange
        cmd = Command(name="edit", args="<filename>", help="h")

        # Act
        actual = format_usage("/", "edit", cmd)

        # Assert
        expected = "Usage: /edit <filename>"
        assert actual == expected, "synopsis comes from the declaration"

    def test_detail_prepended_on_own_line(self):
        # Arrange
        cmd = Command(name="x", args="{N}", help="h")

        # Act
        actual = format_usage("/", "x", cmd, detail="Invalid count: 'z'")

        # Assert
        expected = "Invalid count: 'z'\nUsage: /x {N}"
        assert actual == expected, "detail line precedes the usage line"

    def test_empty_args_has_no_trailing_space(self):
        # Arrange
        cmd = Command(name="cls", help="h")

        # Act
        actual = format_usage("/", "cls", cmd)

        # Assert
        assert actual == "Usage: /cls", "empty synopsis is rstripped"

    def test_params_command_synthesizes_synopsis(self):
        # Arrange -- params command: args= is forbidden, synopsis synthesized
        cmd = Command(name="p", help="h", params=[ParamSpec("count")])

        # Act
        actual = format_usage("/", "p", cmd)

        # Assert
        assert "count" in actual, "synopsis synthesized from ParamSpec list"

    def test_custom_prefix_rendered(self):
        # Arrange
        cmd = Command(name="edit", args="<filename>", help="h")

        # Act
        actual = format_usage(">", "edit", cmd)

        # Assert
        assert actual == "Usage: >edit <filename>", "prefix is caller-supplied"


# -- dispatcher rendering ------------------------------------------------------


def _raiser(detail: str = ""):
    def _handler(ctx, args):
        raise UsageError(detail)

    return _handler


class TestDispatchRendersUsage:
    def test_bare_raise_renders_declaration(self, engine):
        # Arrange
        eng, output, cfg = engine
        eng.register_hook("utest", "<thing>", "help", _raiser())

        # Act
        result = eng.dispatch("utest")

        # Assert
        assert result.success is False, "UsageError fails the dispatch"
        assert result.error == "Usage: /utest <thing>", (
            "usage line rendered from the registered args= declaration"
        )

    def test_detail_shown_above_usage_line(self, engine):
        # Arrange
        eng, output, cfg = engine
        eng.register_hook("utest", "<thing>", "help", _raiser("Bad value: 'x'"))

        # Act
        result = eng.dispatch("utest nonsense")

        # Assert
        expected = "Bad value: 'x'\nUsage: /utest <thing>"
        assert result.error == expected, "detail precedes the usage line"

    def test_dotted_subcommand_full_name(self, engine):
        # Arrange
        eng, output, cfg = engine
        eng.register_hook("utest.sub", "<n>", "help", _raiser())

        # Act
        result = eng.dispatch("utest.sub")

        # Assert
        assert result.error == "Usage: /utest.sub <n>", (
            "dotted name renders in full"
        )

    def test_custom_prefix_in_usage_line(self, engine):
        # Arrange -- engine prefix derives LIVE from the backing cfg dict
        eng, output, cfg = engine
        cfg["cmd_prefix"] = ">"
        eng.register_hook("utest", "<thing>", "help", _raiser())

        # Act
        result = eng.dispatch("utest")

        # Assert
        assert result.error == "Usage: >utest <thing>", (
            "usage line honors the configured prefix, not a hardcoded /"
        )

    def test_migrated_builtin_env_set(self, engine):
        # Arrange
        eng, output, cfg = engine

        # Act -- /env.set with missing value is the migrated arity path
        result = eng.dispatch("env.set ONLYNAME")

        # Assert
        assert result.error == "Usage: /env.set <name> <value>", (
            "migrated builtin renders its declaration"
        )

    def test_params_error_honors_prefix(self, engine):
        # Arrange -- /repeat is a params command; parse errors are rendered
        # by the same single owner, so the prefix must follow cfg too
        eng, output, cfg = engine
        cfg["cmd_prefix"] = ">"

        # Act
        result = eng.dispatch("repeat count=notanint cmd=/print hi")

        # Assert
        assert ">repeat" in result.error, "param-error line uses the prefix"
        assert "Usage: >repeat" in result.error, "usage line uses the prefix"
        assert "/repeat" not in result.error, "no hardcoded / remains"
