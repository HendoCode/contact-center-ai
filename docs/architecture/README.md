# Architecture diagrams

Source: `workspace.dsl` (Structurizr DSL), one C4 model of the whole repo.
Layout: `workspace.json` holds the fixed positions of the Containers and deployment views; the
other views lay themselves out. Renderer: the `structurizr` Compose service (Structurizr Local).
[docs/MAP.md](../MAP.md) shows the exported views with a capability table.

## Render locally

```bash
docker compose up structurizr
# Open http://localhost:8080
```

Structurizr Local reads `workspace.dsl`, merges the layout from `workspace.json`, and rewrites
`workspace.json` on start and whenever you save a layout in the browser; commit that file only
when you meant to change the layout. A container added to the model lands in the top-left corner
of the Containers view until you place it. The container runs as uid 65532: on a Linux
host, make this directory writable for it once (`chmod o+w docs/architecture`) or it exits with
"Data directory /usr/local/structurizr is not writable". It also creates `.structurizr/` here,
which is gitignored.

## Export

Open a view, then use the diagram page's export buttons for PNG or SVG (the `structurizr export`
CLI in the same image exports DSL formats such as PlantUML and Mermaid, not PNG or SVG). The
committed images were exported this way with diagram metadata on, then the PNGs were scaled to
3840 px wide; each one stays legible at 1920 px. After a model change, re-export every view
whose content changed and keep the file names below.

## Views

| Key | Type | Image | What it shows |
|---|---|---|---|
| `Landscape` | System landscape | [landscape](landscape.png) ([svg](landscape.svg)) | The three kinds of user, the sandbox, and the systems it calls: model APIs, LangSmith, Snowflake, Databricks, Azure, GitHub, and the stubbed S3 source |
| `Containers` | Container | [containers](containers.png) ([svg](containers.svg)) | Every container, store and Compose service, grouped by capability, without connecting lines; the demo-path pieces stand out |
| `DemoPathSequence` | Dynamic | [demo-path](demo-path.png) ([svg](demo-path.svg)) | `make dev-data` then `make demo-change`, as 13 numbered calls between the pieces it uses |
| `DeploymentLocal` | Deployment | [deployment-local](deployment-local.png) ([svg](deployment-local.svg)) | Host processes under uv next to the Compose default, `app` and `gpu` profiles |
| `DeploymentAzure` | Deployment | [deployment-azure](deployment-azure.png) ([svg](deployment-azure.svg)) | The `infra/azure` roots: Container Apps behind Easy Auth, PostgreSQL, ADLS, the optional GPU VM and Databricks, Key Vault, and the Snowflake account |

## Conventions

Every element carries one status tag, styled the same in every view:

| Tag | Meaning | Style |
|---|---|---|
| `Demo path` | Exercised by `make demo-change` (relationships too) | dark fill, orange border, orange lines |
| `Built` | Built and documented, off the demo path | light fill |
| `Partial` | Some built, the rest designed or declared only (vLLM, the fine-tune dataset, the GPU VM, Databricks workspace) | dashed border |
| `Planned` | Not built yet: the `agent-api` Container App slot | dotted border |
| `Stub` | The code path raises `NotImplementedError` (S3) | dotted border, grey text |

Deployment views inherit each container's tag, so the Azure view marks PostgreSQL as a demo-path
container even though the demo itself runs locally. pgvector and LanceDB are modeled as peers
behind one Retriever interface; the diagrams make no performance claim about either.

## Architectural decisions the diagrams surface

**Numbers come from the metric layer, words from the calls.** In `DemoPathSequence`,
`explain_change` reads the metric for both windows through MetricFlow and counts calls through
the Retriever; the model only labels drivers and supplies quotes that code checks verbatim.

**CSAT is SQL, not vectors.** `query_csat` reads the `f_csat` fact in Postgres; CSAT is not
embedded, so it cannot be combined with semantic search.

**Auth is infrastructure-only.** On Azure, Entra Easy Auth sits in front of the Container App;
the Python code has no auth logic. Locally, the HTTP transport binds to loopback unless a bearer
token is set.

**Provider coupling is isolated.** Chat models go through `get_llm()` in `rag/pipeline.py` and
embeddings through `get_embeddings()` in `rag/embeddings.py`; the demo runs on Ollama with no
keys.

**S3 is a stub.** `rag.pipeline --source s3` raises `NotImplementedError`; ingest reads the
synthetic files and upserts by `call_id`, so re-running it changes nothing.
