import pytest

from peltolohko.koordinaatit import tm35fin, wgs84


def test_helsingin_tuomiokirkko():
    e, n = tm35fin(60.1699, 24.9384)
    assert e == pytest.approx(385611, abs=2) and n == pytest.approx(6672118, abs=2)


def test_keskimeridiaani():
    assert tm35fin(0, 27) == pytest.approx((500000, 0))


@pytest.mark.parametrize("lat,lon", [(60.0, 20.0), (65.0, 25.5), (69.9, 28.0)])
def test_edestakaisin(lat, lon):
    assert wgs84(*tm35fin(lat, lon)) == pytest.approx((lat, lon), abs=1e-9)
