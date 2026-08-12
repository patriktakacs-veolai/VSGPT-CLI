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
| `--scan-directory PATH` | Fájlútvonal | Kilistázza a RAG ETL számára új fájlokat. | Nincs |
| `--scanner-db PATH` | Fájlútvonal | A scanner SQLite állapotadatbázisának útvonala. | `scanner_state.sqlite` |

---

## 6. Dokumentum-szkenner RAG ETL folyamathoz

A `document_scanner.py` a RAG adat-előkészítő folyamat első, **Scanner** lépését valósítja meg. A `DocumentScanner` rekurzívan bejárja a megadott könyvtárat, minden fájlhoz 64 KB-os blokkokban SHA-256 hash-t számol, majd az SQLite állapotadatbázis alapján kiválasztja az új tartalmú fájlokat.

Az állapotadatbázis a megadott `db_path` helyen jön létre. Tartalmazza a `processed_files` táblát az alábbi mezőkkel: `id`, `file_path`, `file_hash` és `processed_at`.

### Használat

```python
from pathlib import Path

from document_scanner import DocumentScanner

scanner = DocumentScanner(
    target_dir=Path("test_documents"),
    db_path=Path("scanner_state.sqlite"),
)

new_files = scanner.get_unprocessed_files()
for file_path in new_files:
    print(file_path)
```

A `get_unprocessed_files()` abszolút fájlútvonalak listáját adja vissza. A metódus kizárólag olvassa az állapotadatbázist; a hash-ek rögzítését csak egy későbbi, sikeres feldolgozást végző ETL lépésnek kell elvégeznie.

### Parancssori használat

A scanner a meglévő kliens parancsán keresztül is futtatható; ehhez nincs szükség API-hitelesítő adatokra:

```bash
python connect_to_vsgpt.py --scan-directory "C:\dokumentumok" --scanner-db "C:\rag\scanner_state.sqlite"
```

A `--scanner-db` elhagyásakor a program az aktuális mappában lévő `scanner_state.sqlite` adatbázist használja. A parancs minden új fájl abszolút útvonalát külön sorban írja ki.

---

## 7. RAG ETL pipeline

A `pipeline.py` összeköti a szkennelést, a Veolia API-n végzett PDF-kinyerést és a RAG staging kimenet előállítását. A pipeline az `extraction_prompt.md` által meghatározott JSON-választ YAML front matterrel és strukturált törzsszöveggel egészíti ki, majd `.txt` fájlként menti a `staging_output` mappába.

```bash
python pipeline.py "C:\dokumentumok" "C:\rag\scanner_state.sqlite"
```

Egyedi kimeneti és prompt-mappa is megadható:

```bash
python pipeline.py "C:\dokumentumok" "C:\rag\scanner_state.sqlite" --staging-dir "C:\rag\staging_output" --prompt-file "C:\rag\extraction_prompt.md"
```

A pipeline a jelenlegi API-csatolóval PDF fájlokat dolgoz fel. Sikeres API-hívás, JSON-feldolgozás és staging mentés után a `mark_as_processed()` rögzíti a dokumentum hash-ét és a feldolgozás UTC időpontját. Sikertelen fájl esetén hibaüzenetet ír, de nem jelöli azt feldolgozottnak, ezért a következő futás újra megpróbálhatja.