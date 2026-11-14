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


SATO = {"satodata": True, "tuotteet": ["yield", "growth", "space"]}


def test_koko_ajo(tmp_path, tyokalut, rekisteri):
    a = Ajuri(tmp_path / "t", {"varasto": str(tmp_path / "v"), **SATO}, rekisteri)
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
    a = Ajuri(tmp_path / "t", SATO, rekisteri)
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
    ajo = odota(a.aloita(tulkitse("1641431471"), ["growth"], kayttaja="miiro"))
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


def test_ilman_satodataa(tmp_path, tyokalut, rekisteri):
    a = Ajuri(tmp_path / "t", {"varasto": str(tmp_path / "v"), "satodata": False}, rekisteri)
    assert a.tarjolla() == ["growth", "space"] and not a.satodata
    csv = tmp_path / "abc_sato.csv"
    csv.write_text("x")
    s = tulkitse("1641431471")
    s.satotiedostot.append(str(csv))
    ajo = odota(a.aloita(s, ["yield", "growth"]))
    assert ajo.tuotteet == ["growth"] and not ajo.syote.satotiedostot
    assert all(v.tuote != "yield" for v in ajo.vaiheet)
    # Yield vaatii satodatan: pelkkä tuotteet-asetus ei riitä
    assert Ajuri(tmp_path / "t2", {"tuotteet": ["yield", "growth"], "satodata": False}, rekisteri).tarjolla() == ["growth"]


def test_satotiedosto_poistetaan_ajon_jalkeen(tmp_path, tyokalut, rekisteri):
    a = Ajuri(tmp_path / "t", {"varasto": str(tmp_path / "v")}, rekisteri)
    assert a.satodata and a.tarjolla() == ["yield", "growth", "space"]  # MVP:n oletus
    a.lataukset.mkdir(parents=True, exist_ok=True)
    lataus = a.lataukset / "abc123_puinti.csv"
    lataus.write_text("lat;lon;sato")
    muualla = tmp_path / "oma.csv"  # latauskansion ulkopuolista ei kosketa
    muualla.write_text("x")
    s = tulkitse("1641431471")
    s.satotiedostot += [str(lataus), str(muualla)]
    ajo = odota(a.aloita(s, ["yield"]))
    assert ajo.tila == "valmis"
    assert not lataus.exists() and muualla.exists()
    assert any("satotiedosto poistettu" in r and "puinti.csv" in r for r in ajo.loki)


def test_satotiedosto_poistetaan_myos_virheessa(tmp_path, rekisteri, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # työkaluja ei ole: vaiheet päättyvät virheeseen
    a = Ajuri(tmp_path / "t", {}, rekisteri)
    a.lataukset.mkdir(parents=True, exist_ok=True)
    lataus = a.lataukset / "abc_sato.csv"
    lataus.write_text("x")
    s = tulkitse("1641431471")
    s.satotiedostot.append(str(lataus))
    ajo = odota(a.aloita(s, ["yield"]))
    assert ajo.tila == "virhe" and not lataus.exists()


def test_sailytys_asetuksella(tmp_path, tyokalut, rekisteri):
    a = Ajuri(tmp_path / "t", {"satotiedosto_poistetaan": False}, rekisteri)
    a.lataukset.mkdir(parents=True, exist_ok=True)
    lataus = a.lataukset / "abc_sato.csv"
    lataus.write_text("x")
    s = tulkitse("1641431471")
    s.satotiedostot.append(str(lataus))
    odota(a.aloita(s, ["yield"]))
    assert lataus.exists()


def test_kayttamattomat_lataukset_siivotaan(tmp_path, rekisteri):
    import os

    a = Ajuri(tmp_path / "t", {}, rekisteri)
    a.lataukset.mkdir(parents=True, exist_ok=True)
    vanha, uusi = a.lataukset / "a_vanha.csv", a.lataukset / "b_uusi.csv"
    vanha.write_text("x")
    uusi.write_text("x")
    tunti_sitten = time.time() - 2 * 3600
    os.utime(vanha, (tunti_sitten, tunti_sitten))
    assert a.siivoa_lataukset() == ["a_vanha.csv"]
    assert uusi.exists()


def test_keskeytyneen_ajon_satotiedosto_poistetaan(tmp_path, rekisteri):
    from peltolohko.ajot import Ajo

    juuri = tmp_path / "t"
    (juuri / "_lataukset").mkdir(parents=True)
    lataus = juuri / "_lataukset" / "abc_sato.csv"
    lataus.write_text("x")
    s = tulkitse("1641431471")
    s.satotiedostot.append(str(lataus))
    kesken = Ajo("20260101-000000-abcd", juuri / "20260101-000000-abcd", s, ["yield"])
    kesken.kansio.mkdir()
    kesken.tila = "käynnissä"
    kesken.tallenna()
    Ajuri(juuri, {}, rekisteri)  # palvelin käynnistyy uudelleen
    assert not lataus.exists()


def test_pistetason_satomittaukset_poistetaan_raportin_jalkeen(tmp_path, tyokalut, rekisteri):
    a = Ajuri(tmp_path / "t", {"varasto": str(tmp_path / "v")}, rekisteri)
    a.lataukset.mkdir(parents=True, exist_ok=True)
    lataus = a.lataukset / "0a1b2c3d4e5f.csv"
    lataus.write_text("x")
    s = tulkitse("1641431471")
    s.satotiedostot.append(str(lataus))
    ajo = odota(a.aloita(s, ["yield"]))
    tuotos = ajo.kansio / "yield" / "tuotos"
    assert ajo.tila == "valmis"
    assert not list(tuotos.glob("L3_*")) and (tuotos / "L4_x_2025.tif").exists() and (tuotos / "L4_x_2025_qa.json").exists()
    assert any(v.linkki and v.linkki.endswith("_report.html") for v in ajo.vaiheet)  # raportti jää
    assert any("satomittaukset poistettu" in r for r in ajo.loki)


def test_ip_osoitetta_ei_kirjata(tmp_path, tyokalut, rekisteri):
    import csv

    a = Ajuri(tmp_path / "t", {"varasto": str(tmp_path / "v")}, rekisteri)
    ajo = odota(a.aloita(tulkitse("1641431471"), ["growth"], kayttaja="beanstalk", osoite="192.168.1.20"))
    assert ajo.osoite == "" and "192.168" not in (ajo.kansio / "ajo.json").read_text()
    assert all(r["osoite"] == "" for r in csv.DictReader((tmp_path / "t" / "kaytto.csv").open(encoding="utf-8")))
    b = Ajuri(tmp_path / "t2", {"kirjaa_osoite": True}, rekisteri)
    assert odota(b.aloita(tulkitse("1641431471"), ["growth"], osoite="10.0.0.5")).osoite == "10.0.0.5"
