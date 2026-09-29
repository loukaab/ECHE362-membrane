"""Unchanged perfect-mixing solver extracted from membrane-data.ipynb.

Initialization, residual equations, bounds and scipy tolerances are preserved.
The checked wrapper in numerics.py validates returned residuals separately.
"""

import numpy as np
from scipy import optimize

def membrane_stage(F, z, A, perm_O2, alpha, PR, PP=14.7):

    def residuals(x):
        VR, VP, yR, yP = x

        # Perfect-mixing RT equation
        yR_RT = yP * ((alpha - 1) * (PP / PR) * (1 - yP) + 1) / (alpha - yP * (alpha - 1))

        # Scale flow residuals by F so all residuals
        # are roughly comparable in magnitude
        r1 = (VR + VP - F) / F

        r2 = (yR * VR + yP * VP - z * F) / F

        r3 = (yP * VP - perm_O2 * A * (yR * PR - yP * PP)) / F

        r4 = yR - yR_RT

        return [r1, r2, r3, r4]

    # Initial guess
    theta_guess = 0.25

    VP_guess = theta_guess * F
    VR_guess = F - VP_guess

    yP_guess = min(z * 1.8, 0.95)

    yR_guess = (
        z * F - yP_guess * VP_guess
    ) / VR_guess

    yR_guess = np.clip(yR_guess, 0.001, 0.999)

    x0 = [
        VR_guess,
        VP_guess,
        yR_guess,
        yP_guess,
    ]

    # Physical bounds
    lower = [0, 0, 0, 0]
    upper = [F, F, 1, 1]

    solution = optimize.least_squares(
        residuals,
        x0,
        bounds=(lower, upper)
    )

    if not solution.success:
        raise RuntimeError("Membrane solver did not converge")

    VR, VP, yR, yP = solution.x

    return {
        "retentate_flow": VR,
        "permeate_flow": VP,
        "retentate_O2": yR,
        "permeate_O2": yP,
        "stage_cut": VP / F,
    }
