# Infonäytön käyttöönotto-ohjeet

Tämä ohje käy läpi vaiheet, joilla infonäyttöön saadaan:
1. Toimiston neljän työhuoneen varaustilanne Outlookin kalentereista
2. Kiertävä oikea palsta: työhuoneet ↔ Nysse ↔ (valinnainen) lomakuva
3. Lomakuvanäkymä, joka aktivoituu automaattisesti kun kuva lisätään kansioon

## Kokonaiskuva

```
Outlook (M365)  ──►  Azure AD -sovellus  ──►  GitHub Action (5 min välein)
                                                     │
                                                     ▼
                                              varaukset.json repoon
                                                     │
                                                     ▼
                                          index.html (GitHub Pages)
                                                     │
                                                     ▼
                                               TV-selain
```

GitHub Action hakee varaukset Microsoft Graph API:sta ajastetusti, suodattaa
pois kaiken arkaluontoisen tiedon, ja kirjoittaa repoon siivotun JSON-tiedoston.
Infonäyttö lukee tätä tiedostoa minuutin välein.

## Tiedostorakenne

Kopioi nämä tiedostot repoosi seuraaville paikoille:

```
/
├── index.html                                  ← päivitetty versio
├── varaukset.json                              ← paikkamerkki (Action ylikirjoittaa)
├── palautteet.json                             ← (ennallaan, oma)
├── kuvat/
│   └── (tähän lomatiedot.png tarvittaessa)
├── .github/
│   └── workflows/
│       └── fetch-room-bookings.yml             ← ajastettu työnkulku
└── scripts/
    └── fetch_room_bookings.py                  ← hakuskripti
```

## Näkymien kierto

Oikea palsta kiertää näkymien välillä 20 sekunnin välein:

| # | Näkymä       | Milloin näkyy                                   |
|---|--------------|-------------------------------------------------|
| 1 | Työhuoneet   | Aina                                            |
| 2 | Nysse        | Aina                                            |
| 3 | Lomatiedot   | Vain kun `kuvat/lomatiedot.*` on olemassa       |

Näkymäsarja alkaa aina työhuoneista. Näkymäjärjestys on hardkoodattu, mutta
voit muuttaa sitä muokkaamalla `index.html`:ssä olevaa `views`-listaa
`CyclingRightColumn`-komponentissa.

## Työhuoneiden statukset

Jokainen huonekortti näyttää huoneen statuksen värillisellä pallolla ja
tekstillä:

| Pallo       | Teksti     | Tarkoittaa                                    |
|-------------|------------|-----------------------------------------------|
| 🟢 Vihreä   | VAPAA      | Huone vapaa nyt, seuraava varaus yli 30 min   |
| 🟡 Keltainen| ALKAMASSA  | Huone vapaa nyt, mutta varaus alkaa ≤30 min   |
| 🔴 Punainen | VARATTU    | Huoneessa käynnissä oleva varaus              |

Statuksen alla näkyy tarkempi aika (esim. "Vapautuu 14:30" tai
"Alkaa 13:15"). Pystysuora aikajana 7:00–17:00 näyttää päivän kaikki
varaukset — menneet haaleampina, nykyinen kirkkaan punaisena, tulevat
himmeämmän punaisena. Keltainen viiva merkitsee nykyistä hetkeä.

---

## Vaihe 1: Azure AD -sovelluksen rekisteröinti

Tämän vaiheen tekee henkilö, jolla on **Global Administrator**- tai
**Application Administrator** -oikeus Microsoft 365 -tenantissa.

