"""Paikallinen palvelin: etusivu, syötteen tulkinta, tiedostojen vastaanotto ja ajot.

Vain standardikirjasto. Oletuksena kuunnellaan vain osoitetta 127.0.0.1, koska palvelin käynnistää
komentoja koneella.

| Reitti | |
|---|---|
| ``GET /`` | etusivu |
| ``GET /api/tila`` | ``{"ok": true}`` terveystarkistukselle (ei vaadi salasanaa) |
| ``POST /api/tulkitse`` | ``{"teksti"}`` -> tunnistetut lohkot, pisteet ja alueet |
| ``GET /api/asetukset`` | tarjolla olevat tuotteet ja otetaanko satotiedostoja vastaan |
| ``POST /api/tiedosto?nimi=…`` | tiedoston sisältö rungossa -> ``{"id", "syote"}``; hylätty satotiedosto: ``id`` on ``null`` |
| ``POST /api/ajo`` | ``{"teksti", "tiedostot": [id], "tuotteet": [...], "vuodet"}`` -> ``{"id"}`` |
| ``GET /api/ajo/<id>`` | ajon tilanne, vaiheet, linkit ja loki |
| ``GET /api/ajot?lohko=…`` | aiemmat ajot uusimmasta alkaen, valinnaisesti peruslohkon mukaan |
| ``GET /api/aiemmat?lohkot=a,b`` | jokaiselle lohkolle uusin ajo, jossa on tuloksia |
| ``GET /api/kaytto`` · ``/api/kaytto.csv`` | käyttölokin kooste kuukausittain · koko loki |
| ``GET /tulokset/<id>/<polku>`` | ajon tuotokset (raportit, katselimet); 4D-näkymä premium-käyttäjälle premium-versiona |
| ``POST /api/analyysi`` | premium, 4D-näkymän klikkaus: ``{"tunnus", "vuosi"}`` -> ``{"id", "tila"}`` (429 = kiintiö) |
| ``GET /api/analyysi/<id>`` | klikatun analyysin tila lähteittäin ja valmiina ``nakyma`` |

Kirjautuminen (HTTP Basic) ja premium-käyttäjät ovat tämän palvelimen asia. Premium-näkymä avataan täältä, joten
sen rajapintakutsut kulkevat saman kirjautumisen alla; palvelutason sisältö tulee Beanstalk Spacesta.
"""

from __future__ import annotations

import base64
import hmac
import json
import mimetypes
import re
import urllib.parse
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import lohkoanalyysi
from .ajot import Ajuri
from .syote import Syote, luokittele_tiedosto, tulkitse

DATA = Path(__file__).parent / "data"
SUURIN_TIEDOSTO = 2 * 1024**3
STAATTISET = {"/": "etusivu.html", "/valot.jpg": "valot.jpg"}


def _turvallinen_nimi(nimi: str) -> str:
    nimi = Path(nimi.replace("\\", "/")).name
    return re.sub(r"[^\w.\-]+", "_", nimi)[:120] or "tiedosto"


