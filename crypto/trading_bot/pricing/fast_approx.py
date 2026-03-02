"""
Fast analytical approximation for Student-t touch probabilities.

Uses GBM first-passage-time formula as base, with a 2D lookup table
correction calibrated against MC Student-t (2M paths per point).

Method:
  1. Compute GBM touch probability (exact, ~1µs)
  2. Look up correction ratio from pre-computed table (bilinear interp, ~1µs)
  3. Return GBM * correction

Accuracy: mean abs error ~0.9%, max ~2% vs MC 1M paths
Speed: ~7ms for 108 markets (vs ~30s for MC)

The correction ratio depends on:
  - x = |ln(K/S)| / (sigma * sqrt(T))  -- normalized barrier distance
  - n = days to expiry (CLT convergence rate)
  - df -- Student-t degrees of freedom (determines table)

Tables calibrated with mu=0 (risk-neutral). Correction is approximately
invariant to drift and sigma (error ~1-2%).
"""

import math
from typing import Dict, List, Tuple

import numpy as np
from scipy.stats import norm


# === Pre-computed correction tables ===
# ratio[i][j] = MC_student_t / GBM for (x_grid[i], n_grid[j])
# Calibrated with 2M MC paths, seed=42+n_days, S=100, sigma=0.52 (BTC) / 0.70 (ETH)

_X_GRID = np.array([
    0.02, 0.05, 0.08, 0.10, 0.13, 0.15, 0.18, 0.20,
    0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60,
    0.70, 0.80, 0.90, 1.00, 1.10, 1.20, 1.30, 1.50,
    1.70, 2.00, 2.30, 2.60, 3.00,
])

_N_GRID = np.array([3, 5, 7, 10, 14, 21, 29, 45, 60, 90, 120, 180, 240, 305, 365])
_LN_N_GRID = np.log(_N_GRID.astype(float))

# BTC: df=2.61, calibrated with sigma=0.52
_RATIO_BTC = np.array([
    [0.680,0.747,0.784,0.818,0.845,0.872,0.890,0.910,0.922,0.935,0.942,0.952,0.957,0.961,0.963],
    [0.669,0.736,0.773,0.807,0.834,0.862,0.880,0.901,0.912,0.925,0.933,0.942,0.947,0.951,0.954],
    [0.656,0.723,0.761,0.795,0.823,0.851,0.869,0.891,0.902,0.916,0.924,0.934,0.939,0.943,0.946],
    [0.647,0.714,0.753,0.787,0.815,0.843,0.862,0.884,0.896,0.910,0.919,0.929,0.934,0.938,0.941],
    [0.633,0.701,0.739,0.774,0.802,0.831,0.851,0.874,0.887,0.901,0.911,0.921,0.927,0.931,0.934],
    [0.623,0.691,0.730,0.765,0.794,0.824,0.844,0.867,0.881,0.896,0.906,0.916,0.923,0.926,0.929],
    [0.609,0.677,0.716,0.751,0.781,0.812,0.834,0.858,0.872,0.888,0.898,0.910,0.916,0.920,0.922],
    [0.599,0.667,0.707,0.743,0.773,0.805,0.827,0.852,0.866,0.883,0.893,0.905,0.912,0.915,0.918],
    [0.574,0.643,0.683,0.721,0.753,0.786,0.810,0.837,0.852,0.870,0.881,0.894,0.901,0.904,0.907],
    [0.550,0.619,0.661,0.700,0.733,0.768,0.793,0.822,0.839,0.858,0.870,0.883,0.890,0.894,0.896],
    [0.526,0.596,0.638,0.679,0.714,0.751,0.777,0.808,0.825,0.846,0.858,0.872,0.879,0.883,0.885],
    [0.503,0.574,0.617,0.658,0.695,0.733,0.761,0.794,0.812,0.833,0.846,0.861,0.868,0.872,0.874],
    [0.482,0.552,0.596,0.639,0.676,0.716,0.745,0.779,0.798,0.821,0.835,0.850,0.857,0.861,0.862],
    [0.462,0.532,0.576,0.619,0.658,0.700,0.729,0.765,0.785,0.809,0.823,0.839,0.846,0.849,0.850],
    [0.443,0.512,0.557,0.601,0.641,0.683,0.714,0.751,0.772,0.796,0.811,0.827,0.835,0.838,0.839],
    [0.425,0.494,0.538,0.583,0.623,0.667,0.699,0.737,0.759,0.784,0.800,0.816,0.823,0.826,0.826],
    [0.393,0.460,0.505,0.549,0.591,0.636,0.669,0.709,0.733,0.760,0.776,0.793,0.800,0.803,0.801],
    [0.367,0.431,0.475,0.519,0.561,0.607,0.641,0.683,0.708,0.736,0.753,0.770,0.776,0.778,0.775],
    [0.346,0.406,0.448,0.492,0.534,0.580,0.615,0.658,0.684,0.713,0.730,0.747,0.753,0.753,0.749],
    [0.329,0.386,0.427,0.469,0.510,0.556,0.591,0.635,0.660,0.690,0.707,0.724,0.729,0.728,0.722],
    [0.317,0.371,0.410,0.450,0.489,0.534,0.569,0.613,0.639,0.669,0.686,0.702,0.705,0.703,0.696],
    [0.309,0.360,0.396,0.435,0.472,0.517,0.551,0.594,0.620,0.649,0.665,0.680,0.682,0.678,0.669],
    [0.306,0.353,0.388,0.424,0.460,0.503,0.535,0.577,0.603,0.632,0.647,0.659,0.660,0.654,0.642],
    [0.313,0.354,0.384,0.416,0.449,0.487,0.514,0.555,0.578,0.602,0.615,0.622,0.618,0.607,0.592],
    [0.339,0.374,0.402,0.428,0.456,0.489,0.512,0.547,0.566,0.587,0.593,0.594,0.581,0.566,0.545],
    [0.427,0.455,0.475,0.495,0.515,0.538,0.551,0.573,0.583,0.593,0.585,0.570,0.544,0.518,0.489],
    [0.615,0.632,0.643,0.652,0.662,0.670,0.669,0.668,0.666,0.651,0.625,0.585,0.537,0.499,0.456],
    [0.995,0.994,0.996,0.981,0.976,0.957,0.933,0.892,0.863,0.804,0.745,0.663,0.579,0.517,0.460],
    [2.261,2.213,2.162,2.059,1.991,1.884,1.787,1.620,1.517,1.320,1.177,0.949,0.776,0.648,0.547],
])

