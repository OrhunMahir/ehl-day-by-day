# Scout: Lieferantenportal (customer portal), tender VG-2027-7093

Site: https://atira-rfq-customer-portal.kyora.run. Scouted 2026-10-10 with headless Chromium at 1280x800.
Scratch scripts and raw HTML dumps: `/private/tmp/claude-501/-Users-julienhartzer-ehl-hackathon/fd212338-599c-4aa2-a6fc-bee2055537fd/scratchpad/scout/customer/`. The useful ones are `common.py` (login, consent, recursive frame dump), `s4.py` (access grant, then all viewer pages of one revision) and `s5.py` (negative tests, "Weitere laden").

The site is server-rendered HTML with small inline scripts. There is no SPA framework and no JSON API.

## 1. Navigation map

| Step | URL | German UI labels and what to do |
|---|---|---|
| Login | `/login?returnTo=%2F` | Form `POST /login`. Fields `username` ("Benutzername") and `password` ("Passwort"), button **Anmelden**. A hidden `csrf` field is already in the form. |
| Cookie consent (after the first login) | `/consent?returnTo=...` | "Cookie-Einstellungen". Click the form button **Notwendige Cookies akzeptieren** (`POST /consent`). **Trap:** the dialog link "Akzeptieren und fortfahren" goes to `/documents/supplier-guide` (wrong page). |
| Home | `/` ("Startseite") | "Willkommen im Lieferantenservice". Buyer name: **Stadt Buchenstadt, Verkehrsbetriebe**. Links: "Dokumentenübersicht" (3 of them: one is a `<button data-unavailable>` dead button, one goes to `/documents`, one to supplier-guide), "Anlieferhinweise öffnen" → `/documents/delivery`. |
| Side nav | (every page) | "Startseite" `/`. "Dokumente ▾" is a hover/focus dropdown, CSS `display:none` until hover. It contains "Register öffnen" → `/documents` and "Lieferantenverwaltung" → `/documents/supplier-guide`. "Abmelden" (`POST /logout`). |
| Document register | `/documents?page=N` (N=1..4) | Heading "Dokumentenregister". Columns: Dokumentnummer, Titel, Zuordnung, Typ. **One row per page**, 4 pages, with "Nächste Seite" / "Vorherige Seite" and "Seite X von 4". Each row has an **"Aktionen"** button that opens a hidden menu with the link **"Dokument anzeigen"**. That link is in the DOM but hidden until click/hover. |
| Register rows | | INF-001 Leitfaden für Lieferanten → `/documents/supplier-guide`. EK-003 Verhaltenskodex für Geschäftspartner → `/documents/conduct`. **LH-VG-2027-7093 "Lastenheft Hydraulikzylinder für Werkstatt-Hebeanlagen"**, Zuordnung `VG-2027-7093`, Typ "Technische Spezifikation" → `/documents/specification`. LOG-014 Anlieferung und Warenannahme → `/documents/delivery`. The row order across pages may vary, so look for the tender number rather than a fixed page. |
| Access gate | `/documents/specification` (when not yet granted). Also any `/documents/specification/viewer?...` before the grant. | "Dokumentenüberlassung". See section 2.3. Form `POST /documents/access`. On failure the URL becomes `/documents/access` and shows "Keine Leseberechtigung erteilt. Belegnummer und Verwendungszweck erneut prüfen." |
| Spec page | `/documents/specification` (defaults to the **oldest** revision R-4034). `/documents/specification?revision=R-XXXX` shows a chosen revision. | H1 "Lastenheft Hydraulikzylinder für Werkstatt-Hebeanlagen", eyebrow "LH-VG-2027-7093 · Technische Unterlagen". Collapsed `<details>` **"Revisionshistorie"** holds a revision table. H2 "Dokumentansicht · Revision R-XXXX" sits above an `<iframe>`. |
| Viewer (iframe) | `/documents/specification/viewer?revision=R-XXXX&page=N` | Works as a top-level URL too (same session). Region "Anforderungsblätter", table with 1 requirement per page, "Seite N von 5", "Nächste Seite" link, and a **"Weitere laden"** button (AJAX append). |

