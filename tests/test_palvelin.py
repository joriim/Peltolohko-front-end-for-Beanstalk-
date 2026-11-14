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


def test_tiedosto_ja_ajo(osoite):
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
    linkki = next(v["linkki"] for v in t["vaiheet"] if v["linkki"])
    assert pyynto(osoite + linkki)[0] == 200
    assert pyynto(f"{osoite}/tulokset/{aid}/..%2F..%2Fetc")[0] in (403, 404)
