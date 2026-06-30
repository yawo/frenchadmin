# CROSSREFERENCE.md - JADE/BOFIP -> LEGI inference strategy
The goal is to infer article-level cross references from:
- `JADE` case law
- `BOFIP` doctrine

to:
- `LEGI` article documents

and store them for:
- law RAG
- graphRAG
- explainable provenance

This strategy is implementation-grade. It tells future code exactly what to build, where current data is trustworthy, and where current ingestion must be compensated for.

---

## 1. Hard facts from current code and live data

### 1.1 Real source tables

Current PostgreSQL tables are created in `database/database_manage.py`.

Actual tables:
- `legi`
- `jade`
- `bofip`

Even though DDL uses uppercase names, PostgreSQL stores them as lowercase because identifiers are unquoted.

### 1.2 Tables are chunk-level

All three tables store one row per chunk, not one row per document.

Current live counts on 2026-04-27:
- `legi`: 75,216 rows, 74,494 distinct `doc_id`
- `jade`: 61,550 rows, 60,654 distinct `doc_id`
- `bofip`: 7,062 rows, 6,309 distinct `doc_id`

Implication:
- every resolver must aggregate by `doc_id`
- provenance should still keep `chunk_id` and `chunk_index`

### 1.3 Real identifier semantics

#### LEGI

From `files_processing.py`:
- `legi.doc_id` = article id from XML `<ID>` such as `LEGIARTI000046868472`
- `legi.category` = parent text id from `CONTEXTE/TEXTE/@cid` such as `LEGITEXT000006069577`
- `legi.number` = article number as displayed to humans such as `42 septies`, `R*196-1`, `150-0 A`
- `legi.start_date` / `legi.end_date` = version validity boundaries as text in `YYYY-MM-DD`

Important:
- one human article number maps to many `LEGIARTI...` versions over time
- example in live data for `category='LEGITEXT000006069577'` and `number='42 septies'`:
  - `LEGIARTI000006302386` valid `1979-07-01` -> `1995-10-27`
  - `LEGIARTI000006302387` valid `1995-10-27` -> `1997-04-11`
  - ...
  - `LEGIARTI000046868472` valid `2023-01-01` -> `2999-01-01`

So a cross reference target is never "article number only". It must resolve to the right versioned `legi.doc_id`.

#### JADE

From `files_processing.py`:
- `jade.doc_id` = `CETATEXT...`
- `jade.number` = docket / decision number such as `04VE01914`
- `jade.decision_date` = decision date as text in `YYYY-MM-DD`

Important parser caveat:
- if `<ANA>` exists, current ingestion stores concatenated `ANA` text instead of the full body
- else it falls back to `BLOC_TEXTUEL/CONTENU`
- this means some JADE records preserve full `VU` / reasoning structure, others only store analysis text

This affects segmentation quality. The implementation must account for it.

#### BOFIP

From `files_processing.py`:
- `bofip.doc_id` currently prefers `document_number` such as `1000-PGP`
- `bofip.contenu_id` stores canonical BOFiP id such as `BOI-TVA-DECLA-20-20-30-20230118`
- `bofip.publication_date` = publication date as text in `YYYY-MM-DD`
- `bofip.links` stores `dc:relation` references to other BOFiP documents

Important:
- graph nodes are keyed by `bofip.doc_id`, not `contenu_id`
- cross-reference storage should use `source_doc_id = bofip.doc_id`
- but provenance should also retain `contenu_id`

### 1.4 Dates are complete and always available in the current snapshot

Checked in live data:
- `jade.decision_date`: no missing values across distinct documents
- `bofip.publication_date`: no missing values across distinct documents
- `legi.start_date` and `legi.end_date`: no missing values across distinct documents

Implication:
- temporal filtering is not optional
- no fallback path is needed for missing source dates in current corpus

### 1.5 Existing explicit links already stored

#### LEGI explicit links

`legi.links` is JSONB extracted from XML `LIENS`.

Live data:
- 74,363 `legi` rows have non-empty `links`
- many links are article-level `CITATION` entries

This is useful as silver data for testing normalization and resolution.

Example observed pattern:

```json
{
  "doc_id": "LEGIARTI000020038640",
  "text_doc_id": "LEGITEXT000006069577",
  "number": "1608",
  "category": "CODE",
  "link_type": "CITATION",
  "title": "CODE GENERAL DES IMPOTS, CGI. - art. 1608 (V)"
}
```

#### BOFIP explicit links

`bofip.links` is JSONB extracted from `dc:relation`.

Live data:
- 5,687 `bofip` rows have non-empty `links`
- these are BOFiP -> BOFiP references only

#### JADE explicit links

There is no explicit `links` column in `jade`.

### 1.6 Current FalkorDB model

Graph code in `database/graph_manage.py` currently uses:
- `(:LegalText {doc_id})` for LEGI documents
- `(:JudicialDecision {doc_id})` for JADE documents
- `(:TaxGuidance {doc_id})` for BOFIP documents

Current relationships:
- `BELONGS_TO_CODE`
- `ISSUED_BY`
- `DECIDED_BY`
- `REFERENCES`

Important current bug/risk:
- `upsert_legi_node()` builds `REFERENCES` edges from `link["doc_id"]` or `link["text_doc_id"]`
- this mixes article ids (`LEGIARTI...`) with text ids (`LEGITEXT...`, `JORFTEXT...`) in the same `LegalText.doc_id` namespace
- that can create placeholder graph nodes that do not correspond to real `legi.doc_id`

Implication for new inferred links:
- never target `LEGITEXT...` or `JORFTEXT...`
- always target canonical `legi.doc_id` values only

### 1.7 `init_graph_schema()` exists but is not wired into startup

`database/graph_manage.py` defines `init_graph_schema()`, but no current code path calls it.

Implication:
- the future cross-reference implementation should call `init_graph_schema()` before graph backfill or during startup / `create_tables`

### 1.8 LEGI ingestion peculiarities the catalog must account for

`download_and_processing/files_processing.py` builds:
- `legi.title` = the deepest `<TITRE_TM>` (the most specific subtitle, often
  not starting with `Code ` / `Livre `);