## 2. Obstacles and generic workarounds

### 2.1 Fake browser chrome and download bar
- Every page and the viewer iframe start with `<section aria-label="Portal-Werkzeugleiste">`. It contains a fake back arrow "←" (a link to supplier-guide or `/documents`), a lock icon, a read-only "Adresse" textbox with a fake address ("Lieferantenservice / Dokumentendienst" or "LH-VG-2027-7093 · Revision R-4034"), and a "Neu laden" ↻ button.
- A fixed bottom download bar, `role=region aria-label="Downloads"`, shows "↓ Überlassungsbedingungen.html Öffnen ×" (in the iframe: "↓ Archivverzeichnis.html Öffnen"). The "Öffnen" link only goes to supplier-guide or `/documents`. **No real download exists anywhere.**
- Tool advice: strip `#portal-chrome` / `#viewer-chrome` (or any `[aria-label="Portal-Werkzeugleiste"]`, `[aria-label=Downloads]`) from the text given to the LLM. Always report the **real** `page.url` / `frame.url`, never the fake "Adresse" box. The bar is `position:fixed; z-index:8` and covers the bottom ~55px, so prefer DOM-level clicks (`locator.click()` / JS) over coordinate clicks.

### 2.2 Overlays and modals
- The `<aside id="portal-banner">` "Aktueller Hinweis" (yellow box) appears after 650 ms. In the iframe it is "Druckvorschau: …" after 800 ms. It **shifts the layout**, so coordinates taken before it appears are wrong.
- `<dialog id="portal-survey">` (top page) and `<dialog id="viewer-maintenance">` (iframe) are both titled "Dokumentenanzeige". They open with `showModal()` after 1.1–1.4 s on some navigations (`data.show`, which repeats every 5 navigations, tracked in sessionStorage). **Escape is blocked.** The "×" close button is 14x14 px at a **random position** (left/top % change per page load). The prominent link "Akzeptieren und fortfahren" is a **trap**: it navigates away to supplier-guide or `/documents`. The text "Die ausgewählte Revision bleibt erhalten" is misleading.
- Tool advice: after each navigation, wait ~1.5 s, then `document.querySelectorAll('dialog[open]').forEach(d=>d.close())` in **all frames**. Another option is to click `dialog [data-close]` / `button[aria-label=Schließen]` through a locator. Never follow "Akzeptieren und fortfahren".

### 2.3 Access gate (Dokumentenüberlassung), with a canvas-rendered receipt number
- The text is in a small scroll box `#access-terms` (210 px high, "Überlassungsbedingungen"). The instruction sits at its bottom, below a large spacer: choose **"Verbindliche Unterlagen zur Kenntnis genommen"** and transfer the shown **Belegnummer**. "Archiv und Vorschau erteilen keine Leseberechtigung."
- The Belegnummer (e.g. `64431`, new on each load) is drawn on a `<canvas id="access-receipt">`. You can still read it without OCR: `aria-label="Belegnummer: 64431"` and the canvas fallback text both carry it, though `innerText` does not.
- The input label "Belegnummer übertragen" is CSS `::before{content:attr(data-label)}`, so it is **not in innerText**. It is only in `aria-label` / `data-label`. The input is `#receipt` / `name=receipt`.
- Radio `name=purpose` values: `preview` "Nur Vorschau verwenden", `reference` "Verbindliche Unterlagen zur Kenntnis genommen" (**the correct one**), `archive` "Archivkopie verwenden". The **radio order is shuffled** on retry, so select by value or label text, not by position.
- The checkbox `acknowledged` is "Überlassungsbedingungen gelesen". There are two submit buttons: "Abbrechen und Eingaben löschen" (`operation=reset`, a trap) and **"Leseberechtigung anfordern"** (`operation=read`).
- Once granted, the session cookie carries the grant. Later visits to `/documents/specification` skip the gate.

