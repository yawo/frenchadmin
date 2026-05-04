I've gathered enough evidence. Below is a thorough analysis of the issue, broken into: codebase context, requirements, current implementation, and the actual root cause of the unresolved mentions you observed (with reproducible evidence).

---

# Cross‑reference inference — root‑cause analysis

## 1. Codebase context (relevant pieces only)

- **Source ingestion** lives in `download_and_processing/files_processing.py`. For LEGI documents (`LEGIARTI…`):
  - `category` ← `<CONTEXTE>/<TEXTE>@cid` (parent text id, e.g. `LEGITEXT000006069577`)
  - `number` ← `<NUM>` (e.g. `L123-3`, `42 septies`)
  - `title` ← the **last** `<TITRE_TM>` element (i.e. the deepest sub‑section heading)
  - `full_title` ← `<TITRE_TXT>` text concatenated with the **whole subtitle chain**:
    
    ```280:307:download_and_processing/files_processing.py
                title = None
                subtitles = []
                for elem in root.find(".//CONTEXTE//TEXTE").iter("TITRE_TM"):
                    subtitles.append(elem.text)
                    title = elem.text  # Keep updating title with the last subtitle found, which is the most specific one
                subtitles = " - ".join(subtitles)
                ...
                title = title.strip(".") if title else maintitle 
                full_title = root.find(".//TITRE_TXT").text+((" "+subtitles) if subtitles else "")
    ```
- **Cross‑reference module** (`crossreference/`) is a standalone pipeline:
  - `extractor.extract_article_mentions` finds `article …` anchors and returns a `matched_text` that **also captures the code name** when introduced with `du / de / d’` (e.g. `1745 du code général des impôts`).
  - `normalizer.normalize_article_number` produces `normalized_number` (primary key for catalog lookup).
  - `alias_detector.infer_code_family` / `extract_code_family_from_mention` map context strings to `(family, parent_text_ids)` (e.g. CGI → 5 LEGITEXT ids).
  - `resolver.resolve_article` runs the cascade A → B → C → D → E.
  - `database/cross_reference_manage.refresh_legi_reference_catalog` rebuilds `legi_reference_catalog` from `legi`.

## 2. What `CROSSREFERENCE.md` requires (relevant invariants)

- §5.1: a catalog row must expose `parent_text_id`, `article_number`, `start_date`, `end_date`, plus a normalized key.
- §5.2: `code_label` must be the **stable leading legal‑text label** (e.g. `Code de commerce`, `Code général des impôts`), not the entire `full_title` chain.
- §5.3: `code_family` ∈ {`CGI`, `LPF`, `CIBS`, `OTHER_CODE`, NULL}. Anything else is out of contract.
- §10.1 (Step A): exact match on `normalized_number` (not on a string that contains the code name).
- §17 (non‑negotiable): always resolve to a canonical `legi.doc_id`, with temporal filtering and family scoping before fuzzy/semantic.

## 3. What the current implementation actually stores

### 3.1 Catalog `code_label` is the full subtitle chain, not a clean code label

`refresh_legi_reference_catalog` in `database/cross_reference_manage.py`:

```283:307:database/cross_reference_manage.py
            code_label = None
            if title:
                t = title.lower().strip()
                if t.startswith("code ") or t.startswith("livre "):
                    code_label = title
            if not code_label and full_title:
                t = full_title.lower().strip()
                if t.startswith("code ") or t.startswith("livre "):
                    code_label = full_title

            # Infer code_family
            family = None
            if category:
                for fam_name, fam_info in CODE_FAMILY_MAP.items():
                    if category in fam_info.get("parent_text_ids", []):
                        family = fam_name
                        break
            
            # Fallback: extract code family from code_label
            if not family and code_label:
                # Extract first part before " - " or " Partie" (e.g., "Code civil", "Code de commerce")
                code_name = code_label.split(" - ")[0].split(" Partie")[0].strip()
                if code_name:
                    family = code_name
```

Because LEGI ingestion stores:
- `title` = the **deepest** `<TITRE_TM>` (e.g. *“Des personnes tenues à l’immatriculation”*) → does **not** start with `Code ` / `Livre `,
- `full_title` = `<TITRE_TXT>` + entire subtitle chain (e.g. `Code de commerce Partie législative - LIVRE Ier : Du commerce en général. - TITRE II : … - Sous-section 1 : …`),

the catalog ends up with `code_label` = the **whole** `full_title` chain. This matches exactly what you observed:

