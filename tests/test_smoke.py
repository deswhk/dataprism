"""Smoke tests — confirm the package and its key modules can be imported."""


def test_package_imports():
    """The dataprism package imports without errors."""
    import dataprism

    assert dataprism.__version__ == "0.1.0"