### 2.4 Revision handling
- `/documents/specification` with no parameter opens the **oldest** revision, R-4034 (marked "ersetzt").
- The revision list sits in a **collapsed `<details>` "Revisionshistorie"**, and inside it in a scroll box `#revision-journal` with a spacer. Its content is **not in innerText** and does not show in Playwright's `aria_snapshot()` until expanded.
- The status ("gültig" / "ersetzt") is **only in a tooltip**. A button "Freigabestand" has `aria-describedby="revision-status-N-value"`, which points to `<span role=tooltip>` (`display:none` until hover/focus). The visible text of all three rows is identical ("Freigabestand").
- Revision IDs are rendered with zero-width spaces (`R​-​6​1​5​6`), while `aria-label="R-6156"` is clean.
- Dates come in **mixed formats** marked in the text: `01/05 (MM/TT) 2027` and `17/01 (TT/MM) 2027`. A clean form is in `aria-label="Datum 01/05, Format MM/TT"`.
- `aria-current="page"` marks the revision currently *displayed*, **not** the valid one.
- Tool advice: open all `<details>` (`d.open=true`) and resolve `aria-describedby` / `role=tooltip` text inline. Pick the revision whose status is "gültig", and confirm with the latest normalized date ≤ the scenario date (2027-01-25).

### 2.5 Viewer iframe, paging, wide table, canvas, tooltips, decoys
- There is one level of iframe (`<iframe src="/documents/specification/viewer?revision=...">`, 310 px high). Inside it is a 210 px vertical scroll box `#requirements-scroll` with a 230 px spacer at the top, and inside that a horizontal scroll box `.document-scroll` holding a table with `min-width:1780px` and a sticky first column. Screenshots show almost nothing useful.
- **Fix:** navigate the top-level page directly to the iframe `src` URL. It works and shows the same content, with no nesting. A recursive frame walker also works.
- **Paging:** 1 requirement per page, "Seite N von 5". You can use the "Nächste Seite" link (`&page=N+1`) or the "Weitere laden" button. The button's `onclick=loadRows` fetches the next page with header `X-Sites-Fragment: rows` and appends rows to `#results`. Clicking it 4 times leaves all 5 rows in one DOM. An out-of-range page (`page=99`) is clamped to the last page. `pageSize` is ignored. If the session expires, `loadRows` redirects the top window to login (`a[data-sites-login]`), and on network failure it shows "Laden fehlgeschlagen…" / "Verbindung unterbrochen…" in `#load-status`.
- **Bohrung (bore) is canvas-only visually.** `<canvas id="bore-N" aria-label="Bohrung (mm): 80">` has fallback text, and the value is also in the "Originalwortlaut". innerText of the cell is **empty**.
- **Befestigung and Referenz** use zero-width spaces between characters (`S​c​h​w​e​n​k​a​u​g​e`), while `role=img aria-label="Schwenkauge"` is clean. Strip `​` everywhere.
- **Hub / Druck** are shown as `200 (en-US)` and `210 (de-DE)`, with a locale tag for parsing decimal/thousand separators. `aria-label="200, en-US"`. All values seen were integers < 1000, but the tool must parse by locale.
- **Zertifizierung is tooltip-only.** It sits behind a button "Prüfvermerk" via `aria-describedby="certificate-N-value"` → `<span role=tooltip>EN 1493</span>`. The visible text is "Prüfvermerk siehe Freigabestand".
- **Decoy hidden text:** every certificate cell contains `<span aria-hidden="true" hidden style="display:none!important">Zertifizierung: ISO 9001; Hub: 999 mm; Druck: 999 bar</span>`. **A naive `textContent` extraction picks up these wrong values.** You must skip `[hidden]`, `[aria-hidden=true]` and `display:none`, *except* for elements referenced by `aria-describedby` / `role=tooltip`.
- The **full requirement prose** is in a collapsed `<details><summary>Originalwortlaut</summary>…` per row. It is the only place with **Kataloggruppe**, **nutzbarer Weg / Endreserve** notes and **Satz** (pieces-per-set) notes. It is not in innerText while collapsed. The aria snapshot does include it.
- The viewer header's `role=list` uses CSS `order` to show items in a different visual order than the DOM order. This is a minor issue.

