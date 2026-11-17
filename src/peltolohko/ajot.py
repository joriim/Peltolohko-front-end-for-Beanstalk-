"""Ajot: syötteestä Beanstalk Yieldin, Growth Analysisin ja Spacen tuotteiksi.

Ajo etenee vaiheittain omassa säikeessään:

1. **Lohkot**: pisteet ja alueet peltolohkonumeroiksi lohkorekisteristä (``rekisteri``).
2. **Growth Analysis** jokaiselle lohkolle: ``kasvukunto analysoi`` -> HTML-raportti.
3. **Space** jokaiselle lohkolle: analyysin 4D-näkymädata -> ``beanstalk4d nayta`` -> HTML-katselin.
   Data etsitään varastosta (``space_data``-mallit); ilman sitä vaihe ohitetaan ja kerrotaan miksi.
4. **Yield**: lohkojen rajaus (``yieldsuite parcel``), field-year-asetus satotiedostoista,
   ``yieldsuite run --to L4`` ja ``yieldsuite report``. Ilman satotiedostoa vaihe ohitetaan.

Jokainen ajo tallentuu kansioonsa (``ajo.json``) ja luetaan takaisin palvelimen käynnistyessä; kesken jääneet
merkitään keskeytyneiksi. Käyttöloki ``kaytto.csv`` (tuloskansion juuressa) kirjaa vaiheittain keston,
Copernicus-käsittely-yksiköiden ja latauksen arvion (``kasvukunto arvioi``) sekä varaston kasvun.

Komennot ovat asetuksissa malleina (``{tunnus}``, ``{varasto}``, ``{kansio}``…), joten niitä voi muuttaa
koskematta koodiin (``peltolohko.toml``, ks. README).
"""

from __future__ import annotations

import csv
import datetime as dt
import glob
import json
import os
import secrets
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .rekisteri import Rekisteri
from .syote import Syote

TUOTTEET = ("yield", "growth", "space")
NIMET = {"yield": "Beanstalk Yield", "growth": "Beanstalk Growth Analysis", "space": "Beanstalk Space"}

OLETUS = {
    "varasto": "varasto",
    "komennot": {
        "growth": [
            "kasvukunto",
            "analysoi",
            "{tunnus}",
            "--varasto",
            "{varasto}",
            "--raportti",
            "{kansio}/growth/{tunnus}.html",
            "{vuodet_arg}",
        ],
        "space": ["beanstalk4d", "nayta", "{data}", "-o", "{kansio}/space/{tunnus}.html", "--otsikko", "Lohko {tunnus}"],
        # premium-näkymä samasta datasta (lohkotunnukset, lohkonumerot, haku klikkaamalla). Rajapinta on tämän
        # palvelimen /api: näkymä avataan samasta osoitteesta, joten kirjautuminen kattaa sen eikä tunnistetta tarvita.
        "space_premium": [
            "beanstalk4d",
            "premium",
            "{data}",
            "-o",
            "{kansio}/space/{tunnus}.premium.html",
            "--otsikko",
            "Lohko {tunnus}",
            "--api",
            "/api",
        ],
        "yield_parcel": ["yieldsuite", "parcel", "{lohkot}", "--out", "{kansio}/yield/rajaus.geojson"],
        "yield_inspect": ["yieldsuite", "inspect", "{tiedosto}"],
        "yield_run": ["yieldsuite", "run", "{kansio}/yield/field.yaml", "--to", "{taso}"],
        "yield_report": ["yieldsuite", "report", "{kansio}/yield/field.yaml", "--out", "{kansio}/yield/raportti"],
        # käyttölokin arvio ennen analyysia: Copernicus-käsittely-yksiköt ja lataus (sama vertailulohkomäärä kuin analysoi)
        "growth_arvio": ["kasvukunto", "arvioi", "{tunnus}", "--vuodet", "{arvio_vuodet}", "--vertailulohkoja", "20", "--json"],
    },
    "kayttoarvio": True,
    # Premium (Beanstalk Space): kirjautuminen ja käyttäjät ovat tämän palvelimen asia, palvelutason sisältö Spacen.
    # premium = true: kaikki kirjautuneet; premium_kayttajat: vain luetellut. Premium-käyttäjälle 4D-näkymä
    # avautuu premium-versiona ja lohkon klikkaus näkymässä käynnistää analyysin (POST /api/analyysi).
    "premium": False,
    "premium_kayttajat": [],
    "analyyseja_paivassa": 20,  # näkymästä klikkaamalla käynnistetyt uudet analyysit / käyttäjä / vrk
    # MVP: satotiedosto otetaan vastaan, mutta sitä ei säilytetä. Raakatiedosto poistetaan, kun ajo päättyy
    # (myös virheeseen tai keskeytykseen), ja ajoon käyttämätön lataus lataus_vanhenee_min minuutin kuluttua.
    # Ilman satodataa: satodata = false (Yield piiloutuu). Säilytys: satotiedosto_poistetaan = false.
    "satodata": True,
    "tuotteet": ["yield", "growth", "space"],
    "satotiedosto_poistetaan": True,
    "lataus_vanhenee_min": 60,
    # Potentiaalisesti GDPR:n alaista dataa ei säilytetä ennen tunnuksia, luvitusta ja tietoturvaa:
    # satotiedoston pistetason tuotokset (L0–L3, esim. L3_*_records.parquet) poistetaan raportin jälkeen
    # (osa satotiedosto_poistetaan-asetusta) eikä käyttäjän IP-osoitetta kirjata.
    "kirjaa_osoite": False,
    # Satoajo (ajossa satotiedosto) ei näy aiempien analyysien luettelossa: siihen pääsee vain ajon omalla
    # linkillä (tunnisteessa 16 satunnaista heksamerkkiä). Satotulokset (satoruudukko, raportti) poistetaan
    # satotulokset_sailytys_h tunnin kuluttua ajon päättymisestä (0 = ei poisteta). Siivous siivousvali_s välein.
    "satotulokset_sailytys_h": 24,
    "siivousvali_s": 600,
    # 4D-näkymän data analyysin varastossa (Growth Analysis -> Space -sovitin, pylvaat3d)
    "space_data": [
        "{varasto}/{peruslohko}*/**/space*.json",
        "{varasto}/{peruslohko}*/**/nakyma*.json",
        "{varasto}/{peruslohko}*/**/*4d*.json",
    ],
    "aikakatkaisu_s": 6 * 3600,
    "rinnakkaiset_ajot": 1,  # NAS:lla neljä ydintä: yksi ajo kerrallaan, muut jonossa
}

