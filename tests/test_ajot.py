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


def test_yield_ilman_tiedostoa_on_kuvista(tmp_path, tyokalut, rekisteri):
    a = Ajuri(tmp_path / "t", {}, rekisteri)
    ajo = odota(a.aloita(tulkitse("1641431471"), ["yield"]))
    assert any("kuvista" in v.otsikko for v in ajo.vaiheet)
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
