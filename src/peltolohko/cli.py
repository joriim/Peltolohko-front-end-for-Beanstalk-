"""Komentorivi: ``peltolohko`` käynnistää etusivun, ``peltolohko tulkitse TEKSTI`` näyttää tulkinnan."""

from __future__ import annotations

import argparse
import json
import tomllib
import webbrowser
from pathlib import Path

from . import __version__
from .ajot import Ajuri
from .palvelin import palvelin
from .syote import tulkitse


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="peltolohko", description="Beanstalkin etusivu ja käynnistin")
    ap.add_argument("--version", action="version", version=f"peltolohko {__version__}")
    ali = ap.add_subparsers(dest="komento")
    k = ali.add_parser("kaynnista", help="etusivu selaimeen (oletus)")
    k.add_argument("--portti", type=int, default=8765)
    k.add_argument("--osoite", default="127.0.0.1", help="kuunneltava osoite (oletus vain tämä kone)")
    k.add_argument("--tulokset", default="tulokset", help="ajojen kansio (oletus tulokset/)")
    k.add_argument("--asetukset", help="TOML: varasto, komennot, space_data (ks. README)")
    k.add_argument("--ei-selainta", action="store_true")
    t = ali.add_parser("tulkitse", help="näytä, miten syöte tulkitaan")
    t.add_argument("teksti", nargs="+")
    a = ap.parse_args(argv)

    if a.komento == "tulkitse":
        print(json.dumps(tulkitse(" ".join(a.teksti)).sanakirja(), ensure_ascii=False, indent=2))
        return 0
    if a.komento is None:
        a = ap.parse_args(["kaynnista", *(argv or [])])
    asetukset = {}
    polku = Path(a.asetukset) if a.asetukset else Path("peltolohko.toml")
    if polku.is_file():
        asetukset = tomllib.loads(polku.read_text(encoding="utf-8"))
    s = palvelin(Ajuri(Path(a.tulokset), asetukset), a.osoite, a.portti)
    url = f"http://{a.osoite}:{s.server_address[1]}/"
    print(f"Beanstalk: {url}  (Ctrl+C lopettaa)")
    if not a.ei_selainta:
        webbrowser.open(url)
    try:
        s.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