def tee_kasittelija(ajuri: Ajuri, tunnus: tuple[str, str] | None = None):
    """``tunnus`` = (käyttäjä, salasana): jos annettu, kaikki paitsi /api/tila vaativat HTTP Basic -kirjautumisen."""
    odotettu = base64.b64encode(f"{tunnus[0]}:{tunnus[1]}".encode()).decode() if tunnus else None
    lataukset = ajuri.juuri / "_lataukset"
    lataukset.mkdir(parents=True, exist_ok=True)

    class K(BaseHTTPRequestHandler):
        server_version = "peltolohko"

        def log_message(self, fmt, *args):  # hiljaisempi loki
            if "/api/ajo/" not in (args[0] if args else ""):
                super().log_message(fmt, *args)

        # -------------------------------------------------------------- apurit
        def _json(self, data, tila: int = 200) -> None:
            b = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(tila)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)

        def _tiedosto(self, polku: Path) -> None:
            if not polku.is_file():
                return self._json({"virhe": "ei löydy"}, 404)
            tyyppi = mimetypes.guess_type(polku.name)[0] or "application/octet-stream"
            if tyyppi.startswith("text/") or tyyppi.endswith("javascript"):
                tyyppi += "; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", tyyppi)
            self.send_header("Content-Length", str(polku.stat().st_size))
            self.end_headers()
            with polku.open("rb") as f:
                while lohko := f.read(1 << 16):
                    self.wfile.write(lohko)

        def _runko(self, enintaan: int = 10 * 1024**2) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            if n > enintaan:
                raise ValueError("liian suuri")
            return self.rfile.read(n)

        def _kayttaja(self) -> str:
            otsake = self.headers.get("Authorization", "")
            if otsake.startswith("Basic "):
                try:
                    return base64.b64decode(otsake[6:].strip()).decode().split(":", 1)[0]
                except (ValueError, UnicodeDecodeError):
                    return ""
            return ""

        def _sallittu(self) -> bool:
            if odotettu is None:
                return True
            otsake = self.headers.get("Authorization", "")
            if otsake.startswith("Basic ") and hmac.compare_digest(otsake[6:].strip(), odotettu):
                return True
            b = b"Kirjaudu sis\xc3\xa4\xc3\xa4n."
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Beanstalk", charset="UTF-8"')
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return False

        def _perusosoite(self) -> str:
            kaava = "https" if self.headers.get("X-Forwarded-Proto") == "https" else "http"
            isanta = re.sub(r"[^A-Za-z0-9.:\[\]-]", "", self.headers.get("Host", "")) or "127.0.0.1"
            return f"{kaava}://{isanta}"

        def _analyysin_tila(self, ajo) -> dict:
            v = lohkoanalyysi.tila(ajo)
            if "nakyma" in v:  # katselin hyväksyy vain täydet osoitteet
                v["nakyma"] = self._perusosoite() + v["nakyma"]
            return v

        # -------------------------------------------------------------- GET
        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            p = url.path
            if p == "/api/tila":
                return self._json({"ok": True})
            if not self._sallittu():
                return
            if p in STAATTISET:
                return self._tiedosto(DATA / STAATTISET[p])
            if p.startswith("/fontit/"):
                return self._tiedosto(DATA / "fontit" / _turvallinen_nimi(p[8:]))
            if m := re.fullmatch(r"/api/analyysi/([\w\-]+)", p):
                if not ajuri.premium(self._kayttaja()):
                    return self._json({"viesti": "lohkoanalyysi näkymästä kuuluu Premiumiin"}, 403)
                ajo = ajuri.ajot.get(m.group(1))
                if ajo is None or ajo.yksityinen:
                    return self._json({"viesti": "tuntematon analyysi"}, 404)
                return self._json(self._analyysin_tila(ajo))
            if m := re.fullmatch(r"/api/ajo/([\w\-]+)", p):
                ajo = ajuri.ajot.get(m.group(1))
                return self._json(ajo.tilanne()) if ajo else self._json({"virhe": "tuntematon ajo"}, 404)
            q = urllib.parse.parse_qs(url.query)
            if p == "/api/ajot":
                return self._json(ajuri.luettelo(q.get("lohko", [""])[0].replace(" ", "").split(",")))
            if p == "/api/aiemmat":
                return self._json(ajuri.aiemmat([t for t in q.get("lohkot", [""])[0].split(",") if t]))
            if p == "/api/kaytto":
                return self._json(ajuri.kaytto())
            if p == "/api/asetukset":
                return self._json(
                    {
                        "tuotteet": ajuri.tarjolla(),
                        "premium": ajuri.premium(self._kayttaja()),
                        "satodata": ajuri.satodata,
                        "satotiedosto_poistetaan": bool(ajuri.asetukset.get("satotiedosto_poistetaan")),
                        "satotulokset_sailytys_h": float(ajuri.asetukset.get("satotulokset_sailytys_h") or 0),
                    }
                )
            if p == "/api/kaytto.csv":
                polku = ajuri.juuri / "kaytto.csv"
                return self._tiedosto(polku) if polku.exists() else self._json({"virhe": "lokia ei vielä ole"}, 404)
            if m := re.fullmatch(r"/tulokset/([\w\-]+)/(.+)", p):
                ajo = ajuri.ajot.get(m.group(1))
                if ajo is None:  # vain tunnettujen ajojen tulokset; ei esim. ladattuja satotiedostoja
                    return self._json({"virhe": "ei löydy"}, 404)
                kansio = ajo.kansio
                kohde = (kansio / urllib.parse.unquote(m.group(2))).resolve()
                if not kohde.is_relative_to(kansio.resolve()):
                    return self._json({"virhe": "kielletty"}, 403)
                # 4D-näkymä: premium-käyttäjälle premium-versio samasta osoitteesta; muille sitä ei anneta lainkaan
                premium = ajuri.premium(self._kayttaja())
                if kohde.name.endswith(".premium.html") and not premium:
                    return self._json({"virhe": "kuuluu Premiumiin"}, 403)
                if premium and kohde.parent.name == "space" and kohde.suffix == ".html":
                    parempi = kohde.with_name(kohde.stem + ".premium.html")
                    if parempi.is_file():
                        kohde = parempi
                return self._tiedosto(kohde)
            return self._json({"virhe": "ei löydy"}, 404)

        # -------------------------------------------------------------- POST
        def do_POST(self):
            url = urllib.parse.urlparse(self.path)
            if not self._sallittu():
                return
            try:
                if url.path == "/api/tulkitse":
                    data = json.loads(self._runko() or b"{}")
                    return self._json(tulkitse(str(data.get("teksti", ""))).sanakirja())
                if url.path == "/api/tiedosto":
                    nimi = urllib.parse.parse_qs(url.query).get("nimi", ["tiedosto"])[0]
                    n = int(self.headers.get("Content-Length") or 0)
                    if n > SUURIN_TIEDOSTO:
                        return self._json({"virhe": "tiedosto on liian suuri"}, 413)
                    ajuri.siivoa_lataukset()  # vanhat käyttämättömät lataukset pois
                    tid = uuid.uuid4().hex[:12]
                    # satunnainen nimi: alkuperäinen tiedostonimi (voi sisältää henkilön nimen) ei jää palvelimelle
                    paate = re.sub(r"[^a-z0-9.]", "", Path(nimi).suffix.lower())[:10]
                    kohde = lataukset / f"{tid}{paate}"
                    with kohde.open("wb") as f:
                        jaljella = n
                        while jaljella > 0:
                            lohko = self.rfile.read(min(1 << 20, jaljella))
                            if not lohko:
                                break
                            f.write(lohko)
                            jaljella -= len(lohko)
                    luokka = luokittele_tiedosto(kohde, nimi)
                    if luokka.satotiedostot and not ajuri.satodata:
                        kohde.unlink(missing_ok=True)  # ei tallenneta: tässä versiossa ei yksityistä dataa
                        luokka.satotiedostot = []
                        luokka.huomiot.append(f"{nimi}: satotiedostoja ei oteta vastaan tässä versiossa")
                        return self._json({"id": None, "syote": luokka.sanakirja()})
                    return self._json({"id": kohde.name, "syote": luokka.sanakirja()})
                if url.path == "/api/analyysi":
                    if not ajuri.premium(self._kayttaja()):
                        return self._json({"viesti": "lohkoanalyysi näkymästä kuuluu Premiumiin"}, 403)
                    data = json.loads(self._runko(4096) or b"{}")
                    try:
                        ajo, uusi = lohkoanalyysi.aloita(
                            ajuri, str(data.get("tunnus", "")), int(data.get("vuosi") or 0), self._kayttaja()
                        )
                    except lohkoanalyysi.Kiintio as e:
                        return self._json({"viesti": str(e)}, 429)
                    except (ValueError, TypeError) as e:
                        return self._json({"viesti": f"virheellinen pyyntö: {e}"}, 400)
                    return self._json({"id": ajo.id, "tila": self._analyysin_tila(ajo)["tila"]}, 202 if uusi else 200)
                if url.path == "/api/ajo":
                    data = json.loads(self._runko() or b"{}")
                    s = tulkitse(str(data.get("teksti", "")))
                    for tid in data.get("tiedostot", []):
                        polku = lataukset / _turvallinen_nimi(str(tid))
                        if polku.is_file():
                            s.yhdista(luokittele_tiedosto(polku, polku.name))
                    if s.tyhja():
                        return self._json(
                            {
                                "virhe": "anna peltolohkonumero, koordinaatit tai alue"
                                + (" tai satotiedosto" if ajuri.satodata else ""),
                                "syote": s.sanakirja(),
                            },
                            400,
                        )
                    ajo = ajuri.aloita(
                        s,
                        list(data.get("tuotteet") or []),
                        str(data.get("vuodet") or s.vuodet),
                        kayttaja=self._kayttaja(),
                        osoite=self.client_address[0],
                    )
                    return self._json({"id": ajo.id, "syote": s.sanakirja()}, 201)
            except (ValueError, json.JSONDecodeError) as e:
                return self._json({"virhe": str(e)}, 400)
            return self._json({"virhe": "ei löydy"}, HTTPStatus.NOT_FOUND)

    return K


def palvelin(
    ajuri: Ajuri, osoite: str = "127.0.0.1", portti: int = 8765, tunnus: tuple[str, str] | None = None
) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((osoite, portti), tee_kasittelija(ajuri, tunnus))


__all__ = ["Syote", "palvelin", "tee_kasittelija"]
