"""Tests for headless protocol test runner and JSON result output."""

import json
import time
from pathlib import Path

import pytest

from termapy.demo import FakeSerial
from termapy.proto_runner import (
    _bytes_to_hex,
    _bytes_to_text,
    _build_test_result,
    _read_frame,
    expand_result_template,
    run_proto_tests,
)
from termapy.protocol import TestCase


# -- Helper formatting --------------------------------------------------------


class TestBytesToHex:
    def test_empty(self) -> None:
        assert _bytes_to_hex(b"") == ""  # empty input → empty string

    def test_single_byte(self) -> None:
        assert _bytes_to_hex(b"\x0a") == "0A"  # single byte → uppercase hex

    def test_multiple_bytes(self) -> None:
        actual = _bytes_to_hex(b"\x01\x02\xff")
        assert actual == "01 02 FF"  # space-separated uppercase hex

    def test_printable_ascii(self) -> None:
        actual = _bytes_to_hex(b"OK")
        assert actual == "4F 4B"  # ASCII chars rendered as hex


class TestBytesToText:
    def test_empty(self) -> None:
        assert _bytes_to_text(b"") == ""  # empty input → empty string

    def test_printable(self) -> None:
        assert _bytes_to_text(b"OK") == "OK"  # plain text preserved

    def test_with_cr_lf(self) -> None:
        actual = _bytes_to_text(b"OK\r\n")
        assert "\\r" in actual  # CR escaped
        assert "\\n" in actual  # LF escaped


# -- Template expansion -------------------------------------------------------


class TestExpandTemplate:
    def test_name_defaults_to_proto_name(self) -> None:
        actual = expand_result_template("{name}_results.json", "at_test")
        assert actual == "at_test_results.json"  # falls back to proto name

    def test_name_uses_config_name(self) -> None:
        actual = expand_result_template("{name}_results.json", "at_test",
                                        config_name="demo")
        assert actual == "demo_results.json"  # config name used

    def test_proto_name_placeholder(self) -> None:
        actual = expand_result_template("{name}-{proto_name}.json", "at_test",
                                        config_name="demo")
        assert actual == "demo-at_test.json"  # both placeholders

    def test_with_datetime(self) -> None:
        actual = expand_result_template("{proto_name}_{datetime}.json", "test")
        # Should contain name and a datetime stamp
        assert actual.startswith("test_")  # starts with proto name
        assert actual.endswith(".json")  # ends with extension
        assert len(actual) > len("test_.json")  # datetime was inserted

    def test_date_and_time_separate(self) -> None:
        actual = expand_result_template("{date}_{time}.json", "x")
        parts = actual.replace(".json", "").split("_")
        assert len(parts[0]) == 8  # YYYYMMDD
        assert len(parts[1]) == 6  # HHMMSS


# -- Test result building -----------------------------------------------------


class TestBuildResult:
    def test_pass_result(self) -> None:
        # Arrange
        tc = TestCase(index=1, name="AT basic", send_data=b"AT\r",
                      expect_data=b"OK\r\n", expect_mask=b"\xff\xff\xff\xff")
        response = b"OK\r\n"
        elapsed_ms = 12.3

        # Act
        result = _build_test_result(tc, response, elapsed_ms, True)

        # Assert
        assert result["index"] == 1  # test index
        assert result["name"] == "AT basic"  # test name
        assert result["passed"] is True  # passed
        assert result["elapsed_ms"] == 12.3  # timing
        assert result["send_hex"] == "41 54 0D"  # hex of AT\r
        assert result["actual_hex"] == "4F 4B 0D 0A"  # hex of OK\r\n

    def test_timeout_result(self) -> None:
        # Arrange
        tc = TestCase(index=2, name="Timeout test", send_data=b"\x01",
                      expect_data=b"\x02", expect_mask=b"\xff",
                      timeout_ms=500)

        # Act
        result = _build_test_result(tc, None, 500.0, False)

        # Assert
        assert result["passed"] is False  # failed
        assert result["actual_hex"] == ""  # no response
        assert "Timeout" in result["error"]  # error message
        assert "500" in result["error"]  # timeout value in message

    def test_format_specs_included(self) -> None:
        # Arrange
        tc = TestCase(index=1, name="Fmt test", send_data=b"\x01",
                      expect_data=b"\x02", expect_mask=b"\xff",
                      send_fmt="Addr:H1", expect_fmt="Resp:H1")

        # Act
        result = _build_test_result(tc, b"\x02", 1.0, True)

        # Assert
        assert result["send_fmt"] == "Addr:H1"  # send format preserved
        assert result["expect_fmt"] == "Resp:H1"  # expect format preserved

    def test_no_format_specs_omitted(self) -> None:
        # Arrange
        tc = TestCase(index=1, name="No fmt", send_data=b"\x01",
                      expect_data=b"\x02", expect_mask=b"\xff")

        # Act
        result = _build_test_result(tc, b"\x02", 1.0, True)

        # Assert
        assert "send_fmt" not in result  # omitted when empty
        assert "expect_fmt" not in result  # omitted when empty


# -- Frame reading from FakeSerial -------------------------------------------


class TestReadFrame:
    def test_read_at_response(self) -> None:
        # Arrange
        dev = FakeSerial()
        dev.write(b"AT\r")
        time.sleep(0.05)  # let FakeSerial process

        # Act
        frame = _read_frame(dev, frame_gap_ms=50, timeout_ms=1000)

        # Assert
        assert b"OK" in frame  # AT command returns OK

    def test_timeout_returns_empty(self) -> None:
        # Arrange
        dev = FakeSerial()
        # Don't send anything — no response expected

        # Act
        frame = _read_frame(dev, frame_gap_ms=50, timeout_ms=100)

        # Assert
        assert frame == b""  # timeout → empty bytes


