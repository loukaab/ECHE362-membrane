"""Project pressure conventions; standard flow remains slpm throughout."""

import math

ATM_PSIA = 14.7
RESIDUAL_TOLERANCE = 1e-6


def finite(value: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def psia_to_psig(pressure: float) -> float:
    return finite(pressure, "Pressure") - ATM_PSIA


def psig_to_psia(pressure: float) -> float:
    return finite(pressure, "Pressure") + ATM_PSIA


def close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=RESIDUAL_TOLERANCE, abs_tol=RESIDUAL_TOLERANCE)
