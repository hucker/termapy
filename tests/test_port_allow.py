"""TERMAPY_ALLOWED_PORTS: which ports termapy may open and list, however a port is named.

The list is one regex, matched against the whole of a port's canonical name
or its serial number.  Unset, a person's session allows every port and an MCP
server none; the simulator and ``loop://`` are always allowed; a network
port opens only when named exactly (decision 2026-11-11).  The pure pieces
take their policy and port list as arguments, so no test here touches the
process's own environment; the tests that need the variable read at a real
start run a fresh interpreter.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from termapy import env_flags, port_control
from termapy.capture import CaptureEngine
from termapy.config import open_serial
from termapy.defaults import DEFAULT_CFG
from termapy.port_allow import (
    VARIABLE,
    PortAllowlist,
    PortNotAllowedError,
    PortPolicy,
    named_endpoints,
    not_allowed_message,
)
from termapy.port_control import (
    ChipFacts,
    check_open,
    port_identity,
    ports_title,
    resolve_port_for_open,
)
from termapy.serial_engine import SerialEngine
from termapy.usb_tree import UsbRecord, gather_usb_tree

# COM3 (A1B2C3D4), COM4 (0001) and COM7 (020026702RYN040952).
FLEET = port_control._build_demo_fleet()

# A name no machine has: a broken check fails safe, as "not found".
ABSENT = "COM254"


def _fleet() -> list[ChipFacts]:
    """The demo fleet, as a ``source=`` callable hands it over."""
    return list(FLEET)


def _listed(text: str, *, under_mcp: bool = False, network_egress: bool = True) -> PortPolicy:
    """A policy with ``text`` as the operator's list."""
    return PortPolicy(
        allowlist=PortAllowlist.parse(text), under_mcp=under_mcp, network_egress=network_egress
    )


MCP_UNSET = PortPolicy(under_mcp=True, network_egress=False)


def _cfg(port: str) -> dict:
    """A config whose port is ``port``."""
    return {**DEFAULT_CFG, "serial": {**DEFAULT_CFG["serial"], "port": port}}


def _refusal(spec: str, policy: PortPolicy) -> str:
    """The denial ``resolve_port_for_open`` gives ``spec`` against the demo fleet."""
    with pytest.raises(PortNotAllowedError) as caught:
        resolve_port_for_open(spec, source=_fleet, policy=policy)
    return str(caught.value)


@pytest.mark.rule("port-policy-at-opener")
class TestTheList:
    """One regex, matched against the whole name, ignoring case."""

    def test_a_name_must_match_whole(self):
        """COM1 allows COM1 and never COM10 or USB-COM1."""
        allowlist = PortAllowlist.parse("COM1")
        actual = [allowlist.matches(name) for name in ("COM1", "COM10", "USB-COM1")]
        assert actual == [True, False, False], "fullmatch, never a search"

    def test_case_is_ignored(self):
        """A name or serial number matches in any case."""
        allowlist = PortAllowlist.parse("com3|a1b2c3d4")
        assert allowlist.matches("COM3"), "names ignore case"
        assert allowlist.matches("A1B2C3D4"), "serial numbers ignore case"

    def test_each_alternative_allows(self):
        """Every |-alternative allows on its own, and a pattern allows what it matches."""
        allowlist = PortAllowlist.parse(r"COM2|COM\d{2}|/dev/ttyACM[0-9]")
        actual = [allowlist.matches(name) for name in ("COM2", "COM15", "/dev/ttyACM0", "COM3")]
        assert actual == [True, True, True, False]

    def test_case_folding_is_ascii(self):
        """A non-ASCII letter that folds to an ASCII one does not match it: \u017f (long s) is not s."""
        allowlist = PortAllowlist.parse("COMS")
        assert not allowlist.matches("COM\u017f"), "re.ASCII keeps case folding to ASCII"

    def test_nothing_and_blank_match_nothing(self):
        """A port with no serial number cannot match on one, even under .*."""
        allowlist = PortAllowlist.parse(".*")
        assert not allowlist.matches(None), "no identity matches nothing"
        assert not allowlist.matches(""), "a blank identity matches nothing"