ASENNUS = {
    "kasvukunto": "pip install -e Beanstalk-Growth-Analysis",
    "beanstalk4d": "pip install -e Beanstalk-Space",
    "yieldsuite": 'pip install -e "Beanstalk-Yield[geo]"',
}


@dataclass
class Vaihe:
    tuote: str
    otsikko: str
    komento: list[str] = field(default_factory=list)
    tulos: str | None = None  # polku tai glob ajokansion sisällä
    tila: str = "jonossa"  # jonossa | käynnissä | valmis | virhe | ohitettu
    viesti: str = ""
    linkki: str | None = None
    kesto_s: float | None = None
    kaytto: dict = field(default_factory=dict)  # pu_arvio, lataus_mt_arvio, varasto_kasvu_mt


@dataclass
class Ajo:
    id: str
    kansio: Path
    syote: Syote
    tuotteet: list[str]
    vuodet: str = ""
    lohkot: list[str] = field(default_factory=list)
    vaiheet: list[Vaihe] = field(default_factory=list)
    tila: str = "jonossa"
    loki: list[str] = field(default_factory=list)
    alkoi: str = field(default_factory=lambda: dt.datetime.now().isoformat(timespec="milliseconds"))
    kayttaja: str = ""
    osoite: str = ""
    yksityinen: bool = False  # satoajo: ei luettelossa, vain omalla linkillä
    paattyi: str = ""
    satotulokset_poistuvat: str = ""  # ISO-aika; tyhjä = ei poistoa
    satotulokset_poistettu: bool = False
    lahde: str = ""  # "klikkaus": käynnistetty 4D-näkymästä lohkoa klikkaamalla (kiintiö)
    _tallennettu: float = 0.0

    def tilanne(self) -> dict:
        return {
            "id": self.id,
            "tila": self.tila,
            "alkoi": self.alkoi,
            "kayttaja": self.kayttaja,
            "osoite": self.osoite,
            "yksityinen": self.yksityinen,
            "paattyi": self.paattyi,
            "satotulokset_poistuvat": self.satotulokset_poistuvat,
            "satotulokset_poistettu": self.satotulokset_poistettu,
            "lahde": self.lahde,
            "tuotteet": self.tuotteet,
            "vuodet": self.vuodet,
            "lohkot": self.lohkot,
            "syote": self.syote.sanakirja(),
            "vaiheet": [asdict(v) for v in self.vaiheet],
            "loki": self.loki[-400:],
        }

    def tallenna(self) -> None:
        """ajo.json ajon kansioon (atominen: kirjoitus väliaikaiseen ja vaihto)."""
        self._tallennettu = time.monotonic()
        polku = self.kansio / "ajo.json"
        tmp = polku.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.tilanne(), ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, polku)

    @classmethod
    def lataa(cls, polku: Path) -> Ajo:
        d = json.loads(polku.read_text(encoding="utf-8"))
        sd = d.get("syote") or {}
        syote = Syote(
            lohkot=list(sd.get("lohkot", [])),
            pisteet=[tuple(p) for p in sd.get("pisteet", [])],
            alueet=[[tuple(p) for p in r] for r in sd.get("alueet", [])],
            satotiedostot=list(sd.get("satotiedostot", [])),
            huomiot=list(sd.get("huomiot", [])),
            vuodet=sd.get("vuodet", ""),
        )
        nimet = {f for f in Vaihe.__dataclass_fields__}
        ajo = cls(
            d["id"],
            polku.parent,
            syote,
            list(d.get("tuotteet", [])),
            d.get("vuodet", ""),
            list(d.get("lohkot", [])),
            [Vaihe(**{k: v for k, v in x.items() if k in nimet}) for x in d.get("vaiheet", [])],
            d.get("tila", "valmis"),
            list(d.get("loki", [])),
            d.get("alkoi", ""),
            d.get("kayttaja", ""),
            d.get("osoite", ""),
            bool(d.get("yksityinen", False)),
            d.get("paattyi", ""),
            d.get("satotulokset_poistuvat", ""),
            bool(d.get("satotulokset_poistettu", False)),
            d.get("lahde", ""),
        )
        return ajo

    def tiivistelma(self) -> dict:
        """Aiempien analyysien luetteloon: ei lokia eikä geometrioita."""
        return {
            "id": self.id,
            "alkoi": self.alkoi,
            "tila": self.tila,
            "lohkot": self.lohkot or self.syote.lohkot,
            "tuotteet": self.tuotteet,
            "vuodet": self.vuodet,
            "yhteenveto": self.syote.sanakirja()["yhteenveto"],
            "linkit": [{"tuote": v.tuote, "otsikko": v.otsikko, "linkki": v.linkki} for v in self.vaiheet if v.linkki],
        }