- `legi.full_title` = `<TITRE_TXT>` text **plus** the entire subtitle chain
  (e.g. `Code de commerce Partie législative - LIVRE Ier : ...`).

Implications:
- a naive `code_label = full_title` produces 75k+ unique 200-char chains;
  this destroys the extended alias map and must NOT happen (§5.2);
- `code_label` extraction MUST strip back to the leading legal-text label
  before any `" - "`, `", annexe"`, `" Partie "`, `" LIVRE "`, `" TITRE "`,
  `" CHAPITRE "`, `" Section "`, `" Sous-section "` boundary;
- `legi.category` may also point to `JORFTEXT…` parents (decrees / laws);
  these MUST be filtered out at catalog build time (§4.1, §17 rule 11).

---

## 2. Scope

### 2.1 In-scope for v1

Infer article-level references from:
- `jade`
- `bofip`

to:
- versioned `legi.doc_id` article records

Store:
- mention-level provenance
- aggregated doc-level edges
- graph edges in FalkorDB

### 2.2 Priority target families

Tax core families observed in the current corpus:

```python
CORE_CODE_FAMILIES = {
    "CGI": {
        "parent_text_ids": [
            "LEGITEXT000006069577",  # Code general des impots
            "LEGITEXT000006069568",  # Code general des impots, annexe I
            "LEGITEXT000006069569",  # Code general des impots, annexe II
            "LEGITEXT000006069574",  # Code general des impots, annexe III
            "LEGITEXT000006069576",  # Code general des impots, annexe IV
        ],
        "aliases": [
            "cgi",
            "code general des impots",
            "code des impots",
            "annexe i au cgi",
            "annexe ii au cgi",
            "annexe iii au cgi",
            "annexe iv au cgi",
        ],
    },
    "LPF": {
        "parent_text_ids": ["LEGITEXT000006069583"],
        "aliases": [
            "lpf",
            "livre des procedures fiscales",
        ],
    },
    "CIBS": {
        "parent_text_ids": ["LEGITEXT000044595989"],
        "aliases": [
            "cibs",
            "code des impositions sur les biens et services",
        ],
    },
}
```


### 2.3 Extended target scope

The current `legi` corpus also contains many non-core codes and texts because tax materials cite them:
- `Code du travail`
- `Code de la securite sociale`
- `Code monetaire et financier`
- `Code de l'urbanisme`
- `JORFTEXT...` parent texts for decrees and laws

Strategy:
- v1 should fully support tax core families above
- v1 may also resolve to other `LEGITEXT...` families if the source text explicitly names the code
- v1 should not attempt blind article-number-only matching across the entire extended corpus

### 2.4 Explicit non-goal for v1

Do not try to solve title-only references like:
- `article 1 du decret n...`
- `article 2 de la loi n...`

unless the same mention also contains enough text-title information to map deterministically to a `legi.category` family.

Those references are real, but they need a separate text-title resolver. They should be phase 2, not phase 1.

---

## 3. Architecture decision

### Decision

Implement cross references as a separate inference pipeline, not inline inside the existing ingestion loop.

### Why

Current ingestion already does:
- parsing
- chunking
- embedding
- PostgreSQL writes
- FalkorDB best-effort writes

Cross-reference inference adds:
- document aggregation
- regex extraction
- candidate catalog refresh
- temporal resolution
- optional semantic search
- edge aggregation
- graph updates

That is a separate concern and should be rerunnable independently.

### Consequences

Add a new command or job, for example:

```text
main.py infer_crossreferences (--source=jade|bofip|all) [--model=<model_name>] [--debug]
```

This job should support:
- backfill of all source docs
- incremental re-run for changed source docs only
- safe re-run without duplicate edge creation

---

## 4. New storage model

Use three new PostgreSQL objects:
- `legi_reference_catalog`
- `cross_reference_legi_mentions`
- `cross_reference_legi_edges`

### 4.1 `legi_reference_catalog`

Purpose:
- precompute the versioned target lookup catalog from `legi`
- avoid reparsing every `legi` row during every source run

Recommended schema:

```sql
CREATE TABLE IF NOT EXISTS legi_reference_catalog (
    legi_doc_id TEXT PRIMARY KEY,
    parent_text_id TEXT NOT NULL,
    article_number TEXT NOT NULL,
    normalized_number TEXT NOT NULL,
    normalized_number_loose TEXT NOT NULL,
    code_family TEXT,
    code_label TEXT,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    title TEXT,
    full_title TEXT,
    aliases TEXT[] NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_legi_ref_catalog_number
    ON legi_reference_catalog (normalized_number, start_date, end_date);

CREATE INDEX IF NOT EXISTS idx_legi_ref_catalog_number_loose
    ON legi_reference_catalog (normalized_number_loose, start_date, end_date);

CREATE INDEX IF NOT EXISTS idx_legi_ref_catalog_parent_number
    ON legi_reference_catalog (parent_text_id, normalized_number);

CREATE INDEX IF NOT EXISTS idx_legi_ref_catalog_family
    ON legi_reference_catalog (code_family);
```

Refresh source:

```sql
SELECT DISTINCT ON (doc_id)
    doc_id,
    category,
    number,
    title,
    full_title,
    start_date::date,
    end_date::date
FROM legi
WHERE category LIKE 'LEGITEXT%'
ORDER BY
    doc_id,
    (number IS NULL),
    (category IS NULL),
    (title IS NULL),
    (full_title IS NULL),
    start_date::date DESC,
    end_date::date DESC;
```

The `WHERE category LIKE 'LEGITEXT%'` filter is mandatory: only article-level
`LEGITEXT…` parents are valid resolution targets (see §1.6 and §13.4).
`JORFTEXT…`-parented rows are decree / law parent texts and must never become
inferred edge targets.

### 4.2 `cross_reference_legi_mentions`

Purpose:
- one row per extracted source mention
- keep provenance and explainability
- keep rejected mentions too when useful for audit

Recommended schema:

