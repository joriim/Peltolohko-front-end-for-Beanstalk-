"""Premium: 4D-näkymän premium-versio kirjautuneelle premium-käyttäjälle ja analyysi lohkoa klikkaamalla."""

import base64
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from peltolohko.ajot import Ajuri
from peltolohko.palvelin import palvelin


def _palvelin(tmp_path, rekisteri, asetukset, tunnus=None):
    varasto = tmp_path / "varasto"
    for t in ("1641431471", "1641431472"):  # Growth Analysisin viemä näkymän data varastossa
        (varasto / f"{t}-1").mkdir(parents=True)
        (varasto / f"{t}-1" / "nakyma_2025.json").write_text("{}")
    ajuri = Ajuri(tmp_path / "t", {"varasto": str(varasto), "tuotteet": ["growth", "space"], **asetukset}, rekisteri)
    s = palvelin(ajuri, "127.0.0.1", 0, tunnus)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, ajuri, f"http://127.0.0.1:{s.server_address[1]}"


def pyynto(url, data=None, kirjautuminen=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method="POST" if body is not None else "GET")
    if kirjautuminen:
        req.add_header("Authorization", "Basic " + base64.b64encode(kirjautuminen.encode()).decode())
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _odota(u, aid, kirjautuminen=None):
    for _ in range(200):
        v = json.loads(pyynto(f"{u}/api/analyysi/{aid}", kirjautuminen=kirjautuminen)[1])
        if v["tila"] != "kaynnissa":
            return v
        time.sleep(0.05)
    raise AssertionError(v)


def test_klikkaus_kaynnistaa_analyysin_ja_premium_nakyma(tmp_path, tyokalut, rekisteri):
    s, ajuri, u = _palvelin(tmp_path, rekisteri, {"premium": True, "analyyseja_paivassa": 1})
    try:
        k, b = pyynto(u + "/api/analyysi", {"tunnus": "1641431471-1", "vuosi": 2025})
        assert k == 202
        aid = json.loads(b)["id"]
        v = _odota(u, aid)
        assert v["tila"] == "valmis" and v["nakyma"] == f"{u}/tulokset/{aid}/space/1641431471-1.html"
        tilat = {x["avain"]: x["tila"] for x in v["lahteet"]}
        assert tilat["lpis"] == "valmis" and tilat["s2"] == "valmis" and tilat["gtk"] == "valmis"
        assert tilat["analyysi"] == "valmis" and tilat["planet"] == "ohitettu"
        # sama osoite antaa premium-käyttäjälle premium-version
        assert pyynto(v["nakyma"])[1] == b"<p>premium</p>"
        ajo = ajuri.ajot[aid]
        assert ajo.lahde == "klikkaus" and ajo.tuotteet == ["growth", "space"] and ajo.vuodet == "2025"
        # sama lohko uudelleen: valmis ajo palautuu, ei kuluta kiintiötä
        k, b = pyynto(u + "/api/analyysi", {"tunnus": "1641431471-1", "vuosi": 2025})
        assert k == 200 and json.loads(b) == {"id": aid, "tila": "valmis"}
        # toinen lohko: päivän kiintiö (1) on käytetty
        k, b = pyynto(u + "/api/analyysi", {"tunnus": "1641431472-1", "vuosi": 2025})
        assert k == 429 and "kiintiö" in json.loads(b)["viesti"]
        assert pyynto(u + "/api/analyysi", {"tunnus": "../etc", "vuosi": 2025})[0] == 400
        assert pyynto(u + "/api/analyysi", {"tunnus": "1641431471-1", "vuosi": 1900})[0] == 400
        assert json.loads(pyynto(u + "/api/asetukset")[1])["premium"] is True
    finally:
        s.shutdown()


def test_ilman_premiumia_ei_analyysia_eika_premium_nakymaa(tmp_path, tyokalut, rekisteri):
    s, ajuri, u = _palvelin(tmp_path, rekisteri, {})
    try:
        assert pyynto(u + "/api/analyysi", {"tunnus": "1641431471-1", "vuosi": 2025})[0] == 403
        aid = json.loads(pyynto(u + "/api/ajo", {"teksti": "1641431471-1", "tuotteet": ["growth", "space"]})[1])["id"]
        for _ in range(200):
            if json.loads(pyynto(f"{u}/api/ajo/{aid}")[1])["tila"] != "käynnissä":
                break
            time.sleep(0.05)
        assert pyynto(f"{u}/tulokset/{aid}/space/1641431471-1.html")[1] == b"<p>ok</p>"  # freemium-näkymä
        assert not (ajuri.ajot[aid].kansio / "space" / "1641431471-1.premium.html").exists()
        assert pyynto(f"{u}/api/analyysi/{aid}")[0] == 403
    finally:
        s.shutdown()


def test_premium_on_kayttajakohtainen(tmp_path, tyokalut, rekisteri):
    """Kirjautuminen on etusivun: premium_kayttajat ratkaisee, kuka saa premium-näkymän ja analyysin."""
    s, ajuri, u = _palvelin(tmp_path, rekisteri, {"premium_kayttajat": ["beanstalk"]}, ("beanstalk", "salaisuus"))
    try:
        oma = "beanstalk:salaisuus"
        assert pyynto(u + "/api/analyysi", {"tunnus": "1641431471-1", "vuosi": 2025})[0] == 401  # ei kirjautumista
        aid = json.loads(pyynto(u + "/api/analyysi", {"tunnus": "1641431471-1", "vuosi": 2025}, oma)[1])["id"]
        v = _odota(u, aid, oma)
        assert pyynto(v["nakyma"], kirjautuminen=oma)[1] == b"<p>premium</p>"
        assert ajuri.ajot[aid].kayttaja == "beanstalk"
        assert ajuri.premium("beanstalk") and not ajuri.premium("joku-muu") and not ajuri.premium("")
        # premium-tiedostoa ei anneta suoraan, jos käyttäjä ei ole premium
        ajuri.asetukset["premium_kayttajat"] = []
        assert pyynto(f"{u}/tulokset/{aid}/space/1641431471-1.premium.html", kirjautuminen=oma)[0] == 403
        assert pyynto(v["nakyma"], kirjautuminen=oma)[1] == b"<p>ok</p>"
    finally:
        s.shutdown()


@pytest.mark.parametrize("tila, odotettu", [("jonossa", "jonossa"), ("käynnissä", "haetaan")])
def test_lahteet_ennen_valmistumista(tmp_path, tyokalut, rekisteri, tila, odotettu):
    from peltolohko import lohkoanalyysi
    from peltolohko.ajot import Ajo
    from peltolohko.syote import Syote

    ajo = Ajo("a", tmp_path, Syote(lohkot=["1641431471-1"]), ["growth"], "2025", tila=tila)
    assert {x["avain"]: x["tila"] for x in lohkoanalyysi.lahteet(ajo)}["lpis"] == odotettu
    ajo.tila = "käynnissä"
    ajo.loki = ["12:00:00   2025: porrastettu ketju (HLS + S1)…", "12:00:05     3 lohkovuotta epäonnistui (tahtiraja); uusinta…"]
    x = {d["avain"]: d for d in lohkoanalyysi.lahteet(ajo)}
    assert x["lpis"]["tila"] == "valmis" and x["s1"]["tila"] == "uudelleen" and "tahtiraja" in x["s2"]["viesti"]
    assert lohkoanalyysi.tila(ajo)["tila"] == "kaynnissa"
