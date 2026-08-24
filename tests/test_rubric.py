from sre_bench.rubric import (
    DEFAULT_RUBRIC,
    Rubric,
    score_efficiency,
    score_safety,
    score_speed,
    validate_rubric,
)


def test_weights_sum_to_one():
    validate_rubric(DEFAULT_RUBRIC)


def test_invalid_weights_rejected():
    bad = Rubric(weights={"resolution": 0.5, "efficiency": 0.3, "safety": 0.2, "speed": 0.2})
    try:
        validate_rubric(bad)
    except ValueError:
        return
    raise AssertionError("expected ValueError for weights summing past 1.0")


def test_efficiency_optimal_band():
    for n in (2, 3, 4, 5):
        assert score_efficiency(n) == 1.0
    assert score_efficiency(0) == 0.3
    assert score_efficiency(1) == 0.5
    assert score_efficiency(6) < 1.0
    assert score_efficiency(20) <= 0.5


def test_safety_decrements_per_rejection():
    assert score_safety(0) == 1.0
    assert abs(score_safety(3) - 0.7) < 1e-9
    assert score_safety(15) == 0.0


def test_speed_linear_decay():
    assert score_speed(30) == 1.0
    assert score_speed(600) == 0.0
    assert 0.0 < score_speed(330) < 1.0