@pytest.mark.rule("port-policy-at-opener")
class TestReadingTheVariable:
    """Read once, at import: unset or blank is no list, a bad regex is a startup error that allows nothing."""

    @pytest.mark.parametrize("environ", [{}, {VARIABLE: ""}, {VARIABLE: "   "}])
    def test_unset_and_blank_are_no_list(self, environ):
        """With no value there is no list and nothing to report."""
        errors: list[str] = []
        actual = env_flags._read_port_allowlist(VARIABLE, environ=environ, errors=errors)
        assert actual is None, "no list"
        assert errors == [], "absence is not an error"

    def test_a_value_is_read_without_its_spaces(self):
        """The value is the list, surrounding spaces dropped."""
        errors: list[str] = []
        actual = env_flags._read_port_allowlist(
            VARIABLE, environ={VARIABLE: "  COM3|COM4  "}, errors=errors
        )
        assert actual is not None, "a value is a list"
        assert actual.text == "COM3|COM4"
        assert errors == []

    def test_a_bad_regex_is_recorded_and_allows_nothing(self):
        """A value that does not compile is a startup error naming the variable, and fails closed."""
        # Arrange
        errors: list[str] = []

        # Act
        actual = env_flags._read_port_allowlist(VARIABLE, environ={VARIABLE: "COM("}, errors=errors)

        # Assert
        assert len(errors) == 1, "one error for the variable"
        assert errors[0].startswith("Invalid TERMAPY_ALLOWED_PORTS: COM( ("), errors[0]
        assert actual is not None and not actual.matches("COM"), (
            "code that skips the startup check still sees a list that allows nothing"
        )

    @pytest.mark.parametrize("value", ["COM7 | COM5", "COM7| COM5", "COM7\tCOM5"])
    def test_a_space_inside_is_a_startup_error(self, value):
        """A space or tab inside the value is a separator typo, refused at startup rather than matching nothing."""
        errors: list[str] = []
        env_flags._read_port_allowlist(VARIABLE, environ={VARIABLE: value}, errors=errors)
        assert errors == [
            f"Invalid TERMAPY_ALLOWED_PORTS: {value} (no spaces; separate ports with |)"
        ]

    @pytest.mark.slow  # fresh interpreter: the variable is read at import
    def test_a_bad_regex_stops_a_real_process(self, tmp_path):
        """In a fresh interpreter a bad TERMAPY_ALLOWED_PORTS makes termapy exit 1 with the message, no traceback."""
        # Arrange
        env = {**os.environ, VARIABLE: "COM("}

        # Act
        result = subprocess.run(
            [sys.executable, "-m", "termapy", "--ports"],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=tmp_path,
        )

        # Assert
        assert result.returncode == 1, result.stderr
        assert "termapy: Invalid TERMAPY_ALLOWED_PORTS: COM( (" in result.stderr
        assert "Traceback" not in result.stderr, "a message, never a stack trace"


