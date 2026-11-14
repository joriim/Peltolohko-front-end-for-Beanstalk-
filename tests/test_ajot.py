import datetime as dt
import json
import time

from peltolohko.ajot import Ajuri, satovuosi, yield_asetus
from peltolohko.syote import tulkitse


def odota(ajo, s=20):
    t0 = time.time()
    while ajo.tila in ("jonossa", "käynnissä") and time.time() - t0 < s:
        time.sleep(0.05)
    return ajo


def test_satovuosi():
    assert satovuosi("2023-2025") == 2025
    assert satovuosi("", dt.date(2026, 11, 14)) == 2026
    assert satovuosi("", dt.date(2026, 5, 1)) == 2025


def test_koko_ajo(tmp_path, tyokalut, rekisteri):
    a = Ajuri(tmp_path / "t", {"varasto": str(tmp_path / "v")}, rekisteri)
    csv = tmp_path / "abc_sato.csv"
    csv.write_text("x")
    s = tulkitse("1641431471\n62.79, 22.84")
    s.satotiedostot.append(str(csv))
    ajo = odota(a.aloita(s, ["yield", "growth", "space"], "2024-2025"))
    assert ajo.lohkot == ["1641431471", "7430153621"]
    tilat = {v.otsikko: v.tila for v in ajo.vaiheet}
    assert tilat["Kasvukuntoanalyysi 1641431471"] == "valmis"
    assert tilat["4D-näkymä 1641431471"] == "ohitettu"  # ei näkymädataa varastossa
    assert tilat["Satoraportti"] == "valmis"
    assert ajo.tila == "valmis"
    linkit = [v.linkki for v in ajo.vaiheet if v.linkki]
    assert f"/tulokset/{ajo.id}/growth/1641431471.html" in linkit
    assert any(x.endswith("_report.html") for x in linkit)
    kasvu = next(v for v in ajo.vaiheet if v.otsikko == "Kasvukuntoanalyysi 1641431471")
    assert kasvu.komento[-2:] == ["--vuodet", "2024-2025"]
    asetus = json.loads((ajo.kansio / "yield" / "field.yaml").read_text())
    assert asetus["year"] == 2025 and asetus["parcel_ids"] == ["1641431471", "7430153621"]
    assert asetus["harvest_logs"][0]["path"].endswith("abc_sato.csv")


def test_space_kayttaa_varaston_dataa(tmp_path, tyokalut, rekisteri):
    v = tmp_path / "v" / "1641431471" / "2025"
    v.mkdir(parents=True)
    (v / "space.json").write_text("{}")
    a = Ajuri(tmp_path / "t", {"varasto": str(tmp_path / "v")}, rekisteri)
    ajo = odota(a.aloita(tulkitse("1641431471"), ["space"]))
    (vaihe,) = ajo.vaiheet
    assert vaihe.tila == "valmis" and vaihe.linkki.endswith("space/1641431471.html")