# -- Full test runner ---------------------------------------------------------


class TestRunProtoTests:
    @pytest.fixture
    def demo_cfg(self) -> dict:
        """Minimal config for demo device."""
        return {
            "port": "DEMO",
            "baud_rate": 115200,
            "byte_size": 8,
            "parity": "N",
            "stop_bits": 1,
            "flow_control": "none",
            "encoding": "utf-8",
            "line_ending": "\r",
        }

    @pytest.fixture
    def at_pro(self, tmp_path: Path) -> Path:
        """Create a minimal .pro test file."""
        content = '''\
name = "AT Test"
frame_gap = "100ms"

[[test]]
name = "AT basic"
send = '"AT\\r"'
expect = '"OK\\r\\n"'
'''
        pro_file = tmp_path / "at_test.pro"
        pro_file.write_text(content)
        return pro_file

    @pytest.fixture
    def mixed_pro(self, tmp_path: Path) -> Path:
        """Create a .pro file with a pass and a fail test."""
        content = '''\
name = "Mixed Test"
frame_gap = "100ms"

[[test]]
name = "AT pass"
send = '"AT\\r"'
expect = '"OK\\r\\n"'

[[test]]
name = "Expected fail"
send = '"AT\\r"'
expect = '"WRONG\\r\\n"'
'''
        pro_file = tmp_path / "mixed_test.pro"
        pro_file.write_text(content)
        return pro_file

    def test_all_pass(self, demo_cfg: dict, at_pro: Path,
                      tmp_path: Path) -> None:
        # Act
        results = run_proto_tests(at_pro, demo_cfg, output_dir=tmp_path)

        # Assert
        assert results["summary"]["total"] == 1  # one test
        assert results["summary"]["passed"] == 1  # passed
        assert results["summary"]["failed"] == 0  # no failures

    def test_json_file_written(self, demo_cfg: dict, at_pro: Path,
                               tmp_path: Path) -> None:
        # Act
        run_proto_tests(at_pro, demo_cfg, output_dir=tmp_path)

        # Assert
        json_files = list(tmp_path.glob("*.json"))
        assert len(json_files) == 1  # one result file created
        data = json.loads(json_files[0].read_text())
        assert "meta" in data  # has metadata
        assert "summary" in data  # has summary
        assert "tests" in data  # has test results
        assert "source" in data  # has source content

    def test_meta_fields(self, demo_cfg: dict, at_pro: Path,
                         tmp_path: Path) -> None:
        # Act
        results = run_proto_tests(at_pro, demo_cfg, output_dir=tmp_path)

        # Assert
        meta = results["meta"]
        assert meta["script"] == "at_test.pro"  # script filename
        assert meta["script_name"] == "AT Test"  # script name from TOML
        assert meta["port"] == "DEMO"  # port from config
        assert meta["baud_rate"] == 115200  # baud from config
        assert "timestamp" in meta  # has timestamp

    def test_mixed_pass_fail(self, demo_cfg: dict, mixed_pro: Path,
                             tmp_path: Path) -> None:
        # Act
        results = run_proto_tests(mixed_pro, demo_cfg, output_dir=tmp_path)

        # Assert
        assert results["summary"]["total"] == 2  # two tests
        assert results["summary"]["passed"] == 1  # one passed
        assert results["summary"]["failed"] == 1  # one failed
        assert results["tests"][0]["passed"] is True  # first test passed
        assert results["tests"][1]["passed"] is False  # second test failed

    def test_test_result_fields(self, demo_cfg: dict, at_pro: Path,
                                tmp_path: Path) -> None:
        # Act
        results = run_proto_tests(at_pro, demo_cfg, output_dir=tmp_path)

        # Assert
        test = results["tests"][0]
        assert test["index"] == 1  # test index
        assert test["name"] == "AT basic"  # test name
        assert test["passed"] is True  # passed
        assert "send_hex" in test  # has send hex
        assert "actual_hex" in test  # has actual hex
        assert test["elapsed_ms"] > 0  # timing recorded

    def test_custom_template(self, demo_cfg: dict, at_pro: Path,
                             tmp_path: Path) -> None:
        # Act
        run_proto_tests(at_pro, demo_cfg, output_dir=tmp_path,
                        template="{name}_custom.json")

        # Assert
        expected_file = tmp_path / "at_test_custom.json"
        assert expected_file.exists()  # custom filename used

    def test_output_dir_auto_created(self, demo_cfg: dict, at_pro: Path,
                                     tmp_path: Path) -> None:
        # Arrange
        out_dir = tmp_path / "nested" / "results"

        # Act
        run_proto_tests(at_pro, demo_cfg, output_dir=out_dir)

        # Assert
        assert out_dir.exists()  # directory created
        json_files = list(out_dir.glob("*.json"))
        assert len(json_files) == 1  # result file in nested dir

    def test_flat_format_rejected(self, demo_cfg: dict,
                                  tmp_path: Path) -> None:
        # Arrange — flat format .pro file (no [[test]] sections)
        flat_pro = tmp_path / "flat.pro"
        flat_pro.write_text("@timeout 1000ms\nlabel: test\nsend: 01 02\nexpect: 01 02\n")

        # Act / Assert
        with pytest.raises(ValueError, match="flat format"):
            run_proto_tests(flat_pro, demo_cfg, output_dir=tmp_path)

    def test_source_included(self, demo_cfg: dict, at_pro: Path,
                             tmp_path: Path) -> None:
        # Act
        results = run_proto_tests(at_pro, demo_cfg, output_dir=tmp_path)

        # Assert
        assert "AT Test" in results["source"]  # original .pro content included
        assert "[[test]]" in results["source"]  # TOML structure present
