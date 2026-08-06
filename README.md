# VeoliaSecureGPT CLI Kliens – Teljes Dokumentáció és Használati Útmutató

Ez a dokumentum a **VeoliaSecureGPT** (OpenAI-kompatibilis mesterséges intelligencia API) parancssori Python kliensének leírását, beállítási lépéseit és használati útmutatóját tartalmazza.

---

## 1. A kód leírása és működése

A szkript egy könnyűsúlyú CLI eszköz, amely kizárólag a Python beépített könyvtárait használja (`urllib`, `json`, `argparse`, `base64`, `os`, `pathlib`). Ennek köszönhetően semmilyen harmadik féltől származó csomagot (pl. `requests`) nem kell telepíteni.

### Fő funkciók:

- **Környezeti változók betöltése (`load_env_file`)**:
  - Automatikusan beolvassa a szkript mellett lévő `.env` fájlt.
  - Nem írja felül a már meglévő rendszerszintű környezeti változókat (`os.environ.setdefault`).
- **OAuth2 Hitelesítés (`get_access_token`)**:
  - A megadott `VSGPT_CLIENT_ID` és `VSGPT_CLIENT_SECRET` használatával Basic hitelesítési kérést küld a Veolia OAuth szerverére (`https://api.veolia.com/security/v2/oauth/token`).
  - Visszakap egy ideiglenes `access_token`-t (Bearer token).
- **API Kérések kezelése (`request_json`)**:
  - Hálózati kéréseket indít a VeoliaSecureGPT proxy felé (`https://api.veolia.com/llm/veoliasecuregpt/v1`).
  - Támogatja az elérhető modellek kilistázását (`/models`) és a prompt alapú válaszgenerálást (`/chat/completions`).
- **PDF-csatolmányok**:
  - A `--pdf` kapcsoló a dokumentumot base64-es `data:application/pdf` formátumban küldi a multimodális API-nak.
- **Hibakezelés**:
  - Egyértelmű hibaüzenetet ad vissza HTTP és hálózati (URL) hibák esetén.

---

## 2. Előfeltételek és Beállítás

### A) Szükséges hozzáférések
A szkript futtatásához az alábbi adatokra van szükség:
- `VSGPT_CLIENT_ID`: Az Ön API ügyfél azonosítója.
- `VSGPT_CLIENT_SECRET`: Az Ön API titkos kulcsa.
- `VSGPT_USER_EMAIL`: Az Ön munkahelyi e-mail címe (a proxy ehhez köti a lekérdezéseket).

### B) `.env` fájl létrehozása
Hozzon létre egy `.env` fájlt a szkripttel azonos mappában (`vsgpt_connect/.env`):

```env
VSGPT_CLIENT_ID=a_te_client_id_erteked
VSGPT_CLIENT_SECRET=a_te_client_secret_erteked
VSGPT_USER_EMAIL=felhasznalo@veolia.com
```

---

## 3. Használati Utasítások és Példák

### 1. Elérhető modellek kilistázása
Tekintse meg, hogy milyen modellek érhetők el a fiókjával:

```bash
python connect_to_vsgpt.py --list-models
```

### 2. Kérdés feltevése (Prompt küldése)
*(Fontos: Mindig adja meg a `--model` paramétert, ha az alapértelmezett `gpt-5` nem érhető el az Ön csomagjában!)*

```bash
python connect_to_vsgpt.py "Mi Magyarország fővárosa?" --model gpt-4o
```

### 3. Kérdés feltevése egyéb beállításokkal
Beállíthatja a válasz kreativitását a `--temperature` paranccsal (értéke: `0.0` – pontos/analitikus, `2.0` – kreatív):

```bash
python connect_to_vsgpt.py "Írj egy rövid összefoglalót a megújuló energiákról." --model gpt-4o-mini --temperature 0.3
```

### 4. PDF elemzése
Adjon meg egy PDF fájlt a `--pdf` kapcsolóval. A kérdés és a dokumentum ugyanabban az üzenetben jut el a modellhez:

```bash
python connect_to_vsgpt.py "Foglald össze ezt a dokumentumot magyarul." --pdf "C:\dokumentumok\jelentes.pdf" --model gpt-4o
```

PDF csatolásakor a kliens automatikusan a dokumentumfeldolgozást támogató `/answer` végpontot használja; PDF nélküli kérések továbbra is az OpenAI-kompatibilis `/chat/completions` végpontra mennek.

---

## 4. Gyakori Hibák és Megoldásuk

### ❌ `HTTP 400: This model is not accessible with the product VeoliaSecureGPT...`
- **A hiba oka:** A szkript alapértelmezett modellje a `gpt-5`, amely a prémium VeoliaSecureGPT Flex előfizetést igényli. Ha a fiókja az alap VeoliaSecureGPT csomaggal rendelkezik, az API elutasítja a kérést.
- **Megoldás:**
  1. Használjon egy elérhető modellt a parancsban (pl. `--model gpt-4o` vagy `--model gpt-4o-mini`).
  2. Vagy módosítsa a `connect_to_vsgpt.py` fájlban a `parse_args()` függvényt:
     ```python
     parser.add_argument("--model", default="gpt-4o", help="Model ID (default: gpt-4o).")
     ```

### ❌ `Missing VSGPT_CLIENT_ID or VSGPT_CLIENT_SECRET`
- **A hiba oka:** A szkript nem találja az API kulcsokat.
- **Megoldás:** Ellenőrizze, hogy a `.env` fájl pontosan a szkript mellett található-e, és ki vannak-e töltve benne a megfelelő változók.

---

## 5. Parancssori Opciók Összefoglalása

| Kapcsoló / Paraméter | Típus | Leírás | Alapértelmezett érték |
| :--- | :--- | :--- | :--- |
| `prompt` | Pozícionális | A modellnek küldött kérdés/utasítás. | Kötelező (kivéve `--list-models`) |
| `--model` | Sztring | A használni kívánt modell azonosítója (pl. `gpt-4o`, `gpt-4o-mini`, `gemini-2.5-flash`). | `gpt-5` |
| `--user-email` | Sztring | A felhasználó e-mail címe az azonosításhoz. | `VSGPT_USER_EMAIL` környezeti változó |
| `--temperature` | Float (0.0 - 2.0) | Mintavételezési hőmérséklet (kreativitási tényező). | `0.2` |
| `--pdf` | Fájlútvonal | A kérdéshez csatolandó PDF dokumentum. | Nincs |
| `--list-models` | Flag | Kilistázza a fiókkal elérhető modellek azonosítóit. | `False` |