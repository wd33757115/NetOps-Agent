from importlib.metadata import PackageNotFoundError
from types import SimpleNamespace

import pytest

from src.platform.kernel import cli, dependencies


def test_preflight_reports_missing_package_and_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["cli", "check-dependencies"])
    monkeypatch.setattr(cli, "discover", lambda: {"assistant": SimpleNamespace(id="assistant")})

    def missing(manifest):
        raise PackageNotFoundError("deepseek-harness-sdk")

    monkeypatch.setattr(dependencies, "check_dependencies", missing)
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 1
    output = capsys.readouterr().out
    assert "assistant" in output and "deepseek-harness-sdk" in output


def test_preflight_checks_each_discovered_package(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["cli", "check-dependencies"])
    monkeypatch.setattr(
        cli,
        "discover",
        lambda: {name: SimpleNamespace(id=name) for name in ["assistant", "inspection"]},
    )
    checked = []
    monkeypatch.setattr(
        dependencies, "check_dependencies", lambda manifest: checked.append(manifest.id)
    )
    cli.main()
    assert checked == ["assistant", "inspection"]
    assert capsys.readouterr().out.count("[OK]") == 2
