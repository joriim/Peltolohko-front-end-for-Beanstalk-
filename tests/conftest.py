import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

STUB = """#!/usr/bin/env python3
import sys, pathlib
a = sys.argv[1:]
if "arvioi" in a and "--json" in a:
    print('{"pu": 13.3, "lataus_mt": 474.1}')
    raise SystemExit(0)
print("stub", pathlib.Path(sys.argv[0]).name, *a)
if "analysoi" in a:  # kasvukunto analysoi: tulosteviestit kuten oikeasti (lohkoanalyysin lähteet luetaan näistä)
    print(f"Lohko {a[a.index('analysoi') + 1]}: 4.20 ha, vuodet [2025]")
    print("  2025: porrastettu ketju (HLS + S1 -> tarvekartta -> Sentinel-2)…")
    print("  maaperä, maasto ja hydrologia…")
if "run" in a and a[a.index("run") + 1].endswith("field.yaml"):  # yieldsuite run: tuotokset kuten oikeasti
    tuotos = pathlib.Path(a[a.index("run") + 1]).parent / "tuotos"
    tuotos.mkdir(parents=True, exist_ok=True)
    for n in ("L3_x_2025_records.parquet", "L4_x_2025.tif", "L4_x_2025_qa.json"):
        (tuotos / n).write_text("x")
for flag in ("--raportti", "-o", "--out"):
    if flag in a:
        p = pathlib.Path(a[a.index(flag) + 1])
        if flag == "--out" and "report" in a:
            p.mkdir(parents=True, exist_ok=True); (p / "x_report.html").write_text("<p>ok</p>")
        else:
            p.parent.mkdir(parents=True, exist_ok=True); p.write_text("<p>premium</p>" if "premium" in a else "<p>ok</p>")
"""


@pytest.fixture
def tyokalut(tmp_path, monkeypatch):
    """Korvikkeet kasvukunto-, yieldsuite- ja beanstalk4d-komennoille PATHiin."""
    b = tmp_path / "bin"
    b.mkdir()
    for n in ("kasvukunto", "yieldsuite", "beanstalk4d"):
        p = b / n
        p.write_text(STUB)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{b}{os.pathsep}{os.environ['PATH']}")
    return b


class FakeRekisteri:
    def pisteessa(self, p):
        return ["7430153621"]

    def alueella(self, r):
        return ["7430153621", "7430202323"]


@pytest.fixture
def rekisteri():
    return FakeRekisteri()
