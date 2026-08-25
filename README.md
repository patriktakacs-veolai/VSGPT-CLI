# VeoliaSecureGPT kliens és RAG ETL pipeline

Ez a projekt három részből áll:

| Fájl | Feladat |
| --- | --- |
| `connect_to_vsgpt.py` | Parancssori kliens a VeoliaSecureGPT API-hoz. |
| `document_scanner.py` | Új vagy megváltozott dokumentumok azonosítása SHA-256 hash alapján. |
| `pipeline.py` | PDF-ek feldolgozása, RAG-formátumú staging fájlok létrehozása és az állapot frissítése. |
| `pipeline_config.yaml` | A pipeline futtatási útvonalai, modellje, promptja és dinamikus sémája. |
| `test_schema.py` | A `pipeline_config.yaml` által betöltött alapértelmezett Pydantic séma. |

## Előfeltételek

- Python 3.10 vagy újabb.
- VeoliaSecureGPT hozzáférés: kliensazonosító, kliens titok és felhasználói e-mail cím.
- A RAG pipeline futtatásához a `pypdf`, `pydantic` és `PyYAML` csomag.

```powershell
python -m pip install pypdf pydantic pyyaml
```

Hozzon létre egy `.env` fájlt a projekt gyökerében:

```env
VSGPT_CLIENT_ID=az_on_client_id_erteke
VSGPT_CLIENT_SECRET=az_on_client_secret_erteke
VSGPT_USER_EMAIL=felhasznalo@pelda.hu
```

A `.env` fájl ne kerüljön verziókezelésbe. A program a már beállított rendszerkörnyezeti változókat nem írja felül.

## 1. VeoliaSecureGPT parancssori kliens

A `connect_to_vsgpt.py` küld szöveges kérdéseket és PDF-eket a VeoliaSecureGPT OpenAI-kompatibilis API-jának. A hitelesítéshez OAuth hozzáférési tokent kér, ezért a `.env` fájl három változója szükséges.

### Elérhető modellek

```powershell
python connect_to_vsgpt.py --list-models
```

### Szöveges kérdés

```powershell
python connect_to_vsgpt.py "Foglalld össze röviden a megújuló energia előnyeit." --model gpt-4o-mini
```

### Szöveges kérdés egyedi beállításokkal

```powershell
python connect_to_vsgpt.py "Mik a dokumentum fő kockázatai?" --model gpt-4o --temperature 0.2 --user-email felhasznalo@pelda.hu
```

### PDF elemzése

```powershell
python connect_to_vsgpt.py "Foglald össze magyarul." --pdf "C:\dokumentumok\jelentes.pdf" --model gpt-4o
```

A `--pdf` csak valódi PDF fájlt fogad el. PDF esetén a kliens az `/answer`, szöveges kérdésnél a `/chat/completions` végpontot használja.

### Kliens kapcsolói

| Kapcsoló | Leírás | Alapérték |
| --- | --- | --- |
| `prompt` | Szöveges kérdés vagy utasítás. | Nincs |
| `--model` | Használt modell azonosítója. | `gpt-4o-mini` |
| `--user-email` | Végfelhasználó e-mail címe. | `VSGPT_USER_EMAIL` |
| `--temperature` | Kreativitás, 0 és 2 között. | `0.2` |
| `--pdf PATH` | PDF csatolmány. | Nincs |
| `--list-models` | Kilistázza az elérhető modelleket. | Kikapcsolva |

## 2. Dokumentum-szkenner

A `DocumentScanner` rekurzívan bejár egy megadott mappát, és minden fájl SHA-256 hash-ét 64 KB-os blokkokban számítja ki. Az SQLite adatbázis `processed_files` táblája tárolja a már sikeresen feldolgozott tartalmak hash-ét.

| Mező | Jelentés |
| --- | --- |
| `id` | Elsődleges kulcs. |
| `file_path` | A feldolgozott fájl abszolút útvonala. |
| `file_hash` | A fájl SHA-256 hash-e. |
| `processed_at` | A sikeres feldolgozás UTC időpontja. |

A szkenner tartalom alapján azonosít: két azonos tartalmú, de eltérő nevű fájl közül csak az első számít új tartalomnak. A `get_unprocessed_files()` nem módosítja az adatbázist. A `mark_as_processed()` csak a sikeres feldolgozás után rögzíti a hash-t.

### Scanner parancssorból

Ez a művelethez nem szükséges API-hitelesítés:

```powershell
python connect_to_vsgpt.py --scan-directory "C:\dokumentumok" --scanner-db "C:\rag\scanner_state.sqlite"
```