```
LEGIARTI000006219285 | L123-3 | L123-3 |  | Code de commerce Partie législative - LIVRE Ier : … - Sous-section 1 : …
```

The CROSSREFERENCE.md contract (§5.2) is to extract the *stable leading* label (`"Code de commerce"`). Current code does not do that.

### 3.2 `code_family` is free‑text outside the documented enum

For any LEGI row whose `category` is not in `CODE_FAMILY_MAP.parent_text_ids` (i.e. anything outside the core 5 CGI texts, the LPF text, and the CIBS text), the fallback writes the *cleaned leading label* into `code_family`:

```python
code_name = code_label.split(" - ")[0].split(" Partie")[0].strip()
family = code_name
```

After this fallback the column contains values such as `"Code civil"`, `"Code de commerce"`, `"Code de l'urbanisme"`, etc. CROSSREFERENCE.md §5.3 explicitly restricts the column to `CGI` | `LPF` | `CIBS` | `OTHER_CODE` | NULL.

The user’s catalog sample shows `code_family` blank for the Code‑de‑commerce rows, which means the snapshot was taken **before** commit `c6bd86f` (which introduced this fallback). Whether the column is blank (pre‑c6bd86f) or filled with free‑text labels (post‑c6bd86f), neither matches the documented enum, and neither matches what the resolver is willing to detect from JADE/BOFIP.

### 3.3 Asymmetry between catalog values and resolver/alias outputs

The resolver/alias side is hard‑wired to a small enum:

```22:22:crossreference/resolver.py
TAX_CORE_FAMILIES = {"CGI", "LPF", "CIBS"}
```

`alias_detector.extract_code_family_from_mention()` and `infer_code_family()` can only ever return values in `{None, "CGI", "LPF", "CIBS", "OTHER_CODE"}`. Therefore:

- `_family_prior_resolve` and `_get_scoped_candidates` filter on `code_family = ANY({CGI,LPF,CIBS})` — only CGI/LPF/CIBS rows reachable; rows where the fallback wrote `"Code de commerce"` are invisible to that path.
- `_resolve_ambiguity` filters with `r[3] == detected_family`. Any non‑core mention with detected family `"OTHER_CODE"` cannot match catalog rows whose `code_family = "Code de commerce"`.

That confirms the user’s diagnosis: **what the catalog stores in `code_family` and `code_label` is not part of any vocabulary the resolver uses.**

### 3.4 Extended alias map is effectively dead

`alias_detector._load_extended_aliases_from_catalog`:

```96:132:crossreference/alias_detector.py
            cursor.execute(
                """
                SELECT DISTINCT parent_text_id, code_label
                FROM legi_reference_catalog
                WHERE code_label IS NOT NULL
                  AND code_label <> ''
                """
            )
            ...
    for parent_text_id, code_label in rows:
        ...
        normalized_label = _normalize_for_alias(code_label)
        ...
        alias_map[normalized_label].add(parent_text_id)
```

Because `code_label` is the entire 100–300‑char `full_title` chain, the resulting alias map contains thousands of long, hyper‑specific strings. `_alias_in_text(alias, normalized_text)` uses `\b{alias}\b`, so the chance that a JADE/BOFIP context contains a verbatim chain like `code de commerce partie legislative livre ier du commerce en general titre ii des commercants chapitre iii ...` is zero. In practice extended (non‑core) alias detection never fires — the resolver can only ever see the three core families.

That is independent of the user’s primary symptom but it explains why nothing outside CGI/LPF/CIBS is ever resolved either.

## 4. Why the sample mentions are `unresolved` with confidence 0

Combining the above with the extractor and normalizer behaviour produces the exact failure mode you observed. The proof is reproducible from the actual modules:

```text
SOURCE: 'Vu l article 1745 du code général des impôts qui dispose...'
  matched_text   : '1745 du code général des impôts'
  normalized     : '1745 DU CODE GÉNÉRAL DES IMPÔTS'
  loose          : '1745DUCODEGÉNÉRALDESIMPÔTS'
  ctx_family     : ('CGI', 'code general des impots', ['LEGITEXT000006069577', 'LEGITEXT000006069568', 'LEGITEXT000006069569', 'LEGITEXT000006069574', 'LEGITEXT000006069576'])
  mention_family : CGI

SOURCE: 'L application de l article L. 247 du livre des procédures fiscales'
  matched_text   : 'L. 247 du livre des procédures fiscales'
  normalized     : 'L. 247 DU LIVRE DES PROCÉDURES FISCALES'
  loose          : 'L.247DULIVREDESPROCÉDURESFISCALES'
  ctx_family     : ('LPF', 'livre des procedures fiscales', ['LEGITEXT000006069583'])
  mention_family : LPF

SOURCE: 'application de l article 238 de l annexe II au code général des impôts'
  matched_text   : '238 du l annexe II au code général des impôts'
  normalized     : '238 DU L ANNEXE II AU CODE GÉNÉRAL DES IMPÔTS'

SOURCE: 'application de l article 117 du code général des impôts'
  matched_text   : '117 du code général des impôts'
  normalized     : '117 DU CODE GÉNÉRAL DES IMPÔTS'

SOURCE: 'L article L. 53 du livre des procédures fiscales'
  matched_text   : 'L. 53 du livre des procédures fiscales'
  normalized     : 'L. 53 DU LIVRE DES PROCÉDURES FISCALES'
```