# ETH: df=2.88, calibrated with sigma=0.70
_RATIO_ETH = np.array([
    [0.682,0.749,0.786,0.820,0.846,0.874,0.892,0.913,0.924,0.937,0.944,0.953,0.958,0.961,0.964],
    [0.674,0.741,0.779,0.812,0.840,0.867,0.885,0.906,0.917,0.930,0.937,0.946,0.950,0.953,0.955],
    [0.664,0.732,0.770,0.804,0.831,0.859,0.877,0.899,0.910,0.923,0.930,0.939,0.943,0.946,0.948],
    [0.658,0.726,0.764,0.798,0.826,0.854,0.872,0.894,0.905,0.918,0.926,0.935,0.939,0.942,0.943],
    [0.648,0.716,0.754,0.789,0.816,0.845,0.864,0.886,0.898,0.912,0.920,0.929,0.933,0.935,0.937],
    [0.640,0.709,0.747,0.782,0.810,0.840,0.859,0.881,0.894,0.907,0.916,0.925,0.929,0.931,0.932],
    [0.629,0.698,0.737,0.772,0.801,0.831,0.851,0.874,0.887,0.901,0.910,0.919,0.923,0.925,0.926],
    [0.622,0.691,0.730,0.765,0.795,0.825,0.846,0.870,0.883,0.898,0.906,0.916,0.919,0.921,0.921],
    [0.603,0.672,0.712,0.748,0.779,0.812,0.833,0.858,0.872,0.888,0.897,0.906,0.910,0.911,0.910],
    [0.584,0.653,0.694,0.732,0.764,0.798,0.821,0.848,0.862,0.879,0.888,0.897,0.900,0.900,0.899],
    [0.565,0.634,0.677,0.716,0.750,0.785,0.809,0.837,0.852,0.869,0.879,0.887,0.890,0.889,0.887],
    [0.546,0.616,0.660,0.700,0.735,0.772,0.796,0.826,0.842,0.860,0.870,0.878,0.880,0.878,0.875],
    [0.528,0.599,0.643,0.685,0.721,0.759,0.784,0.815,0.833,0.851,0.860,0.868,0.870,0.867,0.862],
    [0.511,0.582,0.627,0.670,0.707,0.746,0.773,0.805,0.822,0.842,0.851,0.859,0.859,0.855,0.850],
    [0.495,0.566,0.611,0.655,0.692,0.734,0.761,0.795,0.812,0.832,0.842,0.849,0.848,0.843,0.836],
    [0.479,0.550,0.596,0.641,0.679,0.721,0.750,0.784,0.802,0.823,0.832,0.838,0.837,0.831,0.822],
    [0.451,0.522,0.568,0.613,0.654,0.697,0.727,0.764,0.782,0.803,0.813,0.817,0.813,0.805,0.794],
    [0.427,0.496,0.542,0.588,0.629,0.674,0.706,0.744,0.763,0.784,0.793,0.796,0.789,0.777,0.763],
    [0.407,0.474,0.519,0.565,0.607,0.653,0.686,0.724,0.744,0.765,0.773,0.774,0.764,0.749,0.732],
    [0.392,0.456,0.500,0.545,0.588,0.633,0.666,0.706,0.725,0.746,0.752,0.751,0.738,0.719,0.699],
    [0.380,0.441,0.484,0.528,0.570,0.616,0.649,0.689,0.708,0.728,0.732,0.728,0.712,0.689,0.667],
    [0.372,0.430,0.472,0.514,0.555,0.600,0.634,0.672,0.692,0.710,0.714,0.705,0.686,0.659,0.634],
    [0.368,0.423,0.464,0.504,0.544,0.588,0.621,0.659,0.678,0.694,0.695,0.682,0.659,0.630,0.601],
    [0.375,0.424,0.461,0.496,0.533,0.573,0.603,0.637,0.653,0.664,0.660,0.639,0.607,0.571,0.536],
    [0.402,0.445,0.477,0.508,0.538,0.573,0.600,0.627,0.637,0.641,0.631,0.598,0.557,0.515,0.476],
    [0.493,0.526,0.548,0.570,0.590,0.613,0.629,0.639,0.639,0.627,0.602,0.549,0.493,0.441,0.396],
    [0.688,0.704,0.712,0.719,0.719,0.726,0.726,0.706,0.687,0.647,0.602,0.518,0.444,0.383,0.335],
    [1.075,1.061,1.047,1.020,0.998,0.964,0.933,0.859,0.808,0.722,0.641,0.516,0.421,0.348,0.293],
    [2.344,2.230,2.136,1.992,1.885,1.715,1.581,1.364,1.207,0.994,0.816,0.594,0.441,0.341,0.269],
])


