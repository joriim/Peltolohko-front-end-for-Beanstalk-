"""Ajot: syötteestä Beanstalk Yieldin, Growth Analysisin ja Spacen tuotteiksi.

Ajo etenee vaiheittain omassa säikeessään:

1. **Lohkot**: pisteet ja alueet peltolohkonumeroiksi lohkorekisteristä (``rekisteri``).
2. **Growth Analysis** jokaiselle lohkolle: ``kasvukunto analysoi`` -> HTML-raportti.
3. **Space** jokaiselle lohkolle: analyysin 4D-näkymädata -> ``beanstalk4d nayta`` -> HTML-katselin.
   Data etsitään varastosta (``space_data``-mallit); ilman sitä vaihe ohitetaan ja kerrotaan miksi.
4. **Yield**: lohkojen rajaus (``yieldsuite parcel``), field-year-asetus satotiedostoista ja Sentinel-2:sta,
   ``yieldsuite run`` ja ``yieldsuite report``. Ilman satotiedostoa tuote on suhteellinen (kuvista).

Komennot ovat asetuksissa malleina (``{tunnus}``, ``{varasto}``, ``{kansio}``…), joten niitä voi muuttaa
koskematta koodiin (``peltolohko.toml``, ks. README).
"""

from __future__ import annotations

import datetime as dt
import glob
import json
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
        "yield_parcel": ["yieldsuite", "parcel", "{lohkot}", "--out", "{kansio}/yield/rajaus.geojson"],
        "yield_inspect": ["yieldsuite", "inspect", "{tiedosto}"],
        "yield_run": ["yieldsuite", "run", "{kansio}/yield/field.yaml", "--to", "{taso}"],
        "yield_report": ["yieldsuite", "report", "{kansio}/yield/field.yaml", "--out", "{kansio}/yield/raportti"],
    },
    # 4D-näkymän data analyysin varastossa (Growth Analysis -> Space -sovitin, pylvaat3d)
    "space_data": [
        "{varasto}/{peruslohko}*/**/space*.json",
        "{varasto}/{peruslohko}*/**/nakyma*.json",
        "{varasto}/{peruslohko}*/**/*4d*.json",
    ],
    "aikakatkaisu_s": 6 * 3600,
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
    alkoi: str = field(default_factory=lambda: dt.datetime.now().isoformat(timespec="seconds"))

    def tilanne(self) -> dict:
        return {
            "id": self.id,
            "tila": self.tila,
            "alkoi": self.alkoi,
            "tuotteet": self.tuotteet,
            "vuodet": self.vuodet,
            "lohkot": self.lohkot,
            "syote": self.syote.sanakirja(),
            "vaiheet": [asdict(v) for v in self.vaiheet],
            "loki": self.loki[-400:],
        }


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
        if "yield" in ajo.tuotteet:
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
            taso = "L5"
            v.append(
                Vaihe(
                    "yield",
                    "Satokartta " + ("(puimuri + kuvat)" if ajo.syote.satotiedostot else "(kuvista, suhteellinen)"),
                    self._tayta(K["yield_run"], self._arvot(ajo, taso=taso)),
                )
            )
            v.append(
                Vaihe("yield", "Satoraportti", self._tayta(K["yield_report"], self._arvot(ajo)), tulos="yield/raportti/*_report.html")
            )
        ajo.vaiheet = v

    # ------------------------------------------------------------------ ajo
    def aloita(self, syote: Syote, tuotteet: list[str], vuodet: str = "") -> Ajo:
        tuotteet = [t for t in TUOTTEET if t in tuotteet] or list(TUOTTEET)
        aid = dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        ajo = Ajo(aid, self.juuri / aid, syote, tuotteet, vuodet.strip())
        for alakansio in ("growth", "space", "yield"):
            (ajo.kansio / alakansio).mkdir(parents=True, exist_ok=True)
        with self._lukko:
            self.ajot[aid] = ajo
        threading.Thread(target=self._aja, args=(ajo,), daemon=True, name=f"ajo-{aid}").start()
        return ajo

    def _kirjaa(self, ajo: Ajo, rivi: str) -> None:
        ajo.loki.append(f"{dt.datetime.now():%H:%M:%S} {rivi}")

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
            for v in ajo.vaiheet:
                self._aja_vaihe(ajo, v)
            ajo.tila = "virhe" if any(v.tila == "virhe" for v in ajo.vaiheet) else "valmis"
        except Exception as e:  # noqa: BLE001 - ajo ei saa kaataa palvelinta
            ajo.tila = "virhe"
            self._kirjaa(ajo, f"virhe: {e}")

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
        if v.tuote == "yield" and v.otsikko.startswith("Satokartta"):
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
        if koodi != 0:
            v.tila = "virhe"
            v.viesti = v.viesti or f"paluukoodi {koodi}"
            return
        v.tila = "valmis"
        if v.tulos:
            osumat = sorted(glob.glob(str(ajo.kansio / v.tulos)))
            if osumat:
                v.linkki = f"/tulokset/{ajo.id}/" + Path(osumat[-1]).relative_to(ajo.kansio).as_posix()