Az új fájlok abszolút útvonalai külön sorokban jelennek meg. A `--scanner-db` elhagyásakor az adatbázis neve `scanner_state.sqlite` az aktuális mappában.

### Scanner Pythonból

```python
from pathlib import Path

from document_scanner import DocumentScanner

scanner = DocumentScanner(
    target_dir=Path(r"C:\dokumentumok"),
    db_path=Path(r"C:\rag\scanner_state.sqlite"),
)

for file_path in scanner.get_unprocessed_files():
    print(file_path)
```

## 3. RAG ETL pipeline

A `pipeline.py` a teljes dokumentumfeldolgozást vezérli:

1. A scanner megkeresi az adatbázisban még nem szereplő tartalmakat.
2. A pipeline kiválasztja a parancssori szűrőknek megfelelő fájlokat.
3. PDF esetén helyi szövegkinyeréssel eldönti, hogy digitális vagy szkennelt dokumentumról van-e szó.
4. Meghívja a megfelelő Veolia API-végpontot.
5. Az API JSON-válaszából RAG-formátumú `.txt` fájlt készít.
6. Sikeres mentés után a fájl hash-e bekerül az SQLite állapotadatbázisba.

A végső kinyerési válaszokat a YAML konfigurációban megadott Pydantic modell ellenőrzi. A pipeline a dinamikusan betöltött modell `model_json_schema()` sémáját a `/chat/completions` hívásoknál küldi az API-nak az OpenAI-kompatibilis `response_format` paraméterben.

### Alap futtatás

```powershell
python pipeline.py --config "pipeline_config.yaml"
```

A konfigurációban kötelező a `schema_module`, `schema_class` és `prompt`. A `target_dir`, `db_path`, `staging_dir` és `model` is megadható benne.

```yaml
target_dir: '\\szerver\megosztas\dokumentumok'
db_path: 'C:\rag\scanner_state.sqlite'
staging_dir: 'C:\rag\staging_output'
schema_module: 'test_schema'
schema_class: 'DocumentExtraction'
model: 'gpt-4o'
prompt: >
  A dokumentumot a megadott séma szerint dolgozd fel.
```

### A prompt és a séma kapcsolata

A YAML `prompt` mezőjének is egyértelműen le kell írnia az elvárt JSON mezőket és azok szerkezetét. Ez nem ismétlés: a szkennelt PDF-ek a Vision-alapú `/answer` végpontra kerülnek, amelynél a `response_format` paraméter nem használható, mert üres választ okozhat.

Ezért a `/answer` végpont kizárólag a szöveges promptból tudja, milyen JSON-t kell visszaadnia. A promptot mindig tartsa szinkronban a `schema_module` / `schema_class` által betöltött Pydantic modellel. A digitális PDF-ek és a hosszú, szkennelt PDF-ek Reduce lépése a `/chat/completions` végpontot használja, ahol a modell a prompt mellett a szigorú Pydantic sémát is megkapja.

### Parancssori útvonal-felülírás

```powershell
python pipeline.py "C:\dokumentumok" "C:\rag\scanner_state.sqlite" `
  --config "pipeline_config.yaml" `
  --staging-dir "C:\rag\staging_output"
```

### Fájltípus- és PDF-típus-szűrés

```powershell
python pipeline.py --config "pipeline_config.yaml" --file-type pdf --pdf-type digital
```

| Kapcsoló | Értékek | Alapérték | Jelentés |
| --- | --- | --- | --- |
| `--file-type` | `all`, `pdf`, `word`, `excel`, `image` | `pdf` | Kiterjesztés szerinti szűrés. |
| `--pdf-type` | `all`, `digital`, `scanned` | `all` | Csak PDF-eknél használt tartalom szerinti szűrés. |

A fájltípus-csoportok:

| Csoport | Kiterjesztések |
| --- | --- |
| `pdf` | `.pdf` |
| `word` | `.docx`, `.doc` |
| `excel` | `.xlsx`, `.xls` |
| `image` | `.png`, `.jpg`, `.jpeg`, `.webp`, `.bmp`, `.tiff` |

A `digital` PDF helyben kinyert szövege 750 karakternél hosszabb. A `scanned` PDF esetén ez a szöveg legfeljebb 750 karakter. A szűrőnek nem megfelelő PDF-ekről a pipeline rövid üzenetet ír, és nem jelöli őket feldolgozottnak.

> **Jelenlegi korlát:** a pipeline dokumentumkinyerési lépése jelenleg PDF-eket támogat. A `word`, `excel`, `image` és `all` szűrők kiválaszthatnak más fájlokat is, de ezekhez még nincs feldolgozó implementálva, ezért nem kerülnek sikeresen feldolgozott állapotba. Éles RAG feldolgozáshoz használja az alapértelmezett `--file-type pdf` beállítást.