These exactly match the rows the user pasted from `cross_reference_legi_mentions`:
- `matched_text`: `1745 du code général des impôts`
- `normalized_number`: `1745 DU CODE GÉNÉRAL DES IMPÔTS`
- `resolver_method = unresolved`, `confidence = 0`.

### 4.1 Why every cascade step misses

The catalog row for CGI article 1745 has `normalized_number = '1745'`. The mention’s `normalized_number = '1745 DU CODE GÉNÉRAL DES IMPÔTS'`. They differ.

Step A (`_exact_resolve`, `use_loose=False`):

```187:200:crossreference/resolver.py
        cursor.execute(f"""
            SELECT
                legi_doc_id,
                parent_text_id,
                article_number,
                code_family,
                start_date,
                end_date
            FROM legi_reference_catalog
            WHERE {col} = %s
              AND start_date <= %s
              AND end_date >= %s
              {scope_clause}
        """, params)
```

`'1745 DU CODE GÉNÉRAL DES IMPÔTS' = '1745'` → 0 rows. Same for L.247, 238, 117, L.53 (they all carry the suffix).

Step B (loose key `'1745DUCODEGÉNÉRALDESIMPÔTS'` vs catalog’s `'1745'`) → 0 rows.

Step C (`_family_prior_resolve`) is gated on `if not detected_family:` — but `detected_family = "CGI"` here, so step C does not run.

Step D (fuzzy_resolver):
- candidate set = all CGI normalized_numbers in catalog (a few thousand short strings like `1`, `1A`, `1A bis`, `1745`, `1745A`, …).
- `rapidfuzz.process.extractOne('1745 DU CODE GÉNÉRAL DES IMPÔTS', candidates, score_cutoff=96)` cannot reach 96 because the query is overwhelmingly longer than every candidate.
- Even when partial scoring picks `1745`, `_structure_changed` rejects: `re.findall(r"\d+", '1745 DU CODE GÉNÉRAL DES IMPÔTS') == ['1745']` and `re.findall(r"\d+", '1745') == ['1745']` agrees on numeric, but the token‑sort/ratio score for these two strings is ~30, far below 96. → reject.

Step E (semantic_resolve):
- It does run and returns a top neighbour.
- Acceptance threshold: `SEMANTIC_MIN_SCORE = 0.75` when `has_code_alias` is true (it is for CGI/LPF mentions).
- For a JADE chunk talking about *fraude fiscale* and a single citation of `art. 1745 CGI`, the cosine similarity between the 200‑char context and any single CGI article is realistically below 0.75. So semantic also rejects → returns `None`.

Result: the resolver returns `resolver_method = 'unresolved'`. In `pipeline._process_source_document`, `score_confidence("unresolved", …)` resolves to base 0.0; with `detected_code_alias is not None` (CGI), the `−0.05` no_alias penalty is **not** applied; with `is_generic = False` for `1745`, the `−0.10` is not applied either. So `confidence = 0.0`, `is_accepted = False`. That matches the table row `confidence | 0`.

### 4.2 Why the catalog `code_family` / `code_label` issues you flagged are real but not the *direct* cause of these particular zero rows

For the five mentions you pasted, family detection actually succeeds:
- `infer_code_family` finds `CGI` / `LPF` from the surrounding context, and
- `extract_code_family_from_mention` *also* finds `CGI` / `LPF` from the matched text itself.

So `detected_family` is correct and `detected_parents` is correct. The mismatch between catalog values (`code_family = NULL` or `"Code de commerce"`) and resolver enums is irrelevant for CGI/LPF mentions — those rows in the catalog correctly carry `code_family = 'CGI'` or `'LPF'` because they hit the explicit `CODE_FAMILY_MAP` branch in the catalog refresh, not the free‑text fallback.

