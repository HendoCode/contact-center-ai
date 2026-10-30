workspace "Contact Center AI" "A working sandbox exploring what AI can do for a credit union contact center, on synthetic data." {

    !identifiers flat

    # Every element carries one status tag:
    #   "Demo path"  exercised by `make demo-change` (also styled on relationships)
    #   "Built"      built and documented, outside the demo path
    #   "Partial"    some of it built, the rest designed or declared only
    #   "Planned"    not in the repo yet: modeled by its agreed interface
    #   "Stub"       the code path exists and raises NotImplementedError
    # docs/MAP.md has the same split as a table.

    model {

        # ── People ──────────────────────────────────────────────────────────────

        supervisor = person "Contact center supervisor" "Asks why a number moved, what members said, and what to coach on." {
            tags "Demo path"
        }
        analyst = person "Data and analytics team" "Owns the governed metrics and the warehouses they run on." {
            tags "Built"
        }
        engineer = person "Platform engineer" "Runs the stack, the evals and the cloud deployment." {
            tags "Built"
        }

        # ── External systems ────────────────────────────────────────────────────

        mcpClients = softwareSystem "MCP clients" "Claude Desktop, Cursor and other MCP-compatible assistants." {
            tags "Built" "External"
        }
        hostedModels = softwareSystem "Hosted model APIs" "OpenAI-compatible (OpenRouter by default), Anthropic, Fireworks. Optional: the demo runs keyless." {
            tags "Built" "External"
        }
        langsmith = softwareSystem "LangSmith" "Traces and live eval experiments, on by env only." {
            tags "Built" "External"
        }
        snowflake = softwareSystem "Snowflake" "dbt target and raw-table loader destination." {
            tags "Built" "External"
        }
        databricks = softwareSystem "Databricks" "dbt target and raw-table loader destination." {
            tags "Built" "External"
        }
        azure = softwareSystem "Microsoft Azure" "Terraform roots in infra/azure: Container Apps behind Entra Easy Auth, PostgreSQL, ADLS." {
            tags "Built" "External"
        }
        github = softwareSystem "GitHub" "CI gates (lint, tests, offline evals, Terraform validate) and the blog on GitHub Pages." {
            tags "Built" "External"
        }
        s3 = softwareSystem "AWS S3" "Source for real call data. rag.pipeline --source s3 raises NotImplementedError." {
            tags "External" "Stub"
        }

        # ── The sandbox ─────────────────────────────────────────────────────────

        sandbox = softwareSystem "Contact Center AI sandbox" "Calls, CSAT and a member ledger behind governed metrics, cited search and an agent, on synthetic data." {
            tags "Demo path"

            group "Data" {
                generator = container "Synthetic data generator" "1,540 calls, CSAT, an OLTP ledger, four dated events and their answer key. make seed" "Python, seed 42" {
                    tags "Demo path"
                }
                postgres = container "PostgreSQL + pgvector" "OLTP tables, dbt marts, call vectors and agent checkpoints. Compose db locally." "PostgreSQL 16" {
                    tags "Database" "Demo path"
                }
                lancedb = container "LanceDB" "Peer vector store, embedded: vector, full-text and hybrid search. RETRIEVER_BACKEND=lancedb" "Lance files" {
                    tags "Database" "Built"
                }
            }

            group "Semantic layer" {
                metricLayer = container "dbt + MetricFlow" "Star schema and 47 governed metrics; mf compiles the SQL per target." "dbt, MetricFlow" {
                    tags "Demo path"
                }
                loaders = container "Warehouse loaders + parity" "Load OLTP rows to Snowflake or Databricks; compare five metrics on three targets. make parity" "Python" {
                    tags "Built"
                }
            }

            group "Access" {
                retrieval = container "Retrieval and RAG" "Embeds and upserts calls by call_id; one Retriever interface over pgvector or LanceDB." "rag/, retrieval/" {
                    tags "Demo path"
                }
                mcpServer = container "MCP server" "Six tools: search_transcripts, get_call_summary, query_csat, query_metric, ask_the_analyst, explain_change." "ccai_mcp, stdio" {
                    tags "Demo path"

                    router = component "Tool router" "Receives each MCP tool call, checks its arguments against the tool's schema and hands it to the tool that owns it." "ccai_mcp/server.py" {
                        tags "Demo path"
                    }
                    searchTools = component "Search and call tools" "search_transcripts, get_call_summary, query_csat: cited search over the calls, one call's summary, CSAT counts from SQL." "ccai_mcp/tools.py" {
                        tags "Built"
                    }
                    metricTools = component "Metric tools" "query_metric and ask_the_analyst: one governed metric, or a question matched to metrics. Runs MetricFlow's mf and returns the value with its SQL." "ccai_mcp/metrics.py" {
                        tags "Demo path"
                    }
                    changeAnalysis = component "Change analysis" "explain_change: finds the change point, reads the metric before and after, reads the calls, has the model propose and assign reasons, counts them in code, keeps only verbatim quotes." "ccai_mcp/change.py" {
                        tags "Demo path"
                    }
                    masking = component "Account masking" "Masks account numbers in the quotes and answer text it is given, keeping the last four digits." "ccai_mcp/masking.py" {
                        tags "Demo path"
                    }
                }
                mcpHttp = container "MCP HTTP endpoint" "The same six tools over streamable HTTP; bearer token locally (Compose mcp-server :8000), Easy Auth on Azure." "ccai_mcp --http" {
                    tags "Built"
                }
            }

            group "Agent" {
                agent = container "Agent graph" "classify, then retrieve, summarize, metric or change route; ground; answer. One clarify interrupt." "LangGraph" {
                    tags "Demo path"
                }
                demoChange = container "Change-analysis demo" "make demo-change: preflight, six timed questions, a replayable record." "agent/demo_change.py" {
                    tags "Demo path"
                }
                langgraphApi = container "LangGraph dev server" "make demo: five scripted questions; Studio and HTTP API. Compose langgraph-api :2024" "langgraph dev" {
                    tags "Built"
                }
            }

            group "Models" {
                ollama = container "Ollama" "Local chat (llama3.2) and embeddings (nomic-embed-text); no keys. Compose ollama" "Ollama" {
                    tags "Demo path"
                }
                vllm = container "vLLM" "Self-hosted OpenAI-compatible server. Compose gpu profile, declared only." "vLLM" {
                    tags "Partial"
                }
                finetune = container "Fine-tune dataset" "Gold labels and splits built; training and serving designed, not built." "models/finetune" {
                    tags "Partial"
                }
            }

            group "Quality" {
                evals = container "Agent evals" "Golden set and change sets: offline CI gate (make evals), live LangSmith runs (make evals-live)." "evals/" {
                    tags "Built"
                }
                bench = container "Retrieval benchmark" "Both vector stores on 40 labeled queries; results in results/retrieval. make bench" "retrieval/bench.py" {
                    tags "Built"
                }
            }

            group "Explore and explain" {
                jupyter = container "Jupyter notebooks" "The change analysis as a walkthrough: notebooks/change-analysis.ipynb. make notebooks, Compose jupyter 127.0.0.1:8888" "JupyterLab" {
                    tags "Built"
                }
                openWebui = container "Open WebUI" "Chat UI on the local models; can register the MCP HTTP endpoint. Compose :9090" "Open WebUI" {
                    tags "Built"
                }
                structurizr = container "Architecture model" "This C4 model, served by Structurizr. Compose structurizr :8080" "Structurizr DSL" {
                    tags "Built"
                }
                blog = container "Anchoring AI blog" "Posts built from Markdown with pandoc; four published." "blogs/" {
                    tags "Built"
                }
            }
        }

        # ── Landscape relationships (stated first, so container links don't add implied ones) ─

        supervisor -> sandbox "Asks questions through the agent or an MCP client" {
            tags "Demo path"
        }
        supervisor -> mcpClients "Asks questions in"
        mcpClients -> sandbox "Call the six tools" "MCP stdio or HTTP"
        analyst -> sandbox "Defines metrics, explores in notebooks"
        engineer -> sandbox "Runs the stack, evals and benchmarks"
        engineer -> azure "Plans and applies Terraform"
        sandbox -> hostedModels "Chat and embeddings, when chosen instead of Ollama" "HTTPS"
        sandbox -> langsmith "Sends traces and eval experiments" "HTTPS"
        sandbox -> snowflake "Loads raw tables, builds marts, runs parity" "dbt, COPY INTO"
        sandbox -> databricks "Loads raw tables, builds marts, runs parity" "dbt, COPY INTO"
        sandbox -> azure "Deploys the MCP server, Postgres and Lance storage to" "Terraform"
        sandbox -> github "Runs CI and publishes the blog on" "GitHub Actions"
        sandbox -> s3 "Reads real call data from (stubbed)" "HTTPS"

        # ── Container relationships ──────────────────────────────────────────────

        # The change-analysis demo path (`make up dev-data`, then `make demo-change`)
        supervisor -> demoChange "Runs make demo-change" {
            tags "Demo path"
        }
        generator -> postgres "Loads the OLTP ledger, CSAT and change log" "olap/seed.py" {
            tags "Demo path"
        }
        retrieval -> postgres "Upserts and searches call vectors" "pgvector" {
            tags "Demo path"
        }
        retrieval -> ollama "Embeds calls and questions" "nomic-embed-text" {
            tags "Demo path"
        }
        metricLayer -> postgres "Builds marts; runs metric SQL" "dbt build, mf query" {
            tags "Demo path"
        }
        demoChange -> postgres "Preflight: ledger, marts, vector index" "SQL" {
            tags "Demo path"
        }
        demoChange -> agent "Asks steps 1 to 4 on checkpointed threads" "in process" {
            tags "Demo path"
        }
        demoChange -> retrieval "Opens one cited call, accounts masked" "get_by_id" {
            tags "Demo path"
        }
        demoChange -> mcpServer "Asks step 6 as an MCP client would" "explain_change" {
            tags "Demo path"
        }
        agent -> ollama "Classifies and frames the question" "chat" {
            tags "Demo path"
        }
        agent -> mcpServer "Calls explain_change and query_metric" "MCP stdio" {
            tags "Demo path"
        }
        agent -> retrieval "Grounds citations: call exists, inside the window" "Retriever" {
            tags "Demo path"
        }
        agent -> postgres "Checkpoints paused and follow-up threads" "AsyncPostgresSaver" {
            tags "Demo path"
        }
        mcpServer -> metricLayer "Reads the metric for both windows" "mf" {
            tags "Demo path"
        }
        mcpServer -> retrieval "Counts and reads the calls in each window" "Retriever" {
            tags "Demo path"
        }
        mcpServer -> ollama "Proposes drivers, assigns verbatim quotes" "chat" {
            tags "Demo path"
        }

        # Inside the MCP server (the McpServerComponents view)
        agent -> router "Calls explain_change and query_metric" "MCP stdio" {
            tags "Demo path"
        }
        demoChange -> router "Asks step 6 as an MCP client would" "explain_change" {
            tags "Demo path"
        }
        mcpClients -> router "Call the six tools" "MCP stdio"
        router -> searchTools "Passes search_transcripts, get_call_summary, query_csat" "in process"
        router -> metricTools "Passes query_metric, ask_the_analyst" "in process"
        router -> changeAnalysis "Passes explain_change; returns the cited answer and the change result" "in process" {
            tags "Demo path"
        }
        changeAnalysis -> metricTools "Reads the governed metric for the before and after windows" "query_metric_raw" {
            tags "Demo path"
        }
        metricTools -> metricLayer "Runs the governed metric and gets its SQL" "mf, governed SQL" {
            tags "Demo path"
        }
        changeAnalysis -> retrieval "Counts the calls in each window; reads them by id" "Retriever" {
            tags "Demo path"
        }
        searchTools -> retrieval "Searches calls; opens one call by id" "Retriever"
        searchTools -> postgres "Reads CSAT scores from the f_csat fact" "SQL"
        changeAnalysis -> ollama "Proposes reasons; assigns each call to one" "chat" {
            tags "Demo path"
        }
        changeAnalysis -> hostedModels "The same, when a hosted model is chosen" "HTTPS"
        changeAnalysis -> masking "Masks account numbers in every quote and answer line" "in process" {
            tags "Demo path"
        }
        changeAnalysis -> router "Returns change result v1: change_point, windows (before/after value, n, SQL), drivers and patterns (counts, call ids, quotes), limits" "text plus JSON" {
            tags "Demo path"
        }

        # Built, outside the demo path
        supervisor -> openWebui "Chats with the local models"
        supervisor -> langgraphApi "Watches make demo; opens Studio"
        analyst -> jupyter "Walks the change analysis step by step"
        analyst -> metricLayer "Declares metrics and dimensions"
        engineer -> evals "Runs the offline gate and live experiments"
        engineer -> bench "Compares the vector stores"
        mcpClients -> mcpServer "Spawn and call over stdio" "MCP stdio"
        mcpClients -> mcpHttp "Call over the network" "MCP streamable HTTP"
        mcpHttp -> mcpServer "Serves the same tool code" "in process"
        mcpServer -> postgres "Reads CSAT for query_csat" "SQL"
        retrieval -> lancedb "Upserts and searches; hybrid and full-text" "Lance"
        retrieval -> s3 "Reads source files (raises NotImplementedError)" "HTTPS"
        agent -> hostedModels "Chats through OpenRouter, Anthropic or Fireworks" "HTTPS"
        agent -> vllm "Chats through a self-hosted model" "OpenAI-compatible"
        langgraphApi -> agent "Serves the graph" "HTTP :2024"
        evals -> agent "Scores the real graph" "in process"
        evals -> langsmith "Uploads live experiments" "HTTPS"
        bench -> retrieval "Runs every labeled query, each store and mode" "Retriever"
        generator -> retrieval "Supplies transcripts to ingest" "transcripts.json"
        finetune -> hostedModels "Requests teacher labels" "HTTPS"
        loaders -> snowflake "Loads raw tables; compares metrics" "COPY INTO, make parity"
        loaders -> databricks "Loads raw tables; compares metrics" "COPY INTO, make parity"
        metricLayer -> snowflake "Builds and queries marts" "dbt --target snowflake"
        metricLayer -> databricks "Builds and queries marts" "dbt --target databricks"
        jupyter -> agent "Runs the graph and its MCP tools in process" "Python"
        jupyter -> retrieval "Opens cited calls" "Retriever"
        openWebui -> ollama "Chats with local models" "HTTP"
        mcpHttp -> azure "Runs on Container Apps behind Easy Auth" "Terraform"

        # ── Deployment: local ───────────────────────────────────────────────────

        local = deploymentEnvironment "Local" {
            deploymentNode "Developer machine" "Linux or macOS" "Docker Compose and uv" {
                deploymentNode "Host process (uv)" "make demo-change, make evals, make bench" "Python 3.12" {
                    containerInstance demoChange
                    containerInstance agent
                    containerInstance mcpServer
                    containerInstance retrieval
                    containerInstance metricLayer
                    containerInstance generator
                    containerInstance lancedb
                    containerInstance evals
                    containerInstance bench
                }
                deploymentNode "Compose default profile" "make up" "Docker Compose" {
                    containerInstance postgres
                    containerInstance ollama
                    containerInstance openWebui
                    containerInstance structurizr
                }
                deploymentNode "Compose app profile" "make demo, make notebooks, docker compose --profile app" "Docker Compose" {
                    containerInstance langgraphApi
                    containerInstance mcpHttp
                    containerInstance jupyter
                }
                deploymentNode "Compose gpu profile" "Needs an NVIDIA GPU" "Docker Compose" {
                    tags "Partial"
                    containerInstance vllm
                }
            }
        }

        # ── Deployment: Azure ───────────────────────────────────────────────────

        cloud = deploymentEnvironment "Azure" {
            deploymentNode "Azure subscription" "infra/azure: bootstrap once, envs/dev per session" "Terraform" {
                keyVault = infrastructureNode "Key Vault and budget" "Bootstrap root: app secrets by name, state storage, budget alerts." "Azure"
                deploymentNode "rg-ccai-dev" "Destroyed after each session" "Resource group" {
                    entra = infrastructureNode "Entra Easy Auth" "Rejects unauthenticated calls before they reach Python." "Container Apps auth"
                    deploymentNode "Container Apps environment" "Scale to zero, one replica" "Azure Container Apps" {
                        mcpHttpAzure = containerInstance mcpHttp
                        agentSlot = infrastructureNode "agent-api app slot" "enable_agent_api; no agent HTTP image is built yet." "Container App" {
                            tags "Planned"
                        }
                    }
                    deploymentNode "PostgreSQL Flexible Server" "B1ms, vector extension allow-listed" "PostgreSQL 16" {
                        containerInstance postgres
                    }
                    deploymentNode "ADLS Gen2" "lance and dbt-artifacts filesystems" "Storage account" {
                        containerInstance lancedb
                    }
                    deploymentNode "GPU VM" "enable_gpu, Spot, auto-shutdown" "Linux VM" {
                        tags "Partial"
                        containerInstance vllm
                    }
                    databricksWs = infrastructureNode "Databricks workspace" "enable_databricks; a dbt target." "Azure Databricks" {
                        tags "Partial"
                    }
                    entra -> mcpHttpAzure "Authenticates every request"
                }
            }
            deploymentNode "Snowflake account" "envs/snowflake root: warehouse, database, role, dbt user, resource monitor" "Snowflake" {
                snowflakeWh = infrastructureNode "XS warehouse" "dbt target for the same metrics." "Snowflake"
            }
        }
    }

    views {

        # ── Landscape ────────────────────────────────────────────────────────────

        systemLandscape "Landscape" {
            include *
            autoLayout lr 600 60
            title "Contact Center AI: system landscape"
            description "Who uses the sandbox and what it talks to. Orange marks the change-analysis demo path; dashed boxes are planned or stubbed."
        }

        # ── Containers: everything the repo runs ─────────────────────────────────

        container sandbox "Containers" {
            include *
            exclude "element.type==Person"
            exclude "*->*"
            title "Contact Center AI: everything the repo runs"
            description "Every container, store and Compose service, grouped by capability, with the systems outside it at either side. Dark with an orange border: exercised by make demo-change. Light: built. Dashed: partial. Dotted: planned or stubbed. DemoPathSequence draws the connections."
        }

        # ── The demo path ────────────────────────────────────────────────────────

        dynamic sandbox "DemoPathSequence" "make demo-change, step by step" {
            generator -> postgres "make dev-data: load the ledger, CSAT and change log"
            retrieval -> postgres "make dev-data: embed and upsert 1,540 calls"
            metricLayer -> postgres "make dev-data: dbt build the marts"
            demoChange -> postgres "Preflight: data, marts and vector index present"
            demoChange -> agent "Ask: why did fee-call satisfaction drop after the fee change?"
            agent -> ollama "classify and frame the question"
            agent -> mcpServer "explain_change (and query_metric for the fee question)"
            mcpServer -> metricLayer "The metric before and after the change, with SQL"
            mcpServer -> retrieval "Count and read the calls in each window"
            mcpServer -> ollama "Propose drivers; keep only verbatim quotes"
            agent -> retrieval "ground: every citation exists and sits in its window"
            demoChange -> retrieval "Open one citation, accounts masked"
            demoChange -> mcpServer "Step 6: the same question as an MCP client asks it"
            autoLayout lr 300 150
            title "make demo-change, step by step"
            description "Steps 1 to 3 are make dev-data, once. The agent checkpoints every thread in Postgres. make demo-change-replay prints the last verified run with no model or database."
        }

        # ── Inside the MCP server ────────────────────────────────────────────────

        component mcpServer "McpServerComponents" {
            include router searchTools metricTools changeAnalysis masking
            include agent mcpClients metricLayer retrieval postgres ollama hostedModels
            exclude "agent -> retrieval"
            exclude "agent -> ollama"
            exclude "agent -> postgres"
            exclude "agent -> hostedModels"
            exclude "agent -> mcpServer"
            exclude "mcpClients -> mcpServer"
            exclude "mcpServer -> *"
            exclude "retrieval -> postgres"
            exclude "retrieval -> ollama"
            exclude "metricLayer -> postgres"
            autoLayout lr 500 250
            title "Inside the MCP server: how explain_change answers"
            description "The five parts of ccai_mcp and what each calls. The line back from Change analysis is the change result (version 1); docs/design/change-analysis.md Appendix A has its full schema."
        }

        # ── Deployment ───────────────────────────────────────────────────────────

        deployment sandbox local "DeploymentLocal" {
            include *
            exclude "*->*"
            title "Where it runs locally"
            description "Host processes under uv next to the Compose profiles. make demo-change runs on the host against the default profile's Postgres and Ollama."
        }

        deployment sandbox cloud "DeploymentAzure" {
            include *
            title "Where it runs on Azure"
            description "Terraform in infra/azure. Costly pieces are off by default (enable_gpu, enable_databricks, enable_mcp_server, enable_agent_api). The demo runs locally; orange marks the containers it uses."
        }

        # ── Styles ───────────────────────────────────────────────────────────────

        styles {
            element "Element" {
                fontSize 28
                width 480
                height 300
                background #DCE9F7
                color #10243E
                stroke #6E8DB5
                strokeWidth 2
            }
            element "Person" {
                shape Person
                width 460
                background #DCE9F7
            }
            element "Group" {
                color #4A5D75
                fontSize 30
            }
            element "Software System" {
                background #DCE9F7
            }
            element "External" {
                background #EEEEEE
                color #333333
                stroke #9A9A9A
            }
            element "Database" {
                shape Cylinder
            }
            element "Built" {
                background #DCE9F7
            }
            element "Partial" {
                background #FFFFFF
                color #4A5D75
                stroke #8A9BB0
                border dashed
            }
            element "Planned" {
                background #FFFFFF
                color #4A5D75
                stroke #8A9BB0
                border dotted
            }
            element "Stub" {
                background #FFFFFF
                color #777777
                stroke #A0A0A0
                border dotted
            }
            element "Demo path" {
                background #1F4E8C
                color #FFFFFF
                stroke #F08C00
                strokeWidth 8
            }
            element "Deployment Node" {
                background #FFFFFF
                color #4A5D75
                stroke #8A9BB0
            }
            element "Infrastructure Node" {
                background #F4F4F4
                color #333333
            }
            relationship "Relationship" {
                fontSize 30
                thickness 2
                color #8A9BB0
                style solid
                width 360
            }
            relationship "Demo path" {
                thickness 6
                color #F08C00
            }
        }
    }
}
