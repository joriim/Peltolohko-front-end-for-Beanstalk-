"""Lohkoanalyysi 4D-näkymästä: premium-käyttäjä klikkaa lohkoa Beanstalk Spacessa, ja ajo käynnistyy täällä.

Rajapinta on Spacen sopimus (Beanstalk Space ``docs/premium.md``, 2.2): ``POST /api/analyysi {tunnus, vuosi}``
-> ``{id}`` ja ``GET /api/analyysi/<id>`` -> ``{tila, lahteet, nakyma}``. Näkymä avataan tämän palvelimen
osoitteesta, joten **kirjautuminen kattaa rajapinnan**: erillistä tunnistetta ei tarvita, ja ajo menee samaan
jonoon, käyttölokiin ja aiempiin analyyseihin kuin etusivulta käynnistetty.

Vastuut: kirjautuminen, premium-käyttäjät, kiintiö ja ajot ovat etusivun (tämä paketti); palvelutason sisältö
ja näkymä Spacen; analyysi Growth Analysisin.
"""

from __future__ import annotations

import datetime as dt
import re

from .ajot import Ajo, Ajuri
from .syote import Syote

TUNNUS = re.compile(r"^\d{6,12}(-\d{1,3})?$")
LAHTEET = [
    ("lpis", "Ruokavirasto: rajat ja kasvit"),
    ("s2", "Sentinel-2 L2A, 10 m"),
    ("hls", "HLS (Landsat + Sentinel-2), 30 m"),
    ("s1", "Sentinel-1 tutka"),
    ("saa", "Sää (FMI, ERA5)"),
    ("dtw", "Märkyys (Luke DTW)"),
    ("mml", "Korkeusmalli (MML)"),
    ("gtk", "Maaperä (GTK)"),
    ("planet", "PlanetScope"),
    ("analyysi", "Growth Analysis"),
]
# Growth Analysisin tulosteviesti -> lähteet, joita vaihe hakee (ensimmäinen osuma ratkaisee)
VIESTIT: list[tuple[re.Pattern, tuple[str, ...]]] = [
    (re.compile(r"Lohko \S+: [\d.,]+ ha"), ("lpis",)),
    (re.compile(r"porrastettu ketju"), ("hls", "s1", "s2")),
    (re.compile(r"kauden ulkopuoli"), ("s2", "saa")),
    (re.compile(r"PlanetScope"), ("planet",)),
    (re.compile(r"hiilivuot|sisäinen vaihtelu|talvi \d"), ("analyysi",)),
    (re.compile(r"maaperä, maasto"), ("gtk", "mml", "dtw")),
    (re.compile(r"märkyys \(DTW\)"), ("dtw",)),
    (re.compile(r"Sentinel-2-lohkotilastot|vertailulohkot|ehdokkaat"), ("s2", "lpis")),
    (re.compile(r"FMI"), ("saa",)),
]
UUSINTA = re.compile(r"tahtiraja|uusinta")


class Kiintio(Exception):
    """Päivän kiintiö on käytetty (HTTP 429)."""


def _sama(ajo: Ajo, tunnus: str, vuosi: int) -> bool:
    return not ajo.yksityinen and tunnus in (ajo.lohkot or ajo.syote.lohkot) and ajo.vuodet.strip() == str(vuosi)


