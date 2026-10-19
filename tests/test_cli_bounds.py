"""The direct CLIs refuse numbers the plain-English path would never pass on.

Live 10-19: find_spot.py took --reps 0 and searched for a "0x10" plan, took
--rep-minutes -5 and looked for a stretch "~-1.7 mi long", dropped --watts 0
and --total-kg 0 without a word, and answered --max-stops -1 with "allow a
stop". The parsed path already bounds these (policy.PARSE_BOUNDS); the CLIs
now share those bounds and say which flag is wrong before any lookup.
"""
import sys

import pytest

import compose_route
import find_spot

SPOT = ["find_spot.py", "--address", "Monona Terrace, Madison WI",
        "--reps", "2", "--rep-minutes", "10", "--kind", "flat"]
ROUTE = ["compose_route.py", "--address", "Monona Terrace, Madison WI", "--miles", "20"]


def run(monkeypatch, module, argv, capsys):
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as stopped:
        module.main()
    return stopped.value.code, capsys.readouterr().err


@pytest.mark.parametrize("flag,value", [
    ("--reps", "0"), ("--rep-minutes", "-5"), ("--max-travel-minutes", "0"),
    ("--watts", "0"), ("--total-kg", "0"), ("--max-stops", "-1"), ("--rep-minutes", "nan"),
])
def test_find_spot_refuses_a_nonsense_number(monkeypatch, capsys, flag, value):
    monkeypatch.setattr(find_spot, "run_spot_search",
                        lambda *a, **kw: pytest.fail("searched with a nonsense number"))
    args = [a for a in SPOT]
    if flag in args:
        args[args.index(flag) + 1] = value
    else:
        args += [flag, value]
    code, err = run(monkeypatch, find_spot, args, capsys)
    assert code == 2
    assert flag in err and "must be between" in err


def test_find_spot_passes_sensible_numbers_through(monkeypatch):
    seen = []
    monkeypatch.setattr(find_spot, "run_spot_search", lambda spec, *a, **kw: seen.append(spec) or [1])
    monkeypatch.setattr(sys, "argv", SPOT + ["--watts", "250", "--total-kg", "86", "--max-stops", "0"])
    assert find_spot.main() == 0
    spec = seen[0]
    assert (spec.reps, spec.rep_minutes, spec.watts, spec.total_kg, spec.max_stops) == (2, 10.0, 250.0, 86.0, 0)


@pytest.mark.parametrize("flag,value", [("--miles", "0"), ("--miles", "-20"), ("--max-climb-ft", "-1")])
def test_compose_route_refuses_a_nonsense_number(monkeypatch, capsys, flag, value):
    monkeypatch.setattr(compose_route, "resolve_avoid",
                        lambda *a, **kw: pytest.fail("looked up places with a nonsense number"))
    args = [a for a in ROUTE]
    if flag in args:
        args[args.index(flag) + 1] = value
    else:
        args += [flag, value]
    code, err = run(monkeypatch, compose_route, args, capsys)
    assert code == 2
    assert flag in err and "must be between" in err