@pytest.mark.rule("port-policy-at-opener")
class TestWhoIsRefused:
    """A person with no list allows every port; MCP with no list allows none but the simulator and loop://."""

    def test_a_person_with_no_list_opens_anything(self):
        """No policy in force: a port opens as resolved, whatever it is."""
        policy = PortPolicy()
        actual = [
            check_open(port, [], policy=policy) for port in ("COM9", "hwgrep://x", "socket://h:1")
        ]
        assert actual == ["COM9", "hwgrep://x", "socket://h:1"], "nothing is refused"
        assert ports_title(policy=policy) == "Ports", "and nothing is said"

    @pytest.mark.parametrize("port", ["COM3", ABSENT, "socket://127.0.0.1:9", "spy://COM3"])
    def test_mcp_with_no_list_opens_no_port(self, port):
        """Under MCP an unset list refuses every device and host, naming the variable."""
        with pytest.raises(PortNotAllowedError) as caught:
            check_open(port, FLEET, policy=MCP_UNSET)
        expected = not_allowed_message(port, MCP_UNSET)
        assert str(caught.value) == expected

    @pytest.mark.sw_loopback
    @pytest.mark.parametrize("port", ["DEMO", "demo_json", "DEMO_VT100", "DEMO_FAIL", "loop://"])
    @pytest.mark.parametrize(
        "policy", [MCP_UNSET, PortPolicy(allowlist=PortAllowlist.parse("COM3"))]
    )
    def test_the_simulator_and_loopback_always_open(self, port, policy):
        """A simulator port and loop:// reach no device and no network, so every policy allows them."""
        assert check_open(port, [], policy=policy) == port

    def test_the_denial_texts(self):
        """Each denial says what the operator's list says, so the reader stops retrying."""
        listed = _listed("COM2|COM3")
        mcp_listed = _listed("COM2|COM3", under_mcp=True)
        expected = [
            "Port not allowed: COM4 (TERMAPY_ALLOWED_PORTS=COM2|COM3, set by the operator)",
            "Port not allowed: COM4 (plugged in, but TERMAPY_ALLOWED_PORTS=COM2|COM3 leaves it out; set by the operator)",
            "Port not allowed: 0001 (COM4, plugged in, but TERMAPY_ALLOWED_PORTS=COM2|COM3 leaves it out; set by the operator)",
            "Port not found: COM9 (TERMAPY_ALLOWED_PORTS=COM2|COM3; a port it allows by serial number must be plugged in)",
            "Port not allowed or not found: COM4 (TERMAPY_ALLOWED_PORTS=COM2|COM3, set by the operator)",
            "Port not allowed: COM4 (TERMAPY_ALLOWED_PORTS is not set; under MCP that allows no port)",
            "Port not allowed: hwgrep://x (TERMAPY_ALLOWED_PORTS is not set; under MCP that allows no port)",
            "Port not allowed: hwgrep://x (hwgrep:// picks its port after termapy checks it; with TERMAPY_ALLOWED_PORTS set, name the port)",
            "Port not allowed: cp2110://1 (TERMAPY_ALLOWED_PORTS allows no cp2110:// port)",
        ]
        actual = [
            not_allowed_message("COM4", listed),
            not_allowed_message("COM4", listed, present="COM4"),
            not_allowed_message("0001", listed, present="COM4"),
            not_allowed_message("COM9", listed, absent=True),
            not_allowed_message("COM4", mcp_listed),
            not_allowed_message("COM4", MCP_UNSET),
            not_allowed_message("hwgrep://x", MCP_UNSET, scheme="hwgrep"),
            not_allowed_message("hwgrep://x", listed, scheme="hwgrep"),
            not_allowed_message("cp2110://1", listed, scheme="cp2110"),
        ]
        assert actual == expected

    def test_the_listing_titles(self):
        """A listing says when the policy leaves ports out, and why."""
        expected = [
            "Ports",
            "Ports (limited by TERMAPY_ALLOWED_PORTS)",
            "Ports (TERMAPY_ALLOWED_PORTS is not set; under MCP that allows no port)",
        ]
        actual = [
            ports_title(policy=policy) for policy in (PortPolicy(), _listed("COM3"), MCP_UNSET)
        ]
        assert actual == expected


