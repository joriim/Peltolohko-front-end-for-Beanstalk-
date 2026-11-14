import json
import threading
import time
import urllib.request

import pytest

from peltolohko.ajot import Ajuri
from peltolohko.palvelin import palvelin


@pytest.fixture
def osoite(tmp_path, tyokalut, rekisteri):
    s = palvelin(Ajuri(tmp_path / "t", {}, rekisteri), "127.0.0.1", 0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()


@pytest.fixture
def osoite_sato(tmp_path, tyokalut, rekisteri):
    """Palvelin, jossa satodata on sallittu (MVP:n oletuksena ei)."""
    asetukset = {"satodata": True, "tuotteet": ["yield", "growth", "space"]}
    s = palvelin(Ajuri(tmp_path / "t", asetukset, rekisteri), "127.0.0.1", 0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()


def pyynto(url, data=None, raaka=None):
    body = raaka if raaka is not None else (json.dumps(data).encode() if data is not None else None)
    req = urllib.request.Request(url, data=body, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_etusivu_ja_kuvat(osoite):
    k, b = pyynto(osoite + "/")
    assert k == 200 and b"Beanstalk" in b
    assert pyynto(osoite + "/valot.jpg")[0] == 200
    assert pyynto(osoite + "/fontit/jost-latin-400-normal.woff2")[0] == 200


def test_tulkitse(osoite):
    k, b = pyynto(osoite + "/api/tulkitse", {"teksti": "1641431471, 62.79 22.84"})
    d = json.loads(b)
    assert k == 200 and d["lohkot"] == ["1641431471"]


def test_tyhja_ajo_hylataan(osoite):
    assert pyynto(osoite + "/api/ajo", {"teksti": "hei"})[0] == 400


def test_tiedosto_ja_ajo(osoite_sato):
    osoite = osoite_sato
    k, b = pyynto(osoite + "/api/tiedosto?nimi=../../sato.csv", raaka=b"a;b\n1;2\n")
    d = json.loads(b)
    assert k == 200 and d["id"].endswith("_sato.csv") and d["syote"]["satotiedostot"]
    k, b = pyynto(osoite + "/api/ajo", {"teksti": "1641431471", "tiedostot": [d["id"]], "tuotteet": ["growth", "yield"]})
    aid = json.loads(b)["id"]
    for _ in range(200):
        t = json.loads(pyynto(f"{osoite}/api/ajo/{aid}")[1])
        if t["tila"] not in ("jonossa", "käynnissä"):
            break
        time.sleep(0.05)
    assert t["tila"] == "valmis", t["loki"]
    assert any("satotiedosto poistettu" in r for r in t["loki"])  # MVP: raakatiedosto ei jää palvelimelle
    linkki = next(v["linkki"] for v in t["vaiheet"] if v["linkki"])
    assert pyynto(osoite + linkki)[0] == 200
    assert pyynto(f"{osoite}/tulokset/{aid}/..%2F..%2Fetc")[0] in (403, 404)


def test_salasana(tmp_path, rekisteri):
    import base64

    s = palvelin(Ajuri(tmp_path / "t", {}, rekisteri), "127.0.0.1", 0, ("beanstalk", "salaisuus"))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    u = f"http://127.0.0.1:{s.server_address[1]}"
    try:
        assert pyynto(u + "/")[0] == 401
        assert pyynto(u + "/api/tulkitse", {"teksti": "1641431471"})[0] == 401
        assert pyynto(u + "/api/tila")[0] == 200  # terveystarkistus ilman salasanaa
        req = urllib.request.Request(u + "/")
        req.add_header("Authorization", "Basic " + base64.b64encode(b"beanstalk:salaisuus").decode())
        with urllib.request.urlopen(req) as r:
            assert r.status == 200
    finally:
        s.shutdown()


def test_aiemmat_ja_kaytto_reitit(osoite):
    k, b = pyynto(osoite + "/api/ajo", {"teksti": "1641431471", "tuotteet": ["growth"]})
    aid = json.loads(b)["id"]
    for _ in range(200):
        if json.loads(pyynto(f"{osoite}/api/ajo/{aid}")[1])["tila"] not in ("jonossa", "käynnissä"):
            break
        time.sleep(0.05)
    lista = json.loads(pyynto(osoite + "/api/ajot?lohko=1641431471")[1])
    assert [x["id"] for x in lista] == [aid]
    assert json.loads(pyynto(osoite + "/api/ajot?lohko=9999999999")[1]) == []
    assert aid == json.loads(pyynto(osoite + "/api/aiemmat?lohkot=1641431471-1")[1])["1641431471"]["id"]
    assert json.loads(pyynto(osoite + "/api/kaytto")[1])["rivit"][0]["ajot"] == 1
    k, b = pyynto(osoite + "/api/kaytto.csv")
    assert k == 200 and b.startswith(b"aika,ajo,")


def test_ladattuja_tiedostoja_ei_jaeta(osoite_sato):
    osoite = osoite_sato
    k, b = pyynto(osoite + "/api/tiedosto?nimi=sato.csv", raaka=b"salainen")
    tid = json.loads(b)["id"]
    assert pyynto(f"{osoite}/tulokset/_lataukset/{tid}")[0] == 404


def test_asetukset_oletus(osoite):
    a = json.loads(pyynto(osoite + "/api/asetukset")[1])
    assert a == {"tuotteet": ["yield", "growth", "space"], "satodata": True, "satotiedosto_poistetaan": True}


def test_ilman_satodataa_tiedostoa_ei_oteta_vastaan(tmp_path, tyokalut, rekisteri):
    s = palvelin(Ajuri(tmp_path / "t", {"satodata": False}, rekisteri), "127.0.0.1", 0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    osoite = f"http://127.0.0.1:{s.server_address[1]}"
    a = json.loads(pyynto(osoite + "/api/asetukset")[1])
    assert a["tuotteet"] == ["growth", "space"] and a["satodata"] is False
    k, b = pyynto(osoite + "/api/tiedosto?nimi=sato.csv", raaka=b"lat;lon;sato\n")
    d = json.loads(b)
    assert k == 200 and d["id"] is None and not d["syote"]["satotiedostot"]
    assert "ei oteta vastaan" in d["syote"]["huomiot"][0]
    assert not list((tmp_path / "t" / "_lataukset").iterdir())  # ei tallennettu
    kml = (
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark><Polygon><outerBoundaryIs><LinearRing>'
        "<coordinates>22.84,62.79,0 22.85,62.79,0 22.85,62.80,0 22.84,62.79,0</coordinates>"
        "</LinearRing></outerBoundaryIs></Polygon></Placemark></kml>"
    )
    k, b = pyynto(osoite + "/api/tiedosto?nimi=raja.kml", raaka=kml.encode())
    assert json.loads(b)["id"].endswith("_raja.kml")  # rajaus otetaan vastaan
    s.shutdown()
