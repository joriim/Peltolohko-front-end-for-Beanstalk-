"""Paikallinen palvelin: etusivu, syötteen tulkinta, tiedostojen vastaanotto ja ajot.

Vain standardikirjasto. Oletuksena kuunnellaan vain osoitetta 127.0.0.1, koska palvelin käynnistää
komentoja koneella.

| Reitti | |
|---|---|
| ``GET /`` | etusivu |
| ``POST /api/tulkitse`` | ``{"teksti"}`` -> tunnistetut lohkot, pisteet ja alueet |
| ``POST /api/tiedosto?nimi=…`` | tiedoston sisältö rungossa -> ``{"id", "syote"}`` |
| ``POST /api/ajo`` | ``{"teksti", "tiedostot": [id], "tuotteet": [...], "vuodet"}`` -> ``{"id"}`` |
| ``GET /api/ajo/<id>`` | ajon tilanne, vaiheet, linkit ja loki |
| ``GET /tulokset/<id>/<polku>`` | ajon tuotokset (raportit, katselimet) |
"""

from __future__ import annotations

import json
import mimetypes
import re
import urllib.parse
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .ajot import Ajuri
from .syote import Syote, luokittele_tiedosto, tulkitse

DATA = Path(__file__).parent / "data"
SUURIN_TIEDOSTO = 2 * 1024**3
STAATTISET = {"/": "etusivu.html", "/valot.jpg": "valot.jpg"}


def _turvallinen_nimi(nimi: str) -> str:
    nimi = Path(nimi.replace("\\", "/")).name
    return re.sub(r"[^\w.\-]+", "_", nimi)[:120] or "tiedosto"


def tee_kasittelija(ajuri: Ajuri):
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

        # -------------------------------------------------------------- GET
        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            p = url.path
            if p in STAATTISET:
                return self._tiedosto(DATA / STAATTISET[p])
            if p.startswith("/fontit/"):
                return self._tiedosto(DATA / "fontit" / _turvallinen_nimi(p[8:]))
            if m := re.fullmatch(r"/api/ajo/([\w\-]+)", p):
                ajo = ajuri.ajot.get(m.group(1))
                return self._json(ajo.tilanne()) if ajo else self._json({"virhe": "tuntematon ajo"}, 404)
            if m := re.fullmatch(r"/tulokset/([\w\-]+)/(.+)", p):
                ajo = ajuri.ajot.get(m.group(1))
                kansio = ajo.kansio if ajo else ajuri.juuri / m.group(1)
                kohde = (kansio / urllib.parse.unquote(m.group(2))).resolve()
                if not kohde.is_relative_to(kansio.resolve()):
                    return self._json({"virhe": "kielletty"}, 403)
                return self._tiedosto(kohde)
            return self._json({"virhe": "ei löydy"}, 404)

        # -------------------------------------------------------------- POST
        def do_POST(self):
            url = urllib.parse.urlparse(self.path)
            try:
                if url.path == "/api/tulkitse":
                    data = json.loads(self._runko() or b"{}")
                    return self._json(tulkitse(str(data.get("teksti", ""))).sanakirja())
                if url.path == "/api/tiedosto":
                    nimi = urllib.parse.parse_qs(url.query).get("nimi", ["tiedosto"])[0]
                    n = int(self.headers.get("Content-Length") or 0)
                    if n > SUURIN_TIEDOSTO:
                        return self._json({"virhe": "tiedosto on liian suuri"}, 413)
                    tid = uuid.uuid4().hex[:12]
                    kohde = lataukset / f"{tid}_{_turvallinen_nimi(nimi)}"
                    with kohde.open("wb") as f:
                        jaljella = n
                        while jaljella > 0:
                            lohko = self.rfile.read(min(1 << 20, jaljella))
                            if not lohko:
                                break
                            f.write(lohko)
                            jaljella -= len(lohko)
                    return self._json({"id": kohde.name, "syote": luokittele_tiedosto(kohde, nimi).sanakirja()})
                if url.path == "/api/ajo":
                    data = json.loads(self._runko() or b"{}")
                    s = tulkitse(str(data.get("teksti", "")))
                    for tid in data.get("tiedostot", []):
                        polku = lataukset / _turvallinen_nimi(str(tid))
                        if polku.is_file():
                            s.yhdista(luokittele_tiedosto(polku, polku.name.split("_", 1)[-1]))
                    if s.tyhja():
                        return self._json(
                            {"virhe": "anna peltolohkonumero, koordinaatit, alue tai satotiedosto", "syote": s.sanakirja()}, 400
                        )
                    ajo = ajuri.aloita(s, list(data.get("tuotteet") or []), str(data.get("vuodet") or ""))
                    return self._json({"id": ajo.id, "syote": s.sanakirja()}, 201)
            except (ValueError, json.JSONDecodeError) as e:
                return self._json({"virhe": str(e)}, 400)
            return self._json({"virhe": "ei löydy"}, HTTPStatus.NOT_FOUND)

    return K


def palvelin(ajuri: Ajuri, osoite: str = "127.0.0.1", portti: int = 8765) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((osoite, portti), tee_kasittelija(ajuri))


__all__ = ["Syote", "palvelin", "tee_kasittelija"]
