"""Tests for dataprism.cli.paths.

These tests verify path resolution against the real project tree -
get_project_root() walks up from dataprism.__file__ and finds the
checkout. Tests run inside the project, so we know where everything
should be.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from dataprism.cli.paths import (
    get_audit_log_path,
    get_policy_path,
    get_project_root,
    get_report_path,
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


class TestGetReportPath:
    """Report paths map a timestamp to reports/<YYYY-MM-DD-HHMMSS>.html."""

    def test_returns_path_in_reports_directory(self):
        """Returned path is under <project-root>/reports/."""
        ts = datetime(2026, 6, 17, 14, 30, 45, tzinfo=timezone.utc)
        path = get_report_path(ts)
        assert path.parent.name == "reports"

    def test_appends_html_extension(self):
        """The .html extension is appended."""
        ts = datetime(2026, 6, 17, 14, 30, 45, tzinfo=timezone.utc)
        path = get_report_path(ts)
        assert path.suffix == ".html"

    def test_filename_encodes_timestamp(self):
        """The stem is the YYYY-MM-DD-HHMMSS encoding of the timestamp."""
        ts = datetime(2026, 6, 17, 14, 30, 45, tzinfo=timezone.utc)
        path = get_report_path(ts)
        assert path.stem == "2026-06-17-143045"

    def test_creates_reports_directory(self, tmp_path, monkeypatch):
        """Calling get_report_path() creates reports/ if absent.

        Uses monkeypatch to redirect the project root to a tmp_path
        so we can assert directory creation without touching the
        real project's reports/ folder.
        """
        # Create a sham project root that has pyproject.toml
        (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
        monkeypatch.setattr("dataprism.cli.paths.get_project_root", lambda: tmp_path)

        ts = datetime(2026, 6, 17, 14, 30, 45, tzinfo=timezone.utc)
        assert not (tmp_path / "reports").exists()
        path = get_report_path(ts)
        assert (tmp_path / "reports").is_dir()
        assert path == tmp_path / "reports" / "2026-06-17-143045.html"

    def test_does_not_create_file(self):
        """The function only creates the directory, not the file."""
        ts = datetime(2026, 6, 17, 14, 30, 45, tzinfo=timezone.utc)
        path = get_report_path(ts)
        assert not path.exists()  # the .html itself isn't created

    def test_different_timestamps_yield_different_filenames(self):
        """Two timestamps a minute apart produce different filenames."""
        ts1 = datetime(2026, 6, 17, 14, 30, 0, tzinfo=timezone.utc)
        ts2 = datetime(2026, 6, 17, 14, 31, 0, tzinfo=timezone.utc)
        assert get_report_path(ts1).name != get_report_path(ts2).name

    def test_naive_datetime_also_works(self):
        """A naive datetime (no tzinfo) is also accepted by strftime."""
        ts = datetime(2026, 6, 17, 14, 30, 45)  # no tzinfo
        path = get_report_path(ts)
        assert path.stem == "2026-06-17-143045"