```sql
CREATE TABLE IF NOT EXISTS cross_reference_legi_mentions (
    mention_hash TEXT PRIMARY KEY,
    source_type TEXT NOT NULL CHECK (source_type IN ('jade', 'bofip')),
    source_doc_id TEXT NOT NULL,
    source_chunk_id TEXT NOT NULL,
    source_chunk_index INTEGER NOT NULL,
    source_date DATE NOT NULL,
    source_hash TEXT NOT NULL,
    source_title TEXT,
    source_secondary_id TEXT,
    relation_kind TEXT NOT NULL CHECK (relation_kind IN ('applies_to', 'interprets')),
    matched_text TEXT NOT NULL,
    match_start INTEGER,
    match_end INTEGER,
    normalized_number TEXT,
    normalized_number_loose TEXT,
    detected_code_alias TEXT,
    detected_code_family TEXT,
    detected_parent_text_ids TEXT[] NOT NULL DEFAULT '{}',
    target_legi_doc_id TEXT,
    target_parent_text_id TEXT,
    target_article_number TEXT,
    target_start_date DATE,
    target_end_date DATE,
    resolver_stage TEXT NOT NULL,
    resolver_method TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    is_accepted BOOLEAN NOT NULL,
    context_window TEXT NOT NULL,
    explain JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cross_legi_mentions_source
    ON cross_reference_legi_mentions (source_type, source_doc_id);

CREATE INDEX IF NOT EXISTS idx_cross_legi_mentions_target
    ON cross_reference_legi_mentions (target_legi_doc_id);

CREATE INDEX IF NOT EXISTS idx_cross_legi_mentions_hash
    ON cross_reference_legi_mentions (source_hash);
```

### 4.3 `cross_reference_legi_edges`

Purpose:
- aggregated accepted edges for RAG / graphRAG
- one row per `(source_type, source_doc_id, target_legi_doc_id)`

Recommended schema:

```sql
CREATE TABLE IF NOT EXISTS cross_reference_legi_edges (
    source_type TEXT NOT NULL CHECK (source_type IN ('jade', 'bofip')),
    source_doc_id TEXT NOT NULL,
    source_date DATE NOT NULL,
    source_hash TEXT NOT NULL,
    relation_kind TEXT NOT NULL CHECK (relation_kind IN ('applies_to', 'interprets')),
    target_legi_doc_id TEXT NOT NULL,
    target_parent_text_id TEXT NOT NULL,
    target_article_number TEXT NOT NULL,
    best_confidence DOUBLE PRECISION NOT NULL,
    occurrence_count INTEGER NOT NULL,
    resolver_methods TEXT[] NOT NULL DEFAULT '{}',
    source_chunk_ids TEXT[] NOT NULL DEFAULT '{}',
    normalized_numbers TEXT[] NOT NULL DEFAULT '{}',
    explain JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (source_type, source_doc_id, target_legi_doc_id)
);

CREATE INDEX IF NOT EXISTS idx_cross_legi_edges_target
    ON cross_reference_legi_edges (target_legi_doc_id);
```

---

## 5. Build the LEGI reference catalog first

This is mandatory. All later resolution steps depend on it.

### 5.1 Canonical target row

Each catalog row represents one versioned LEGI article:
- `legi_doc_id`
- `parent_text_id`
- `article_number`
- `start_date`
- `end_date`

### 5.2 `code_label`

`code_label` must be the **stable leading legal-text label** (e.g.
`Code de commerce`, `Code général des impôts`). It must NOT contain the
subtitle chain that LEGI ingestion concatenates into `full_title`.

Rules:
- if `title` starts with `Code ` or `Livre ` (case-insensitive), use `title`,
  trimmed of trailing punctuation;
- else, if `full_title` starts with `Code ` or `Livre `, extract the prefix up
  to the first of these boundary markers and trim:
  `" - "`, `", annexe"`, `" Partie "`, `" LIVRE "`, `" TITRE "`,
  `" CHAPITRE "`, `" Section "`, `" Sous-section "` (and their lowercase
  variants);
- else leave null. In particular, decrees / laws (`JORFTEXT…`-parented rows
  in `legi`, or rows whose title starts with `Loi `, `Décret `, `Ordonnance `)
  must produce `code_label = NULL`. Those rows are out of scope for v1 (§2.4)
  and the catalog refresh already excludes them via `category LIKE 'LEGITEXT%'`
  (§4.1).

Examples (from live data):
- `Code général des impôts` (regardless of which annexe parent the row uses)
- `Livre des procédures fiscales`
- `Code des impositions sur les biens et services`
- `Code de commerce`
- `Code du travail`
- `Code de l'urbanisme`

Reference implementation: `database.cross_reference_manage._extract_leading_code_label`.

### 5.3 `code_family`

`code_family` is a closed enum:

```
{ "CGI", "LPF", "CIBS", "OTHER_CODE", NULL }
```

Rules:
- if `category` is one of `CODE_FAMILY_MAP[fam].parent_text_ids` for
  `fam ∈ {CGI, LPF, CIBS}`, set `code_family = fam`;
- else, if `code_label` is non-null (i.e. the row is a `Code …` / `Livre …`
  outside the tax core), set `code_family = "OTHER_CODE"`;
- else, leave `code_family = NULL`.

Free-text values (e.g. `"Code civil"`, `"Code de commerce"`) MUST NOT be
written into this column. The resolver and alias detector only ever emit
values inside the closed enum, so any other value would be invisible to
ambiguity resolution and to family-prior fallbacks.

### 5.4 Alias generation

Use:
- `unaccent(lower(...))`
- punctuation-stripped variants
- short aliases from the manual family map above

Example aliases for one row in `LEGITEXT000006069577`:
- `cgi`
- `code general des impots`
- `code des impots`

Note:
- `unaccent` extension already exists in `database/sql_scripts/init.sql`
- use it for SQL-side matching if needed

---

## 6. Source document aggregation queries

Remember: source tables are chunk-level.

### 6.1 JADE document aggregation

`jade.text` is currently the full document text repeated on every chunk row.

So use:

```sql
SELECT
    doc_id,
    MIN(number) AS number,
    MIN(title) AS title,
    MIN(jurisdiction) AS jurisdiction,
    MIN(formation) AS formation,
    MIN(decision_date)::date AS source_date,
    MD5(string_agg(chunk_xxh64, '' ORDER BY chunk_index)) AS source_hash,
    MIN(text) AS full_text
FROM jade
GROUP BY doc_id;
```

When chunk-level provenance is needed, also fetch:

