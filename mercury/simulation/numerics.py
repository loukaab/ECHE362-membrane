"""Existing compressor methods and independent membrane-result checks."""

from pathlib import Path

import numpy as np
import pandas as pd

from .membrane import membrane_stage
from .models import MembraneProperties, Stream
from .units import ATM_PSIA, RESIDUAL_TOLERANCE, finite

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def compressor(flowrate: float, rpm: int, Pin: float = ATM_PSIA, *, data: pd.DataFrame) -> dict[str, float]:
    """Notebook interpolation, with the compressor dataframe explicitly supplied."""
    if str(rpm) not in data:
        raise ValueError(f"No compressor curve for {rpm} rpm")
    curve = data[["V", str(rpm)]].dropna()
    min_flow, max_flow = curve["V"].min(), curve["V"].max()
    if not min_flow <= flowrate <= max_flow:
        raise ValueError(f"{rpm} rpm curve only covers {min_flow:.0f} to {max_flow:.0f} slpm")
    p_ratio = np.interp(flowrate, curve["V"], curve[str(rpm)])
    return {"p_out": float(p_ratio * Pin), "p_ratio": float(p_ratio)}


def ideal_compressor_power(flow_slpm: float, p_ratio: float, T_in: float = 298.15,
                           gamma: float = 1.4) -> float:
    """Notebook ideal isentropic power; not motor power. Same 22.414 L/mol basis."""
    R = 8.314
    Vm_std = 22.414
    n_dot = flow_slpm / Vm_std / 60
    power_W = n_dot * gamma / (gamma - 1) * R * T_in * (p_ratio ** ((gamma - 1) / gamma) - 1)
    return power_W / 1000


class CompressorMap:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else PROJECT_ROOT / "data" / "comp_data.csv"
        self.data = pd.read_csv(self.path)
        self.data.columns = self.data.columns.str.strip()
        self.speeds = tuple(int(c) for c in self.data.columns if c.isdigit())
        if "V" not in self.data or not self.speeds:
            raise ValueError("Compressor CSV needs V and numeric RPM columns")
        for rpm in self.speeds:
            curve = self.data[["V", str(rpm)]].dropna().to_numpy(dtype=float)
            if (len(curve) < 2 or not np.isfinite(curve).all() or (curve[:, 0] < 0).any()
                    or not (np.diff(curve[:, 0]) > 0).all()
                    or not (np.diff(curve[:, 1]) < 0).all() or (curve[:, 1] <= 1).any()):
                raise ValueError(f"Invalid or noninvertible compressor curve at {rpm} rpm")

    def limits(self, rpm: int) -> tuple[float, float]:
        curve = self._curve(rpm)
        return float(curve["V"].min()), float(curve["V"].max())

    def _curve(self, rpm: int) -> pd.DataFrame:
        if rpm not in self.speeds:
            raise ValueError(f"RPM must be one of {self.speeds}")
        return self.data[["V", str(rpm)]].dropna()

    def forward(self, flow_slpm: float, rpm: int, inlet_psia: float) -> dict[str, float]:
        self._curve(rpm)
        finite(flow_slpm, "Compressor flow")
        if finite(inlet_psia, "Compressor inlet pressure") <= 0:
            raise ValueError("Compressor inlet pressure must be positive psia")
        return compressor(flow_slpm, rpm, inlet_psia, data=self.data)

    def inverse(self, outlet_psia: float, rpm: int, inlet_psia: float) -> float:
        if finite(inlet_psia, "Compressor inlet pressure") <= 0:
            raise ValueError("Compressor inlet pressure must be positive psia")
        finite(outlet_psia, "Compressor outlet pressure")
        curve = self._curve(rpm)
        ratios = curve[str(rpm)].to_numpy(dtype=float)
        ratio = outlet_psia / inlet_psia
        low, high = ratios[-1], ratios[0]
        # Only absorb floating-point roundoff at the endpoints, never extrapolate.
        if ratio < low and np.isclose(ratio, low, rtol=1e-12, atol=0):
            ratio = low
        if ratio > high and np.isclose(ratio, high, rtol=1e-12, atol=0):
            ratio = high
        if not low <= ratio <= high:
            raise ValueError(f"{rpm} rpm outlet pressure must be {low * inlet_psia:.3f}–"
                             f"{high * inlet_psia:.3f} psia at this inlet pressure")
        return float(np.interp(ratio, ratios[::-1], curve["V"].to_numpy()[::-1]))


def checked_membrane(feed: Stream, p: MembraneProperties) -> dict[str, float]:
    F, z, PR, PP = feed.flow_slpm, feed.oxygen, feed.pressure_psia, p.permeate_pressure_psia
    if F <= 0:
        raise ValueError("Membrane requires positive feed flow; its equations divide by feed flow")
    if PR <= PP:
        raise ValueError("Membrane inlet pressure must exceed permeate pressure; add compression if needed")
    result = membrane_stage(F, z, p.area_m2, p.oxygen_permeance, p.alpha, PR, PP)
    R, P, x, y, theta = (result[k] for k in
                         ("retentate_flow", "permeate_flow", "retentate_O2", "permeate_O2", "stage_cut"))
    if (not np.isfinite([R, P, x, y, theta]).all() or not (0 <= R <= F and 0 <= P <= F)
            or not (0 <= x <= 1 and 0 <= y <= 1 and 0 <= theta <= 1)):
        raise ValueError("Membrane solver returned nonfinite or nonphysical values")
    a = p.alpha
    x_model = y * ((a - 1) * (PP / PR) * (1 - y) + 1) / (a - y * (a - 1))
    residuals = [(R + P - F) / F, (R * x + P * y - F * z) / F,
                 (P * y - p.oxygen_permeance * p.area_m2 * (x * PR - y * PP)) / F,
                 x - x_model, theta - P / F]
    residual = max(abs(value) for value in residuals)
    if residual > RESIDUAL_TOLERANCE:
        raise ValueError(f"Membrane equation residual {residual:.3g} exceeds {RESIDUAL_TOLERANCE:g}; "
                         "the solver did not find an acceptable physical solution")
    return {key: float(value) for key, value in result.items()}