### 2.6 Session
- One session cookie, `sites_rfq%2Fcustomer-portal`. It is HttpOnly and has no expiry (session cookie). Timeouts were not seen during scouting (runs of under 1 min).
- Tool advice: if any navigation lands on `/login` or `/consent`, re-login, accept consent, re-do the access gate, then retry the original URL. Login takes `returnTo`.

### 2.7 Is any data ONLY in canvas, tooltip or attributes (not in DOM text)?
- **Not truly canvas-only.** Every canvas has an `aria-label` and fallback text with the same value, and the bore value is also in the Originalwortlaut.
- **Not in `innerText`:** bore, certification (tooltip), Originalwortlaut (closed details: Kataloggruppe, Satz, nutzbarer Weg), revision status (tooltip), revision table (closed details), Belegnummer (canvas), receipt label (CSS content).
- So a DOM extractor that reads **aria-label of role=img/canvas, aria-describedby targets, closed `<details>` content, and strips zero-width chars and hidden/aria-hidden decoys** gets everything. A pure screenshot/vision approach would need hovers, expanding, scrolling in 2 directions inside a 310 px iframe, and 5 pages, so it is not recommended.

## 3. Data found (VG-2027-7093) — data shapes only, not for hardcoding

Document: **LH-VG-2027-7093** "Lastenheft Hydraulikzylinder für Werkstatt-Hebeanlagen". Buyer: Stadt Buchenstadt, Verkehrsbetriebe.

### Revisions (Revisionshistorie)
| Revision | Date (raw → ISO) | Status (tooltip) | Änderungsvermerk |
|---|---|---|---|
| R-4034 | `01/05 (MM/TT) 2027` → 2027-01-05 | ersetzt | Erstausgabe |
| **R-6156** | `17/01 (TT/MM) 2027` → 2027-01-17 | **gültig** | A-1: Hubmaß angepasst. Bitte ausschließlich die aktuelle Revision verwenden. |
| R-9711 | `11/01 (TT/MM) 2027` → 2027-01-11 | ersetzt | A-1: Hubmaß angepasst. Bitte ausschließlich die aktuelle Revision verwenden. (same note as R-6156, which is a trap) |

The current revision is **R-6156**. It is marked only by the "gültig" tooltip, and it is also the latest date before 2027-01-25. The table order is not chronological, and the ID numbers do not show the order.

### Requirements in R-6156 (current)
| Ref | Bohrung mm | Hub mm | Druck bar | Befestigung | Zertifizierung | Kataloggruppe | Notes from Originalwortlaut |
|---|---|---|---|---|---|---|---|
| A-1 | 80 | 200 | 210 | Schwenkauge | EN 1493 | GR-8923 | — |
| A-2 | 63 | 489 | 250 | Flansch | EN 1493 | GR-5152 | **nutzbarer Weg**: "Hub/Stellweg ist nutzbarer Weg. ERP-Nennweg = nutzbarer Weg + 2 × Endreserve. Endreserve-Schlüssel ER-4001; revisionsgebundene Technikfreigabe beachten. Auch technische Nachträge nennen den nutzbaren Weg." |
| A-3 | 40 | 600 | 210 | Fußbefestigung | keine gefordert | GR-6333 | — |
| A-4 | 50 | 576 | 210 | Schwenkauge | keine gefordert | GR-6617 | **nutzbarer Weg**, Endreserve-Schlüssel **ER-9526** (same wording as A-2) |
| A-5 | 125 | 300 | 250 | Flansch | keine gefordert | GR-4287 | **Satz**: "Lieferumfang: 1 Satz = 5 Stück. ERP-Bestände, Preisstaffeln und Angebotsmengen beziehen sich auf Stück." |

Every Originalwortlaut ends with "Kataloggruppe GR-xxxx: in der ERP-Suche eingeben und technische Merkmale einzeln prüfen." This means the Kataloggruppe is the ERP search key, and each attribute must be checked individually. Each Originalwortlaut follows the pattern "Bohrung X mm, Hub Y mm, Betriebsdruck Z bar, Befestigung M, Zertifizierung C."