```sql
SELECT
    chunk_id,
    doc_id,
    chunk_index,
    chunk_text
FROM jade
WHERE doc_id = %(doc_id)s
ORDER BY chunk_index;
```

Use `chunk_text` for local mention context in JADE, not `text`, because `text` is duplicated per chunk row.

### 6.2 BOFIP document aggregation

`bofip.text` is the per-chunk raw content, and `bofip.chunk_text` is enriched with title / type / taxonomy / date.

Use:

```sql
SELECT
    doc_id,
    MIN(contenu_id) AS contenu_id,
    MIN(document_number) AS document_number,
    MIN(title) AS title,
    MIN(category_path) AS category_path,
    MIN(publication_date)::date AS source_date,
    MD5(string_agg(chunk_xxh64, '' ORDER BY chunk_index)) AS source_hash,
    string_agg(text, E'\n' ORDER BY chunk_index) AS full_text
FROM bofip
GROUP BY doc_id;
```

For chunk-level provenance:

```sql
SELECT
    chunk_id,
    doc_id,
    chunk_index,
    text,
    chunk_text
FROM bofip
WHERE doc_id = %(doc_id)s
ORDER BY chunk_index;
```

Use `text` for regex extraction and `chunk_text` only when semantic fallback needs the enriched embedding context.

---

## 7. Mention extraction strategy

### 7.1 Work at chunk level, aggregate at document level

Why:
- provenance needs `chunk_id`
- graphRAG edge should still be document-level
- multiple chunks can mention the same target

### 7.2 Source-specific relation kind

Set once per source type:

```python
RELATION_KIND = {
    "jade": "applies_to",
    "bofip": "interprets",
}
```

### 7.3 JADE segmentation

Current JADE storage does not preserve structured XML sections in dedicated fields.

v1 segmentation strategy:
- detect headings in `chunk_text` / `full_text` with regex
- high-signal markers:
  - `^VU\b`
  - `^Vu la procedure`
  - `^Consid[eé]rant`
  - `^Aux termes de l'article`
  - `^Sur le moyen` / `^Sur le fondement` / `^Sur le bien-fondé`
- score mentions higher when found near these markers
- recommended detection regex (for the +0.05 confidence boost):
  ```python
  _VU_PATTERN = re.compile(
      r'\b(?:Vu\s+la|Vu\s+le|Vu\s+.*?proc[eé]dure|VU|Consid[eé]rant|Aux\s+termes\s+de\s+l|Sur\s+le\s+(?:moyen|fondement|bien-fond[eé]))\b',
      re.IGNORECASE,
  )
  ```
  Note: bare `Sur` is too broad (fires on `Sur les dépens`, `Sur ce point`). Restrict to legal-reasoning patterns that signal intentional article citation.

Important:
- because current parser prefers `<ANA>` when present, some decisions will have no `VU` structure to segment
- implementation must gracefully fall back to plain chunk scanning

Recommended phase-1.5 improvement:
- extend JADE ingestion to preserve both:
  - `ana_text`
  - full body text from `BLOC_TEXTUEL`

That is optional for first delivery, but strongly recommended for recall.

### 7.4 BOFIP segmentation

BOFIP is simpler:
- scan each `bofip.text` chunk
- keep a local context window around every candidate mention
- use `title`, `subjects`, and `category_path` only as metadata priors, not as proof of a target law

### 7.5 Use a parser, not one regex only

Single-regex extraction will miss too many live article formats.

Observed live `legi.number` examples include:
- `42 septies`
- `150-0 A`
- `1012 ter A`
- `R*196-1`
- `L*142-2`
- `A421-46-1`
- `10 G-0 bis`
- `01 bis`

Extraction should be two-step:

1. Find an anchor:
   - `article`
   - `articles`
   - `art.`

2. Parse one or more following article tokens until punctuation or a code-name boundary.

### 7.6 Article token grammar

The parser must support at least:
- numeric article numbers
- numeric + hyphen chains
- letter prefixes such as `L`, `R`, `D`, `A` (plus `L.O.`)
- optional star after prefix, such as `R*`
- spaced suffix letters such as `A`, `B`, `AA`, `ZH`
- suffix letters followed by hyphen-digit chains such as `G-0` in `10 G-0 bis`
- French ordinal suffixes such as `bis`, `ter`, `quater`, `quinquies`, `sexies`, `septies`, `octies`, `nonies`, `decies`, `undecies`, `duodecies`, `terdecies`

Note: patterns like `10 G-0 bis` (alpha suffix followed by hyphen-digits) are
supported — the regex alpha-tail groups include optional `(?:-\d+(?:-\d+)*)?`
continuations to handle this numbering structure.

Minimal extraction regex for a single token:

```python
ARTICLE_TOKEN_RE = re.compile(
    r"""
    (?:
        (?:L\.O\.|[LRDA])(?:\*)?\s*[-.]?\s*\d+(?:-\d+)*
        (?:\s+(?-i:[A-Z]{1,3}))*
        (?:\s+(?:bis|ter|quater|quinquies|sexies|septies|octies|nonies|decies|undecies|duodecies|terdecies|quaterdecies|quindecies|sexdecies|septdecies|octodecies|novodecies|vicies)|(?:er|ère|ers))?
        (?:\s+(?-i:[A-Z]{1,3}))*
        |
        \d+(?:-\d+)*
        (?:\s+(?-i:[A-Z]{1,3}))*
        (?:\s+(?:bis|ter|quater|quinquies|sexies|septies|octies|nonies|decies|undecies|duodecies|terdecies|quaterdecies|quindecies|sexdecies|septdecies|octodecies|novodecies|vicies)|(?:er|ère|ers))?
        (?:\s+(?-i:[A-Z]{1,3}))*
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)
```

Critical: the alpha-tail groups MUST be locally case-sensitive
(`(?-i:[A-Z]{1,3})`). Otherwise, under `re.IGNORECASE`, lowercase French
words (`du`, `de`, `cod`, `liv`, `et`) get absorbed as fake suffixes and
contaminate the article token. Centralise the pattern in a single shared
module (e.g. `crossreference._patterns`) so the extractor and normalizer
agree on what counts as a valid article token.

