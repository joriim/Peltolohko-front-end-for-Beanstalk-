# Beanstalk NAS:lla (QNAP TS-253D)

Sivu ja kaikki kolme työkalua (Growth Analysis, Yield, Space) yhdessä Docker-kontissa. TS-253D:ssä on
Intel Celeron J4125 (x86-64, neljä ydintä); 16 Gt muistia riittää. Kontti saa kolme ydintä ja 12 Gt,
loput jäävät NAS:lle. Ajot menevät jonoon ja ajetaan yksi kerrallaan.

## Tarvitaan

- QTS 5 ja **Container Station** (App Center).
- **SSH** päälle: Ohjauspaneeli → Verkko- ja tiedostopalvelut → Telnet/SSH.
- Pääsy GitHubin repoihin. Yksityisiin repoihin tarvitaan henkilökohtainen token
  (GitHub → Settings → Developer settings → Fine-grained token, oikeus *Contents: read*).
- Tunnukset: Copernicus Data Space (OAuth client), Copernicus CDS (API key), valinnaisesti Planet.

## Kansiot

```
/share/Container/beanstalk/
  src/                                   neljä repoa rinnakkain (rakennusympäristö)
    Beanstalk-Growth-Analysis/
    Beanstalk-Yield/
    Beanstalk-Space/
    Peltolohko-front-end-for-Beanstalk-/
  data/                                  kontin /data
    tulokset/                            ajot, raportit, ladatut satotiedostot
    varasto/                             analyysien välitulokset (satelliittiaineisto)
    peltolohko.toml                      valinnaiset asetukset
```

## Asennus

Kirjaudu NAS:lle (`ssh admin@<nas-osoite>`) ja aja:

```sh
mkdir -p /share/Container/beanstalk/src /share/Container/beanstalk/data
chown 1000:1000 /share/Container/beanstalk/data      # kontin käyttäjä on uid 1000
cd /share/Container/beanstalk/src

# QTS:ssä ei ole gitiä: käytetään gitiä kontista. Yksityiset repot kysyvät käyttäjän ja tokenin.
git() { docker run --rm -it -v "$PWD":/git -w /git alpine/git "$@"; }
git clone https://github.com/joriim/Beanstalk-Growth-Analysis.git
git clone https://github.com/joriim/Beanstalk-Yield.git
git clone https://github.com/joriim/Beanstalk-Space.git
git clone https://github.com/joriim/Peltolohko-front-end-for-Beanstalk-.git
```

**Haara:** etusivu, Spacen muutokset ja Growth Analysisin välimuisti ovat toistaiseksi haarassa
`ccr-33eac0db-7lt4my`, ja Peltolohko-repon oletushaara on tyhjä. Kunnes haarat on yhdistetty, vaihda ne:

```sh
git -C Peltolohko-front-end-for-Beanstalk- checkout ccr-33eac0db-7lt4my
git -C Beanstalk-Space checkout ccr-33eac0db-7lt4my
git -C Beanstalk-Growth-Analysis checkout ccr-33eac0db-7lt4my
```

Asetukset ja käynnistys:

```sh
cd Peltolohko-front-end-for-Beanstalk-/nas
cp .env.esimerkki .env
vi .env                       # salasana ja tunnukset
docker compose up -d --build  # vanhemmassa Container Stationissa: docker-compose up -d --build
```

Ensimmäinen rakennus lataa Python-paketit ja kestää NAS:lla kauan. Sivu aukeaa osoitteessa
`http://<nas-osoite>:8765/`, ja selain kysyy `.env`-tiedoston käyttäjää ja salasanaa.

## Turvallisuus

- **Aseta `PELTOLOHKO_SALASANA`.** Ilman sitä kuka tahansa lähiverkossa voi käynnistää ajoja.
- **Älä avaa porttia internetiin** (reitittimen porttiohjaus, myQNAPcloud). Sivu käynnistää komentoja
  NAS:lla, ja HTTP Basic -salasana kulkee ilman HTTPS:ää salaamattomana.
- Etäkäyttöön VPN: QNAPin **QVPN** tai **Tailscale** (App Center). Silloin sivu näkyy vain omille laitteille.
- HTTPS lähiverkossa: QTS 5.1:n käänteinen välityspalvelin (Ohjauspaneeli → Verkko- ja tiedostopalvelut
  → Käänteinen välityspalvelin) NAS:n varmenteella, kohteena `http://127.0.0.1:8765`.

## Käyttö ja ylläpito

| Tehtävä | Komento (kansiossa `src/Peltolohko-front-end-for-Beanstalk-/nas`) |
|---|---|
| loki | `docker logs -f beanstalk` |
| tila | `docker ps` (kohta *healthy*) |
| päivitys | `git pull` jokaisessa repossa, sitten `docker compose up -d --build` |
| pysäytys | `docker compose down` |
| latauksen koko etukäteen | `docker exec beanstalk kasvukunto arvioi <lohko> --vuodet 2023-2025` |
| varaston koko | `du -sh /share/Container/beanstalk/data/varasto` |

- Ajolista on palvelimen muistissa: uudelleenkäynnistys tyhjentää listan, mutta tulokset jäävät
  kansioon `data/tulokset/`.
- Varmuuskopioi `data/` Hybrid Backup Syncillä. `tulokset/` sisältää raportit, ajot (`ajo.json`),
  käyttölokin (`kaytto.csv`) ja ladatut satotiedostot: varmuuskopioi se. `varasto/` on suurin ja ladattavissa
  uudelleen; sen `_valimuisti/` säästää aikaa ja Copernicus-yksiköitä, kun samaa lohkoa analysoidaan uudelleen.
- Asetukset (`data/peltolohko.toml`) ks. pää-README. Esimerkiksi kaksi ajoa rinnakkain:
  `rinnakkaiset_ajot = 2`. Neljällä ytimellä yksi on turvallisin.
