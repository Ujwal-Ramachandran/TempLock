"""Tests for the CLI module."""

import pytest

from ghost_in_the_template import EXIT_ERROR
from ghost_in_the_template.cli import build_parser, main


class TestBuildParser:
    """Test argument parser construction."""

    def test_version_flag(self):
        parser = build_parser()
        with pytest.raises(SystemExit, match="0"):
            parser.parse_args(["--version"])

    def test_integrity_command_requires_reference(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["integrity", "target.gguf"])

    def test_integrity_command_parses(self):
        parser = build_parser()
        args = parser.parse_args(["integrity", "target.gguf", "--reference", "ref.gguf"])
        assert args.command == "integrity"
        assert args.target == "target.gguf"
        assert args.reference == "ref.gguf"

    def test_structural_command_parses(self):
        parser = build_parser()
        args = parser.parse_args(["structural", "target.gguf"])
        assert args.command == "structural"

    def test_extract_command_parses(self):
        parser = build_parser()
        args = parser.parse_args(["extract", "file.gguf"])
        assert args.command == "extract"

    def test_format_option(self):
        parser = build_parser()
        args = parser.parse_args(["structural", "target.gguf", "--format", "json"])
        assert args.output_format == "json"


class TestMainNoArgs:
    """Test main with no arguments."""

    def test_no_command_returns_error(self):
        exit_code = main([])
        assert exit_code == EXIT_ERROR

    def test_nonexistent_file_returns_error(self):
        exit_code = main(["extract", "/nonexistent/file.gguf"])
        assert exit_code == EXIT_ERROR

    def test_integrity_nonexistent_returns_error(self):
        exit_code = main([
            "integrity",
            "/nonexistent/target.gguf",
            "--reference", "/nonexistent/ref.gguf",
        ])
        assert exit_code == EXIT_ERROR

    def test_structural_nonexistent_returns_error(self):
        exit_code = main(["structural", "/nonexistent/target.gguf"])
        assert exit_code == EXIT_ERROR