Do not rely on this regex alone. Wrap it in parser logic that also handles:
- enumerations separated by `,`, `et`, `ou`
- patterns like `articles 38, 39 et 39 A`
- patterns like `article 150-0 A du CGI`

### 7.7 Extractor output contract

`extract_article_mentions(text)` MUST yield, per mention:

```
(matched_text, article_token, start, end, context_window)
```

Where:
- `matched_text` is the rich human-readable span. It may include a trailing
  code-name clause introduced by a French preposition
  (`du`, `des`, `de la`, `de l'`, `au`, `aux`, `de`). Stored on the mention
  row for provenance and fed to `extract_code_family_from_mention`.
- `article_token` is the **clean article span** (just the article number,
  letter prefix, star, suffix, ordinal). It is the canonical input for
  `normalize_article_number` and is the only field the resolver SHOULD use
  for catalog lookup.
- `context_window` is ~200 chars around the match for alias detection and
  semantic fallback.

The preposition regex used to extend `matched_text` MUST recognise the full
set above. In particular, `de l'annexe II` must be consumed as one
preposition unit so the stray `l` does not leak into the article portion
(this was the cause of the `"238 du l annexe II ..."` bug in v0).

---

## 8. Normalization

The old draft was wrong to remove hyphens blindly.

Example:
- `150-0 A` must not collapse directly to `1500A` as the primary key
- `R*196-1` must preserve the `*`

Use two normalized forms:

### 8.1 Primary normalized number

Purpose:
- exact deterministic lookup

Rules:
- strip leading anchors (`article`, `articles`, `art.`, `art`, `n°`)
- normalize Unicode spaces (NFKC)
- normalize repeated whitespace to one space
- **strip the trailing code-name clause**: drop a `\s+(?:du|des|de\s+la|de\s+l['’\s]|au|aux|de)\s+.+$` suffix WHEN the prefix before the
  preposition is a valid `ARTICLE_TOKEN_RE` match. This is what makes
  `normalize_article_number("1745 du code général des impôts")` collapse to
  `"1745"` so it can match the catalog key. Do not strip when the prefix is
  not a valid article token, or when the segment after the preposition is
  purely numeric (defensive guard).
- collapse `<letter> .? <space>+ <digit>` → `<letter><digit>`, e.g.
  `L. 247` → `L247`, `L 53` → `L53`. Preserve `R*196-1` (the star branch is
  excluded). This aligns mention keys with LEGI's stored convention
  (`L123-3`, never `L. 123-3`).
- collapse `L.O. <space>+ <digit>` → `L.O.<digit>`, e.g.
  `L.O. 234-5` → `L.O.234-5`. Same rationale: LEGI stores the compact form.
- remove spaces around hyphens
- remove spaces between prefix and star: `R* 196-1` → `R*196-1`
- uppercase
- remove leading zeros from a purely numeric leading head (`01 BIS` → `1 BIS`)
- keep hyphens
- keep `*`
- keep suffix words like `BIS`, `TER`

Examples:

```python
normalize_article_number("art. 150-0 A") == "150-0 A"
normalize_article_number("article R* 196-1") == "R*196-1"
normalize_article_number("article 1012 ter A") == "1012 TER A"
normalize_article_number("article 01 bis") == "1 BIS"
normalize_article_number("article L.O. 234-5") == "L.O.234-5"

# Trailing code-name clause must be stripped:
normalize_article_number("1745 du code général des impôts") == "1745"
normalize_article_number("L. 247 du livre des procédures fiscales") == "L247"
normalize_article_number("238 de l'annexe II au code général des impôts") == "238"
normalize_article_number("L. 1242-1 du code du travail") == "L1242-1"
normalize_article_number("article R*196-1 du LPF") == "R*196-1"
```

The rule "extractor provides `article_token`; normalizer cleans it; both keys
match the catalog" is non-negotiable. Storing a `normalized_number` that
contains the code-name tail (as v0 did) breaks Step A and Step B of the
resolver cascade and forces every mention into Step E (semantic), which is
not designed to carry deterministic precision.

### 8.2 Loose normalized number

Purpose:
- restricted fallback matching for formatting drift

Rules:
- start from primary normalized form
- remove spaces only
- keep hyphens and `*`

Examples:

```python
loose_key("150-0 A") == "150-0A"
loose_key("R*196-1") == "R*196-1"
loose_key("1012 TER A") == "1012TERA"
```

### 8.3 Do not use a hyphenless key in deterministic matching

A fully hyphenless key can be used only in very constrained fuzzy rescue logic and must carry a penalty.

Reason:
- `150-0 A` and `1500 A` are not safely equivalent

---

## 9. Code alias detection

Resolve article numbers inside a legal family first. This is the best precision lever.

### 9.1 Core alias detection

Normalize nearby context with:
- lowercase
- `unidecode` or `unaccent`
- punctuation stripping

Detect at least:
- `cgi`
- `code general des impots`
- `annexe i au cgi`
- `annexe ii au cgi`
- `annexe iii au cgi`
- `annexe iv au cgi`
- `lpf`
- `livre des procedures fiscales`
- `cibs`
- `code des impositions sur les biens et services`

### 9.2 Extended code detection

For non-core codes, derive aliases from `legi_reference_catalog.code_label`.

Example:
- if source text says `code de l'urbanisme`
- and that code label exists in the target catalog
- restrict candidates to that `parent_text_id`

### 9.3 Alias output

For each extracted mention, produce:

```python
{
    "detected_code_alias": "cgi",
    "detected_code_family": "CGI",
    "detected_parent_text_ids": [
        "LEGITEXT000006069577",
        "LEGITEXT000006069569",
        "LEGITEXT000006069574",
        "LEGITEXT000006069576",
    ],
}
```

If no explicit code alias is found:
- leave family null
- keep candidate family set empty
- later reject many ambiguous low-information cases

---

## 10. Resolution cascade

Use this exact order.

### 10.1 Step A: exact deterministic resolution

Query `legi_reference_catalog` with:
- `normalized_number`
- `source_date`
- one of three scope clauses, in order of preference:
  1. `detected_parent_text_ids` (when alias detection produced parent ids);
  2. `code_family = <detected_family>` (when a family was detected but no
     parent ids — rare, e.g. `OTHER_CODE` with the parent list unresolved);
  3. `code_family = ANY(ARRAY['CGI','LPF','CIBS'])` as a last-resort scope so
     deterministic matching is never run against the full corpus.

