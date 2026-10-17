"""The USB tree: assembly, rendering, and the serial-port cross-reference.

The two gathering backends are OS calls -- cfgmgr32 on Windows, sysfs on
Linux -- and only one of them can run on any given machine.  Everything
between them and the screen is pure and is tested here on every platform:
records in, tree out, text out.  ``build_tree`` is where the notation
argument actually lives, so it carries the most weight.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from contextlib import redirect_stdout

import pytest

from termapy.usb_tree import (
    UnsupportedPlatform,
    UsbNode,
    UsbRecord,
    _ascii_fold,
    _clean_desc,
    _linux_records,
    _parent_path,
    build_tree,
    gather_usb_tree,
    render_tree,
    tag_serial_ports,
    to_json_records,
)


def _bus() -> list[UsbRecord]:
    """A hub with two plain adapters and one composite device below it.

    Mirrors the shape that motivated the feature: at one tier, a device
    with interfaces sits next to a device that is itself a hub.
    """
    return [
        UsbRecord(path="1", description="USB Root Hub"),
        UsbRecord(path="1-8", description="Generic USB Hub"),
        UsbRecord(path="1-8.2", description="USB Serial Converter", vid_pid="0403:6001"),
        UsbRecord(path="1-8.4", description="USB Composite Device", vid_pid="04D8:9036"),
        UsbRecord(path="1-8.4", interface_number="0", description="Debugger"),
        UsbRecord(path="1-8.4", interface_number="1", description="USB Serial Device"),
    ]


class _Facts:
    """Stand-in for ChipFacts, carrying only what the tagger reads."""

    def __init__(self, device, location, interface_number=None):
        self.device = device
        self.location = location
        self.interface_number = interface_number


class TestParentPath:
    def test_a_hub_tier_drops_the_last_dot_group(self):
        """A device behind a hub finds its parent by dropping the last .N port group."""
        # Act / Assert
        assert _parent_path("1-8.4") == "1-8", "one tier up is the hub"

    def test_the_first_tier_drops_back_to_the_bus(self):
        """A root-hub port such as 1-8 hangs directly off bus 1."""
        assert _parent_path("1-8") == "1", "a root-hub port hangs off the bus"

    def test_a_bus_has_no_parent(self):
        """A bare bus number is a tree root, with no parent path."""
        assert _parent_path("1") is None, "the bus is a root"


class TestBuildTree:
    def test_devices_nest_under_the_hub_they_are_plugged_into(self):
        """Devices nest under their hub, and the hub under its bus, in one root."""
        # Act
        roots = build_tree(_bus())

        # Assert -- one bus, one hub below it, two devices below that.
        assert len(roots) == 1, f"a single bus is a single root; got {len(roots)}"
        hub = roots[0].children[0]
        actual = [child.path for child in hub.children]
        assert actual == ["1-8.2", "1-8.4"], f"devices sit under the hub; got {actual}"

    def test_interfaces_nest_under_their_own_device(self):
        """Interfaces are children of their device, sharing its path, not a deeper tier."""
        # Act
        roots = build_tree(_bus())
        composite = roots[0].children[0].children[1]

        # Assert -- this is the whole notation argument.  An interface is
        # a child of its device, NOT another hub tier, so it can never be
        # confused with a device plugged into a hub at the same path.
        actual = [child.interface_number for child in composite.children]
        assert actual == ["0", "1"], f"both interfaces hang off the device at 1-8.4; got {actual}"
        assert all(child.path == "1-8.4" for child in composite.children), (
            "an interface keeps its device's path -- it is not a deeper tier"
        )

    def test_ports_are_ordered_numerically_not_alphabetically(self):
        """Sibling ports sort as numbers, so 1-10 comes after 1-2 and 1-8."""
        # Arrange -- string ordering would put 1-10 before 1-2.
        records = [
            UsbRecord(path="1"),
            UsbRecord(path="1-10"),
            UsbRecord(path="1-2"),
            UsbRecord(path="1-8"),
        ]

        # Act
        roots = build_tree(records)

        # Assert
        actual = [child.path for child in roots[0].children]
        assert actual == ["1-2", "1-8", "1-10"], (
            f"ports are numbers and sort as numbers; got {actual}"
        )

    def test_interfaces_come_before_devices_below_the_same_node(self):
        """A hub-plus-function device lists its own interfaces before downstream devices."""
        # Arrange -- a composite device that is also a hub is unusual but
        # legal; its own functions should read before what hangs off it.
        records = [
            UsbRecord(path="1-8"),
            UsbRecord(path="1-8", interface_number="0", description="function"),
            UsbRecord(path="1-8.1", description="downstream device"),
        ]

        # Act
        roots = build_tree(records)

        # Assert
        actual = [child.label for child in roots[0].children]
        assert actual == [":0", "1-8.1"], (
            f"what the device IS reads before what is plugged into it; got {actual}"
        )

    def test_a_device_whose_hub_is_missing_still_appears(self):
        """A device whose parent was not reported is shown as a root, never dropped."""
        # Arrange -- backend reported a leaf but not its parent.
        records = [UsbRecord(path="1-8.4", description="orphan")]

        # Act
        roots = build_tree(records)

        # Assert -- a partial tree beats a silently shortened one.
        actual = [node.path for node in roots]
        assert actual == ["1-8.4"], (
            f"an unattached device is shown as a root, not dropped; got {actual}"
        )

    def test_an_interface_whose_device_is_missing_gets_one(self):
        """An orphan interface gets a synthesized device node to hang from."""
        # Arrange
        records = [UsbRecord(path="1-8.4", interface_number="1", description="fn")]

        # Act
        roots = build_tree(records)

        # Assert
        assert len(roots) == 1 and roots[0].path == "1-8.4", (
            "a device node is synthesized so the interface has somewhere to hang"
        )
        assert [c.interface_number for c in roots[0].children] == ["1"], (
            "and the interface is attached to it"
        )

    def test_an_empty_bus_builds_an_empty_forest(self):
        """No records build no roots, without error."""
        # Act / Assert
        assert build_tree([]) == [], "nothing in, nothing out"


class TestTagSerialPorts:
    def test_an_interface_that_carries_a_port_is_marked(self):
        """On a composite device only the interface owning the port is tagged."""
        # Arrange -- COM3 is function 1 of the device at 1-8.4.
        roots = build_tree(_bus())
        facts = [_Facts("COM3", "1-8.4", "1")]

        # Act
        tag_serial_ports(roots, facts)

        # Assert
        composite = roots[0].children[0].children[1]
        tagged = {c.interface_number: c.port_name for c in composite.children}
        assert tagged == {"0": None, "1": "COM3"}, (
            f"only the interface that owns the port is marked; got {tagged}"
        )

    def test_a_single_function_device_is_marked_on_the_device_node(self):
        """A port with no interface number tags the device node itself."""
        # Arrange -- a plain adapter has no interface to qualify.
        roots = build_tree(_bus())

        # Act
        tag_serial_ports(roots, [_Facts("COM7", "1-8.2", None)])

        # Assert
        adapter = roots[0].children[0].children[0]
        assert adapter.port_name == "COM7", (
            f"the device node carries the port; got {adapter.port_name!r}"
        )

    def test_a_port_at_an_unknown_location_marks_nothing(self):
        """A port with no resolved location tags nothing rather than guessing a device."""
        # Arrange -- a port whose location did not resolve.
        roots = build_tree(_bus())

        # Act
        tag_serial_ports(roots, [_Facts("COM9", None, None)])

        # Assert -- no location means no node to attach it to, and
        # guessing would put a real port on the wrong device.
        marked = [n.path for r in roots for n in r.walk() if n.port_name]
        assert marked == [], f"nothing is marked on a locationless port; got {marked}"


class TestRenderTree:
    def test_the_shape_is_drawn_with_ascii_connectors(self):
        """The tree uses ASCII +-- and `-- connectors so legacy codepages can print it."""
        # Arrange -- box-drawing characters raise UnicodeEncodeError on a
        # Windows console with a legacy codepage, and this text is
        # printed with a plain print().
        roots = build_tree(_bus())

        # Act
        lines = render_tree(roots)

        # Assert
        blob = "\n".join(lines)
        assert blob.isascii(), "every line must survive a legacy codepage"
        assert "+-- " in blob, "branch connector present"
        assert "`-- " in blob, "last-child connector present"
        assert lines[0].startswith("1 "), (
            f"the bus sits flush left, all else hangs off it; got {lines[0]!r}"
        )

    def test_a_tagged_node_names_its_port(self):
        """Exactly one line shows -> COM3, and it is the interface line, not its device."""
        # Arrange
        roots = build_tree(_bus())
        tag_serial_ports(roots, [_Facts("COM3", "1-8.4", "1")])

        # Act
        lines = render_tree(roots)

        # Assert
        hits = [line for line in lines if "-> COM3" in line]
        assert len(hits) == 1, f"exactly one line claims COM3; got {hits}"
        assert ":1" in hits[0], "and it is the interface line, not the device"

    def test_an_empty_bus_says_so(self):
        """An empty bus renders an explicit no-devices line, not zero output."""
        # Act
        actual = render_tree([])

        # Assert
        assert actual == ["(no USB devices found)"], (
            f"an empty bus gets a line, not zero output; got {actual}"
        )


