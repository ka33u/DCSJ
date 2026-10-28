"""Exact stock identity must survive lookup reuse and legacy float32 inputs."""
import math
from types import SimpleNamespace

import pytest

from dcsj import manufacturing as stock


@pytest.mark.parametrize('sizes', [[.71, .8, 1.06], [0., 1., 2.], [1e-6, 1000000.],
                                  [i / 100 + .123456789 for i in range(64)]])
def test_exact_and_float32_values_only(sizes):
    variable = SimpleNamespace(values=sizes)
    allowed = {value for x in sizes for value in (x, stock.single(x))}
    for nominal in sizes:
        assert stock.nominal(variable, nominal) == nominal
        assert stock.nominal(variable, stock.single(nominal)) == nominal
        for value in (nominal, stock.single(nominal)):
            for direction in (-math.inf, math.inf):
                neighbor = math.nextafter(value, direction)
                if neighbor not in allowed:
                    assert stock.nominal(variable, neighbor) is None
    for value in (math.nan, math.inf, -math.inf, [], {}):
        assert stock.nominal(variable, value) is None


def test_list_edits_and_first_match_precedence_do_not_reuse_stale_values():
    exact, encoded = .8, stock.single(.8)
    variable = SimpleNamespace(values=[exact, encoded])
    assert stock.nominal(variable, encoded) == exact
    variable.values.reverse()
    assert stock.nominal(variable, encoded) == encoded
    variable.values[:] = [.71, 1.06]
    assert stock.nominal(variable, encoded) is None
    assert stock.nominal(variable, stock.single(.71)) == .71
    variable.values = None
    assert stock.nominal(variable, .71) is None


def test_matching_zero_retains_the_declared_sign():
    for zero in (-0., 0.):
        variable = SimpleNamespace(values=[zero])
        # Equal-valued cache keys must not replace the chosen specification's
        # sign, which is observable in persisted raw numeric inputs.
        assert math.copysign(1., stock.nominal(variable, 0.)) == math.copysign(1., zero)