def aloita(ajuri: Ajuri, tunnus: str, vuosi: int, kayttaja: str = "") -> tuple[Ajo, bool]:
    """Ajo lohkolle ja vuodelle: (ajo, uusi). Valmis tai käynnissä oleva sama analyysi palautetaan sellaisenaan
    (ei kuluta kiintiötä); muuten uusi ajo (Growth Analysis + Space), jos käyttäjän päiväkiintiö riittää."""
    if not TUNNUS.match(tunnus):
        raise ValueError("virheellinen lohkotunnus")
    if not 2015 <= vuosi <= dt.date.today().year + 1:
        raise ValueError("virheellinen vuosi")
    for ajo in sorted(ajuri.ajot.values(), key=lambda a: a.alkoi, reverse=True):
        if _sama(ajo, tunnus, vuosi) and (ajo.tila in ("jonossa", "käynnissä") or (ajo.tila == "valmis" and _nakyma(ajo, tunnus))):
            return ajo, False
    raja = int(ajuri.asetukset.get("analyyseja_paivassa") or 0)
    tanaan = dt.date.today().isoformat()
    kaytetty = sum(a.lahde == "klikkaus" and a.kayttaja == kayttaja and a.alkoi.startswith(tanaan) for a in ajuri.ajot.values())
    if kaytetty >= raja:
        raise Kiintio(f"päivän kiintiö ({raja} analyysiä) on käytetty")
    tuotteet = [t for t in ("growth", "space") if t in ajuri.tarjolla()]
    return ajuri.aloita(Syote(lohkot=[tunnus]), tuotteet, str(vuosi), kayttaja=kayttaja, lahde="klikkaus"), True


def _nakyma(ajo: Ajo, tunnus: str) -> str | None:
    """Valmiin ajon tulos lohkolle: 4D-näkymä, jos se on tehty, muuten raportti."""
    for tuote in ("space", "growth"):
        for v in ajo.vaiheet:
            if v.tuote == tuote and v.linkki and v.otsikko.endswith(tunnus):
                return v.linkki
    return None


def lahteet(ajo: Ajo) -> list[dict]:
    """Haun tila lähteittäin ajon lokista (Growth Analysisin tulosteviestit)."""
    x = {a: {"avain": a, "nimi": n, "tila": "jonossa", "osuus": 0.0, "viesti": ""} for a, n in LAHTEET}
    aktiiviset: tuple[str, ...] = ()
    if ajo.tila != "jonossa":
        x["lpis"]["tila"], aktiiviset = "haetaan", ("lpis",)
    for rivi in ajo.loki:
        v = rivi[9:] if re.match(r"\d\d:\d\d:\d\d ", rivi) else rivi
        if UUSINTA.search(v):
            for a in aktiiviset:
                x[a].update(tila="uudelleen", viesti=v.strip()[:160])
            continue
        avaimet = next((a for r, a in VIESTIT if r.search(v)), None)
        if not avaimet:
            continue
        for a in aktiiviset:
            if a not in avaimet:
                x[a].update(tila="valmis", osuus=1.0, viesti="")
        for a in avaimet:
            x[a].update(tila="haetaan", viesti="")
        aktiiviset = avaimet
    for d in x.values():
        if d["tila"] in ("haetaan", "uudelleen"):
            if ajo.tila == "valmis":
                d.update(tila="valmis", osuus=1.0, viesti="")
            elif ajo.tila == "virhe":
                d.update(tila="virhe")
            else:
                d["osuus"] = 0.5  # vaiheen sisäistä edistymistä ei tiedetä
        elif d["tila"] == "jonossa" and ajo.tila in ("valmis", "virhe"):
            d.update(tila="ohitettu", viesti="tilataan erikseen" if d["avain"] == "planet" else "ei tarvittu")
    if ajo.tila == "valmis":
        x["analyysi"].update(tila="valmis", osuus=1.0, viesti="")
    return list(x.values())


def tila(ajo: Ajo, tunnus: str | None = None) -> dict:
    """Katselimen kysymä tila: ``kaynnissa`` | ``valmis`` | ``virhe``, lähteet ja valmiina näkymän polku."""
    tunnus = tunnus or next(iter(ajo.lohkot or ajo.syote.lohkot), "")
    nakyma = _nakyma(ajo, tunnus)
    if ajo.tila in ("jonossa", "käynnissä"):
        return {"tila": "kaynnissa", "lahteet": lahteet(ajo)}
    if nakyma:  # tulos on, vaikka jokin sivuvaihe olisi epäonnistunut
        return {"tila": "valmis", "lahteet": lahteet(ajo), "nakyma": nakyma}
    viesti = next((f"{v.otsikko}: {v.viesti}" for v in ajo.vaiheet if v.tila == "virhe" and v.viesti), "")
    return {"tila": "virhe", "lahteet": lahteet(ajo), "viesti": viesti or "analyysi epäonnistui"}