The actual reason these CGI/LPF mentions are unresolved is upstream of any catalog content: the **`normalized_number` written into the mentions row is the article number plus the trailing code‑name phrase**, so the equality test in Step A/B never finds the correctly‑classified catalog row.

The catalog `code_family` / `code_label` problems you correctly identified become decisive for *non‑core* mentions (e.g. *"article L. 1242-1 du code du travail"*): with the current shape of `code_label` and free‑text `code_family`, even if the normalizer issue were fixed they still wouldn’t resolve cleanly, because:
- the extended alias map is built from a 200‑char string and never matches in source context;
- when the alias does match (say through the core map for CGI), `_resolve_ambiguity` and the family‑prior cascade compare on a free‑text `code_family` that the resolver code does not produce.

So both concerns are real; they sit at different layers.

## 5. Summary of misalignments (catalog vs. resolver vs. extractor)

1. **Extractor → normalizer asymmetry (primary cause of your sample failures).**
   `extract_article_mentions` deliberately captures the article *and* the code name into `matched_text` (`"1745 du code général des impôts"`). `normalize_article_number` only strips a leading anchor (`article|art.|art|n°`) — it does **not** strip the trailing `du <code>` clause. Result: the mention’s `normalized_number` is a long string that cannot equal the catalog’s short `normalized_number`, so steps A and B always miss. Steps D (fuzzy) and E (semantic) cannot rescue this because the comparison space is wrong, not noisy.
   *Note that commit `1d7da7c` once stripped code names in the normalizer and was reverted by `e07a7b1`, leaving the system in this asymmetric state.*

2. **Catalog `code_label` violates the spec.**
   It stores `full_title` (full subtitle chain) instead of the leading legal‑text label. CROSSREFERENCE.md §5.2 explicitly asks for the leading label. Direct consequences:
   - Extended alias detection (`_load_extended_aliases_from_catalog`) becomes useless: aliases are 100–300‑char strings that never appear verbatim in JADE/BOFIP context.
   - The fallback that derives `code_family` from `code_label` (`split(" - ")[0].split(" Partie")[0]`) is only correct because of accidents of LEGI title formatting; non‑Code/non‑Livre texts (`Loi …`, `Décret …`) never get a usable label at all.

3. **Catalog `code_family` violates the documented enum.**
   The fallback writes free‑text leading labels into `code_family` (e.g. `"Code de commerce"`). The resolver and alias detector only ever produce `{CGI, LPF, CIBS, OTHER_CODE}`. Direct consequences:
   - `_family_prior_resolve` (Step C) is restricted to `code_family = ANY({CGI,LPF,CIBS})`, so non‑core rows are unreachable from this path.
   - `_resolve_ambiguity`’s `r[3] == detected_family` cannot match `"OTHER_CODE"` against `"Code de commerce"`.
   - In an existing snapshot (pre‑`c6bd86f`) `code_family` is simply NULL for non‑core families, with the same effect.

4. **Catalog refresh is whole‑corpus but does not enforce target validity.**
   The catalog `SELECT DISTINCT ON (doc_id) … FROM legi` keeps a single row per `legi_doc_id` over the whole corpus, including rows where `category` is a `JORFTEXT…` parent. CROSSREFERENCE.md §1.6 / §13.4 forbids ever resolving to a `JORFTEXT…` target. The catalog is not pruning these, which means the deterministic resolver can in principle return a `JORFTEXT`-parented article when `detected_parents` is empty (for non‑core OTHER_CODE matches). Not a current symptom for the five mentions, but a precision risk per §17.

5. **Hash/sync side‑effect.** Because the catalog body changes whenever LEGI is re‑ingested (and `updated_at` is included in the JSON used for `catalog_hash`… actually it isn’t, good — but `code_family`/`code_label` are), any change to how `code_label`/`code_family` is computed forces every `cross_reference_source_state.catalog_hash` to invalidate, triggering a full rebuild. So a fix here costs one full rerun across JADE+BOFIP — consistent with §14.3 but worth noting.

## 6. Minimum set of changes required to align with the spec

These follow directly from the analysis above. They’re scoped to the layer where each misalignment lives, so the fixes don’t leak across modules.