class TestJsonRecords:
    def test_every_node_carries_the_full_schema(self):
        """Each JSON record has the full fixed key set so consumers never probe for fields."""
        # Arrange
        roots = build_tree(_bus())

        # Act
        records = to_json_records(roots)

        # Assert -- fixed shape so consumers can rely on it.
        expected_keys = {
            "path",
            "interface_number",
            "description",
            "vid_pid",
            "serial_number",
            "driver",
            "port",
            "children",
        }
        assert set(records[0]) == expected_keys, (
            f"every documented field present; got {sorted(records[0])}"
        )

    def test_the_nesting_survives_the_conversion(self):
        """Records keep the bus -> hub -> device -> interface nesting via children."""
        # Arrange
        roots = build_tree(_bus())

        # Act
        records = to_json_records(roots)

        # Assert -- bus -> hub -> composite -> two interfaces.
        composite = records[0]["children"][0]["children"][1]
        actual = [child["interface_number"] for child in composite["children"]]
        assert actual == ["0", "1"], f"interfaces stay nested; got {actual}"

    def test_the_result_is_serializable(self):
        """The records go through json.dumps with no custom encoder."""
        # Act -- the point of the shape is to be dumped.
        actual = json.dumps(to_json_records(build_tree(_bus())))

        # Assert
        assert '"1-8.4"' in actual, "round-trips through json.dumps"


