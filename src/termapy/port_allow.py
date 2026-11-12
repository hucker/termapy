"""Which ports this process may open and list: ``TERMAPY_ALLOWED_PORTS``, read once.

The operator names the allowed ports in one regex, ``|`` between
alternatives (``COM2|COM5|BG03U7VTA``), matched against the whole of a
port's canonical name or its USB serial number, ignoring case.  Unset, a
person at the TUI or CLI may open any port; an MCP server may open none
until the variable is set (registry ``port-policy-at-opener``, decision
2026-11-11).  The simulator ports and ``loop://`` reach no device and no
network, so every policy allows them.

A network port (``socket://``, ``rfc2217://``) is a connection out of the
machine, so a pattern never opens one: an alternative must spell its
endpoint out exactly, ``scheme://host:port`` (registry
``network-port-named-exactly``).  ``.*`` allows every device and no host.

This module is the parsing, the policy and the one owner of every denial
text.  It is pure: ``env_flags`` builds the policy at import from the
environment, and ``port_control`` applies it to what the system lists and
to what ``config.open_serial`` opens.  The list limits termapy's own
commands, not the machine: any other program, an agent's shell included,
reaches every port the operating system lets it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: The environment variable that holds the list.
VARIABLE = "TERMAPY_ALLOWED_PORTS"

#: The URL schemes that open a connection out of the machine.
NETWORK_SCHEMES = frozenset({"socket", "rfc2217"})

#: The URL schemes that wrap a native port named inside the URL.
WRAPPER_SCHEMES = frozenset({"spy", "alt"})

#: The URL scheme that reaches no device and no network.
LOOPBACK_SCHEME = "loop"

#: The URL scheme that picks its port inside pyserial, after any check here.
HWGREP_SCHEME = "hwgrep"

# Regex characters that make an alternative more than plain text.  A "."
# is read as a literal dot in an endpoint, where it can only be one.
_METACHARACTERS = frozenset("^$*+?{}[]()|")


class PortNotAllowedError(ValueError):
    """The port policy refuses this port; the message says why and names the variable."""


def url_scheme(port: str) -> str | None:
    """The scheme of a URL port as pyserial reads it, lowercased; None for a device name."""
    lowered = port.lower()
    if "://" not in lowered:
        return None
    return lowered.split("://", 1)[0]


def wrapped_port(url: str) -> str:
    """The native port a ``spy://`` or ``alt://`` URL opens, read as pyserial reads it."""
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    return parts.netloc + parts.path


def url_endpoint(url: str) -> tuple[str, str, int] | None:
    """``(scheme, host, port)`` of a network URL as pyserial connects to it, or None.

    pyserial's socket and RFC 2217 handlers connect to ``urlsplit``'s
    ``hostname`` (lowercased, IPv6 brackets dropped) and ``port``; options
    after ``?`` change how, not where.  A URL with no host or no valid port
    has no endpoint.
    """
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if not parts.hostname or port is None:
        return None
    return (parts.scheme.lower(), parts.hostname.lower(), port)


# A backslash before punctuation (``\.``, ``\[``) escapes that one character.
_ESCAPED_PUNCTUATION = re.compile(r"\\([^0-9A-Za-z])")


def named_endpoints(pattern: str) -> frozenset[tuple[str, str, int]]:
    """The network endpoints a pattern spells out exactly: each ``|`` piece that is plain text.

    A piece is plain when, its escaped punctuation aside, it holds no regex
    metacharacter and no other escape (``\\d`` is a pattern).  ``.`` and
    ``\\.`` both read as a dot, ``\\[::1\\]`` as an IPv6 host.  Only the
    operator writes the value, so a piece counts whenever the endpoint is
    typed out in full; no wildcard ever names one.
    """
    endpoints = set()
    for piece in pattern.split("|"):
        if any(
            char in _METACHARACTERS or char == "\\" for char in _ESCAPED_PUNCTUATION.sub("", piece)
        ):
            continue
        text = _ESCAPED_PUNCTUATION.sub(r"\1", piece)
        endpoint = url_endpoint(text) if url_scheme(text) in NETWORK_SCHEMES else None
        if endpoint is not None:
            endpoints.add(endpoint)
    return frozenset(endpoints)