1. **Strip the trailing code‑name phrase before storing `normalized_number`** (mandatory; this alone fixes your five sample rows).
   Either:
   - in `crossreference.normalizer.normalize_article_number`, after the existing leading‑anchor strip, also strip a trailing `\s+(?:du|de la|de l['’]?\s*|au|aux)\s+.*$` clause whose tail matches a code‑label / family alias, **or**
   - in `crossreference.extractor.extract_article_mentions`, keep the rich `matched_text` for explainability but emit a separate `article_token_text` (just the article number) and feed *that* to the normalizer.
   The second option is cleaner because it preserves the matched span and its provenance unchanged while giving the resolver a clean key. Either way, the normalizer must guarantee `normalize_article_number("1745 du code général des impôts") == "1745"`.

2. **In `refresh_legi_reference_catalog`, replace `code_label = full_title` with the leading label.**
   Compute the leading code/livre label by:
   - strip leading `Code de l'…`, `Code …`, `Livre …` portion until the first ` - `, ` Partie`, `, annexe`, etc.
   - fall back to the LEGITEXT‑level top title when `full_title` lacks structure (e.g. `Loi n°…`, `Décret n°…`) and leave `code_label = NULL` for those rather than writing a 200‑char chain.

3. **Restrict `code_family` to the documented enum.**
   - keep `CGI` / `LPF` / `CIBS` from `CODE_FAMILY_MAP`.
   - replace the free‑text fallback with `code_family = 'OTHER_CODE'` when a clean leading `code_label` exists, and `NULL` otherwise.
   - update `_resolve_ambiguity` and the ambiguity preference to be aware of `OTHER_CODE`.

4. **Reshape extended alias loading.**
   In `_load_extended_aliases_from_catalog`, build aliases from the (now clean) leading label, plus a generated short alias (e.g. `_normalize_for_alias("Code du travail")`). Skip aliases longer than ~80 chars to avoid the dead‑weight problem.

5. **Filter the catalog at refresh time** to exclude `parent_text_id` values that are not `LEGITEXT…` (i.e. drop `JORFTEXT…`‑parented articles, or at least ensure they never become resolution targets unless an explicit `JORFTEXT…` family is added in scope). §1.6 / §13.4.

6. **Backfill** after fixes:
   - rerun `refresh_legi_reference_catalog` (catalog_hash will rotate).
   - clean current `cross_reference_legi_mentions` / `cross_reference_legi_edges` / `cross_reference_source_state` via the existing `clean_cross_reference_data("all")` helper, so the pipeline reprocesses every JADE/BOFIP doc with the corrected normalization.

After steps 1 and 6 alone, the five mentions in your sample (`1745`, `L.247`, `238`, `117`, `L.53`) should hit Step A as `exact_number_and_explicit_code` (confidence 0.99) and produce CGI / LPF article‑level edges — `art. 1745 CGI`, `art. L.247 LPF`, `art. 117 CGI`, `art. L.53 LPF`, and (depending on how `238 du l annexe II` is cleaned in the extractor — note the spurious `du l` that comes from the *de l'annexe II au code général des impôts* preposition handling) `art. 238 CGI annexe II`.

That last one warrants a small extractor follow‑up: `extract_article_mentions` currently produces `"238 du l annexe II au code général des impôts"` (extra `l`) because `re.match(r"^(.+?)\s+(?:du|de|d['\s])\s+", lookahead)` consumes only `de` and leaves the `l` (apostrophe variant from `de l'annexe`) inside the article portion. Fix: tighten the preposition regex to `(?:du|des|de\s+la|de\s+l['’\s]|au|aux|de)\s+` and treat `l'` / `l ` as part of the preposition, not the article.

---

### Bottom line

Your statement is accurate, but it’s actually **two distinct misalignments stacked on top of each other**:

- **Direct cause of the five `unresolved` rows you pasted:** the normalizer keeps the code‑name tail in `normalized_number`, so deterministic Step A/B compare a long string against the catalog’s short article key and never match — even though `detected_code_family` (CGI/LPF) was correctly inferred. This is a normalizer/extractor problem, not a catalog content problem.
- **Catalog content problem you flagged (`code_family` / `code_label` not aligned with what JADE/BOFIP resolution can recognize):** real, but its blast radius is the *non‑core* (OTHER_CODE) families — extended alias detection never fires, and ambiguity resolution can’t tie‑break across families because the column carries values outside the resolver’s vocabulary. It would block resolution for every non‑CGI/LPF/CIBS reference even after the normalizer fix.

Both should be fixed together; otherwise fixing only the normalizer leaves OTHER_CODE references in the same `unresolved` state, and fixing only the catalog leaves CGI/LPF mentions like `1745 du code général des impôts` exactly as they are now.