class TestDescriptions:
    def test_the_inf_indirection_is_stripped(self):
        """The Windows @file.inf,%key%; prefix is removed, leaving only readable text."""
        # Act -- Windows stores the localizable key ahead of the text.
        actual = _clean_desc("@usbser.inf,%usbserial.devicedesc%;USB Serial Device")

        # Assert
        assert actual == "USB Serial Device", f"only the human part; got {actual!r}"

    def test_trademark_symbols_are_dropped_not_spelled_out(self):
        """Registered and trademark signs are removed, not expanded into the letters TM."""
        # Act -- NFKD alone expands a trademark sign into the letters
        # "TM", which reads worse than removing it.
        actual = _ascii_fold("MPLAB® PICkit™5")

        # Assert
        assert actual == "MPLAB PICkit5", f"reads the way it is said; got {actual!r}"

    def test_accents_fold_rather_than_vanish(self):
        """Accented letters fold to their ASCII base so names stay readable and findable."""
        # Act
        actual = _ascii_fold("Café Widget")

        # Assert -- dropping the letter would corrupt the name; folding
        # keeps it findable.
        assert actual == "Cafe Widget", f"got {actual!r}"

    def test_an_empty_description_stays_empty(self):
        """A missing description stays None instead of becoming an empty string."""
        assert _clean_desc(None) is None, "no description, no value"