@dataclass(frozen=True)
class PortAllowlist:
    """The operator's list: one regex over device names and serial numbers, plus named hosts."""

    text: str
    regex: re.Pattern[str] = field(repr=False)
    endpoints: frozenset[tuple[str, str, int]]

    @classmethod
    def parse(cls, text: str) -> PortAllowlist:
        """Compile ``text``, ignoring ASCII case.

        ``re.ASCII`` keeps case folding to ASCII, so ``ſ`` never matches ``s``.
        A space is refused: no port name or serial number holds one, so it is
        a separator typo (``COM7 | COM5``) that would otherwise match nothing.

        Raises:
            ValueError: The value holds a space, or the regex does not
                compile, as ``Invalid TERMAPY_ALLOWED_PORTS: <text> (<reason>)``.
        """
        if any(char.isspace() for char in text):
            raise ValueError(f"Invalid {VARIABLE}: {text} (no spaces; separate ports with |)")
        try:
            regex = re.compile(text, re.IGNORECASE | re.ASCII)
        except re.error as e:
            raise ValueError(f"Invalid {VARIABLE}: {text} ({e})") from e
        return cls(text=text, regex=regex, endpoints=named_endpoints(text))

    @classmethod
    def matching_nothing(cls, text: str) -> PortAllowlist:
        """A list that allows no port, kept for a value that did not compile.

        ``termapy`` refuses to start on such a value; this is what code that
        imports termapy without its entry point sees instead of every port.
        """
        return cls(text=text, regex=re.compile(r"(?!)"), endpoints=frozenset())

    def matches(self, identity: str | None) -> bool:
        """Whether the whole of ``identity`` matches; None and blank match nothing."""
        return bool(identity) and self.regex.fullmatch(identity) is not None


@dataclass(frozen=True)
class PortPolicy:
    """The ports this process may open: the operator's list, and whether it serves MCP.

    ``in_force`` is False only for a person's session with no list, which
    allows every port as termapy always has.  Under MCP an unset list
    allows no device and no host.
    """

    allowlist: PortAllowlist | None = None
    under_mcp: bool = False
    network_egress: bool = True
    host_info: bool = False

    @property
    def hides_host(self) -> bool:
        """Whether what this process shows stops at the allowed devices: MCP without ``TERMAPY_MCP_HOST_INFO``.

        Then a program holding a port is not named (decision 2026-11-12);
        the commands that show the machine need the ``host_info`` capability.
        """
        return self.under_mcp and not self.host_info

    @property
    def in_force(self) -> bool:
        """Whether any port is refused: a list is set, or this is an MCP server."""
        return self.allowlist is not None or self.under_mcp

    def allows_device(self, name: str | None, serial: str | None = None) -> bool:
        """Whether a device with this canonical name or serial number may be opened and listed."""
        if not self.in_force:
            return True
        if self.allowlist is None:
            return False
        return self.allowlist.matches(name) or self.allowlist.matches(serial)

    def network_refusal(self, url: str) -> str | None:
        """Why a network URL is refused, or None when it may open.

        The URL's endpoint must be one the list spells out
        (``named_endpoints``); no pattern opens a network port.  Under MCP
        the server's ``TERMAPY_MCP_NET_EGRESS`` is needed too.  The
        decision is made on the text, so a refused host is never looked up.
        """
        if not self.in_force:
            return None
        if self.allowlist is None:
            return not_allowed_message(url, self)
        endpoint = url_endpoint(url)
        if endpoint is None or endpoint not in self.allowlist.endpoints:
            return (
                f"Port not allowed: {url} (a network port must be named exactly, "
                f"scheme://host:port, in {VARIABLE}={self.allowlist.text})"
            )
        if self.under_mcp and not self.network_egress:
            return (
                f"Port not allowed: {url} (under MCP a network port also needs "
                "TERMAPY_MCP_NET_EGRESS=1 in the server's environment)"
            )
        return None

    def listing_note(self) -> str | None:
        """What a port listing says about the ports it leaves out; None when it leaves none out."""
        if not self.in_force:
            return None
        if self.allowlist is None:
            return f"{VARIABLE} is not set; under MCP that allows no port"
        return f"limited by {VARIABLE}"


def not_allowed_message(
    port: str,
    policy: PortPolicy,
    scheme: str | None = None,
    present: str | None = None,
    absent: bool = False,
) -> str:
    """The denial for ``port``: what the operator's list says, so the reader stops retrying.

    A person is told which it is: a refused port that is plugged in, or one
    that is not there (a port the list allows only by serial number is
    recognized only while plugged in).  Under MCP both get one answer, so
    an agent cannot map the ports the listings hide.

    Args:
        port: The port as resolved, the user's spelling when nothing matched.
        policy: The policy that refused it.
        scheme: A URL scheme the policy refuses outright (``hwgrep``, an
            unknown one); None for a device.
        present: For a person, the plugged-in device ``port`` names.
        absent: For a person, ``port`` names nothing plugged in.
    """
    if policy.allowlist is None:
        return f"Port not allowed: {port} ({VARIABLE} is not set; under MCP that allows no port)"
    listed = f"{VARIABLE}={policy.allowlist.text}"
    if scheme == HWGREP_SCHEME:
        return (
            f"Port not allowed: {port} (hwgrep:// picks its port after termapy "
            f"checks it; with {VARIABLE} set, name the port)"
        )
    if scheme is not None:
        return f"Port not allowed: {port} ({VARIABLE} allows no {scheme}:// port)"
    if present is not None:
        which = "" if present == port else f"{present}, "
        return f"Port not allowed: {port} ({which}plugged in, but {listed} leaves it out; set by the operator)"
    if absent:
        return f"Port not found: {port} ({listed}; a port it allows by serial number must be plugged in)"
    if policy.under_mcp:
        return f"Port not allowed or not found: {port} ({listed}, set by the operator)"
    return f"Port not allowed: {port} ({listed}, set by the operator)"
