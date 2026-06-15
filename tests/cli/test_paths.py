"""Tests for dataprism.cli.paths.

These tests verify path resolution against the real project tree -
get_project_root() walks up from dataprism.__file__ and finds the
checkout. Tests run inside the project, so we know where everything
should be.
"""

from __future__ import annotations

from pathlib import Path

from dataprism.cli.paths import (
    get_audit_log_path,
    get_policy_path,
    get_project_root,
)


class TestGetProjectRoot:
    """Project root discovery walks up from dataprism.__file__."""

    def test_returns_path_object(self):
        """Returns a pathlib.Path, not a string."""
        root = get_project_root()
        assert isinstance(root, Path)

    def test_contains_pyproject_toml(self):
        """The returned path contains pyproject.toml."""
        root = get_project_root()
        assert (root / "pyproject.toml").exists()

    def test_contains_src_directory(self):
        """The returned path contains the src/ source tree."""
        root = get_project_root()
        assert (root / "src").is_dir()

    def test_contains_config_directory(self):
        """The returned path contains the config/ working files."""
        root = get_project_root()
        assert (root / "config").is_dir()

    def test_returns_absolute_path(self):
        """The returned path is absolute, not relative."""
        root = get_project_root()
        assert root.is_absolute()


class TestGetAuditLogPath:
    """Audit log path resolves to <project-root>/audit/audit.jsonl."""

    def test_returns_path_inside_project_root(self):
        """The audit log lives under the project root."""
        audit_path = get_audit_log_path()
        root = get_project_root()
        assert root in audit_path.parents

    def test_lives_in_audit_subdirectory(self):
        """The audit log is in an 'audit' directory."""
        audit_path = get_audit_log_path()
        assert audit_path.parent.name == "audit"

    def test_filename_is_audit_jsonl(self):
        """The file is named 'audit.jsonl'."""
        audit_path = get_audit_log_path()
        assert audit_path.name == "audit.jsonl"

    def test_creates_audit_directory(self):
        """Calling the function creates the audit/ directory if missing."""
        audit_path = get_audit_log_path()
        assert audit_path.parent.is_dir()


class TestGetPolicyPath:
    """Policy paths map name to config/policies/<name>.yaml."""

    def test_returns_path_in_config_policies(self):
        """Returned path is under config/policies/."""
        path = get_policy_path("example")
        assert path.parent.name == "policies"
        assert path.parent.parent.name == "config"

    def test_appends_yaml_extension(self):
        """The .yaml extension is appended to the name."""
        path = get_policy_path("example")
        assert path.suffix == ".yaml"

    def test_uses_name_as_stem(self):
        """The name parameter becomes the file's stem (filename without extension)."""
        path = get_policy_path("strict")
        assert path.stem == "strict"

    def test_does_not_check_existence(self):
        """The function returns a path even for nonexistent files."""
        # Should not raise
        path = get_policy_path("definitely-does-not-exist")
        assert isinstance(path, Path)
        # And confirm the file really doesn't exist
        assert not path.exists()