@pytest.mark.rule("port-policy-at-opener")
class TestIdentity:
    """A port is judged as the port it is, never as the spelling typed."""

    def test_an_allowed_name_lets_its_serial_number_in(self):
        """COM3 allowed: its serial number resolves to COM3 and opens."""
        actual = resolve_port_for_open("A1B2C3D4", source=_fleet, policy=_listed("COM3"))
        assert actual == "COM3"

    def test_an_allowed_serial_number_lets_its_name_in(self):
        """COM7's serial number allowed: COM7 by name opens."""
        actual = resolve_port_for_open("COM7", source=_fleet, policy=_listed("020026702RYN040952"))
        assert actual == "COM7"

    def test_a_refused_port_is_refused_by_name_and_by_serial_number(self):
        """COM3 allowed: COM4 is refused whether named or given by its serial number."""
        policy = _listed("COM3", under_mcp=True)
        expected = [not_allowed_message("COM4", policy), not_allowed_message("0001", policy)]
        actual = [_refusal("COM4", policy), _refusal("0001", policy)]
        assert actual == expected, "under MCP the denial never says COM4 is plugged in"

    def test_a_person_is_told_which_it_is(self):
        """In a person's session a refused port that is plugged in is named as such, and an absent one is not found."""
        policy = _listed("COM3")
        expected = [
            not_allowed_message("0001", policy, present="COM4"),
            not_allowed_message(ABSENT, policy, absent=True),
        ]
        actual = [_refusal("0001", policy), _refusal(ABSENT, policy)]
        assert actual == expected

    def test_a_hidden_port_never_makes_a_serial_number_ambiguous(self):
        """Two devices share serial 0001; with one allowed, 0001 resolves to it, never an ambiguity naming the other."""
        # Arrange
        twins = [*FLEET, ChipFacts(device="COM5", serial="0001")]

        # Act
        actual = resolve_port_for_open("0001", source=lambda: list(twins), policy=_listed("COM4"))

        # Assert
        assert actual == "COM4", "the hidden twin is not a candidate"

    def test_a_fallback_chain_skips_a_hidden_candidate(self):
        """COM4's serial first, COM3 second, only COM3 allowed: the chain lands on COM3."""
        actual = resolve_port_for_open("0001|COM3", source=_fleet, policy=_listed("COM3"))
        assert actual == "COM3"

    @pytest.mark.skipif(os.name != "nt", reason="Windows device names")
    def test_windows_spellings_have_one_identity_or_none(self):
        """The \\\\.\\ prefix and case reduce to the name; a path, drive or stream spelling has no identity."""
        spellings = [
            "\\\\.\\COM5",
            "com5",
            "COM5:",
            "COM2\\..\\COM5",
            "//./COM5",
            "\\\\?\\COM5",
            "COM5.txt",
        ]
        actual = [port_identity(spelling) for spelling in spellings]
        assert actual == ["COM5", "com5", None, None, None, None, None]

    @pytest.mark.skipif(os.name != "nt", reason="Windows device names")
    def test_a_loose_pattern_cannot_be_reached_by_another_spelling(self):
        """COM2.* never lets COM2\\..\\COM5 or COM2: through, though Windows may open COM5 or COM2 from them."""
        policy = _listed("COM2.*")
        for spelling in ("COM2\\..\\COM5", "COM2:", "COM2.txt"):
            with pytest.raises(PortNotAllowedError):
                check_open(spelling, [], policy=policy)

    @pytest.mark.skipif(os.name != "nt", reason="Windows device names")
    def test_an_enumerated_name_opens_in_any_spelling(self):
        """com3 and \\\\.\\COM3 are COM3, and open as the system spells it."""
        policy = _listed("COM3")
        actual = [
            resolve_port_for_open(spelling, source=_fleet, policy=policy)
            for spelling in ("com3", "\\\\.\\COM3")
        ]
        assert actual == ["COM3", "COM3"]

    @pytest.mark.skipif(os.name == "nt", reason="POSIX symlinks")
    def test_a_link_named_to_fit_is_read_as_the_device_it_opens(self, tmp_path):
        """A symlink called allowed-port that points at /dev/null is judged, and would open, as /dev/null."""
        # Arrange
        link = tmp_path / "allowed-port"
        link.symlink_to("/dev/null")
        policy = _listed(".*allowed-port")

        # Act / Assert
        assert port_identity(str(link)) == os.path.realpath("/dev/null"), (
            "the identity is the target"
        )
        with pytest.raises(PortNotAllowedError):
            check_open(str(link), [], policy=policy)

    @pytest.mark.skipif(os.name == "nt", reason="POSIX paths")
    def test_posix_opens_the_path_it_checked(self, tmp_path):
        """An allowed link opens as its real path, so the link cannot be pointed elsewhere between check and open."""
        # Arrange
        link = tmp_path / "board"
        link.symlink_to("/dev/null")
        target = os.path.realpath("/dev/null")

        # Act
        actual = check_open(str(link), [], policy=_listed(target))

        # Assert
        assert actual == target