class TestGatherInjection:
    def test_an_injected_bus_needs_no_hardware(self):
        """source= replaces the OS backend, so the tree builds from injected records alone."""
        # Act -- same injection contract as port_control's source=.
        roots = gather_usb_tree(source=_bus, facts_list=[])

        # Assert
        assert [node.path for node in roots] == ["1"], (
            "the tree is built from the records handed in"
        )

    def test_injected_facts_tag_the_injected_bus(self):
        """Injected port facts are cross-referenced onto the injected tree too."""
        # Act
        roots = gather_usb_tree(source=_bus, facts_list=[_Facts("COM3", "1-8.4", "1")])

        # Assert
        marked = [n.port_name for r in roots for n in r.walk() if n.port_name]
        assert marked == ["COM3"], f"cross-reference runs on injection too; got {marked}"

    def test_an_unsupported_platform_raises_rather_than_lying(self, monkeypatch):
        """A platform with no backend raises, since an empty tree would mean no devices."""
        # Arrange -- no backend for this platform.
        monkeypatch.setattr("termapy.usb_tree.sys.platform", "sunos5")

        # Act / Assert -- an empty tree would read as "no devices",
        # which is a different and wrong statement.
        with pytest.raises(UnsupportedPlatform, match="sunos5"):
            gather_usb_tree()


class TestUsbFlag:
    def _run(self, source, **flags):
        options = {"json": False}
        options.update(flags)
        args = argparse.Namespace(**options)
        buf = io.StringIO()
        with pytest.raises(SystemExit) as exc, redirect_stdout(buf):
            from termapy import cli_flags

            cli_flags.run_usb(args, source=source)
        return buf.getvalue(), exc.value.code

    def test_the_tree_is_printed_and_exits_zero(self):
        """--usb prints the rendered tree to stdout and exits 0 when devices are present."""
        # Act
        out, code = self._run(_bus)

        # Assert
        assert code == 0, "a bus with devices exits 0"
        assert "1-8.4" in out, "the tree reached stdout"

    def test_an_empty_bus_exits_nonzero(self):
        """--usb exits 1 on an empty bus, matching --ports, and still prints a message."""
        # Act
        out, code = self._run(list)

        # Assert
        assert code == 1, "nothing found is a failure exit, matching --ports"
        assert "(no USB devices found)" in out, "and says so"

    def test_json_emits_the_nested_records(self):
        """--usb with --json prints the nested records as JSON instead of the text tree."""
        # Act
        out, code = self._run(_bus, json=True)

        # Assert
        records = json.loads(out)
        assert code == 0, "exit 0 with devices"
        assert records[0]["path"] == "1", f"nested records, not text; got {out[:40]}"


class TestNodeLabels:
    def test_a_device_labels_itself_with_its_path(self):
        """A device node's label is its full topology path."""
        assert UsbNode(path="1-8.4").label == "1-8.4", "devices show the path"

    def test_an_interface_labels_itself_with_only_its_number(self):
        """An interface label is just :N, since its device's path is the line above."""
        # The path is on the parent line directly above, and there is no
        # "x" placeholder because a tree has somewhere to put the
        # distinction pyserial has to spell inline.
        actual = UsbNode(path="1-8.4", interface_number="1").label
        assert actual == ":1", f"got {actual!r}"


def _sysfs(root, name, **fields):
    """Create one fake sysfs node with attribute files."""
    node = os.path.join(root, name)
    os.makedirs(node, exist_ok=True)
    for key, value in fields.items():
        with open(os.path.join(node, key), "w", encoding="utf-8") as handle:
            handle.write(value + "\n")
    return node


