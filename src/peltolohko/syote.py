"""Syötteen tulkinta: peltolohkonumerot, koordinaatit, alueet ja tiedostot.

Yksi tekstikenttä ottaa vastaan kaiken:

- **peltolohkonumero** (peruslohkotunnus, 10 numeroa), yksi tai useampi: ``1641431471``,
  ``1641431471-2`` (kasvulohko), ``164 143 1471``;
- **piste**: rivillä yksi koordinaattipari, WGS84 (``62.79, 22.84``, ``62,79 22,84``, ``62°47'N 22°50'E``)
  tai ETRS-TM35FIN (``N 6963000 E 288000``). Järjestys päätellään arvoista;
- **alue**: rivillä kaksi paria (``;`` saa erottaa parit) (suorakaide kulmista) tai vähintään kolme paria (monikulmio), WKT
  ``POLYGON((...))`` tai GeoJSON-geometria;
- **tiedostot**: satodata (CSV/TXT, Excel, ISOXML TASKDATA, Shapefile-zip, GeoPackage, pistemuotoinen
  GeoJSON) tai rajaus (KML, monikulmio-GeoJSON).

Kaikki geometriat palautetaan ETRS-TM35FIN-koordinaateissa (E, N), joissa lohkorekisteriä haetaan.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .koordinaatit import tm35fin

Piste = tuple[float, float]  # (E, N) EPSG:3067
Rengas = list[Piste]

_LOHKO = re.compile(r"(?<!\d)(?<!\d\.)(\d{3}\s?\d{3}\s?\d{4})(?:\s*[-/]\s*(\d{1,3}))?(?!\d|\.\d)")
_DMS = re.compile(
    r"(\d{1,3})\s*°\s*(?:(\d{1,2}(?:[.,]\d+)?)\s*['′])?\s*(?:(\d{1,2}(?:[.,]\d+)?)\s*(?:\"|″|''))?\s*([NSEWPIL])?",
    re.IGNORECASE,
)
_NUMERO = re.compile(r"-?(?:\d{1,3},\d+|\d+(?:\.\d+)?)")
_WKT = re.compile(r"(MULTI)?POLYGON\s*\(", re.IGNORECASE)

SATO_PAATTEET = {".csv", ".txt", ".xlsx", ".xls", ".zip", ".xml", ".shp", ".gpkg", ".dbf", ".bin", ".dat"}
RAJAUS_PAATTEET = {".kml"}
GEOJSON_PAATTEET = {".geojson", ".json"}


@dataclass
class Syote:
    lohkot: list[str] = field(default_factory=list)
    pisteet: list[Piste] = field(default_factory=list)
    alueet: list[Rengas] = field(default_factory=list)
    satotiedostot: list[str] = field(default_factory=list)
    huomiot: list[str] = field(default_factory=list)

    def tyhja(self) -> bool:
        return not (self.lohkot or self.pisteet or self.alueet or self.satotiedostot)

    def yhdista(self, muu: Syote) -> Syote:
        for k in ("pisteet", "alueet", "satotiedostot", "huomiot"):
            getattr(self, k).extend(getattr(muu, k))
        for t in muu.lohkot:
            if t not in self.lohkot:
                self.lohkot.append(t)
        return self

    def sanakirja(self) -> dict:
        d = asdict(self)
        d["yhteenveto"] = yhteenveto(self)
        return d


def yhteenveto(s: Syote) -> str:
    osat = []
    if s.lohkot:
        osat.append(f"{len(s.lohkot)} lohko" + ("a" if len(s.lohkot) > 1 else ""))
    if s.pisteet:
        osat.append(f"{len(s.pisteet)} piste" + ("ttä" if len(s.pisteet) > 1 else ""))
    if s.alueet:
        osat.append(f"{len(s.alueet)} alue" + ("tta" if len(s.alueet) > 1 else ""))
    if s.satotiedostot:
        osat.append(f"{len(s.satotiedostot)} satotiedosto" + ("a" if len(s.satotiedostot) > 1 else ""))
    return " · ".join(osat)


def _desimaali(t: str) -> float:
    return float(t.replace(",", "."))


def _dms_desimaaleiksi(rivi: str) -> str:
    """``62°47'24"N`` -> ``62.79000``; etelä ja länsi negatiivisiksi."""

    def korvaa(m: re.Match) -> str:
        v = int(m.group(1)) + (_desimaali(m.group(2)) / 60 if m.group(2) else 0)
        v += _desimaali(m.group(3)) / 3600 if m.group(3) else 0
        if (m.group(4) or "").upper() in ("S", "W", "L"):  # L = länsi
            v = -v
        return f" {v:.7f} "

    return _DMS.sub(korvaa, rivi)