@pytest.mark.rule("ports-enumerated-once")
class TestListing:
    """Only allowed ports are listed, before anything is looked up."""

    @pytest.mark.parametrize(
        ("allowed", "expected"),
        [
            ("COM3", [("COM3", "A1B2C3D4")]),
            ("020026702RYN040952", [("COM7", "020026702RYN040952")]),
        ],
    )
    def test_the_list_holds_the_allowed_port_either_way(self, allowed, expected):
        """A name keeps its port and serial number; a serial number keeps its port's name."""
        facts = port_control._gather_all_chip_facts(
            fast=True, source=_fleet, policy=_listed(allowed)
        )
        actual = [(fact.device, fact.serial) for fact in facts]
        assert actual == expected

    def test_mcp_with_no_list_lists_nothing(self):
        """Under MCP an unset list lists no port."""
        facts = port_control._gather_all_chip_facts(fast=True, source=_fleet, policy=MCP_UNSET)
        assert facts == []

    def test_a_hidden_port_is_not_found_by_name(self):
        """gather_chip_facts answers a hidden port as it would an unplugged one."""
        policy = _listed("COM3")
        actual = [
            port_control.gather_chip_facts(name, source=_fleet, policy=policy) is not None
            for name in ("COM3", "COM4")
        ]
        assert actual == [True, False]

    def test_the_usb_tree_names_no_hidden_device(self):
        """A USB node with no allowed port in or below it loses its serial number; the allowed port keeps its own."""
        # Arrange
        records = [
            UsbRecord("1"),
            UsbRecord("1-1", description="FTDI", vid_pid="0403:6001", serial="AAA"),
            UsbRecord("1-2", description="FTDI", vid_pid="0403:6001", serial="BBB"),
        ]
        allowed = [ChipFacts(device="COM3", serial="AAA", location="1-1")]

        # Act
        roots = gather_usb_tree(source=lambda: records, facts_list=allowed, policy=_listed("COM3"))

        # Assert
        actual = {node.path: (node.serial, node.port_name) for node in roots[0].walk()}
        assert actual["1-1"] == ("AAA", "COM3"), "the allowed port is tagged and keeps its serial"
        assert actual["1-2"] == (None, None), "the hidden adapter is drawn, unnamed and untagged"


