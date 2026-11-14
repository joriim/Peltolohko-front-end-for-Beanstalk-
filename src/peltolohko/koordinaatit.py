"""WGS84 / ETRS89 (lat, lon) <-> ETRS-TM35FIN (EPSG:3067) ilman pyprojia.

TM35FIN on poikittainen Mercator GRS80-ellipsoidilla: keskimeridiaani 27° E, mittakaava 0,9996,
itäsiirtymä 500 000 m. Kaavat ovat Krügerin n-sarjat (Karney 2011, kuudes kertaluku), tarkkuus
alle millimetrin Suomen alueella. ETRS89 ja WGS84 eroavat alle metrin, mikä ei vaikuta lohkon hakuun.
"""

from __future__ import annotations

import math

A = 6378137.0
F = 1 / 298.257222101  # GRS80
K0 = 0.9996
LON0 = math.radians(27.0)
E0 = 500000.0

_n = F / (2 - F)
_A = A / (1 + _n) * (1 + _n**2 / 4 + _n**4 / 64 + _n**6 / 256)
_e = math.sqrt(F * (2 - F))
_ALPHA = (
    _n / 2 - 2 * _n**2 / 3 + 5 * _n**3 / 16 + 41 * _n**4 / 180 - 127 * _n**5 / 288 + 7891 * _n**6 / 37800,
    13 * _n**2 / 48 - 3 * _n**3 / 5 + 557 * _n**4 / 1440 + 281 * _n**5 / 630 - 1983433 * _n**6 / 1935360,
    61 * _n**3 / 240 - 103 * _n**4 / 140 + 15061 * _n**5 / 26880 + 167603 * _n**6 / 181440,
    49561 * _n**4 / 161280 - 179 * _n**5 / 168 + 6601661 * _n**6 / 7257600,
    34729 * _n**5 / 80640 - 3418889 * _n**6 / 1995840,
    212378941 * _n**6 / 319334400,
)
_BETA = (
    _n / 2 - 2 * _n**2 / 3 + 37 * _n**3 / 96 - _n**4 / 360 - 81 * _n**5 / 512 + 96199 * _n**6 / 604800,
    _n**2 / 48 + _n**3 / 15 - 437 * _n**4 / 1440 + 46 * _n**5 / 105 - 1118711 * _n**6 / 3870720,
    17 * _n**3 / 480 - 37 * _n**4 / 840 - 209 * _n**5 / 4480 + 5569 * _n**6 / 90720,
    4397 * _n**4 / 161280 - 11 * _n**5 / 504 - 830251 * _n**6 / 7257600,
    4583 * _n**5 / 161280 - 108847 * _n**6 / 3991680,
    20648693 * _n**6 / 638668800,
)


def tm35fin(lat: float, lon: float) -> tuple[float, float]:
    """(lat, lon) asteina -> (E, N) metreinä EPSG:3067:ssa."""
    phi, lam = math.radians(lat), math.radians(lon) - LON0
    t = math.sinh(math.atanh(math.sin(phi)) - _e * math.atanh(_e * math.sin(phi)))
    xi_ = math.atan2(t, math.cos(lam))
    eta_ = math.atanh(math.sin(lam) / math.sqrt(1 + t * t))
    xi, eta = xi_, eta_
    for j, a in enumerate(_ALPHA, 1):
        xi += a * math.sin(2 * j * xi_) * math.cosh(2 * j * eta_)
        eta += a * math.cos(2 * j * xi_) * math.sinh(2 * j * eta_)
    return E0 + K0 * _A * eta, K0 * _A * xi


def wgs84(e: float, n: float) -> tuple[float, float]:
    """(E, N) EPSG:3067 -> (lat, lon) asteina."""
    xi, eta = n / (K0 * _A), (e - E0) / (K0 * _A)
    xi_, eta_ = xi, eta
    for j, b in enumerate(_BETA, 1):
        xi_ -= b * math.sin(2 * j * xi) * math.cosh(2 * j * eta)
        eta_ -= b * math.cos(2 * j * xi) * math.sinh(2 * j * eta)
    chi = math.asin(math.sin(xi_) / math.cosh(eta_))
    lam = math.atan2(math.sinh(eta_), math.cos(xi_))
    # konforminen leveys -> geodeettinen (Newton)
    tau_ = math.tan(chi)
    tau = tau_
    for _ in range(6):
        s = math.sinh(_e * math.atanh(_e * tau / math.sqrt(1 + tau * tau)))
        t_ = tau * math.sqrt(1 + s * s) - s * math.sqrt(1 + tau * tau)
        d = (tau_ - t_) / math.sqrt(1 + t_ * t_) * (1 + (1 - _e * _e) * tau * tau) / ((1 - _e * _e) * math.sqrt(1 + tau * tau))
        tau += d
        if abs(d) < 1e-14:
            break
    return math.degrees(math.atan(tau)), math.degrees(lam + LON0)