def _pari(a: float, b: float) -> Piste | None:
    """Koordinaattipari (E, N) EPSG:3067:ssa; järjestys ja koordinaatisto päätellään arvoista."""
    on_lat = lambda x: 55 <= x <= 72
    on_lon = lambda x: 15 <= x <= 35
    if on_lat(a) and on_lon(b):
        return tm35fin(a, b)
    if on_lon(a) and on_lat(b):
        return tm35fin(b, a)
    on_n = lambda x: 6_500_000 <= x <= 7_900_000
    on_e = lambda x: 40_000 <= x <= 800_000
    if on_n(a) and on_e(b):
        return (b, a)
    if on_e(a) and on_n(b):
        return (a, b)
    return None


def _pisteet_rivilta(rivi: str) -> tuple[list[Piste], str | None]:
    luvut = [_desimaali(t) for t in _NUMERO.findall(_dms_desimaaleiksi(rivi))]
    if not luvut:
        return [], None
    if len(luvut) % 2:
        return [], f"pariton määrä lukuja rivillä: {rivi.strip()[:60]}"
    out = []
    for a, b in zip(luvut[::2], luvut[1::2], strict=True):
        p = _pari(a, b)
        if p is None:
            return [], f"ei tunnistettu Suomen koordinaatiksi: {a:g}, {b:g}"
        out.append(p)
    return out, None


def _suorakaide(p: Piste, q: Piste) -> Rengas:
    (e0, n0), (e1, n1) = p, q
    e0, e1 = sorted((e0, e1))
    n0, n1 = sorted((n0, n1))
    return [(e0, n0), (e1, n0), (e1, n1), (e0, n1), (e0, n0)]


def _sulje(r: Rengas) -> Rengas:
    return r if r[0] == r[-1] else [*r, r[0]]


def _renkaat_geojsonista(g: dict) -> tuple[list[Rengas], list[Piste], int]:
    """GeoJSON (Feature, FeatureCollection tai geometria) -> monikulmiot, pisteet, pistemäärä."""
    alueet: list[Rengas] = []
    pisteet: list[Piste] = []
    n_pisteita = 0
    t = g.get("type")
    if t == "FeatureCollection":
        for f in g.get("features", []):
            a, p, n = _renkaat_geojsonista(f)
            alueet += a
            pisteet += p
            n_pisteita += n
        return alueet, pisteet, n_pisteita
    if t == "Feature":
        return _renkaat_geojsonista(g.get("geometry") or {})
    if t == "GeometryCollection":
        for gg in g.get("geometries", []):
            a, p, n = _renkaat_geojsonista(gg)
            alueet += a
            pisteet += p
            n_pisteita += n
        return alueet, pisteet, n_pisteita

    def muunna(c) -> Piste | None:
        return _pari(float(c[1]), float(c[0])) if abs(c[0]) <= 180 and abs(c[1]) <= 90 else _pari(c[0], c[1])

    def rengas(cs) -> Rengas:
        return [p for p in (muunna(c) for c in cs) if p is not None]

    c = g.get("coordinates")
    if t == "Polygon" and c:
        alueet.append(rengas(c[0]))
    elif t == "MultiPolygon" and c:
        alueet += [rengas(pol[0]) for pol in c]
    elif t == "Point" and c:
        n_pisteita = 1
        p = muunna(c)
        if p:
            pisteet.append(p)
    elif t == "MultiPoint" and c:
        n_pisteita = len(c)
    return [a for a in alueet if len(a) >= 4], pisteet, n_pisteita


def _wkt(teksti: str) -> list[Rengas]:
    """Monikulmioiden ulkorenkaat WKT:stä (POLYGON / MULTIPOLYGON)."""
    out = []
    for m in re.finditer(r"\(\s*\(\s*([^()]+)\)", teksti):
        r = []
        for pari in m.group(1).split(","):
            luvut = [float(x) for x in pari.split()]
            if len(luvut) >= 2:
                p = _pari(luvut[1], luvut[0]) if abs(luvut[0]) <= 180 else _pari(luvut[0], luvut[1])
                if p:
                    r.append(p)
        if len(r) >= 3:
            out.append(_sulje(r))
    return out