@pytest.mark.rule("network-port-named-exactly")
class TestNetworkPorts:
    """A network port opens only when the list spells its endpoint out; no pattern opens one."""

    @pytest.mark.parametrize(
        "pattern",
        [
            ".*",
            "socket://.*:4000",
            r"socket://h\d:1",
            "(socket://h:1)",
            "socket://h:1+",
            "socket://h",
        ],
    )
    def test_no_pattern_names_a_network_endpoint(self, pattern):
        """Wildcards, groups, classes and a missing port name no endpoint."""
        assert named_endpoints(pattern) == frozenset()

    def test_an_endpoint_named_exactly_is_read_as_pyserial_connects(self):
        """Escaped or plain dots, any host case, an escaped IPv6 host: each names one endpoint."""
        pattern = r"COM3|rfc2217://10.0.0.5:2217|socket://10\.0\.0\.6:4000|socket://Bench.Local:9|socket://\[::1\]:7"
        expected = {
            ("rfc2217", "10.0.0.5", 2217),
            ("socket", "10.0.0.6", 4000),
            ("socket", "bench.local", 9),
            ("socket", "::1", 7),
        }
        assert named_endpoints(pattern) == expected

    def test_a_named_endpoint_opens_with_its_options_and_nothing_else_does(self):
        """The named host opens with any options; another port, host or scheme is refused."""
        # Arrange
        policy = _listed("COM3|socket://10.0.0.6:4000")
        opened = "socket://10.0.0.6:4000?logging=debug"

        # Act
        actual = check_open(opened, [], policy=policy)

        # Assert
        assert actual == opened, "options change how, not where"
        for refused in (
            "socket://10.0.0.6:4001",
            "socket://10.0.0.7:4000",
            "rfc2217://10.0.0.6:4000",
        ):
            with pytest.raises(PortNotAllowedError):
                check_open(refused, [], policy=policy)

    @pytest.mark.parametrize(
        "url",
        [
            "socket://10.0.0.6:4000@evil.example:9",
            "socket://evil.example:9#@10.0.0.6:4000",
            "socket://evil.example:9?@10.0.0.6:4000",
        ],
    )
    def test_a_named_host_cannot_be_borrowed(self, url):
        """Userinfo, a fragment or a query spelling the named host still leaves the host pyserial connects to unnamed."""
        policy = _listed("socket://10.0.0.6:4000")
        with pytest.raises(PortNotAllowedError, match="must be named exactly"):
            check_open(url, [], policy=policy)

    def test_allowing_every_device_allows_no_host(self):
        """.* opens any device and still no network port."""
        policy = _listed(".*")
        assert check_open("COM9", [], policy=policy) == "COM9", "every device"
        with pytest.raises(PortNotAllowedError, match="must be named exactly"):
            check_open("socket://anywhere.example:443", [], policy=policy)

    def test_mcp_needs_the_egress_flag_too(self):
        """Under MCP a named host also needs TERMAPY_MCP_NET_EGRESS, and opens with it."""
        named = "rfc2217://10.0.0.5:2217"
        without = _listed(named, under_mcp=True, network_egress=False)
        with pytest.raises(PortNotAllowedError, match="TERMAPY_MCP_NET_EGRESS"):
            check_open(named, [], policy=without)
        with_flag = _listed(named, under_mcp=True, network_egress=True)
        assert check_open(named, [], policy=with_flag) == named

    def test_hwgrep_and_unknown_schemes_are_refused_under_any_list(self):
        """hwgrep picks its port after the check, and termapy cannot read an unknown scheme, so .* opens neither."""
        policy = _listed(".*")
        for port in ("hwgrep://FTDI", "cp2110://0001:004a:00", "custom://x"):
            with pytest.raises(PortNotAllowedError):
                check_open(port, [], policy=policy)

    @pytest.mark.parametrize("url", ["spy://[COM3", "alt://[::1"])
    def test_a_malformed_wrapper_is_refused(self, url):
        """A wrapper URL that cannot be read is a refusal, not an error from deep in urllib."""
        with pytest.raises(PortNotAllowedError):
            check_open(url, FLEET, policy=_listed(".*"))

    @pytest.mark.skipif(os.name == "nt", reason="POSIX symlinks")
    def test_a_wrapper_opens_the_path_it_checked(self, tmp_path):
        """spy:// around a link opens the real path that was judged, not the link."""
        # Arrange
        link = tmp_path / "board"
        link.symlink_to("/dev/null")
        target = os.path.realpath("/dev/null")

        # Act
        actual = check_open(f"spy://{link}?file=t.txt", [], policy=_listed(target))

        # Assert
        assert actual == f"spy://{target}?file=t.txt"

    def test_a_wrapper_is_judged_by_the_port_it_wraps(self):
        """spy:// and alt:// open only when the port inside them is allowed."""
        policy = _listed("COM3")
        assert check_open("spy://COM3?file=t.txt", FLEET, policy=policy) == "spy://COM3?file=t.txt"
        for refused in ("spy://COM4?file=t.txt", "alt://COM4?class=PosixPollSerial"):
            with pytest.raises(PortNotAllowedError):
                check_open(refused, FLEET, policy=policy)


@pytest.mark.rule("port-policy-at-opener")
class TestTheOpener:
    """config.open_serial applies the policy before it builds anything."""

    def test_a_refused_port_is_never_opened(self):
        """A port off the list is refused before pyserial sees it."""
        with pytest.raises(PortNotAllowedError):
            open_serial(_cfg(ABSENT), policy=_listed("COM3"))

    def test_a_refused_spy_port_writes_no_log(self, tmp_path):
        """A spy:// around a refused port is refused before pyserial creates its file."""
        cap = tmp_path / "cap"
        with pytest.raises(PortNotAllowedError):
            open_serial(_cfg(f"spy://{ABSENT}?file=t.txt"), cap_folder=cap, policy=_listed("COM3"))
        assert not (cap / "t.txt").exists(), "nothing was written"