def test_puuttuva_tyokalu(tmp_path, rekisteri, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    a = Ajuri(tmp_path / "t", {}, rekisteri)
    ajo = odota(a.aloita(tulkitse("1641431471"), ["growth"]))
    assert ajo.tila == "virhe"
    assert "kasvukunto ei ole asennettu" in ajo.vaiheet[0].viesti


def test_ei_lohkoja(tmp_path, rekisteri):
    class Tyhja:
        def alueella(self, r):
            return []

    a = Ajuri(tmp_path / "t", {}, Tyhja())
    ajo = odota(a.aloita(tulkitse("62.79 22.84; 62.80 22.85"), ["growth"]))
    assert ajo.tila == "virhe" and not ajo.vaiheet


def test_yield_ilman_tiedostoa_ohitetaan(tmp_path, tyokalut, rekisteri):
    a = Ajuri(tmp_path / "t", {}, rekisteri)
    ajo = odota(a.aloita(tulkitse("1641431471"), ["yield"]))
    (vaihe,) = ajo.vaiheet
    assert vaihe.tila == "ohitettu" and "satotiedosto" in vaihe.viesti
    assert ajo.tila == "valmis"
    assert not (ajo.kansio / "yield" / "field.yaml").exists()
    assert yield_asetus(ajo, None)["harvest_logs"] == []


def test_ajot_jonossa_yksi_kerrallaan(tmp_path, tyokalut, rekisteri):
    import os
    import stat

    hidas = tyokalut / "kasvukunto"
    hidas.write_text("#!/usr/bin/env python3\nimport time; time.sleep(0.6)\n")
    hidas.chmod(hidas.stat().st_mode | stat.S_IEXEC)
    assert os.access(hidas, os.X_OK)
    a = Ajuri(tmp_path / "t", {}, rekisteri)
    eka = a.aloita(tulkitse("1641431471"), ["growth"])
    toka = a.aloita(tulkitse("1641431472"), ["growth"])
    time.sleep(0.3)
    assert {eka.tila, toka.tila} == {"käynnissä", "jonossa"}
    odota(eka)
    odota(toka)
    assert eka.tila == toka.tila == "valmis"


def test_ajot_sailyvat_uudelleenkaynnistyksessa(tmp_path, tyokalut, rekisteri):
    a = Ajuri(tmp_path / "t", {"varasto": str(tmp_path / "v")}, rekisteri)
    ajo = odota(a.aloita(tulkitse("1641431471"), ["growth"], kayttaja="miiro", osoite="192.168.1.20"))
    assert (ajo.kansio / "ajo.json").exists()
    b = Ajuri(tmp_path / "t", {"varasto": str(tmp_path / "v")}, rekisteri)
    sama = b.ajot[ajo.id]
    assert sama.tila == "valmis" and sama.lohkot == ["1641431471"] and sama.kayttaja == "miiro"
    assert sama.vaiheet[0].linkki == ajo.vaiheet[0].linkki


def test_kesken_jaanyt_ajo_merkitaan_keskeytyneeksi(tmp_path, rekisteri):
    from peltolohko.ajot import Ajo, Vaihe

    kesken = Ajo("20260101-000000-abcd", tmp_path / "t" / "20260101-000000-abcd", tulkitse("1641431471"), ["growth"])
    kesken.kansio.mkdir(parents=True)
    kesken.tila = "käynnissä"
    kesken.vaiheet = [Vaihe("growth", "Kasvukuntoanalyysi 1641431471", ["kasvukunto"], tila="käynnissä")]
    kesken.tallenna()
    b = Ajuri(tmp_path / "t", {}, rekisteri)
    luettu = b.ajot["20260101-000000-abcd"]
    assert luettu.tila == "keskeytynyt" and luettu.vaiheet[0].tila == "keskeytynyt"


def test_luettelo_ja_aiemmat(tmp_path, tyokalut, rekisteri):
    a = Ajuri(tmp_path / "t", {"varasto": str(tmp_path / "v")}, rekisteri)
    eka = odota(a.aloita(tulkitse("1641431471"), ["growth"]))
    toka = odota(a.aloita(tulkitse("1641431472-2"), ["growth"]))
    assert [x["id"] for x in a.luettelo()] == [toka.id, eka.id]
    assert [x["id"] for x in a.luettelo(["1641431472"])] == [toka.id]
    assert a.luettelo(["9999999999"]) == []
    assert a.aiemmat(["1641431471-1", "9999999999"]) == {"1641431471": {"id": eka.id, "alkoi": eka.alkoi}}
    assert a.luettelo()[0]["linkit"][0]["tuote"] == "growth"


def test_kayttoloki(tmp_path, tyokalut, rekisteri):
    v = tmp_path / "v"
    a = Ajuri(tmp_path / "t", {"varasto": str(v)}, rekisteri)
    ajo = odota(a.aloita(tulkitse("1641431471"), ["growth", "yield"], kayttaja="miiro"))
    kasvu = ajo.vaiheet[0]
    assert kasvu.kaytto["pu_arvio"] == 13.3 and kasvu.kaytto["lataus_mt_arvio"] == 474.1
    assert "varasto_kasvu_mt" in kasvu.kaytto
    import csv

    rivit = list(csv.DictReader((tmp_path / "t" / "kaytto.csv").open(encoding="utf-8")))
    assert len(rivit) == len(ajo.vaiheet) and rivit[0]["kayttaja"] == "miiro" and rivit[0]["pu_arvio"] == "13.3"
    (k,) = a.kaytto()["rivit"]
    assert k["ajot"] == 1 and k["pu_arvio"] == 13.3 and k["kayttaja"] == "miiro"