### Hibrid PDF útválasztás

| PDF típusa | Feltétel | Feldolgozás |
| --- | --- | --- |
| Digitális PDF | A kinyert szöveg több mint 750 karakter. | A teljes helyi szöveg a `/chat/completions` végpontra megy. |
| Rövid szkennelt PDF | Legfeljebb 25 oldal és kevés vagy nincs szöveg. | A teljes PDF a Vision-alapú `/answer` végpontra megy. |
| Hosszú szkennelt PDF | Több mint 25 oldal és kevés vagy nincs szöveg. | A PDF 25 oldalas részekre bomlik, minden rész külön kivonatot kap, majd a kivonatokból a `/chat/completions` végpont állítja elő a végső JSON-t. |

A hosszú, szkennelt PDF-ek részfájljai csak ideiglenesen léteznek. A 25 oldalas határ csökkenti a nagy szkennelt fájlok API Gateway 413 hibájának kockázatát. A használt modell a YAML `model` mezőjéből érkezik.

## 4. Staging kimenet

A pipeline minden sikeresen feldolgozott dokumentumhoz létrehoz egy `.txt` fájlt a staging mappában. A staging útvonal az eredeti fájl teljes abszolút útvonalát tükrözi, például a `C:\dokumentumok\jelentes.pdf` kimenete `staging_output\C\dokumentumok\jelentes.txt`.

```text
---
doc_id: "egyedi-uuid"
title: "A dokumentum címe"
category: "A dokumentum kategóriája"
tags: ["címke1", "címke2", "címke3"]
source_path: "C:\dokumentumok\jelentes.pdf"
last_modified: "2026-08-12"
---

# A dokumentum címe

## Összefoglaló
...

## Megválaszolt kérdések
- ...
```

| Mező | Jelentés |
| --- | --- |
| `doc_id` | Új UUID minden elkészült RAG dokumentumhoz. |
| `title` | Az API által kinyert cím. |
| `category` | Az API által meghatározott kategória. |
| `tags` | Az API által kinyert címkék listája. |
| `source_path` | Az eredeti fájl abszolút útvonala. |
| `last_modified` | Az eredeti fájl utolsó módosítási dátuma UTC szerint. |

Az API-válasznak a konfigurációban megadott Pydantic séma szerint kell a RAG kimenethez szükséges `title`, `category`, `tags`, `summary` és `questions_answered` mezőket tartalmaznia.

Az abszolút forrásútvonal a staging mappán belül is tükröződik, ezért az azonos nevű, eltérő helyen lévő dokumentumok nem írják felül egymást.

## 5. Hibakezelés és újrapróbálás

- A pipeline két tényleges dokumentumfeldolgozás között 5 másodpercet vár, hogy ne terhelje túl az API-t.
- HTTP 503 vagy üres API-válaszból eredő Pydantic JSON-validációs hiba esetén egy dokumentum feldolgozása legfeljebb háromszor fut le.
- A Vision `/answer` végpont `File input is not supported in this region` HTTP 400 hibájánál a fájl `Skipping: API Region limitation for Vision model` üzenettel kimarad.
- API-hiba, sérült PDF, érvénytelen JSON vagy mentési hiba esetén az érintett fájl kimarad.
- A kimaradt fájl nem kerül a `processed_files` táblába, ezért a következő futás ismét megpróbálja feldolgozni.
- A pipeline csak a staging fájl sikeres mentése után hívja meg a `mark_as_processed()` metódust.
- A PDF JSON-válaszának meg kell felelnie a konfigurációban kiválasztott Pydantic sémának.

Ha egy dokumentumot szándékosan újra kell feldolgozni, annak hash-ét el kell távolítani a választott SQLite adatbázis `processed_files` táblájából. Az adatbázis teljes törlése minden dokumentumot újnak tekint a következő futásban.

## 6. Gyakori problémák

| Probléma | Teendő |
| --- | --- |
| `Missing VSGPT_CLIENT_ID...` | Ellenőrizze a `.env` fájlt vagy a környezeti változókat. |
| `Attachment must be a .pdf file` | A közvetlen PDF-feldolgozás csak `.pdf` kiterjesztést és érvényes PDF-fejlécet fogad el. |
| `Unable to read PDF` | Ellenőrizze, hogy a fájl nem sérült, jelszóval védett vagy éppen használatban van. |
| Pydantic validációs hiba | Az API válasza nem felelt meg a konfigurált sémának; a fájl a következő futáskor újrapróbálható. |
| Nem jelenik meg új fájl | A tartalom hash-e már szerepel az állapotadatbázisban, vagy a fájl nem felel meg az aktív szűrőknek. |
