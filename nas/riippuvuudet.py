"""Tulostaa pyproject.toml:n riippuvuudet rivi kerrallaan (pip install -r), valinnaisesti osa ohitettuna."""

import argparse
import re
import tomllib

ap = argparse.ArgumentParser()
ap.add_argument("pyproject")
ap.add_argument("--ohita", action="append", default=[], help="paketin nimi, joka jätetään pois")
a = ap.parse_args()
deps = tomllib.load(open(a.pyproject, "rb"))["project"].get("dependencies", [])
nimi = lambda d: re.split(r"[\s\[<>=!~;@]", d, maxsplit=1)[0].lower()  # noqa: E731
print("\n".join(d for d in deps if nimi(d) not in a.ohita))
