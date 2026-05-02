# Product: MEDIATECH

MEDIATECH is an open-source ETL pipeline built by [Etalab](https://www.etalab.gouv.fr/) (DINUM) that processes public French government data and makes it available as vectorized, AI-ready datasets.

## Purpose

The pipeline downloads raw data from French public administration sources, chunks and embeds the text using local HuggingFace models, stores the vectors in a PostgreSQL/pgvector database, and exports the results as Parquet files uploaded to the [AgentPublic Hugging Face organization](https://huggingface.co/AgentPublic).

The output is intended for use in RAG (Retrieval-Augmented Generation) pipelines within the French public sector.

## Data Sources

Each source has its own processing pipeline and database table:

| Source key | Description |
|---|---|
| `legi` | French legislation (DILA open data) |
| `jade` | Judicial decisions (Conseil d'État) |
| `bofip` | Tax guidance (Direction générale des Finances publiques) |
| `cnil` | CNIL deliberations and decisions |
| `constit` | Constitutional Council decisions |
| `dole` | Collective agreements (travail) |
| `travail_emploi` | Labour and employment information sheets |
| `service_public` | Public service information sheets (pro + particuliers) |
| `state_administrations_directory` | State administration directory |
| `local_administrations_directory` | Local administration directory |
| `data_gouv_datasets_catalog` | data.gouv.fr dataset catalog |

## Outputs

- PostgreSQL (pgvector) tables with dense vector embeddings (HNSW index, cosine similarity)
- Qdrant vector store (optional, parallel to PostgreSQL)
- FalkorDB knowledge graph (optional, for LEGI/JADE/BOFiP relational data)
- Parquet files exported to Hugging Face Hub
