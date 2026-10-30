import pytest

from main import parse_args


def test_defaults():
    args = parse_args(["run"])
    assert (args.threshold, args.limit, args.send_live) == (90.0, 5, False)


@pytest.mark.parametrize("argv", [
    ["dedupe", "--threshold", "150"],
    ["dedupe", "--threshold", "-1"],
    ["notify", "--limit", "0"],
    ["notify", "--limit", "-3"],
    ["unknown-stage"],
])
def test_invalid_values_are_rejected(argv):
    with pytest.raises(SystemExit):
        parse_args(argv)


def test_plain_python_main_runs_the_full_pipeline():
    assert parse_args([]).stage == "run"