def tulkitse(teksti: str) -> Syote:
    """Tekstikentän sisältö -> Syote."""
    s = Syote()
    teksti = teksti or ""
    alku = teksti.strip()
    if alku.startswith("{"):
        try:
            a, p, _ = _renkaat_geojsonista(json.loads(alku))
            s.alueet += [_sulje(r) for r in a]
            s.pisteet += p
            if not (a or p):
                s.huomiot.append("GeoJSONissa ei ollut monikulmioita eikä pisteitä")
            return s
        except (ValueError, TypeError, KeyError, IndexError):
            s.huomiot.append("teksti alkoi kuin GeoJSON, mutta sitä ei voitu lukea")
            return s
    if _WKT.search(teksti):
        s.alueet += _wkt(teksti)
        teksti = re.sub(r"(MULTI)?POLYGON\s*\((?:[^()]|\([^()]*\)|\((?:[^()]|\([^()]*\))*\))*\)", " ", teksti, flags=re.IGNORECASE)

    def lohko(m: re.Match) -> str:
        t = re.sub(r"\s", "", m.group(1)) + (f"-{int(m.group(2))}" if m.group(2) else "")
        if t not in s.lohkot:
            s.lohkot.append(t)
        return " "

    teksti = _LOHKO.sub(lohko, teksti)
    for rivi in teksti.splitlines():
        rivi = re.sub(r"\b(alue|bbox|piste|lat|lon|leveys|pituus|[NE])\b\s*[:=]?", " ", rivi, flags=re.IGNORECASE)
        if not rivi.strip(" ,\t"):
            continue
        pisteet, virhe = _pisteet_rivilta(rivi)
        if virhe:
            s.huomiot.append(virhe)
        elif len(pisteet) == 1:
            s.pisteet.append(pisteet[0])
        elif len(pisteet) == 2:
            s.alueet.append(_suorakaide(*pisteet))
        elif len(pisteet) >= 3:
            s.alueet.append(_sulje(pisteet))
        elif re.search(r"[A-Za-zÅÄÖåäö]{3,}", rivi):
            s.huomiot.append(f"ei tunnistettu: {rivi.strip()[:60]}")
    return s


def _kml(polku: Path) -> list[Rengas]:
    out = []
    for el in ET.parse(polku).iter():
        if el.tag.endswith("coordinates") and el.text:
            r = []
            for kolmikko in el.text.split():
                lon, lat, *_ = (float(x) for x in kolmikko.split(","))
                p = _pari(lat, lon)
                if p:
                    r.append(p)
            if len(r) >= 3:
                out.append(_sulje(r))
    return out


def luokittele_tiedosto(polku: str | Path, nimi: str | None = None) -> Syote:
    """Ladattu tiedosto: rajaus (alueet) tai satodata. ``nimi`` on alkuperäinen tiedostonimi."""
    polku = Path(polku)
    nimi = nimi or polku.name
    paate = Path(nimi).suffix.lower()
    s = Syote()
    if paate in RAJAUS_PAATTEET:
        try:
            s.alueet += _kml(polku)
        except ET.ParseError as e:
            s.huomiot.append(f"{nimi}: KML:ää ei voitu lukea ({e})")
        if not s.alueet:
            s.huomiot.append(f"{nimi}: ei monikulmioita")
        return s
    if paate in GEOJSON_PAATTEET:
        try:
            g = json.loads(polku.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            s.huomiot.append(f"{nimi}: JSONia ei voitu lukea ({e})")
            return s
        alueet, _, n_pisteita = _renkaat_geojsonista(g)
        if n_pisteita > 20:  # pistepilvi = satodata (puimurin mittauspisteet)
            s.satotiedostot.append(str(polku))
        elif alueet:
            s.alueet += [_sulje(r) for r in alueet]
        else:
            s.huomiot.append(f"{nimi}: ei rajauksia eikä mittauspisteitä")
        return s
    if paate in SATO_PAATTEET or not paate:
        s.satotiedostot.append(str(polku))
        return s
    s.huomiot.append(f"{nimi}: tuntematon tiedostotyyppi {paate}")
    return s