```sql
SELECT
    legi_doc_id,
    parent_text_id,
    article_number,
    code_family,
    start_date,
    end_date
FROM legi_reference_catalog
WHERE normalized_number = %(normalized_number)s
  AND start_date <= %(source_date)s
  AND end_date >= %(source_date)s
  AND (
      -- one of the three scope clauses above; never an unbounded match
      parent_text_id = ANY(%(detected_parent_text_ids)s)
      -- or: code_family = %(detected_family)s
      -- or: code_family = ANY(ARRAY['CGI','LPF','CIBS'])
  );
```

Accept immediately when:
- exactly one row remains.

If multiple rows survive, fall through to the §10.6 ambiguity resolver
before declaring the step a failure.

### 10.2 Step B: exact deterministic resolution on loose key

Only run if step A fails. Same scope clauses as §10.1.

```sql
SELECT
    legi_doc_id,
    parent_text_id,
    article_number,
    code_family,
    start_date,
    end_date
FROM legi_reference_catalog
WHERE normalized_number_loose = %(normalized_number_loose)s
  AND start_date <= %(source_date)s
  AND end_date >= %(source_date)s
  AND (
      parent_text_id = ANY(%(detected_parent_text_ids)s)
      -- or: code_family = %(detected_family)s
      -- or: code_family = ANY(ARRAY['CGI','LPF','CIBS'])
  );
```

Accept only when:
- exactly one row remains (otherwise fall through to §10.6).

### 10.3 Step C: family-prior deterministic resolution without explicit code

Only run when no explicit code alias exists.

Restrict to tax core families first:
- `CGI`
- `LPF`
- `CIBS`

Accept only when:
- one temporally valid candidate remains
- and the article number is not "too generic"

Reject examples:
- `article 1`
- `article 2`
- `article 3`

Suggested generic-number rule:

```python
def is_generic_numeric_ref(normalized_number: str) -> bool:
    return bool(re.fullmatch(r"\d{1,2}(?:\s+(?:BIS|TER))?", normalized_number))
```

If `is_generic_numeric_ref(...)` is true and no explicit code alias was found:
- reject unless there is exactly one candidate in the chosen family scope and confidence remains capped

### 10.4 Step D: restricted fuzzy rescue

Use `rapidfuzz` only after deterministic steps fail.

Rules:
- fuzzy search only inside the already scoped candidate set
- if no family scope exists, fuzzy search only inside tax core, not full corpus
- compare both `normalized_number` and `normalized_number_loose`
- minimum score: `96`
- reject if top-2 scores are too close, for example delta `< 2`
- reject if fuzzy match changes the numeric sequence (different digit groups)
  OR changes the alpha prefix (e.g. `L` vs `R`). Either change alone is
  sufficient to reject — the word "both" in earlier drafts was misleading

Good use cases:
- OCR spacing drift
- `R* 196-1` vs `R*196-1`
- `1012 terA` vs `1012 ter A`

Bad use cases:
- `150-0 A` vs `1500 A`
- `1` vs `11`

### 10.5 Step E: semantic fallback

Semantic fallback is last resort, never first resort.

Use it only when:
- deterministic and fuzzy both fail
- or multiple temporally valid candidates remain inside a family scope

#### Critical correction

Current embedding indexes are cosine HNSW indexes.

So semantic SQL must use:
- `<=>` cosine distance

Not:
- `<->`

#### Query shape

Use the live embedding column naming convention:

```python
embedding_column = f'"embeddings_{format_model_name(model)}"'
```

`format_model_name()` already exists in `utils.data_helpers`.

Recommended candidate search:

```sql
WITH ranked AS (
    SELECT
        l.doc_id,
        l.category AS parent_text_id,
        l.number AS article_number,
        l.start_date::date AS start_date,
        l.end_date::date AS end_date,
        l.chunk_id,
        l.chunk_index,
        1 - (l."embeddings_bge-m3" <=> %(query_embedding)s::vector) AS cosine_similarity,
        ROW_NUMBER() OVER (
            PARTITION BY l.doc_id
            ORDER BY l."embeddings_bge-m3" <=> %(query_embedding)s::vector
        ) AS rn
    FROM legi l
    WHERE l.start_date::date <= %(source_date)s
      AND l.end_date::date >= %(source_date)s
      AND (
          COALESCE(%(detected_parent_text_ids)s, ARRAY[]::text[]) = ARRAY[]::text[]
          OR l.category = ANY(%(detected_parent_text_ids)s)
      )
)
SELECT
    doc_id,
    parent_text_id,
    article_number,
    start_date,
    end_date,
    cosine_similarity
FROM ranked
WHERE rn = 1
ORDER BY cosine_similarity DESC
LIMIT 10;
```

Notes:
- the exact quoted embedding column depends on the active model
- example above uses the default current model family name
- implementation must build this SQL dynamically

#### Semantic query text

Embed:
- the local context window around the unresolved mention

Not:
- the whole JADE or BOFIP document

Why:
- whole-document embeddings dilute the legal citation signal

### 10.6 Step F: ambiguity resolver

Step F is reachable from Step A, Step B, and Step C whenever the SQL returns
more than one row. The implemented rules are:

1. if `detected_family` is set, narrow the candidate list to rows whose
   `code_family` equals it; if exactly one remains, accept;
2. if `detected_family` is not set, pick the best family tier present, in
   order `CGI` > `LPF` > `CIBS` > `OTHER_CODE` > `NULL`. Accept only if
   exactly one row sits at the chosen tier;
3. otherwise reject.

The "exact over loose" and "highest semantic score" preferences listed in
earlier drafts are enforced **upstream**, not inside the tie-break: Step A
runs before Step B (so an exact hit wins over a loose hit by construction);
the semantic resolver applies its own threshold and `LIMIT 1` on best
cosine similarity before returning. The tie-break only sees the rows the
upstream layers were unable to disambiguate by family scope.

Implementation: :func:`crossreference.resolver._resolve_ambiguity`.

---

## 11. Confidence scoring

Confidence is not just a numeric similarity score. It must encode why the link is trustworthy.