KAYTTO_SARAKKEET = [
    "aika",
    "ajo",
    "kayttaja",
    "osoite",
    "tuote",
    "vaihe",
    "tila",
    "kesto_s",
    "pu_arvio",
    "lataus_mt_arvio",
    "varasto_kasvu_mt",
]


def _koko_mt(polku: Path) -> float:
    if not polku.exists():
        return 0.0
    yht = 0
    for juuri, _, tiedostot in os.walk(polku):
        for t in tiedostot:
            try:
                yht += os.path.getsize(os.path.join(juuri, t))
            except OSError:
                pass
    return yht / 1e6


def _vuodet_lista(vuodet: str) -> list[int]:
    out: list[int] = []
    for osa in vuodet.replace(" ", "").split(","):
        if "-" in osa:
            a, b = osa.split("-", 1)
            out += list(range(int(a), int(b) + 1))
        elif osa:
            out.append(int(osa))
    return sorted(set(out))


def satovuosi(vuodet: str, nyt: dt.date | None = None) -> int:
    """Yieldin satovuosi: annetuista vuosista viimeisin, muuten kulunut (syyskuusta alkaen) tai edellinen kausi."""
    lista = _vuodet_lista(vuodet) if vuodet else []
    if lista:
        return lista[-1]
    nyt = nyt or dt.date.today()
    return nyt.year if nyt.month >= 9 else nyt.year - 1


def yield_asetus(ajo: Ajo, rajaus: Path | None) -> dict:
    """field.yaml (JSON on kelvollista YAMLia) Beanstalk Yieldille."""
    vuosi = satovuosi(ajo.vuodet)
    peruslohkot = sorted({t.split("-")[0] for t in ajo.lohkot})
    asetus: dict = {
        "field_id": peruslohkot[0] if len(peruslohkot) == 1 else f"ajo-{ajo.id}",
        "year": vuosi,
        "parcel_ids": peruslohkot,
        "crs": "EPSG:3067",
        "reference_moisture_pct": 14,
        "grid": {"resolution_m": 20, "origin": [0, 0]},
        "harvest_logs": [{"path": str(Path(p).resolve()), "format": "auto"} for p in ajo.syote.satotiedostot],
        "imagery": [
            {
                "kind": "sentinel2",
                "start": f"{vuosi}-05-01",
                "end": f"{vuosi}-09-01",
                "indices": ["NDVI", "NDRE", "CIre", "NDMI"],
                "max_cloud_cover": 80,
                "min_valid_share": 0.8,
            }
        ],
        "output_dir": "tuotos",
    }
    if rajaus is not None:
        asetus["boundary"] = str(rajaus.resolve())
    return asetus


