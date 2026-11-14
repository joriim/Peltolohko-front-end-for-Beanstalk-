import json

import pytest

from peltolohko.syote import luokittele_tiedosto, tulkitse


def test_lohkot_eri_muodoissa():
    s = tulkitse("1641431471 1641431472-2, 164 143 1473\n1641431474/3")
    assert s.lohkot == ["1641431471", "1641431472-2", "1641431473", "1641431474-3"]
    assert not s.huomiot


def test_lohko_ei_tuplaannu():
    assert tulkitse("1641431471, 1641431471").lohkot == ["1641431471"]


@pytest.mark.parametrize("teksti", ["62.79, 22.84", "62,79 22,84", "22.84 62.79", "lat 62.79 lon 22.84"])
def test_wgs84_piste(teksti):
    s = tulkitse(teksti)
    assert len(s.pisteet) == 1
    e, n = s.pisteet[0]
    assert e == pytest.approx(287882, abs=2) and n == pytest.approx(6969045, abs=2)


def test_dms():
    s = tulkitse("62°47'24\"N 22°50'24\"E")
    assert len(s.pisteet) == 1


@pytest.mark.parametrize("teksti", ["N 6963000 E 288000", "288000, 6963000", "6963000 288000"])
def test_tm35fin_piste(teksti):
    assert tulkitse(teksti).pisteet == [(288000, 6963000)]


def test_kulmat_suorakaiteeksi():
    s = tulkitse("6963000,288000; 6964000,289000")
    assert len(s.alueet) == 1 and len(s.alueet[0]) == 5


def test_monikulmio_riviltä():
    s = tulkitse("62.79 22.84 62.80 22.85 62.80 22.84")
    assert len(s.alueet) == 1 and s.alueet[0][0] == s.alueet[0][-1]


def test_wkt_ja_lohko_samassa():
    s = tulkitse("1641431471\nPOLYGON((22.84 62.79, 22.85 62.79, 22.85 62.80, 22.84 62.79))")
    assert s.lohkot == ["1641431471"] and len(s.alueet) == 1 and not s.huomiot


def test_geojson_teksti():
    g = {"type": "Polygon", "coordinates": [[[22.84, 62.79], [22.85, 62.79], [22.85, 62.8], [22.84, 62.79]]]}
    assert len(tulkitse(json.dumps(g)).alueet) == 1


def test_rivit_ovat_eri_kohteita():
    s = tulkitse("62.79, 22.84\n62.80, 22.85")
    assert len(s.pisteet) == 2 and not s.alueet


def test_huomiot():
    assert tulkitse("40.1, -3.2").huomiot
    assert tulkitse("kissa 12").huomiot
    assert tulkitse("").tyhja()


def test_tiedostot(tmp_path):
    csv = tmp_path / "sato.csv"
    csv.write_text("lat;lon;sato\n")
    assert luokittele_tiedosto(csv).satotiedostot == [str(csv)]
    kml = tmp_path / "raja.kml"
    kml.write_text(
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark><Polygon><outerBoundaryIs><LinearRing>'
        "<coordinates>22.84,62.79,0 22.85,62.79,0 22.85,62.80,0 22.84,62.79,0</coordinates>"
        "</LinearRing></outerBoundaryIs></Polygon></Placemark></kml>"
    )
    assert len(luokittele_tiedosto(kml).alueet) == 1
    pisteet = tmp_path / "puimuri.geojson"
    pisteet.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [22.84 + i * 1e-4, 62.79]}, "properties": {}}
                    for i in range(50)
                ],
            }
        )
    )
    assert luokittele_tiedosto(pisteet).satotiedostot
    raja = tmp_path / "raja.geojson"
    raja.write_text(
        json.dumps({"type": "Polygon", "coordinates": [[[288000, 6963000], [289000, 6963000], [289000, 6964000], [288000, 6963000]]]})
    )
    assert len(luokittele_tiedosto(raja).alueet) == 1