Reference implementation: :func:`crossreference.confidence.score_confidence`.
Calibrated values currently in use:

```python
confidence = 0.0

if resolver_method == "exact_number_and_explicit_code":
    confidence = 0.99
elif resolver_method == "exact_number_and_temporal_unique":
    confidence = 0.96
elif resolver_method == "exact_loose_and_explicit_code":
    confidence = 0.92
elif resolver_method == "exact_loose":
    confidence = 0.85
elif resolver_method == "exact_number_core_family_only":
    confidence = 0.84
elif resolver_method == "fuzzy_scoped":
    confidence = 0.78
elif resolver_method == "semantic_scoped":
    confidence = 0.65
```

Adjustments:

```python
if source_type == "jade" and mention_in_vu_like_section:
    confidence += 0.05

if repeated_in_multiple_chunks:
    confidence += 0.05

# Tiered semantic boost on top of the semantic_scoped base when the
# resolver_method is "semantic_scoped":
if resolver_method == "semantic_scoped" and semantic_similarity is not None:
    if semantic_similarity >= 0.92: confidence += 0.35
    elif semantic_similarity >= 0.88: confidence += 0.30
    elif semantic_similarity >= 0.84: confidence += 0.25
    elif semantic_similarity >= 0.80: confidence += 0.20
    elif semantic_similarity >= 0.75: confidence += 0.15

if no_explicit_code_alias:
    confidence -= 0.05

if generic_numeric_ref:
    confidence -= 0.10

confidence = max(0.0, min(1.0, confidence))
```

Acceptance rule (in :func:`crossreference.pipeline._process_source_document`):

```python
is_accepted = confidence >= 0.55
```

The threshold is deliberately lower than 0.70 to admit fuzzy-scoped (base
0.78) and semantic-scoped (base 0.65 + tiered boost) hits when they survive
the resolver's precision-first guards. The base scores and adjustment
weights compensate, and the no-alias / generic-number penalties stack to
push genuinely low-signal mentions below the threshold.

Stronger semantic rule (enforced inside the semantic resolver itself, not
in the confidence layer):

- if a code alias was detected, require cosine similarity ≥ 0.75
- otherwise require cosine similarity ≥ 0.85

---

## 12. Mention deduplication and edge aggregation

### 12.1 Mention identity

`mention_hash` identifies a **source mention span**, not the resolution
outcome. It MUST NOT include `target_legi_doc_id`: re-running the resolver
(after a normalizer or alias-detector change) must update the existing
mention row in place rather than creating a new one. Including the target
in the hash would defeat that and accumulate duplicate mentions.

Reference implementation:
:func:`crossreference.pipeline._compute_mention_hash`.

```python
mention_hash = sha1(
    "|".join([
        source_type,
        source_doc_id,
        source_chunk_id,
        str(match_start),
        str(match_end),
        matched_text,
    ]).encode("utf-8")
).hexdigest()
```

Note: with this hash and the JADE chunk-text fix (§6.1), the same mention
can only appear once per `(source_doc_id, source_chunk_id, span)`. The
per-doc rebuild path always deletes mentions before reinsertion, so
collisions are exercised only by the graph-retry branch (§14).

### 12.2 Aggregate accepted mentions into edges

Group by:

```python
(source_type, source_doc_id, target_legi_doc_id)
```

Aggregate:
- `best_confidence = max(confidence)`
- `occurrence_count = count(*)`
- `resolver_methods = sorted(distinct methods)`
- `source_chunk_ids = sorted(distinct source_chunk_id)`
- `normalized_numbers = sorted(distinct normalized_number)`

### 12.3 Rebuild edges per source document

When a source document changes:
- delete existing mention rows for that `source_type + source_doc_id`
- delete existing edge rows for that `source_type + source_doc_id`
- recompute from scratch

This is simpler and safer than diffing old and new mentions.

---

## 13. Graph injection strategy

Graph edges must be written only from `cross_reference_legi_edges`, not from raw mentions.

### 13.1 Edge labels

Use:
- `(:JudicialDecision)-[:APPLIES_TO]->(:LegalText)`
- `(:TaxGuidance)-[:INTERPRETS]->(:LegalText)`

Reason:
- the semantic distinction is valuable for graphRAG
- it is clearer than a generic `REFERENCES` edge

### 13.2 Correct Cypher

Use `doc_id`, not `id`.

For JADE:

```cypher
MATCH (s:JudicialDecision {doc_id: $source_doc_id})
MATCH (t:LegalText {doc_id: $target_legi_doc_id})
MERGE (s)-[r:APPLIES_TO]->(t)
SET r.confidence = $best_confidence,
    r.occurrence_count = $occurrence_count,
    r.resolver_methods = $resolver_methods,
    r.normalized_numbers = $normalized_numbers,
    r.updated_at = $updated_at
```

For BOFIP:

```cypher
MATCH (s:TaxGuidance {doc_id: $source_doc_id})
MATCH (t:LegalText {doc_id: $target_legi_doc_id})
MERGE (s)-[r:INTERPRETS]->(t)
SET r.confidence = $best_confidence,
    r.occurrence_count = $occurrence_count,
    r.resolver_methods = $resolver_methods,
    r.normalized_numbers = $normalized_numbers,
    r.updated_at = $updated_at
```

### 13.3 Graph prerequisites

Before first graph backfill:
- call `init_graph_schema()`

Also:
- ensure all LEGI, JADE, and BOFIP nodes already exist in graph before injecting inferred edges

### 13.4 No placeholder targets

Do not `MERGE` targets by:
- `LEGITEXT...`
- `JORFTEXT...`

Only `legi.doc_id` values are valid target node ids for inferred edges.

---

## 14. Incremental processing

Cross-reference inference should be document-incremental.

### 14.1 Source hash

Use these document hashes:

JADE:

```sql
MD5(string_agg(chunk_xxh64, '' ORDER BY chunk_index))
```

BOFIP:

```sql
MD5(string_agg(chunk_xxh64, '' ORDER BY chunk_index))
```

Store the hash in both:
- `cross_reference_legi_mentions.source_hash`
- `cross_reference_legi_edges.source_hash`

### 14.2 Source state tracking table