class Ajuri:
    """Pitää ajot muistissa ja ajaa ne taustasäikeissä."""

    def __init__(self, juuri: Path, asetukset: dict | None = None, rekisteri: Rekisteri | None = None):
        self.juuri = Path(juuri)
        self.juuri.mkdir(parents=True, exist_ok=True)
        a = json.loads(json.dumps(OLETUS))
        for k, v in (asetukset or {}).items():
            if isinstance(v, dict) and isinstance(a.get(k), dict):
                a[k].update(v)
            else:
                a[k] = v
        self.asetukset = a
        self.rekisteri = rekisteri or Rekisteri()
        self.ajot: dict[str, Ajo] = {}
        self._lukko = threading.Lock()
        self._vuoro = threading.Semaphore(max(1, int(self.asetukset["rinnakkaiset_ajot"])))
        self._lue_ajot()

    def _lue_ajot(self) -> None:
        """Aiemmat ajot levyltä. Kesken jääneet (palvelin pysähtyi) merkitään keskeytyneiksi."""
        for polku in sorted(self.juuri.glob("*/ajo.json")):
            try:
                ajo = Ajo.lataa(polku)
            except (OSError, ValueError, KeyError, TypeError) as e:
                print(f"ajoa ei voitu lukea: {polku}: {e}")
                continue
            if ajo.tila in ("jonossa", "käynnissä"):
                self._poista_satotiedostot(ajo)
                self._aseta_poistoaika(ajo)
                ajo.tila = "keskeytynyt"
                for v in ajo.vaiheet:
                    if v.tila in ("jonossa", "käynnissä"):
                        v.tila, v.viesti = "keskeytynyt", "Palvelin käynnistettiin uudelleen kesken ajon."
                ajo.tallenna()
            self.ajot[ajo.id] = ajo
        self.siivoa_lataukset()
        self.siivoa_satotulokset()
        vali = float(self.asetukset.get("siivousvali_s") or 0)
        if vali > 0:
            threading.Thread(target=self._siivoa_ajoittain, args=(vali,), daemon=True, name="siivous").start()

    def _siivoa_ajoittain(self, vali: float) -> None:
        while True:
            time.sleep(vali)
            try:
                self.siivoa_lataukset()
                self.siivoa_satotulokset()
            except Exception as e:  # noqa: BLE001 - siivous ei saa pysäyttää palvelinta
                print(f"siivous epäonnistui: {e}")

    def _aseta_poistoaika(self, ajo: Ajo, nyt: dt.datetime | None = None) -> None:
        h = float(self.asetukset.get("satotulokset_sailytys_h") or 0)
        if ajo.yksityinen and h > 0 and not ajo.satotulokset_poistuvat:
            ajo.satotulokset_poistuvat = ((nyt or dt.datetime.now()) + dt.timedelta(hours=h)).isoformat(timespec="seconds")

    def siivoa_satotulokset(self, nyt: dt.datetime | None = None) -> list[str]:
        """Poistaa satoajojen Yield-tulokset (satoruudukko, raportti), kun säilytysaika on kulunut.
        Growth Analysisin ja Spacen tulokset (julkisesta datasta) jäävät. Palauttaa käsiteltyjen ajojen tunnisteet."""
        nyt = nyt or dt.datetime.now()
        poistetut = []
        for ajo in list(self.ajot.values()):
            if not ajo.satotulokset_poistuvat or ajo.satotulokset_poistettu:
                continue
            if nyt < dt.datetime.fromisoformat(ajo.satotulokset_poistuvat):
                continue
            kansio = ajo.kansio / "yield"
            if kansio.exists():
                shutil.rmtree(kansio)
                kansio.mkdir()
            for v in ajo.vaiheet:
                if v.tuote == "yield":
                    v.linkki = None
                    if v.tila == "valmis":
                        v.viesti = f"Satotulokset poistettu {nyt:%d.%m.%Y %H.%M}."
            ajo.satotulokset_poistettu = True
            self._kirjaa(ajo, "satotulokset poistettu säilytysajan päätyttyä")
            ajo.tallenna()
            poistetut.append(ajo.id)
        return poistetut

    # ------------------------------------------------------------------ satotiedostojen poisto
    @property
    def lataukset(self) -> Path:
        return self.juuri / "_lataukset"

    def _poista_satotiedostot(self, ajo: Ajo) -> None:
        """Ajon raakasatotiedostot pois latauskansiosta (asetus ``satotiedosto_poistetaan``).

        Vain latauskansion tiedostot poistetaan: muualla olevia polkuja (esim. testit, komentorivi) ei kosketa."""
        if not self.asetukset.get("satotiedosto_poistetaan"):
            return
        juuri = self.lataukset.resolve()
        for p in ajo.syote.satotiedostot:
            polku = Path(p).resolve()
            if polku.is_relative_to(juuri) and polku.exists():
                polku.unlink()
                self._kirjaa(ajo, f"satotiedosto poistettu palvelimelta: {polku.name}")
        # Pistetason tuotokset (yksittäiset mittaukset): käytännössä satotiedoston sisältö jalostettuna.
        # Jäljelle jäävät satoruudukko (L4), laatu- ja varmuustiedot sekä raportti.
        pisteet = [
            f
            for f in (ajo.kansio / "yield").rglob("*")
            if f.is_file() and len(f.name) > 3 and f.name[0] == "L" and f.name[1] in "0123" and f.name[2] in "_AB"
        ]
        for f in pisteet:
            f.unlink()
        if pisteet:
            self._kirjaa(ajo, f"satomittaukset poistettu (pistetaso L0–L3): {len(pisteet)} tiedostoa")

    def siivoa_lataukset(self, nyt: float | None = None) -> list[str]:
        """Poistaa latauskansiosta tiedostot, joita mikään jonossa tai käynnissä oleva ajo ei käytä ja jotka ovat
        vanhempia kuin ``lataus_vanhenee_min``. Palauttaa poistettujen nimet."""
        if not self.asetukset.get("satotiedosto_poistetaan") or not self.lataukset.exists():
            return []
        nyt = nyt or time.time()
        raja = nyt - float(self.asetukset.get("lataus_vanhenee_min", 60)) * 60
        kaytossa = {
            str(Path(p).resolve()) for a in self.ajot.values() if a.tila in ("jonossa", "käynnissä") for p in a.syote.satotiedostot
        }
        poistetut = []
        for polku in self.lataukset.iterdir():
            try:
                if polku.is_file() and str(polku.resolve()) not in kaytossa and polku.stat().st_mtime < raja:
                    polku.unlink()
                    poistetut.append(polku.name)
            except OSError:
                pass
        return poistetut

    # ------------------------------------------------------------------ luettelot ja käyttö
    def luettelo(self, lohkot: list[str] | None = None) -> list[dict]:
        """Ajot uusimmasta vanhimpaan; ``lohkot`` rajaa peruslohkon mukaan."""
        haku = {t.split("-")[0] for t in (lohkot or []) if t}
        out = []
        for ajo in sorted(self.ajot.values(), key=lambda a: a.alkoi, reverse=True):
            omat = {t.split("-")[0] for t in (ajo.lohkot or ajo.syote.lohkot)}
            if ajo.yksityinen:  # satoajot vain omalla linkillä
                continue
            if haku and not (haku & omat):
                continue
            out.append(ajo.tiivistelma())
        return out

    def aiemmat(self, lohkot: list[str]) -> dict[str, dict]:
        """Jokaiselle peruslohkolle uusin valmis ajo, jossa on tuloksia (etusivun vihje)."""
        out: dict[str, dict] = {}
        for t in {x.split("-")[0] for x in lohkot}:
            for a in self.luettelo([t]):
                if a["linkit"]:
                    out[t] = {"id": a["id"], "alkoi": a["alkoi"]}
                    break
        return out

    def _kaytto_rivi(self, ajo: Ajo, v: Vaihe) -> None:
        polku = self.juuri / "kaytto.csv"
        rivi = {
            "aika": dt.datetime.now().isoformat(timespec="seconds"),
            "ajo": ajo.id,
            "kayttaja": ajo.kayttaja,
            "osoite": ajo.osoite,
            "tuote": v.tuote,
            "vaihe": v.otsikko,
            "tila": v.tila,
            "kesto_s": v.kesto_s if v.kesto_s is not None else "",
            **{k: v.kaytto.get(k, "") for k in ("pu_arvio", "lataus_mt_arvio", "varasto_kasvu_mt")},
        }
        with self._lukko:
            uusi = not polku.exists()
            with polku.open("a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=KAYTTO_SARAKKEET)
                if uusi:
                    w.writeheader()
                w.writerow(rivi)

    def kaytto(self) -> dict:
        """Käyttölokin kooste kuukausittain ja käyttäjittäin."""
        polku = self.juuri / "kaytto.csv"
        kk: dict[tuple[str, str], dict] = {}
        if polku.exists():
            with polku.open(encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    avain = (r["aika"][:7], r["kayttaja"])
                    k = kk.setdefault(
                        avain,
                        {
                            "kuukausi": avain[0],
                            "kayttaja": avain[1],
                            "ajot": set(),
                            "vaiheet": 0,
                            "kesto_s": 0.0,
                            "pu_arvio": 0.0,
                            "lataus_mt_arvio": 0.0,
                            "varasto_kasvu_mt": 0.0,
                        },
                    )
                    k["ajot"].add(r["ajo"])
                    k["vaiheet"] += 1
                    for x in ("kesto_s", "pu_arvio", "lataus_mt_arvio", "varasto_kasvu_mt"):
                        try:
                            k[x] += float(r[x] or 0)
                        except ValueError:
                            pass
        rivit = []
        for k in sorted(kk.values(), key=lambda k: (k["kuukausi"], k["kayttaja"]), reverse=True):
            rivit.append(
                {
                    **k,
                    "ajot": len(k["ajot"]),
                    **{x: round(k[x], 1) for x in ("kesto_s", "pu_arvio", "lataus_mt_arvio", "varasto_kasvu_mt")},
                }
            )
        return {"rivit": rivit, "cdse_kiintio_pu_kk": 10000}

    def _arvioi(self, ajo: Ajo, v: Vaihe, tunnus: str) -> None:
        """Copernicus-yksiköt ja lataus ennen analyysia (``kasvukunto arvioi``); virhe ei estä ajoa."""
        if not self.asetukset.get("kayttoarvio") or shutil.which("kasvukunto") is None:
            return
        y = satovuosi("")
        arvot = self._arvot(ajo, tunnus=tunnus, arvio_vuodet=ajo.vuodet or f"{y - 2}-{y}")
        try:
            p = subprocess.run(
                self._tayta(self.asetukset["komennot"]["growth_arvio"], arvot), capture_output=True, text=True, timeout=600
            )
            d = json.loads(p.stdout)
            v.kaytto["pu_arvio"] = round(float(d["pu"]), 1)
            v.kaytto["lataus_mt_arvio"] = round(float(d["lataus_mt"]), 1)
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as e:
            self._kirjaa(ajo, f"käyttöarvio ei onnistunut: {e}")

    # ------------------------------------------------------------------ suunnittelu
    def _arvot(self, ajo: Ajo, **lisa) -> dict:
        return {
            "kansio": str(ajo.kansio),
            "varasto": str(Path(self.asetukset["varasto"]).resolve()),
            "vuodet": ajo.vuodet,
            "vuodet_arg": ["--vuodet", ajo.vuodet] if ajo.vuodet else [],
            **lisa,
        }

    @staticmethod
    def _tayta(malli: list[str], arvot: dict) -> list[str]:
        out: list[str] = []
        for osa in malli:
            avain = osa[1:-1] if osa.startswith("{") and osa.endswith("}") else None
            if avain in arvot and isinstance(arvot[avain], list):
                out += [str(x) for x in arvot[avain]]  # lista laajenee useaksi argumentiksi
            else:
                out.append(osa.format(**{k: v for k, v in arvot.items() if not isinstance(v, list)}))
        return out

    def _space_data(self, ajo: Ajo, tunnus: str) -> str | None:
        arvot = self._arvot(ajo, tunnus=tunnus, peruslohko=tunnus.split("-")[0])
        for malli in self.asetukset["space_data"]:
            osumat = sorted(glob.glob(malli.format(**{k: v for k, v in arvot.items() if not isinstance(v, list)}), recursive=True))
            if osumat:
                return osumat[-1]
        return None

    def _premium_nakyma(self, ajo: Ajo, v: Vaihe) -> None:
        """Premium-versio 4D-näkymästä freemiumin viereen (``<tunnus>.premium.html``). Palvelin antaa sen vain
        premium-käyttäjälle; epäonnistuminen ei kaada vaihetta (freemium-näkymä on jo tehty)."""
        tunnus = v.otsikko.rsplit(" ", 1)[-1]
        data = self._space_data(ajo, tunnus)
        if data is None:
            return
        komento = self._tayta(self.asetukset["komennot"]["space_premium"], self._arvot(ajo, tunnus=tunnus, data=data))
        self._kirjaa(ajo, "$ " + " ".join(komento))
        try:
            p = subprocess.run(komento, capture_output=True, text=True, cwd=ajo.kansio, timeout=600)
            if p.returncode != 0:
                self._kirjaa(ajo, f"premium-näkymä ei onnistunut: {(p.stdout + p.stderr).strip()[-300:]}")
        except (OSError, subprocess.TimeoutExpired) as e:
            self._kirjaa(ajo, f"premium-näkymä ei onnistunut: {e}")

    def suunnittele(self, ajo: Ajo) -> None:
        K = self.asetukset["komennot"]
        v: list[Vaihe] = []
        for t in ajo.lohkot:
            if "growth" in ajo.tuotteet:
                v.append(
                    Vaihe(
                        "growth",
                        f"Kasvukuntoanalyysi {t}",
                        self._tayta(K["growth"], self._arvot(ajo, tunnus=t)),
                        tulos=f"growth/{t}.html",
                    )
                )
            if "space" in ajo.tuotteet:
                v.append(Vaihe("space", f"4D-näkymä {t}", [], tulos=f"space/{t}.html"))
        if "yield" in ajo.tuotteet and not ajo.syote.satotiedostot:
            # Yield tekee kartan puimurin datasta; pelkistä kuvista tehtävää suhteellista karttaa siinä ei vielä ole
            v.append(
                Vaihe(
                    "yield",
                    "Satokartta",
                    [],
                    tila="ohitettu",
                    viesti="Tarvitaan puimurin satotiedosto. Pelkistä kuvista Yield ei vielä tee karttaa.",
                )
            )
        elif "yield" in ajo.tuotteet:
            lohkot = sorted({t.split("-")[0] for t in ajo.lohkot})
            if lohkot:
                v.append(Vaihe("yield", "Lohkojen rajaus", self._tayta(K["yield_parcel"], self._arvot(ajo, lohkot=lohkot))))
            for p in ajo.syote.satotiedostot:
                v.append(
                    Vaihe(
                        "yield",
                        f"Satotiedosto {Path(p).name.split('_', 1)[-1]}",
                        self._tayta(K["yield_inspect"], self._arvot(ajo, tiedosto=p)),
                    )
                )
            taso = "L4"  # puimurin satoruudukko; L5 (kuvafuusio) vaatii openEO-tunnuksen ja ajetaan erikseen
            v.append(
                Vaihe(
                    "yield",
                    "Satokartta",
                    self._tayta(K["yield_run"], self._arvot(ajo, taso=taso)),
                )
            )
            v.append(
                Vaihe("yield", "Satoraportti", self._tayta(K["yield_report"], self._arvot(ajo)), tulos="yield/raportti/*_report.html")
            )
        ajo.vaiheet = v

    # ------------------------------------------------------------------ ajo
    @property
    def satodata(self) -> bool:
        """Otetaanko satotiedostoja vastaan (asetus ``satodata``)."""
        return bool(self.asetukset.get("satodata"))

    def tarjolla(self) -> list[str]:
        """Tarjolla olevat tuotteet. Yield vaatii satodatan, joten se on mukana vain, kun satodata on sallittu."""
        sallitut = set(self.asetukset.get("tuotteet") or TUOTTEET)
        return [t for t in TUOTTEET if t in sallitut and (t != "yield" or self.satodata)]

    def premium(self, kayttaja: str = "") -> bool:
        """Onko käyttäjällä premium (asetukset ``premium`` ja ``premium_kayttajat``)."""
        return bool(self.asetukset.get("premium")) or (bool(kayttaja) and kayttaja in (self.asetukset.get("premium_kayttajat") or []))

    def aloita(
        self, syote: Syote, tuotteet: list[str], vuodet: str = "", kayttaja: str = "", osoite: str = "", lahde: str = ""
    ) -> Ajo:
        tarjolla = self.tarjolla()
        tuotteet = [t for t in tarjolla if t in tuotteet] or tarjolla
        if not self.satodata:
            syote.satotiedostot = []  # varmistus: satodataa ei käsitellä, vaikka sitä tulisi
        yksityinen = bool(syote.satotiedostot) and "yield" in tuotteet
        # satoajon linkkiä ei voi arvata: 16 satunnaista heksamerkkiä (64 bittiä)
        loppu = secrets.token_hex(8) if yksityinen else uuid.uuid4().hex[:4]
        aid = dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + loppu
        if not self.asetukset.get("kirjaa_osoite"):
            osoite = ""  # IP-osoite on henkilötieto: ei kirjata ilman asetusta
        ajo = Ajo(
            aid,
            self.juuri / aid,
            syote,
            tuotteet,
            vuodet.strip(),
            kayttaja=kayttaja,
            osoite=osoite,
            yksityinen=yksityinen,
            lahde=lahde,
        )
        for alakansio in ("growth", "space", "yield"):
            (ajo.kansio / alakansio).mkdir(parents=True, exist_ok=True)
        ajo.tallenna()
        with self._lukko:
            self.ajot[aid] = ajo
        threading.Thread(target=self._aja, args=(ajo,), daemon=True, name=f"ajo-{aid}").start()
        return ajo

    def _kirjaa(self, ajo: Ajo, rivi: str) -> None:
        ajo.loki.append(f"{dt.datetime.now():%H:%M:%S} {rivi}")
        if time.monotonic() - ajo._tallennettu > 5:  # loki levylle enintään 5 s välein
            ajo.tallenna()

    def _ratkaise_lohkot(self, ajo: Ajo) -> None:
        lohkot = list(ajo.syote.lohkot)
        for p in ajo.syote.pisteet:
            loydetyt = self.rekisteri.pisteessa(p)
            self._kirjaa(ajo, f"piste E {p[0]:.0f} N {p[1]:.0f}: " + (", ".join(loydetyt) or "ei peltolohkoa"))
            lohkot += [t for t in loydetyt if t not in lohkot]
        for r in ajo.syote.alueet:
            loydetyt = self.rekisteri.alueella(r)
            self._kirjaa(ajo, f"alue: {len(loydetyt)} peruslohkoa")
            lohkot += [t for t in loydetyt if t not in lohkot]
        ajo.lohkot = lohkot

    def _aja(self, ajo: Ajo) -> None:
        with self._vuoro:  # odottaa, kunnes edellinen ajo on valmis
            self._aja_vuorossa(ajo)

    def _aja_vuorossa(self, ajo: Ajo) -> None:
        ajo.tila = "käynnissä"
        ajo.lohkot = list(ajo.syote.lohkot)
        try:
            if ajo.syote.pisteet or ajo.syote.alueet:
                self._kirjaa(ajo, "haetaan peltolohkot lohkorekisteristä")
                self._ratkaise_lohkot(ajo)
            if not ajo.lohkot and not ajo.syote.satotiedostot:
                ajo.tila = "virhe"
                self._kirjaa(ajo, "syötteestä ei löytynyt peltolohkoja")
                return
            self.suunnittele(ajo)
            ajo.tallenna()
            for v in ajo.vaiheet:
                self._aja_vaihe(ajo, v)
                self._kaytto_rivi(ajo, v)
                ajo.tallenna()
            ajo.tila = "virhe" if any(v.tila == "virhe" for v in ajo.vaiheet) else "valmis"
        except Exception as e:  # noqa: BLE001 - ajo ei saa kaataa palvelinta
            ajo.tila = "virhe"
            self._kirjaa(ajo, f"virhe: {e}")
        finally:
            self._poista_satotiedostot(ajo)
            ajo.paattyi = dt.datetime.now().isoformat(timespec="seconds")
            self._aseta_poistoaika(ajo)
            ajo.tallenna()

    def _aja_vaihe(self, ajo: Ajo, v: Vaihe) -> None:
        if v.tuote == "space" and not v.komento:
            tunnus = v.otsikko.rsplit(" ", 1)[-1]
            data = self._space_data(ajo, tunnus)
            if data is None:
                v.tila, v.viesti = (
                    "ohitettu",
                    ("Ei 4D-dataa. Growth Analysis ei vielä tuota sitä (sovitin pylvaat3d, ks. Kasvukuntoraportti/ARKKITEHTUURI.md)."),
                )
                self._kirjaa(ajo, f"{v.otsikko}: ohitettu, ei näkymädataa")
                return
            v.komento = self._tayta(self.asetukset["komennot"]["space"], self._arvot(ajo, tunnus=tunnus, data=data))
        if v.tuote == "yield" and v.otsikko.startswith("Satokartta") and v.komento:
            rajaus = ajo.kansio / "yield" / "rajaus.geojson"
            asetus = yield_asetus(ajo, rajaus if rajaus.exists() else None)
            (ajo.kansio / "yield" / "field.yaml").write_text(json.dumps(asetus, indent=2, ensure_ascii=False))
        if not v.komento:
            v.tila = "ohitettu"
            return
        ohjelma = v.komento[0]
        if shutil.which(ohjelma) is None:
            v.tila = "virhe"
            v.viesti = f"{ohjelma} ei ole asennettu" + (f" ({ASENNUS[ohjelma]})" if ohjelma in ASENNUS else "")
            self._kirjaa(ajo, f"{v.otsikko}: {v.viesti}")
            return
        v.tila = "käynnissä"
        ajo.tallenna()
        varasto = Path(self.asetukset["varasto"]).resolve()
        if v.tuote == "growth":
            self._arvioi(ajo, v, v.otsikko.rsplit(" ", 1)[-1])
            ennen = _koko_mt(varasto)
        self._kirjaa(ajo, "$ " + " ".join(v.komento))
        t0 = time.monotonic()
        try:
            p = subprocess.Popen(v.komento, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=ajo.kansio, bufsize=1)
            assert p.stdout is not None
            for rivi in p.stdout:
                self._kirjaa(ajo, rivi.rstrip())
            koodi = p.wait(timeout=self.asetukset["aikakatkaisu_s"])
        except (OSError, subprocess.TimeoutExpired) as e:
            koodi, v.viesti = -1, str(e)
        v.kesto_s = round(time.monotonic() - t0, 1)
        if v.tuote == "growth":
            v.kaytto["varasto_kasvu_mt"] = round(_koko_mt(varasto) - ennen, 1)
        if koodi != 0:
            v.tila = "virhe"
            v.viesti = v.viesti or f"paluukoodi {koodi}"
            return
        v.tila = "valmis"
        if v.tuote == "space" and (self.asetukset.get("premium") or self.asetukset.get("premium_kayttajat")):
            self._premium_nakyma(ajo, v)
        if v.tulos:
            osumat = sorted(glob.glob(str(ajo.kansio / v.tulos)))
            if osumat:
                v.linkki = f"/tulokset/{ajo.id}/" + Path(osumat[-1]).relative_to(ajo.kansio).as_posix()
