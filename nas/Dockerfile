# Beanstalk NAS:lle (QNAP TS-253D: Intel Celeron J4125, x86-64; QTS Container Station).
# Rakennusympäristö (context) on kansio, jossa neljä repoa ovat rinnakkain:
#   Beanstalk-Growth-Analysis/  Beanstalk-Yield/  Beanstalk-Space/  Peltolohko-front-end-for-Beanstalk-/
# Ks. nas/README.md.
FROM python:3.12-slim

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# rasterion (satelliittikuvat) valmispaketti tarvitsee järjestelmän libexpatin, jota slim-kuvassa ei ole
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
 && apt-get update \
 && apt-get install -y --no-install-recommends libexpat1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/beanstalk
# Yield ensin: Growth Analysis käyttää sitä kirjastona
COPY Beanstalk-Yield ./Beanstalk-Yield
# extra "ingest" jätetään pois: se vaatii isoxml>=1.0, jota PyPI:ssä ei ole (Yield lukee ISOXML:n omalla koodillaan)
RUN pip install "./Beanstalk-Yield[geo,fusion,report]"
COPY Beanstalk-Space ./Beanstalk-Space
RUN pip install "./Beanstalk-Space[geo]"
# Growth Analysisin riippuvuus yieldsuite osoittaa git-osoitteeseen; se on jo asennettu paikallisesta reposta
COPY Beanstalk-Growth-Analysis ./Beanstalk-Growth-Analysis
COPY Peltolohko-front-end-for-Beanstalk-/nas/riippuvuudet.py /tmp/riippuvuudet.py
RUN python /tmp/riippuvuudet.py Beanstalk-Growth-Analysis/pyproject.toml --ohita yieldsuite > /tmp/ga.txt \
 && pip install -r /tmp/ga.txt \
 && pip install --no-deps ./Beanstalk-Growth-Analysis
COPY Peltolohko-front-end-for-Beanstalk- ./peltolohko
RUN pip install ./peltolohko \
 && kasvukunto --version && yieldsuite --help > /dev/null && beanstalk4d --help > /dev/null \
 && python -c "import rasterio, geopandas, pyproj"

RUN useradd --create-home --uid 1000 beanstalk && mkdir -p /data && chown beanstalk:beanstalk /data
USER beanstalk
WORKDIR /data
EXPOSE 8765
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/tila', timeout=4)"
CMD ["peltolohko", "kaynnista", "--osoite", "0.0.0.0", "--tulokset", "/data/tulokset", "--ei-selainta"]
