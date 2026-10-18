"""Every method tool answers --help without doing anything: `fieldkit cards check` holds each card to its --help, and
mutate and mcp-probe refused it until 2026-10-18 because they wanted a command after -- first."""
import pytest

from fieldkit import cli


@pytest.mark.parametrize("name", sorted(cli.METHODS))
def test_every_method_answers_help(name, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main([name, "--help"])
    assert e.value.code == 0
    assert f"fieldkit {name}" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["mutate", "mcp-probe"])
def test_without_help_the_command_after_the_double_dash_is_still_required(name):
    assert cli.main([name, "x"]) == 2