def _bilinear_interp(x: float, ln_n: float, table: np.ndarray) -> float:
    """Fast bilinear interpolation on (x_grid, ln_n_grid) → ratio.

    Uses numpy searchsorted + manual bilinear — avoids scipy overhead.
    Clamps to grid boundaries (no extrapolation).
    """
    # Clamp inputs
    x = max(_X_GRID[0], min(_X_GRID[-1], x))
    ln_n = max(_LN_N_GRID[0], min(_LN_N_GRID[-1], ln_n))

    # Find surrounding indices
    ix = int(np.searchsorted(_X_GRID, x, side='right')) - 1
    ix = max(0, min(ix, len(_X_GRID) - 2))
    jn = int(np.searchsorted(_LN_N_GRID, ln_n, side='right')) - 1
    jn = max(0, min(jn, len(_LN_N_GRID) - 2))

    # Fractional positions
    x0, x1 = _X_GRID[ix], _X_GRID[ix + 1]
    n0, n1 = _LN_N_GRID[jn], _LN_N_GRID[jn + 1]

    fx = (x - x0) / (x1 - x0) if x1 != x0 else 0.0
    fn = (ln_n - n0) / (n1 - n0) if n1 != n0 else 0.0

    # Bilinear
    v00 = table[ix, jn]
    v10 = table[ix + 1, jn]
    v01 = table[ix, jn + 1]
    v11 = table[ix + 1, jn + 1]

    return (v00 * (1 - fx) * (1 - fn) +
            v10 * fx * (1 - fn) +
            v01 * (1 - fx) * fn +
            v11 * fx * fn)