The incremental-skip decision is tracked in `cross_reference_source_state`:

```sql
CREATE TABLE IF NOT EXISTS cross_reference_source_state (
    source_type TEXT NOT NULL CHECK (source_type IN ('jade', 'bofip')),
    source_doc_id TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    catalog_hash TEXT NOT NULL DEFAULT '',
    pipeline_version TEXT NOT NULL DEFAULT '',
    graph_sync_ok BOOLEAN NOT NULL DEFAULT false,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (source_type, source_doc_id)
);
```

- `source_hash`: content fingerprint of the source document (§14.1)
- `catalog_hash`: SHA-1 of the catalog snapshot used for resolution
- `pipeline_version`: value of `crossreference._version.PIPELINE_VERSION` at run time
- `graph_sync_ok`: whether FalkorDB edge injection succeeded

### 14.3 Re-run condition

For each source document:
- if no state row exists -> process
- else if **any** of `source_hash`, `catalog_hash`, `pipeline_version` differs
  from stored values -> rebuild mentions and edges from scratch
- else if `graph_sync_ok` is false -> retry graph injection only (PostgreSQL
  already holds correct mentions and edges, since the triple hash matched)
- else skip

Implementation note: when all three hashes match but `graph_sync_ok` is false,
the pipeline retries graph injection only — it trusts that PostgreSQL already
holds correct mentions and edges since no inference input has changed.

### 14.4 Catalog refresh condition

Before every inference job:
- refresh `legi_reference_catalog`

Reason:
- LEGI versions evolve
- temporal resolution depends on up-to-date validity windows

---

## 15. Evaluation and QA

### 15.1 Silver set from existing LEGI citations

Use current `legi.links` as silver supervision for article-number normalization and resolution logic.

Why this helps:
- many `CITATION` links already point from one LEGI article to another LEGI article
- the same normalization and temporal rules apply

Suggested silver sample query:

```sql
SELECT
    l.doc_id AS source_legi_doc_id,
    elem->>'doc_id' AS target_legi_doc_id,
    elem->>'text_doc_id' AS target_parent_text_id,
    elem->>'number' AS target_article_number,
    elem->>'title' AS raw_title
FROM legi l,
LATERAL jsonb_array_elements(l.links) elem
WHERE elem->>'link_type' = 'CITATION'
  AND elem->>'category' = 'CODE'
  AND elem->>'doc_id' LIKE 'LEGIARTI%';
```

Use this to validate:
- article token extraction
- normalization
- temporal resolution
- ambiguity rejection

### 15.2 Gold set for final quality

Create a manually reviewed gold set:
- 200 JADE documents
- 200 BOFIP documents

Label:
- every accepted source -> target LEGI link
- every false-positive trap

Track:
- precision
- recall
- source-type split
- family split (`CGI`, `LPF`, `CIBS`, `OTHER_CODE`)
- method split (`exact`, `fuzzy`, `semantic`)

### 15.3 Failure buckets to monitor

Track separately:
- missing explicit code alias
- wrong family chosen
- right family but wrong temporal version
- generic numeric article false positive
- semantic fallback false positive
- JADE parser lost structure because only `ANA` was stored

---

## 16. Recommended implementation order

### Phase 0

Create storage and refresh helpers:
- create new tables
- create `legi_reference_catalog` refresh function
- wire `init_graph_schema()` into startup or graph backfill

### Phase 1

Implement deterministic core resolver:
- BOFIP -> LEGI
- JADE -> LEGI
- exact number
- explicit code alias
- temporal filtering

This should already deliver most of the high-precision value.

### Phase 2

Add:
- loose-key deterministic fallback
- restricted fuzzy fallback
- edge aggregation
- FalkorDB injection

### Phase 3

Add semantic fallback:
- context-window embedding
- scoped cosine search over `legi`
- strict acceptance thresholds

### Phase 4

Improve JADE recall:
- preserve full body text in ingestion in addition to `ANA`
- better section-aware scoring on `VU`, reasoning, dispositif

### Phase 5

Optional extended resolver:
- decree / law title matching for `JORFTEXT...` parent texts
- references like `article 1 du decret n...`

---

## 17. Non-negotiable rules

1. Always resolve to canonical `legi.doc_id` article ids.
2. Always apply temporal filtering with `source_date BETWEEN start_date AND end_date`.
3. Never trust article number alone across the full corpus.
4. Use family scoping before fuzzy or semantic rescue.
5. Use `<=>` cosine distance for pgvector semantic search.
6. Keep mention-level provenance, not just final edges.
7. Rebuild per source document when its hash changes.
8. Treat JADE `ANA` preference as a known recall limitation, not as ground truth structure.
9. `code_family` is a closed enum: `{CGI, LPF, CIBS, OTHER_CODE, NULL}`.
   No free-text values, ever. The resolver and alias detector emit values
   strictly inside this set; the catalog must do the same.
10. `code_label` is the **leading legal-text label only** (e.g.
    `Code de commerce`, `Code général des impôts`). Storing the full
    subtitle chain pollutes the extended alias map and breaks OTHER_CODE
    detection.
11. The catalog refresh MUST filter `category LIKE 'LEGITEXT%'` so that
    `JORFTEXT…` parents are never resolution targets.
12. Mention `normalized_number` is computed from the extractor's clean
    `article_token`, never from the rich `matched_text`. The catalog and
    mention keys must converge on the bare article number.
13. Skip-on-incremental requires **all three** of `source_hash`,
    `catalog_hash`, and `pipeline_version` to match. Bumping
    `crossreference._version.PIPELINE_VERSION` forces every source doc to
    be reprocessed on the next run, even when source content and catalog
    content are unchanged. This is mandatory whenever inference logic
    changes (extractor, normalizer, resolver cascade, alias detector,
    confidence scorer, pipeline orchestrator).

---

## 18. Final recommendation

Best first implementation:
- separate `infer_crossreferences` pipeline
- refreshed `legi_reference_catalog`
- deterministic exact resolver with family alias detection and temporal filtering
- mention table plus aggregated edge table
- FalkorDB edges from aggregated table only

This is enough to produce high-precision JADE/BOFIP -> LEGI links for RAG and graphRAG, without pretending the harder decree-title and summary-only JADE cases are already solved.