**The Endreserve value is not on this site.** Only the key (ER-4001 / ER-9526) is given, along with "revisionsgebundene Technikfreigabe beachten", so it must come from another system (ERP or technical release). The ERP nominal stroke is the usable stroke plus 2 × Endreserve. No quantities or pieces per requirement were shown, apart from the Satz conversion for A-5. Quantities presumably come from the RFQ email or another site.

### Differences between revisions (why "use the current one" matters)
| Ref | R-4034 (Erstausgabe) | R-9711 | R-6156 (gültig) |
|---|---|---|---|
| A-1 Hub | 100 | 150 | **200** |
| A-2 Hub | 500, no note | 500, no nutzbarer-Weg note | **489** + nutzbarer-Weg/ER-4001 note |
| A-4 Hub | 600, no note | 600, no note | **576** + nutzbarer-Weg/ER-9526 note |
| A-5 note | no Satz note | no Satz note | **"1 Satz = 5 Stück"** (only in R-6156) |
| A-3 | same | same | same |

All other values (bore, pressure, mounting, certificate, Kataloggruppe) are the same in all three revisions. The R-6156 change note only mentions A-1, but A-2, A-4 and A-5 changed too. Do not rely on the change note; read the full current revision.

## 4. Recommendations for generic agent tools

1. **`read_page(url_or_current, include_frames=True)`** returns a cleaned "semantic text" of the page and **all frames recursively**. Each frame is labelled with its real URL and iframe `src`. Rules:
   - Remove `​` and other zero-width characters.
   - Drop `aria-hidden=true` / `[hidden]` / `display:none` subtrees, **but** inline the text of any element referenced by `aria-describedby`/`aria-labelledby` or with `role=tooltip`. Render it as `Prüfvermerk [tooltip: EN 1493]`.
   - Include the text of closed `<details>` (mark it `[collapsed]`).
   - For `canvas`, `img`, `role=img`, `svg` and `role=group`, use the `aria-label` (and `alt`/title) when there is no visible text. Render a canvas as `[canvas: Bohrung (mm): 80]`.
   - Include CSS `::before/::after` `content` when it is non-empty (receipt label). Optional.
   - Render tables as Markdown or TSV, ignoring overflow/scroll containers, with each row's cells in DOM order.
   - Strip the known chrome regions (`aria-label` "Portal-Werkzeugleiste", "Downloads") or tag them `[fake browser UI]`.
   - List links (text → href), buttons (with `data-url` where present), and forms with fields (name, type, value, label, radio options with values).
2. **`dismiss_overlays()`** runs after every navigation and before interacting. It waits ~1.5 s, then closes `dialog[open]` in all frames via JS. It never follows "Akzeptieren und fortfahren".
3. **`goto(url)`** returns final URL + `read_page`. It auto-detects `/login` and `/consent` and re-authenticates (credentials from config, never shown to the LLM), then retries.
4. **`fill_form(selector_or_form_action, {name: value}, submit_button_text_or_value)`** fills by field **name/value**, not by position, because radio order is shuffled. Submit uses Playwright locators, not coordinates.
5. **`open_frame(url)` / `follow_iframe(index)`** navigates the top page to an iframe's `src` to escape nested, tiny, scroll-boxed viewers.
6. **`collect_paged(start_url, next_link_text="Nächste Seite")`** follows next links (or reads "Seite X von N") and concatenates the table rows. This is generic for both the document register and the viewer, and it avoids relying on the JS "Weitere laden".
7. Optional **`screenshot(frame)`** as a fallback for real canvas-only content. It is not needed here, since aria-labels mirror all canvases.
8. Prompt hints for the LLM:
   - The current revision = status "gültig" from the tooltip, not the default-opened revision and not `aria-current`.
   - Normalize dates using the `(MM/TT)` / `(TT/MM)` markers.
   - Parse numbers using the `(en-US)` / `(de-DE)` markers.
   - Read the "Originalwortlaut" for Kataloggruppe, nutzbarer Weg / Endreserve-Schlüssel and Satz conversions.
   - Ignore hidden "ISO 9001 / 999" decoys.