# === GBM first-passage formulas ===

def touch_above_gbm(S: float, K: float, sigma: float, T: float, mu: float = 0) -> float:
    """P(max_{0..T} S_t >= K | S_0 = S) under GBM with drift mu."""
    if K <= S:
        return 1.0
    if T <= 0 or sigma <= 0:
        return 0.0
    ln = math.log(K / S)
    st = sigma * math.sqrt(T)
    drift = mu - 0.5 * sigma**2
    if abs(drift) < 1e-10:
        return 2 * (1 - norm.cdf(ln / st))
    d1 = (-ln + drift * T) / st
    d2 = (-ln - drift * T) / st
    exp = min(2 * drift * ln / sigma**2, 100)
    return min(norm.cdf(d1) + math.exp(exp) * norm.cdf(d2), 1.0)


def touch_below_gbm(S: float, K: float, sigma: float, T: float, mu: float = 0) -> float:
    """P(min_{0..T} S_t <= K | S_0 = S) under GBM with drift mu."""
    if K >= S:
        return 1.0
    if T <= 0 or sigma <= 0:
        return 0.0
    drift = mu - 0.5 * sigma**2
    ln_ks = math.log(K / S)
    st = sigma * math.sqrt(T)
    if abs(drift) < 1e-10:
        return 2 * (1 - norm.cdf(-ln_ks / st))
    d1 = (ln_ks + drift * T) / st
    d2 = (ln_ks - drift * T) / st
    exp = min(2 * drift * ln_ks / sigma**2, 100)
    return min(norm.cdf(d1) + math.exp(exp) * norm.cdf(d2), 1.0)


# === Main API ===

def fast_touch_prob(
    spot: float,
    strike: float,
    iv: float,
    T: float,
    drift: float = 0.0,
    df: float = 2.61,
) -> float:
    """Fast Student-t touch probability using GBM + lookup table correction.

    Computes GBM first-passage analytically, then applies a correction ratio
    from a pre-calibrated lookup table to approximate MC Student-t results.

    Args:
        spot: Current spot price
        strike: Barrier level
        iv: Annualized implied volatility
        T: Time to expiry in years (days/365)
        drift: Annualized drift (from futures curve)
        df: Student-t degrees of freedom (2.61 for BTC, 2.88 for ETH)

    Returns:
        Approximate touch probability matching MC Student-t within ~1-2%
    """
    if T <= 0 or iv <= 0:
        if strike > spot:
            return 0.0
        else:
            return 1.0

    is_up = strike > spot

    # GBM analytical
    if is_up:
        gbm = touch_above_gbm(spot, strike, iv, T, mu=drift)
    else:
        gbm = touch_below_gbm(spot, strike, iv, T, mu=drift)

    # For extreme probabilities, no correction needed
    if gbm < 0.001 or gbm > 0.999:
        return gbm

    # Normalized barrier distance
    st = iv * math.sqrt(T)
    x = abs(math.log(strike / spot)) / st
    n_days = max(int(T * 365), 1)
    ln_n = math.log(n_days)

    # Select correction table
    table = _RATIO_BTC if df < 2.75 else _RATIO_ETH

    # Interpolate correction ratio
    correction = _bilinear_interp(x, ln_n, table)

    return max(0.0, min(1.0, gbm * correction))


def batch_fast_touch_probabilities(
    spot: float,
    iv: float,
    days: int,
    strikes_above: List[float],
    strikes_below: List[float],
    drift: float = 0.0,
    df: float = 2.61,
) -> Tuple[Dict[float, float], Dict[float, float]]:
    """Batch version matching the MC API signature.

    Drop-in replacement for batch_touch_probabilities() from touch_prob.py.
    """
    T = days / 365 if days > 0 else 0

    above_probs = {}
    for strike in strikes_above:
        if strike <= spot:
            above_probs[strike] = 1.0
        else:
            above_probs[strike] = fast_touch_prob(spot, strike, iv, T, drift, df)

    below_probs = {}
    for strike in strikes_below:
        if strike >= spot:
            below_probs[strike] = 1.0
        else:
            below_probs[strike] = fast_touch_prob(spot, strike, iv, T, drift, df)

    return above_probs, below_probs