def _fake_sysfs(root):
    """A root hub, a hub, an adapter, and a two-function device.

    Names are the real sysfs spelling: ``usb1`` for a root hub, ``1-8.2``
    for a device, ``1-8.2:1.0`` for one of its interfaces.
    """
    _sysfs(root, "usb1", product="xHCI Host Controller", idVendor="1d6b", idProduct="0002")
    _sysfs(root, "1-8", product="USB2.0 Hub", idVendor="2109", idProduct="2817")
    _sysfs(
        root,
        "1-8.2",
        product="FT232R USB UART",
        idVendor="0403",
        idProduct="6001",
        serial="BG03U7VTA",
    )
    _sysfs(root, "1-8.4", product="PICkit5", idVendor="04d8", idProduct="9036")
    _sysfs(root, "1-8.2:1.0", interface="FT232R USB UART")
    _sysfs(root, "1-8.4:1.0", interface="PICkit debug")
    _sysfs(root, "1-8.4:1.1", interface="CDC data")


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="a sysfs interface directory is named '1-8.2:1.0', and Windows "
    "will not create a filename containing a colon",
)
class TestLinuxSysfsBackend:
    """The sysfs parse, against a synthesized tree.

    Linux names its directories after the topology, so this backend is
    mostly a naming convention reader -- which means it can be checked
    without a Linux box, provided the root is injectable.  Run for real
    on Linux too: the fixture is written to disk exactly as sysfs lays
    it out.
    """

    def test_devices_and_interfaces_are_told_apart_by_name(self, tmp_path):
        """A colon in a sysfs name marks an interface; the digit after the dot is its index."""
        # Arrange
        root = str(tmp_path)
        _fake_sysfs(root)

        # Act
        records = _linux_records(root)

        # Assert -- "1-8.4" is a device, "1-8.4:1.1" is its interface 1.
        devices = sorted(r.path for r in records if r.interface_number is None)
        assert devices == ["1", "1-8", "1-8.2", "1-8.4"], (
            f"a colon means interface, no colon means device; got {devices}"
        )
        interfaces = sorted(
            (r.path, r.interface_number) for r in records if r.interface_number is not None
        )
        assert interfaces == [("1-8.2", "0"), ("1-8.4", "0"), ("1-8.4", "1")], (
            f"the interface number is the part after the dot; got {interfaces}"
        )

    def test_a_root_hub_is_named_for_its_bus(self, tmp_path):
        """A sysfs usbN root hub becomes bus N so devices at N-x find their parent."""
        # Arrange -- sysfs calls it usb1; the topology calls it 1.
        root = str(tmp_path)
        _fake_sysfs(root)

        # Act
        records = _linux_records(root)

        # Assert
        assert any(r.path == "1" for r in records), (
            "usb1 becomes bus 1, so devices at 1-8 can find their parent"
        )

    def test_identity_comes_off_the_attribute_files(self, tmp_path):
        """VID:PID (uppercased), serial and product are read from sysfs attribute files."""
        # Arrange
        root = str(tmp_path)
        _fake_sysfs(root)

        # Act
        adapter = next(
            r for r in _linux_records(root) if r.path == "1-8.2" and r.interface_number is None
        )

        # Assert
        assert adapter.vid_pid == "0403:6001", (
            f"idVendor/idProduct are lowercase in sysfs and shown upper; got {adapter.vid_pid!r}"
        )
        assert adapter.serial == "BG03U7VTA", f"got {adapter.serial!r}"
        assert adapter.description == "FT232R USB UART", f"got {adapter.description!r}"

    def test_a_directory_that_is_not_a_usb_path_is_skipped(self, tmp_path):
        """sysfs entries not matching the USB topology pattern are ignored."""
        # Arrange -- sysfs holds more than device nodes.
        root = str(tmp_path)
        _fake_sysfs(root)
        _sysfs(root, "not-a-device", product="noise")

        # Act
        records = _linux_records(root)

        # Assert
        assert all(r.description != "noise" for r in records), (
            "only names matching the topology pattern are devices"
        )

    def test_the_driver_is_read_from_the_symlink(self, tmp_path):
        """The driver name is the basename of the interface's driver symlink target."""
        # Arrange -- sysfs points an interface at its driver by symlink.
        root = str(tmp_path)
        _fake_sysfs(root)
        target = os.path.join(root, "_bus", "ftdi_sio")
        os.makedirs(target)
        try:
            os.symlink(target, os.path.join(root, "1-8.2:1.0", "driver"))
        except OSError:  # Windows without developer mode / admin
            pytest.skip("this OS will not let the test create a symlink")

        # Act
        interface = next(
            r for r in _linux_records(root) if r.path == "1-8.2" and r.interface_number == "0"
        )

        # Assert -- the basename of the link target is the driver name.
        assert interface.driver == "ftdi_sio", (
            f"driver comes from the link, not a file; got {interface.driver!r}"
        )

    def test_an_absent_sysfs_root_is_not_an_error(self, tmp_path):
        """A missing sysfs root reads as an empty bus, not a crash."""
        # Act -- a kernel built without USB, or WSL with nothing attached.
        actual = _linux_records(str(tmp_path / "nope"))

        # Assert -- an empty bus, not a crash.  gather_usb_tree turns this
        # into "(no USB devices found)".
        assert actual == [], f"missing sysfs reads as an empty bus; got {actual}"