1. Mene [Microsoft Entra admin centeriin](https://entra.microsoft.com/) →
   **Identity** → **Applications** → **App registrations** → **New registration**.
2. Anna nimi, esim. `Infonäyttö - huoneiden varaukset`.
3. Supported account types: **Accounts in this organizational directory only**
   (single tenant).
4. Redirect URI: **jätä tyhjäksi**.
5. Klikkaa **Register**.

Tallenna talteen avautuvalta sivulta:
- **Directory (tenant) ID**
- **Application (client) ID**

### Client secret

1. Siirry **Certificates & secrets** → **New client secret**.
2. Anna kuvaus (esim. `Infonäyttö`) ja valitse vanhenemisaika
   (suositus: 12–24 kk — merkitse kalenteriin uusiminen!).
3. **Kopioi Value-sarakkeen arvo heti talteen** — sitä ei enää näytetä myöhemmin.

### API-oikeudet

1. Siirry **API permissions** → **Add a permission** → **Microsoft Graph** →
   **Application permissions**.
2. Etsi ja valitse: **`Calendars.Read`**.
3. Klikkaa **Add permissions**.
4. Klikkaa **Grant admin consent for [tenant]** ja vahvista.

## Vaihe 2: Rajaa sovelluksen pääsy vain 4 huoneeseen (TÄRKEÄ)

Oletuksena sovellus pystyisi lukemaan **koko organisaation** jokaista
kalenteria. Tämä on liian laaja oikeus. Rajataan se vain näihin neljään
huonepostilaatikkoon käyttämällä [Application Access
Policy](https://learn.microsoft.com/en-us/graph/auth-limit-mailbox-access)a.

Exchange Online admin suorittaa nämä komennot PowerShellillä:

```powershell
# Yhdistä Exchange Onlineen (vaatii ExchangeOnlineManagement-moduulin)
Connect-ExchangeOnline

# Luo ryhmä, joka sisältää vain info-näytöllä näytettävät huoneet
New-DistributionGroup `
  -Name "InfonayttoHuoneet" `
  -Type Security `
  -Members @("neukkari1@esimerkki.fi","neukkari2@esimerkki.fi","neukkari3@esimerkki.fi","neukkari4@esimerkki.fi")

# Luo policy joka rajoittaa sovelluksen pääsyn vain tähän ryhmään
New-ApplicationAccessPolicy `
  -AppId "<APPLICATION_CLIENT_ID_TÄHÄN>" `
  -PolicyScopeGroupId "InfonayttoHuoneet" `
  -AccessRight RestrictAccess `
  -Description "Infonäyttösovellus saa lukea vain info-näytöllä näytettäviä huoneita"

# Varmista että policy on voimassa (muutoksen voimaantulo voi kestää ~30 min)
Test-ApplicationAccessPolicy `
  -Identity "neukkari1@esimerkki.fi" `
  -AppId "<APPLICATION_CLIENT_ID_TÄHÄN>"
# -> AccessCheckResult pitäisi olla "Granted"

Test-ApplicationAccessPolicy `
  -Identity "joku.muu@esimerkki.fi" `
  -AppId "<APPLICATION_CLIENT_ID_TÄHÄN>"
# -> AccessCheckResult pitäisi olla "Denied"
```

Tämän jälkeen sovelluksen oikeudet ovat käytännössä rajatut vain näihin
neljään postilaatikkoon, vaikka Graph-luvat olisivat laajat.

## Vaihe 3: GitHub-salaisuudet

1. Mene repossasi: **Settings** → **Secrets and variables** → **Actions** →
   **New repository secret**.
2. Lisää seuraavat salaisuudet:

| Nimi                    | Arvo                                |
|-------------------------|-------------------------------------|
| `AZURE_TENANT_ID`       | Directory (tenant) ID               |
| `AZURE_CLIENT_ID`       | Application (client) ID             |
| `AZURE_CLIENT_SECRET`   | Client secretin **Value**-arvo      |

## Vaihe 4: Konfiguroi huoneiden tiedot

Avaa `scripts/fetch_room_bookings.py` ja muokkaa `ROOMS`-listaa oman
organisaatiosi huoneiden tiedoilla:

```python
ROOMS = [
    {"email": "neukkari1@yritys.fi", "name": "Aurora"},
    {"email": "neukkari2@yritys.fi", "name": "Borealis"},
    {"email": "neukkari3@yritys.fi", "name": "Sauna"},
    {"email": "neukkari4@yritys.fi", "name": "Kahvihuone"},
]
```

`name` on se nimi, joka näkyy infonäytöllä — pidä nimet lyhyinä, koska
1×4-ruudukossa tila on rajallinen (n. 15 merkkiä mahtuu).
`email` on huonepostilaatikon osoite Microsoft 365:ssä.

## Vaihe 5: Käynnistä ensimmäinen ajo

1. Committaa ja pushaa kaikki tiedostot repoon.
2. Mene GitHubissa **Actions**-välilehdelle.
3. Valitse vasemmalta **Päivitä huoneiden varaukset**.
4. Klikkaa **Run workflow** → **Run workflow**.
5. Odota ~30 sekuntia ja tarkista, että ajo onnistui vihreällä.

Jos onnistuu, `varaukset.json` on päivittynyt oikeilla tiedoilla.

---

## Lomakuvan lisääminen (valinnainen)

Voit lisätä kolmannen näkymän kiertoon pudottamalla kuvatiedoston
`kuvat/`-kansioon:

1. Nimeä kuva tarkalleen **`lomatiedot.png`** (tai `.jpg`, `.jpeg`, `.webp`)
2. Committaa ja pushaa repoosi
3. Odota ~5 min — infonäyttö havaitsee kuvan automaattisesti ja lisää sen
   kiertoon

**Suositellut kuva-asetukset:**
- Muoto: vaakasuuntainen, esim. **1280×960** tai **1600×1200** px
- Formaatti: PNG (paras tekstille) tai JPG (paras valokuville)
- Kokosuositus: alle 2 MB

Kuva skaalataan automaattisesti näkymään mahtuvaksi (object-contain), eli
ylimääräiset reunat täytetään mustalla. Mikä tahansa kuvasuhde toimii,
mutta lähellä 4:3-suhdetta oleva kuva näyttää parhaalta.

**Kun loma loppuu:** poista `kuvat/lomatiedot.*` reposta ja pushaa.
Infonäyttö havaitsee poiston 5 min sisällä ja palaa 2-näkymän kiertoon.

---

## Tarkistus & vianetsintä

### Action epäonnistuu "401 Unauthorized" -virheellä
- Tarkista että client secret on kopioitu oikein GitHubiin (ei ylimääräisiä
  välilyöntejä).
- Tarkista että secret ei ole ehtinyt vanhentua.

### Action onnistuu, mutta huone näyttää "Ei varauksia" -tekstiä vaikka niitä pitäisi olla
- Varmista että `ROOMS`-listan email on **huonepostilaatikon osoite**, ei
  varaajien osoite.
- Varmista että `Test-ApplicationAccessPolicy` palauttaa tälle huoneelle
  `Granted`.

### Action näyttää "403 Forbidden" -virhettä vain joillekin huoneille
- Application Access Policy estää pääsyn — lisää huone `InfonayttoHuoneet`-
  ryhmään tai poista policy.

### Sivu näyttää vanhaa dataa
- GitHub Pages päivittyy yleensä ~1 minuutin sisällä committista. Voit
  tarkistaa selaimen DevToolsilla mitä `varaukset.json` palauttaa.

### Lomakuva ei ilmesty näkyviin
- Varmista että tiedoston nimi on tarkalleen `lomatiedot.png` (tai .jpg/
  .jpeg/.webp) ja se on `kuvat/`-kansiossa.
- Odota 5 min — sivu tarkistaa kuvan olemassaolon vain 5 min välein.
- Lataa sivu uudelleen pakottaaksesi tarkistuksen heti.
- Tarkista selaimen DevToolsilla (Network-välilehti) onko kuvan lataus
  onnistunut (HTTP 200).

### Kiertävä näkymä jää jumiin yhteen näkymään
- Lataa sivu uudelleen (TV:n selaimessa yleensä Refresh-painike tai F5).
- Tarkista selaimen DevToolsista mahdollisia JavaScript-virheitä.

---

## Turvallisuusnäkökohdat

- **`varaukset.json` on julkinen** (se on julkisessa repossa ja Pages-sivulla).
  Sen sisältöön EI kirjoiteta kokousten aiheita, osallistujia, kuvauksia tms.
  Ainoastaan aikavälit ja varaajan nimikirjaimet.
- **Lomakuva on myös julkinen.** Älä sijoita sinne mitään luottamuksellista
  (esim. työntekijöiden henkilökohtaisia yhteystietoja tai lomien tarkkoja
  päivämääriä, jos niistä voi päätellä milloin toimisto on tyhjä).
- **Client secret** on GitHub Secretseissä eikä ikinä näy repossa, logeissa,
  tai sivulla. Älä lisää sitä suoraan koodiin.
- **Application Access Policy** rajoittaa sovelluksen teknistä pääsyä —
  vaikka client secret vuotaisi, sen kautta pääsisi käsiksi vain info-näytön
  huoneisiin.
- **Kierrätä client secret** ajoissa ennen sen vanhentumista (merkitse
  kalenteriin).
- Jos olet huolissasi siitä, että nimikirjaimet paljastavat yhdistettynä
  kalenteriaikaan liikaa (esim. pienessä toimistossa), voit poistaa
  `organizerInitials`-kentän asettamisen skriptistä — silloin näkyvät vain
  aikavälit.

---

## Halutessasi: asetusten säätö

Kaikki säätyvät parametrit ovat `index.html`:n alussa `ASETUKSET`-lohkossa:

```javascript
const CYCLE_INTERVAL_MS = 20000;        // 20 s per näkymä
const DAY_START_HOUR = 7;                // aikajanan alku
const DAY_END_HOUR = 17;                 // aikajanan loppu
const STARTING_SOON_MINUTES = 30;        // "ALKAMASSA"-statuksen kynnys
```

Ajastuksen muokkaus (`.github/workflows/fetch-room-bookings.yml`):
- GitHub sallii minimitiheydeksi **5 min** (`*/5`).
- Ajoja voidaan viivästää ruuhka-aikoina joitakin minuutteja — tämä on GitHub
  Actionsin normaalia käytöstä eikä bugi.
- Cron käyttää **UTC-aikaa**. Helsinki-aika on UTC+2 (talvi) tai UTC+3 (kesä).