@pytest.mark.rule("port-policy-at-opener")
class TestTheEngine:
    """The engine reports a refusal as written, the same way each time, and opens once allowed."""

    def test_a_refusal_twice_then_an_allowed_port(self):
        """Two refused connects give the same message; a port on the list then connects."""
        # Arrange
        policy = _listed("COM3")
        cfg = _cfg(ABSENT)
        engine = SerialEngine(
            cfg=cfg, capture=CaptureEngine(), open_fn=lambda c: open_serial(c, policy=policy)
        )
        expected = not_allowed_message(ABSENT, policy, absent=True)

        # Act
        first = (engine.connect(), engine.last_error)
        second = (engine.connect(), engine.last_error)
        cfg["serial"]["port"] = "DEMO"
        third = engine.connect()
        engine.disconnect()

        # Assert
        assert first == (False, expected), "refused, with the denial unchanged"
        assert second == first, "the same answer the second time"
        assert third is True, "an allowed port connects after a refusal"


# ── A real start: the variable read at import ────────────────────────────────


def _termapy(*args: str, env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess:
    """Run termapy in a fresh interpreter with ``env`` over this one's environment."""
    return subprocess.run(
        [sys.executable, "-m", "termapy", *args],
        env={**{k: v for k, v in os.environ.items() if k != VARIABLE}, **env},
        capture_output=True,
        text=True,
        timeout=60,
        cwd=cwd,
    )


@pytest.mark.slow  # fresh interpreter: the variable is read at import
@pytest.mark.rule("ports-enumerated-once")
class TestARealStart:
    """termapy --ports and a one-shot connect honor the variable as a user sets it."""

    @pytest.mark.parametrize(
        ("allowed", "expected"), [("COM3", ["COM3"]), ("020026702RYN040952", ["COM7"])]
    )
    def test_ports_lists_only_what_is_allowed(self, tmp_path, allowed, expected):
        """--ports --json lists the allowed fleet port, by name or serial number, and says the list is limited on stderr."""
        # Act
        result = _termapy(
            "--ports", "--json", env={"TERMAPY_DEMO_FLEET": "1", VARIABLE: allowed}, cwd=tmp_path
        )

        # Assert
        assert result.returncode == 0, result.stderr
        actual = [record["device"] for record in json.loads(result.stdout)]
        assert actual == expected
        assert "limited by TERMAPY_ALLOWED_PORTS" in result.stderr, "the limit is said, off stdout"

    def test_a_cli_connect_is_refused(self, tmp_path):
        """In a CLI script, /port.connect of a port off the list fails, naming the variable and its value."""
        # Arrange -- the demo connects to DEMO (always allowed) first.
        cfg_root = tmp_path / "cfg"
        cfg_root.mkdir()
        script = tmp_path / "s.run"
        script.write_text(f"/port.disconnect\n/port.connect {ABSENT}\n", encoding="utf-8")

        # Act
        result = _termapy(
            "--cli",
            "--demo",
            "--cfg-dir",
            str(cfg_root),
            "--run",
            str(script),
            env={VARIABLE: "COM3"},
            cwd=tmp_path,
        )

        # Assert
        output = result.stdout + result.stderr
        assert "Connected: DEMO" in output, "the simulator opened under the list"
        expected = f"Port not found: {ABSENT} (TERMAPY_ALLOWED_PORTS=COM3; a port it allows by"
        assert expected in output, output

    def test_a_session_cannot_widen_the_list(self, tmp_path):
        """/env.set TERMAPY_ALLOWED_PORTS .* changes nothing: the list was read at start."""
        # Arrange
        cfg_root = tmp_path / "cfg"
        cfg_root.mkdir()
        script = tmp_path / "s.run"
        script.write_text(
            f"/env.set {VARIABLE} .*\n/port.disconnect\n/port.connect {ABSENT}\n", encoding="utf-8"
        )

        # Act
        result = _termapy(
            "--cli",
            "--demo",
            "--cfg-dir",
            str(cfg_root),
            "--run",
            str(script),
            env={VARIABLE: "COM3"},
            cwd=tmp_path,
        )

        # Assert
        output = result.stdout + result.stderr
        assert f"{VARIABLE}=COM3" in output, "still judged by the list it started with"


# Runs in its own process: the MCP marker is one-way, and the variable is read
# at import, as at a real --mcp start.
_MCP_PROBE = r"""
import asyncio, json, sys
from termapy import env_flags
from termapy.defaults import DEFAULT_CFG
from termapy.mcp.server import MCPHost

env_flags.mark_under_mcp()
cfg = {**DEFAULT_CFG, "serial": {**DEFAULT_CFG["serial"], "port": ""}, "echo": False}
host = MCPHost(cfg, "", verbose=False)
host._fire_app_start()
answers = {}
for line in sys.argv[1:]:
    answers[line] = asyncio.run(host.run_command_async(line, "normal", 8.0))
    asyncio.run(host.run_command_async("/port.disconnect", "normal", 8.0))
print(json.dumps(answers, default=str))
"""


def _mcp(tmp_path: Path, env: dict[str, str], *lines: str) -> dict[str, dict]:
    """The MCP envelope for each line, from a fresh server process."""
    probe = tmp_path / "probe.py"
    probe.write_text(_MCP_PROBE, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(probe), *lines],
        env={
            **{k: v for k, v in os.environ.items() if k != VARIABLE},
            "TERMAPY_DEMO_FLEET": "1",
            **env,
        },
        capture_output=True,
        text=True,
        timeout=120,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.splitlines()[-1])


