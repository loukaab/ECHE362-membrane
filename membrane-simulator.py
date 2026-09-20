from pathlib import Path
import numpy as np
import pandas as pd
from scipy.optimize import least_squares
import matplotlib.pyplot as plt

def membrane_stage(F, z, A, perm_O2, alpha, PR, PP=14.7):

    def residuals(x):
        VR, VP, yR, yP = x

        # Perfect-mixing RT equation
        yR_RT = yP * ((alpha - 1) * (PP / PR) * (1 - yP) + 1) / (alpha - yP * (alpha - 1))

        # Scale flow residuals by F so all residuals
        # are roughly comparable in magnitude
        r1 = (VR + VP - F) / F

        r2 = (
            yR * VR
            + yP * VP
            - z * F
        ) / F

        r3 = (
            yP * VP
            - perm_O2 * A
            * (yR * PR - yP * PP)
        ) / F

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

    solution = least_squares(
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


stage1 = membrane_stage(14285, 0.209, 4000, 0.031, 5.5, 115)
stage2 = membrane_stage(stage1["retentate_flow"], stage1["retentate_O2"], 4000, 0.031, 5.5, 115)
stage3 = membrane_stage(stage2["retentate_flow"], stage2["retentate_O2"], 4000, 0.031, 5.5, 115)
new = membrane_stage(stage3["retentate_flow"], stage3[retentate_O2], 2500, )

stages = [stage1, stage2, stage3]

stagedf = pd.DataFrame([
    {"stage": i, **stage}
    for i, stage in enumerate(stages, start=1)
])

print(stagedf)
