"""SRE-Bench: a benchmark for AI SRE agents.

Extracted from the eval framework of pulse-agent (https://github.com/PulseSRE/pulse-agent).
"""

from .loader import load_submission, load_suite, suite_names
from .rubric import DEFAULT_RUBRIC, Rubric
from .scoring import score_reference, score_submission
from .types import Expected, ScenarioScore, SuiteResult, Task, Trajectory

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_RUBRIC",
    "Expected",
    "Rubric",
    "ScenarioScore",
    "SuiteResult",
    "Task",
    "Trajectory",
    "load_submission",
    "load_suite",
    "score_reference",
    "score_submission",
    "suite_names",
    "__version__",
]
