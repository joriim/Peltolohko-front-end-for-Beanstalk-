"""Pisteet ja alueet peltolohkonumeroiksi Ruokaviraston lohkorekisteristä (INSPIRE-WFS).

Haku tehdään uusimmalta julkaistulta peruslohkotasolta (``inspire:LC.LandCoverSurfaces.LPIS.<vuosi>``)
OGC Filter Encodingin ``Intersects``-suodattimella EPSG:3067:ssa. Sama palvelu ja tasot kuin Beanstalk
Growth Analysisissa (``kasvukunto.lahteet.lpis``). Lähde: Ruokavirasto, CC BY 4.0.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from collections.abc import Callable

from .syote import Piste, Rengas

WFS = "https://inspire.ruokavirasto-awsa.com/geoserver/wfs"
PERUSLOHKOT = "inspire:LC.LandCoverSurfaces.LPIS.{vuosi}"
ENINTAAN = 200  # alueen lohkoja yhdellä kerralla (yksi analyysi per lohko)


def _hae(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "peltolohko/0.1"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8")


class Rekisteri:
    def __init__(self, url: str = WFS, hae: Callable[[str], str] = _hae):
        self.url, self._hae = url, hae
        self._vuosi: int | None = None

    def _osoite(self, **p) -> str:
        return self.url + "?" + urllib.parse.urlencode({"service": "WFS", "version": "2.0.0", **p})

    def vuosi(self) -> int:
        if self._vuosi is None:
            vuodet = [int(v) for v in re.findall(r"LPIS\.(\d{4})", self._hae(self._osoite(request="GetCapabilities")))]
            if not vuodet:
                raise LookupError("lohkorekisteri ei listaa peruslohkotasoja")
            self._vuosi = max(vuodet)
        return self._vuosi

    def _intersects(self, gml: str) -> list[dict]:
        suodatin = (
            '<fes:Filter xmlns:fes="http://www.opengis.net/fes/2.0" xmlns:gml="http://www.opengis.net/gml/3.2">'
            f"<fes:Intersects><fes:ValueReference>geom</fes:ValueReference>{gml}</fes:Intersects></fes:Filter>"
        )
        url = self._osoite(
            request="GetFeature",
            typeNames=PERUSLOHKOT.format(vuosi=self.vuosi()),
            outputFormat="application/json",
            srsName="EPSG:3067",
            count=ENINTAAN,
            FILTER=suodatin,
        )
        fc = json.loads(self._hae(url))
        return fc.get("features", [])

    @staticmethod
    def _tunnukset(features: list[dict]) -> list[str]:
        out = []
        for f in features:
            t = str((f.get("properties") or {}).get("PERUSLOHKOTUNNUS", "")).strip()
            if t and t not in out:
                out.append(t)
        return out

    def pisteessa(self, p: Piste) -> list[str]:
        e, n = p
        gml = f'<gml:Point srsName="EPSG:3067"><gml:pos>{e:.2f} {n:.2f}</gml:pos></gml:Point>'
        return self._tunnukset(self._intersects(gml))

    def alueella(self, r: Rengas) -> list[str]:
        # EPSG:3067:n akselijärjestys on E, N
        pos = " ".join(f"{e:.2f} {n:.2f}" for e, n in r)
        gml = (
            '<gml:Polygon srsName="EPSG:3067"><gml:exterior><gml:LinearRing>'
            f"<gml:posList>{pos}</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon>"
        )
        return self._tunnukset(self._intersects(gml))