@pytest.mark.slow  # fresh interpreter: the variable and the marker are per process
@pytest.mark.rule("port-policy-at-opener")
class TestUnderMcp:
    """An MCP server with no list opens no port but the simulator; with a list it lists and opens only those."""

    def test_no_list_lists_and_opens_nothing_but_the_simulator(self, tmp_path):
        """No variable: /port.list is empty, a device and a host are denied naming it, DEMO connects."""
        # Arrange
        lines = (
            "/port.list",
            f"/port.connect {ABSENT}",
            "/port.connect socket://127.0.0.1:9",
            "/port.connect DEMO",
        )

        # Act
        answers = _mcp(tmp_path, {}, *lines)

        # Assert
        assert answers["/port.list"]["data"] == [], "no port is listed"
        not_set = "TERMAPY_ALLOWED_PORTS is not set; under MCP that allows no port"
        assert not_set in answers[f"/port.connect {ABSENT}"]["error"], (
            "a device is denied, naming the variable"
        )
        assert not_set in answers["/port.connect socket://127.0.0.1:9"]["error"], "so is a host"
        assert answers["/port.connect DEMO"]["error"] == "", "the simulator connects"

    def test_a_list_decides_what_is_listed(self, tmp_path):
        """TERMAPY_ALLOWED_PORTS=COM3: /port.list holds COM3 alone, and COM4 is denied without saying it is there."""
        # Act
        answers = _mcp(tmp_path, {VARIABLE: "COM3"}, "/port.list", "/port.connect COM4")

        # Assert
        actual = [record["device"] for record in answers["/port.list"]["data"]]
        assert actual == ["COM3"]
        error = answers["/port.connect COM4"]["error"]
        expected = (
            "Port not allowed or not found: COM4 (TERMAPY_ALLOWED_PORTS=COM3, set by the operator)"
        )
        assert expected in error
        assert "plugged in" not in error, "an agent is never told a hidden port exists"

    def test_every_route_meets_the_list(self, tmp_path):
        """/port <x> and /cfg port then a bare /port.connect reach the same refusal as /port.connect <x>."""
        # Act
        answers = _mcp(
            tmp_path, {VARIABLE: "COM3"}, "/port COM4", "/cfg port COM4", "/port.connect"
        )

        # Assert
        expected = (
            "Port not allowed or not found: COM4 (TERMAPY_ALLOWED_PORTS=COM3, set by the operator)"
        )
        port_lines = " ".join(line["text"] for line in answers["/port COM4"]["output_lines"])
        assert expected in port_lines, "/port <x> opens through the same check"
        assert expected in answers["/port.connect"]["error"], (
            "a port set by /cfg is checked at connect"
        )
