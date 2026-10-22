# Tavily — arquitetura detalhada e engenharia reversa (lado cliente)

Documento gerado em 2026-10-22 a partir do código-fonte aberto clonado em `src/`.

## Índice

0. [Escopo, método e limites](#0-escopo-método-e-limites)
1. [Mapa do ecossistema](#1-mapa-do-ecossistema)
2. [Visão de alto nível](#2-visão-de-alto-nível)
3. [Contrato da API REST](#3-contrato-da-api-rest)
4. [Identidade, autenticação e modo keyless](#4-identidade-autenticação-e-modo-keyless)
5. [Tratamento de erros](#5-tratamento-de-erros)
6. [SDK Python (`tavily-python`)](#6-sdk-python-tavily-python)
7. [SDK JS/TS (`tavily-js`)](#7-sdk-jsts-tavily-js)
8. [Servidor MCP local (`tavily-mcp`)](#8-servidor-mcp-local-tavily-mcp)
9. [CLI `tvly` (`tavily-cli`)](#9-cli-tvly-tavily-cli)
10. [Conector LangChain (`langchain-tavily`)](#10-conector-langchain-langchain-tavily)
11. [Framework de avaliação (`tavily-search-evals`)](#11-framework-de-avaliação-tavily-search-evals)
12. [Fluxos de ponta a ponta](#12-fluxos-de-ponta-a-ponta)
13. [Padrões de projeto e decisões de design](#13-padrões-de-projeto-e-decisões-de-design)
14. [Segurança: o que o código mostra](#14-segurança-o-que-o-código-mostra)
15. [Inferências sobre o backend](#15-inferências-sobre-o-backend)
16. [Linguagens e dependências](#16-linguagens-e-dependências)
17. [Lições para o nosso agente Groq](#17-lições-para-o-nosso-agente-groq)
18. [Lacunas](#18-lacunas)

---

## 0. Escopo, método e limites

**O backend do Tavily NÃO é open source.** O que a organização `tavily-ai` publica no GitHub são
SDKs, um servidor MCP, uma CLI, um conector LangChain e um framework de avaliação. Tudo isso é
*cliente* do serviço hospedado em `https://api.tavily.com` (e `https://mcp.tavily.com`).

Convenção usada em todo o documento:

- **[FATO]** — lido diretamente no código clonado em `src/`.
- **[INFERÊNCIA]** — dedução a partir de nomes de parâmetros, formato das respostas e README de
  benchmarks. Não foi verificada contra o servidor e pode estar errada.

Método: leitura do código, sem executá-lo. Não foi feita nenhuma chamada à API real, nenhuma
tentativa de contornar autenticação ou limites, e nenhuma análise de tráfego. A "engenharia reversa"
aqui é a reconstrução do contrato público a partir dos clientes.

Cobertura de leitura:
- **Lidos por inteiro:** `tavily-python/tavily/{tavily,utils,errors,config}.py`, `hybrid_rag.py`,
  `tavily-mcp/src/index.ts`, `tavily-js/src/{client,search,research,errors,utils}.ts`, `cli.py`,
  `common.py`, `keyless.py`, `mcp_client.py`, `oauth.py`, `commands/research.py`, `commands/search.py`,
  `repl.py`, `run_evaluation.py`, `handlers/base_handler.py`, `handlers/tavily_handler.py`.
- **Lidos em parte:** `async_tavily.py` (diff contra o sync), `config.py` da CLI, `init_skills.py`,
  `update.py`, `defaults.py`, `_utilities.py` e `tavily_search.py` do LangChain, `types.ts`,
  `correctness_evaluator.py`, `post_processor.py`.
- **Só listados (nomes/linhas):** `output.py`, `theme.py`, `commands/{init,crawl,map,extract,feedback}.py`,
  `tavily_{crawl,extract,map,research}.py`, handlers de Exa/Brave/Serper/Perplexity/GPTR.

---

## 1. Mapa do ecossistema

Clonados com `git clone --depth 1` (histórico `.git` removido).

| Pasta | Papel | Linguagem | Versão | Linhas aprox. |
|---|---|---|---|---|
| `tavily-python/` | SDK oficial (sync, async, hybrid RAG) | Python ≥3.8 | 0.8.5 | ~2.000 |
| `tavily-js/` | SDK oficial `@tavily/core` | TypeScript | 0.7.14 | ~1.400 |
| `tavily-mcp/` | Servidor MCP por stdio | TypeScript (Node 22) | 0.2.23 | ~1.260 |
| `tavily-cli/` | CLI `tvly` com OAuth, REPL e instalador de skills | Python ≥3.10 | 0.1.9 | ~4.700 |
| `langchain-tavily/` | Ferramentas LangChain | Python ≥3.10 | — | ~3.000 |
| `tavily-search-evals/` | Benchmark de provedores de busca | Python | — | ~1.000 + datasets |

Não clonados (existem na org, fora do escopo): plugins (Cursor, Grok, Hermes, opencode, n8n), demos,
`tavily-cookbook`, `skills`, e forks de `langchainjs`, `crewAI`, `n8n`.

Árvores dos repositórios principais:

```
tavily-python/
├── setup.py                       # name=tavily-python, deps: requests, tiktoken>=0.5.1, httpx
├── tavily/
│   ├── __init__.py                # exporta TavilyClient, AsyncTavilyClient, erros, TavilyHybridClient
│   ├── tavily.py        (902)     # TavilyClient síncrono (requests.Session)
│   ├── async_tavily.py  (970)     # AsyncTavilyClient (httpx.AsyncClient)
│   ├── errors.py        (71)      # hierarquia de exceções
│   ├── utils.py / config.py       # contagem de tokens (tiktoken, gpt-3.5-turbo, 4000)
│   └── hybrid_rag/hybrid_rag.py   # TavilyHybridClient (MongoDB + Cohere)
├── examples/                      # company_information, hybrid_rag, openai_assistant
└── tests/                         # search, errors, crawl, map, research, feedback, sessões

tavily-js/src/
├── index.ts → client.ts           # fábrica tavily(options)
├── search.ts extract.ts crawl.ts map.ts research.ts feedback.ts   # um módulo por endpoint
├── utils.ts                       # post(), get(), buildHeaders(), tokens, erros
├── errors.ts                      # TavilyKeylessLimitError
└── types.ts (339)                 # todos os tipos públicos

tavily-mcp/src/
├── index.ts (1169)                # servidor, 6 ferramentas, polling/stream do research
└── format-results.ts              # texto legível das respostas

tavily-cli/tavily_cli/
├── cli.py                         # grupo click `tvly`; sem subcomando → REPL
├── config.py (605)                # credenciais, sessão, fábrica de clientes
├── oauth.py (514)                 # OAuth 2.1 + PKCE nativo
├── mcp_client.py                  # McpTavilyClient (JSON-RPC sobre HTTP)
├── keyless.py / common.py         # erros, sanitização de terminal
├── output.py / theme.py / repl.py # renderização rich e REPL
├── init_skills.py (390)           # instala skills de agentes
└── commands/                      # search extract crawl map research auth init defaults feedback update
```

---

## 2. Visão de alto nível

```
 ┌───────────────────────── CLIENTES (código aberto, em src/) ──────────────────────────┐
 │                                                                                      │
 │  Apps do usuário ──► tavily-python ─┐                                                │
 │  Apps Node/TS    ──► tavily-js ─────┤                                                │
 │  LangChain       ──► langchain-tavily (requests/aiohttp direto) ─┐                  │
 │  Evals           ──► aiohttp direto ──────────────────────────────┤                  │
 │  Claude/Cursor   ──► tavily-mcp (stdio) ─ axios ──────────────────┤                  │
 │  Terminal        ──► tavily-cli ─┬─ chave tvly-* → tavily-python ─┤                  │
 │                                  └─ token OAuth → McpTavilyClient ─┼──┐              │
 └──────────────────────────────────────────────────────────────────┼──┼──────────────┘
                                                                     ▼  ▼
 ┌─────────────────────────────────────────────┐   ┌─────────────────────────────────────┐
 │ https://api.tavily.com   (CAIXA-PRETA)      │   │ https://mcp.tavily.com              │
 │  POST /search   POST /extract   POST /crawl │◄──┤  POST /mcp   (JSON-RPC, resposta SSE)│
 │  POST /map      POST /research              │   │  /.well-known/oauth-*  /authorize   │
 │  GET  /research/{id}   POST /feedback       │   │  /token  /register  /revoke         │
 └─────────────────────────────────────────────┘   └─────────────────────────────────────┘
```

**[FATO]** Há duas portas de entrada para o mesmo serviço:

1. **API REST direta** com chave `tvly-...` no header `Authorization: Bearer`.
2. **Endpoint MCP remoto** com token OAuth (JWT). A CLI o usa quando o login é OAuth porque o SDK
   Python só aceita chaves `tvly-*` (`mcp_client.py:1-8`).

**[INFERÊNCIA]** O MCP remoto é um gateway sobre a API REST: ele recebe `X-Session-Id` e `X-Human-Id`
e os repassa "para atribuição de sessão" (comentário em `mcp_client.py:61-63`), e os nomes das
ferramentas (`tavily_search`, …) são os mesmos do MCP local.

Observação importante: **o MCP local (`tavily-mcp`) fala com `api.tavily.com` por REST**, não com
`mcp.tavily.com`. Já a CLI com OAuth fala com `mcp.tavily.com`. São caminhos diferentes.

---

## 3. Contrato da API REST

Base: `https://api.tavily.com` (sobrescrevível por `api_base_url` / `apiBaseURL`).
Corpo sempre JSON. Parâmetros `None`/`undefined` são removidos antes do envio.

### 3.1 Endpoints

| Método | Rota | Função | Timeout padrão |
|---|---|---|---|
| POST | `/search` | Busca web | 60 s (SDK Python limita a 120 s: `min(timeout, 120)`) |
| POST | `/extract` | Extrai conteúdo de URLs | 30 s |
| POST | `/crawl` | Rastreia um site extraindo conteúdo | 150 s |
| POST | `/map` | Só descobre URLs de um site | 150 s |
| POST | `/research` | Tarefa de pesquisa (polling ou stream) | sem padrão |
| GET | `/research/{request_id}` | Consulta resultado (200 ou 202); `?include_usage=true` | 60 s (JS) |
| POST | `/feedback` | Avalia resultados | 10 s |

### 3.2 `/search`

Parâmetros: `query`; `search_depth` (`basic`|`advanced`|`fast`|`ultra-fast`); `topic`
(`general`|`news`|`finance`); `time_range` (`day|week|month|year`, o JS aceita também `d|w|m|y`);
`start_date`/`end_date` (YYYY-MM-DD); `days`; `max_age_hours` (o JS aceita o alias `maxHours`);
`max_results`; `include_domains`/`exclude_domains`; `include_domains_mode` (`restrict`|`prefer`);
`include_answer` (bool|`basic`|`advanced`); `include_raw_content` (bool|`markdown`|`text`);
`include_images`; `include_image_descriptions`; `country`; `auto_parameters`; `include_favicon`;
`include_usage`; `exact_match`; `fetch_timeout`; `cache_fallback`; `language`;
`filter_by_language`; `chunks_per_source`.

Resposta: `query`, `request_id`, `response_time`, `answer?`, `follow_up_questions?`, `images[]`,
`results[]` com `{title, url, content, score, published_date?, raw_content?, favicon?, id, images?}`,
`usage?`, `auto_parameters?` (o que o servidor escolheu: `include_domains`, `exclude_domains`,
`topic`, `time_range`, `search_depth`).

Regras impostas nos clientes:
- MCP: `start_date`/`end_date` junto de `time_range` → `time_range` é descartado (`index.ts:737-739`).
- MCP: `country` presente → força `topic="general"` (`index.ts:544-546`). Aceita nome completo do país,
  não ISO.
- MCP: `max_results` limitado a 5–20 pelo schema da ferramenta.
- JS: normaliza `images` (string ou `{url, description}`) e filtra URLs vazias.

### 3.3 `/extract`
`urls` (lista ou string), `extract_depth` (`basic|advanced`), `format` (`markdown|text`),
`include_images`, `include_favicon`, `include_usage`, `query` (reordena chunks por relevância),
`chunks_per_source`, `timeout`. Resposta: `results[] {url, title?, raw_content, images?, favicon?}`,
`failed_results[] {url, error}`, `response_time`, `request_id`. O SDK Python garante `results` e
`failed_results` via `setdefault`.

### 3.4 `/crawl` e `/map`
Controles de travessia: `max_depth`, `max_breadth`, `limit`, `instructions` (linguagem natural),
`select_paths`/`exclude_paths` e `select_domains`/`exclude_domains` (regex), `allow_external`.
`crawl` acrescenta extração (`extract_depth`, `format`, `chunks_per_source`, `include_favicon`);
`map` devolve `results: string[]` e `base_url`. Defaults de ferramenta no MCP: depth 1, breadth 20,
limit 50.

### 3.5 `/research`
Entrada: `input`, `model` (`mini`|`pro`|`auto`), `output_schema` (JSON Schema), `stream`,
`citation_format` (`numbered|mla|apa|chicago`). Resposta de criação (JS): `request_id`, `created_at`,
`status`, `input`, `model`, `response_time`.

Dois modos de consumo, ambos **[FATO]**:

```
 POLLING                                   STREAM (SSE)
 POST /research ─► {request_id,status}     POST /research {stream:true}
 loop:                                      ◄── event/data frames
   GET /research/{id}                          data: {"choices":[{"delta":{
     202 / status≠completed → espera              "content": "...", "sources": [...],
     status=completed → {content,sources}         "tool_calls": {type, tool_call|tool_response}}}]}
     status=failed → erro                       event: done | event: error
```

- Se `POST` responde `400` com `detail.error_code == "research_stream_required"`, o MCP refaz a
  chamada com `stream:true` e monta o relatório em memória (`index.ts:843-846`).
- A CLI mostra, durante o stream, chamadas de ferramenta do agente (`tool_calls`/`tool_response`) e
  a contagem de fontes encontradas pela ferramenta `WebSearch` (`commands/research.py:62-100`).

**[INFERÊNCIA]** O formato `choices[].delta` é o do Chat Completions da OpenAI; o `/research` expõe um
agente com chamadas de ferramenta internas (`WebSearch` é o nome visto).

### 3.6 `/feedback`
Campos: `session_id`, `request_id`, `agent_score`, `human_score` (1 perfeito / 0 irrelevante /
−1 prejudicial), `extra_scores[{label,value}]`, `comment`, `response_delivered`, `used_urls`,
`used_ids`, `used_citations`, `urls_scores[{id|url, agent_score, scores, comment}]`.
Resposta: `{success, feedback_id, response_time}`. **[INFERÊNCIA]** É um sinal de qualidade/treino
coletado dos próprios agentes: a descrição da ferramenta MCP manda pontuar *todos* os resultados.

---

## 4. Identidade, autenticação e modo keyless

### 4.1 Headers enviados **[FATO]** (`tavily.py:82-92`, `utils.ts:19-43`)

| Header | Quando | Significado |
|---|---|---|
| `Authorization: Bearer <key>` | com chave | autenticação |
| `X-Tavily-Access-Mode: keyless` | sem chave | modo sem chave |
| `X-Client-Source` | sempre | `tavily-python`, `tavily-js`, `MCP`, `tavily-cli`, `langchain-tavily`, ou `*-keyless` |
| `X-Project-ID` | `TAVILY_PROJECT` | projeto (uso/cobrança) |
| `X-Tavily-Orgid` | `TAVILY_ORG_ID` | organização |
| `X-Session-Id` | opcional | agrupa chamadas de uma sessão de agente |
| `X-Human-Id` | opcional | usuário humano final |
| `X-Client-Name` | opcional | nome do app cliente |

Sobrescrita por chamada: `project_id`, `session_id`, `human_id`, `client_name` passados a um método
viram headers só daquela requisição (`_pop_request_headers`).

### 4.2 Credenciais por componente
| Componente | Onde busca a chave |
|---|---|
| SDK Python/JS | argumento → `TAVILY_API_KEY` |
| MCP local | `TAVILY_API_KEY` (e `.env` via `dotenv`); também envia `api_key` no corpo (legado) |
| CLI | env `TAVILY_API_KEY` → `api_key` em `~/.tavily/config.json` → OAuth em `config.json` → legado `~/.mcp-auth` |
| LangChain | `tavily_api_key` ou `TAVILY_API_KEY` (`SecretStr`) |

Proxies: `TAVILY_HTTP_PROXY`/`TAVILY_HTTPS_PROXY` (SDKs).

### 4.3 Modo keyless **[FATO]**
Sem chave, só `search` e `extract` funcionam. As demais chamadas lançam
`KeylessUnsupportedEndpointError` (Python) ou erro equivalente (JS) **antes** de qualquer rede.
Estouro de limite → a API devolve o *envelope recuperável*:

```json
{"error": {"code": "...", "message": "...", "window": "...",
           "retry_after_seconds": 123,
           "next_actions": [
             {"type": "signup", "url": "..."},
             {"type": "agentic_payment", "scheme": "x402", "details": "..."},
             {"type": "bonus_credits", "eligible": true, "credits_on_completion": N,
              "endpoint": "/v1/keyless/bonus", "questions": ["..."]}
           ]}}
```

Cliente levanta `TavilyKeylessLimitError` (Python: `code, window, retry_after_seconds, next_actions`;
JS: `capType, retryAfter, bonusEligible, continuationPaths`). A CLI renderiza e sai com código **3**.
O mesmo formato de envelope também é usado para "endpoint exige chave".

**[INFERÊNCIA]** Funil para agentes autônomos: uso anônimo limitado → cadastro, pagamento x402 ou
créditos-bônus por responder perguntas.

---

## 5. Tratamento de erros

`tavily.py:116-143` (o async espelha com `httpx`):

| Status | Exceção Python | Observação |
|---|---|---|
| 400 | `BadRequestError` | no `/research` pode ter `detail.error_code = research_stream_required` |
| 401 | `InvalidAPIKeyError` | |
| 403, 432, 433 | `ForbiddenError` | **[INFERÊNCIA]** 432/433 não são HTTP padrão; provavelmente limites de plano/chave |
| 429 | `UsageLimitExceededError` | `TavilyKeylessLimitError` herda dela |
| outros | `raise_for_status()` | |
| timeout | `TimeoutError(timeout)` | classe própria que sombreia a builtin |

Mensagem vem de `body.detail.error`. Há ainda `MissingAPIKeyError` (definida, mas o cliente atual
entra em keyless em vez de lançá-la).

No MCP local, erro do axios vira `{isError:true, text:"Tavily API error: ... Documentation: <url>"}`;
só 401 e 429 do `/research` viram exceções com mensagem própria.

Códigos de saída da CLI: **3** = limite/keyless/OAuth refresh/sem chave; **4** = erro de API genérico.
`429` e `432` são tratados como "limite de uso" e sugerem upgrade de plano (`common.py:~118`).

---

## 6. SDK Python (`tavily-python`)

### 6.1 `TavilyClient` (síncrono)
- Cria `requests.Session`, define headers uma vez (só os que a sessão externa ainda não tem).
- Padrão por endpoint: `search()` (público) → `_search()` (monta dict, remove `None`, mescla
  `**kwargs`, POST, trata erro). `**kwargs` deixa passar parâmetros novos da API sem atualizar o SDK.
- `session` externa permitida (pool próprio); `close()` só fecha sessão que o SDK criou.
- Suporta `with TavilyClient() as c:`.
- **Descontinuados:** `get_search_context` (corta resultados por tokens via `tiktoken`, modelo
  `gpt-3.5-turbo`, 4000 tokens), `qna_search` (`include_answer=True`), `Client`.

### 6.2 `AsyncTavilyClient` (`async_tavily.py`)
Mesma API, com `httpx.AsyncClient`. Diferenças **[FATO]**:
- Aceita `company_info_tags=("news","general","finance")` e o método extra `get_company_info()`, que
  dispara uma busca por tag em paralelo (`asyncio.gather`) e funde os resultados.
- Proxies via `httpx.AsyncHTTPTransport` montado por esquema.
- `research(stream=True)` devolve `AsyncGenerator[bytes]`.
- `client=` externo no lugar de `session=`.

### 6.3 `TavilyHybridClient` (`hybrid_rag/hybrid_rag.py`)
RAG híbrido: combina um índice vetorial local **MongoDB Atlas** com a busca web do Tavily.

```
query ─► embed (Cohere embed-english-v3.0, input_type=search_query)
      ├─► MongoDB $vectorSearch (index/embeddings_field; numCandidates=max_local+3)  → "local"
      └─► TavilyClient.search(max_results=max_foreign)                               → "foreign"
 combina ─► ranking_function (Cohere rerank-english-v3.0, top_n) ─► corta em max_results
 opcional: save_foreign=True|fn ─► embeda os "foreign" e insert_many no Mongo
```
`_validate_index` exige índice `vectorSearch`, campo `vector` e similaridade `cosine`. Embedding e
ranking são plugáveis (`embedding_function`, `ranking_function`). Único `db_provider`: `mongodb`.

### 6.4 Testes
`tests/`: search, errors, crawl, map, research, feedback, sessão custom, pooling, override de headers,
interceptação de request (`request_intercept.py`). Sem chamadas reais em `conftest` no que foi visto.

---

## 7. SDK JS/TS (`tavily-js`)

- Fábrica `tavily(options)` (`client.ts`) devolve um **objeto de funções** (não classe):
  `search, extract, crawl, map, research, getResearch, feedback` + deprecadas `searchQNA`,
  `searchContext`. Sem chave → apenas `search`/`extract` reais; o resto vira stubs que lançam
  `"<método>: Keyless mode only supports search and extract..."`.
- Cada endpoint é um módulo que recebe `requestConfig` por *closure* e devolve a função.
- Conversão camelCase → snake_case no envio; resposta normalizada para camelCase
  (`rawContent`, `publishedDate`, `requestId`, `autoParameters`).
- `post()`/`get()` em `utils.ts` montam URL, headers, timeout (em ms) e proxy (`HttpsProxyAgent`).
  `/research` não tem timeout por padrão.
- Contagem de tokens com `js-tiktoken` (`getMaxTokensFromList`, 4000 padrão).
- Build com `tsup` gerando CJS + ESM; `exports` com `import`/`require`.
- Opções por chamada: `sessionId`, `humanId`, `clientName` sobrescrevem a config do cliente.

---

## 8. Servidor MCP local (`tavily-mcp`)

**Transporte:** stdio (`StdioServerTransport`). **SDK:** `@modelcontextprotocol/sdk`. **Nome/versão
anunciados:** `tavily-mcp` / `0.2.22` (o `package.json` diz 0.2.23 — pequena divergência).

### 8.1 Ferramentas expostas
`tavily_search`, `tavily_extract`, `tavily_crawl`, `tavily_map`, `tavily_research`, `tavily_feedback`.
A descrição de cada ferramenta é escrita como *prompt* para o agente (ex.: "use `news` só quando o
usuário pedir notícias"). O `listTools()` do CLI `--list-tools` usa descrições diferentes e mais
promocionais que o `ListTools` real.

### 8.2 Ciclo de uma chamada
```
 host MCP ──CallTool──► setRequestHandler(CallToolRequestSchema)
     switch(name) ──► this.search/extract/crawl/map/research/feedback
         └► axios.post(https://api.tavily.com/<ep>, {…params, api_key?})  [headers: Bearer, X-Session-Id…]
     ◄── formatResults()/formatCrawlResults()/… (texto) ◄── resposta JSON
 erro axios ─► envelope keyless? → texto com opções  |  senão → "Tavily API error: … Documentation: URL"
```

### 8.3 Comportamentos não óbvios **[FATO]**
- `DEFAULT_PARAMETERS` (env, JSON) **sobrescreve** os argumentos do agente quando a chave coincide
  (`index.ts:728-732`: o default vence).
- `stripEmptyValues` remove `""`, `null`, `undefined`, `[]`.
- `crawl` fixa `chunks_per_source: 3`; a formatação do crawl corta cada página em 200 caracteres.
- `research` espera com backoff 2 s ×1,5 até 10 s; limite 5 min (`mini`) ou 15 min (`pro`/`auto`);
  no stream, timeouts de 30 s para iniciar, 5 min de ociosidade, 5/15 min totais.
- `X-Session-Id` = `randomUUID()` por processo; `X-Human-Id` de `TAVILY_HUMAN_ID`.
- `Dockerfile` multi-stage `node:22-alpine`, `ENTRYPOINT node build/index.js`; `smithery.yaml` para
  distribuição.

---

## 9. CLI `tvly` (`tavily-cli`)

### 9.1 Estrutura
`click` (comandos), `rich` (saída), `httpx` (OAuth/MCP), `psutil` (PID), `tavily-python` (cliente).
Sem subcomando → entra no **REPL** (`repl.py`): lê linhas com `readline`, `shlex.split`, remove `tvly`
inicial e redespacha para o mesmo grupo `cli(args, standalone_mode=False)`; `SystemExit` de comando
não derruba o REPL.

Comandos: `search`, `extract`, `crawl`, `map`, `research (run|status|poll)`, `feedback`, `login`,
`logout`, `auth`, `init`, `update`, `set-default`, `unset-default`. Flag global `--json` (herdada por
`json_option`). Entrada `-` lê o texto da stdin (limite 100.000 caracteres).

`research` usa um `ResearchGroup` customizado: se o primeiro argumento não é subcomando, vira
`research run <query>`; `status`/`poll` sem `request_id` também viram query.

### 9.2 Resolução de cliente (`config.py`)
```
get_client_or_keyless():
  key = get_api_key()          # env > config.api_key > OAuth(config) > ~/.mcp-auth
  se key: 
      is_oauth_token(key)? (não começa com "tvly-" e é JWT decodificável)
          sim → McpTavilyClient (https://mcp.tavily.com/mcp)
          não → TavilyClient(api_key, client_source="tavily-cli", session_id, human_id, api_base_url)
  senão → TavilyClient keyless ("tavily-cli-keyless")
```
`search` e `extract` aceitam keyless; `crawl`, `map`, `research`, `feedback` chamam
`require_api_key_friendly` e saem com 3.

**Sessão:** `SESSION_ID` estável por terminal/agente — `~/.tavily/session.json` mapeia PID do pai (e do
avô, para agentes como o Claude Code que criam um shell novo por comando) → UUID; entradas de
processos mortos são podadas (`psutil`). Arquivo criado com `umask 0o077`.

**Config:** `~/.tavily/config.json` com trava de arquivo (`fcntl.flock` no Unix, `msvcrt` no
Windows) e `RLock` de thread.

### 9.3 OAuth 2.1 + PKCE (`oauth.py`) **[FATO]**
```
1. fetch_metadata: GET /.well-known/oauth-authorization-server  (+ protected-resource/mcp)
      valida issuer == https://mcp.tavily.com/ e que todas as URLs são HTTPS sem credenciais
      (fallback estático se a descoberta falhar)
2. sobe HTTPServer em 127.0.0.1:<porta aleatória>  → redirect_uri = http://127.0.0.1:<porta>/callback
3. register_client (RFC 7591): public client, token_endpoint_auth_method=none,
      grants authorization_code + refresh_token, application_type=native, scope "openid offline_access"
4. PKCE: verifier de 128 caracteres, challenge S256; state = token_urlsafe(32)
5. abre o navegador em /authorize?…&resource=https://mcp.tavily.com/mcp   (timeout 180 s)
6. callback: valida state (anti-CSRF), troca code → tokens em /token
7. refresh: access expira em (expires_in − 60 s); renova com refresh_token e "resource"
8. logout: revoga em /revoke (com fallback client_secret="" para bug do MCP Python SDK #2260)
```
`looks_headless()` detecta SSH ou Linux sem `DISPLAY`/`WAYLAND_DISPLAY` e não abre o navegador.
Se o refresh falha, **não apaga** as credenciais (`OAuthError` explicando para tentar de novo).

### 9.4 `McpTavilyClient` (`mcp_client.py`)
POST JSON-RPC `{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{name,arguments}}` em
`https://mcp.tavily.com/mcp` com `Accept: application/json, text/event-stream`. A resposta é lida
linha a linha procurando `data:`; usa `structuredContent` se existir, senão `content[0].text`
(tenta `json.loads`, senão `{"raw": texto}`). Mapeia métodos → ferramentas `tavily_*`
(`get_research` → `tavily_get_research`). Timeout 180 s. Cabeçalhos extra: `mcp-session-id`,
`X-Session-Id`, `X-Human-Id`, `x-client-name`.

### 9.5 `init`, skills e `defaults`
- `init_skills.py` instala as "skills" de agentes (`tavily-best-practices`, `tavily-cli`,
  `tavily-crawl`, `tavily-dynamic-search`, `tavily-extract`, `tavily-map`, `tavily-research`,
  `tavily-search`) para `claude-code`, `codex` e `cursor`. Prefere `npx skills@1.7.4`; sem Node usa um
  tarball do repositório `tavily-ai/skills` **fixado por commit e SHA-256**, com limites de 25 MB por
  arquivo compactado e 5 MB por arquivo extraído.
- `set-default`/`unset-default` (`defaults.py`) tornam o Tavily a busca padrão do agente: adiciona
  `WebSearch` e `WebFetch` à lista `permissions.deny` de `~/.claude/settings.json` (ou
  `.claude/settings.local.json` com projeto) e escreve `web_search = "disabled"` em
  `~/.codex/config.toml`. Ou seja: **desliga as ferramentas web nativas** do Claude Code/Codex.
- `update.py` detecta como foi instalado (pipx, uv tool, uvx, pip, fonte/usuário) e escolhe o comando
  de upgrade; consulta `pypi.org/pypi/tavily-cli/json`.
- `install.sh`: instalador POSIX; exige Python ≥3.10, roda `init` interativo só se houver TTY e não
  estiver em CI/SSH/Linux sem display.

---

## 10. Conector LangChain (`langchain-tavily`)

- Pacote exporta `TavilySearch`, `TavilyExtract`, `TavilyCrawl`, `TavilyMap`, `TavilyResearch`,
  `TavilyGetResearch`.
- **Não usa o SDK `tavily-python`:** `_utilities.py` tem um *wrapper* Pydantic por endpoint
  (`TavilySearchAPIWrapper`, …) que chama `requests.post` (sync) e `aiohttp` (async) direto, com
  `X-Client-Source: langchain-tavily`.
- Dependências: `langchain-core`, `langchain`, `aiohttp`, `requests`.
- Cada ferramenta é um `BaseTool` com schema Pydantic (`TavilySearchInput`, `extra="allow"`) cujas
  `Field(description=...)` são **instruções detalhadas para o LLM** sobre quando usar cada parâmetro
  (ex.: `topic="news"` só para política/esportes/grandes eventos; `time_range` só se o usuário
  mencionar período).
- Diferença de tratamento de erro: o caminho sync lê `detail.error` e lança `ValueError`; o async só
  lança `Exception("Error {status}: {reason}")` (menos informação).

---

## 11. Framework de avaliação (`tavily-search-evals`)

### 11.1 Pipeline
```
 config.json (parâmetros por provedor)
        │
 run_evaluation.py ── carrega dataset (SimpleQA .csv | Document Relevance .json), amostra/índices
        │           ── instancia handlers: tavily, exa, serper, brave, perplexity(_search), gptr
        ├── SimpleQA:  handler.search → post_process → PostProcessor.extract_answer (gpt-4.1)
        │                → CorrectnessEvaluator (classifica CORRECT/INCORRECT/NOT_ATTEMPTED)
        │                lotes de 3 exemplos + sleep 3 s; salva cada resultado (retomável --rerun)
        └── Document Relevance: handler.search → post_process → QuotientAI.log(user_query, documents)
                                 espera 60 s → lê logs → % de documentos com is_relevant
        ▼
 summary.csv + {provedor}_{tipo}_results.csv em results/{tipo}/{timestamp}/
```

### 11.2 Handlers
`ProviderHandler` (ABC) exige `api_key` e `api_url` e define `search()` e `post_process()`. Endpoints
usados **[FATO]**: Tavily `POST /search`; Exa `POST https://api.exa.ai/search`; Brave `GET`
`https://api.search.brave.com/res/v1/web/search`; Serper `https://google.serper.dev/search`;
Perplexity `https://api.perplexity.ai/chat/completions` e `/search`. O handler do Tavily envia
`api_key` **no corpo** (não no header), via `aiohttp`.

### 11.3 Resultados publicados (pelo próprio Tavily)
| Provedor | SimpleQA | Document Relevance |
|---|---|---|
| Tavily | 93,3% | 83,02% |
| Perplexity Search | 85,92% | 71,2% |
| Google (Serper) | 82,15% | 58,11% |
| Brave | 76,05% | 56,2% |
| Exa | 71,24% | 51,33% |

**Ressalvas verificáveis no código:** (a) o Tavily roda com `search_depth: advanced` e 10 resultados,
Exa só com `highlights`; (b) o juiz e o extrator de resposta são LLMs (`gpt-4.1`), com prompt que
manda escolher a fonte "mais confiável" (Wikipedia/.gov/.edu), o que pode favorecer quem devolve mais
conteúdo; (c) foi o Tavily quem escreveu e publicou. Resultados independentes (AIMultiple,
Openbenchmarks) deram um quadro mais empatado. Trate como material de marketing até reproduzir.

---

## 12. Fluxos de ponta a ponta

### 12.1 Busca simples (SDK Python)
```
TavilyClient.search("q", max_results=5)
  → _search: monta dict (remove None) → json.dumps → session.post(base_url+"/search", timeout)
  → 200: resp.json() → setdefault("results", [])      | ≠200: _handle_error_response
```

### 12.2 Agente com MCP (Claude Code → tavily-mcp)
```
Claude ──tools/call tavily_search{query,…}──► tavily-mcp
   aplica DEFAULT_PARAMETERS, regras country/time_range, stripEmptyValues
   ──POST /search──► api.tavily.com ──► JSON ──► formatResults() ──► texto para o modelo
   (Request ID e IDs dos resultados vão no texto, para o modelo poder chamar tavily_feedback)
```

### 12.3 Login OAuth da CLI
Ver §9.3; depois `search` usa `McpTavilyClient` e fala com `mcp.tavily.com/mcp`.

### 12.4 Research com fallback de stream
```
POST /research(model=auto) ─► 200 {request_id} ─► poll GET /research/{id} …
                          └► 400 research_stream_required ─► repete com stream:true ─► SSE até event: done
```

---

## 13. Padrões de projeto e decisões de design

1. **Superfície única, muitos clientes finos.** Toda a lógica está no servidor; os clientes só
   empacotam parâmetros e normalizam respostas. Isso explica a repetição de código entre SDKs.
2. **`**kwargs` de passagem** (Python) / `...kwargs` (JS): o SDK não precisa ser atualizado para novos
   parâmetros da API.
3. **Parâmetros opcionais = ausentes**, nunca `null`: filtragem sistemática de `None`.
4. **Descrições como prompt:** schemas de ferramentas (MCP, LangChain) são escritos para guiar o LLM.
5. **Telemetria declarativa por headers** (`X-Client-Source`, sessão, humano, projeto, organização).
6. **Funil de crescimento embutido no cliente:** keyless → signup/x402/bônus, `set-default` que
   desliga as buscas nativas do agente, skills instaladas nos agentes.
7. **Feedback loop:** `/feedback` e a insistência nas descrições para pontuar cada resultado.
8. **Deprecação suave:** `get_search_context`, `qna_search`, `searchQNA`, `searchContext` avisam e
   delegam para `search`.
9. **Duplicação conhecida:** a lógica de limite keyless e os formatadores existem em Python (SDK, CLI),
   TypeScript (SDK, MCP) e não são compartilhados.
10. **Inconsistências visíveis:** versão do MCP (0.2.22 no código × 0.2.23 no pacote); `api_key` no
    corpo *e* no header; tratamento de erro diferente entre sync e async do LangChain;
    `MissingAPIKeyError` sem uso.

---

## 14. Segurança: o que o código mostra

- **Injeção de escape em terminal:** `common.sanitize_control` remove bytes C0/C1 de qualquer texto
  vindo da web/API/MCP antes de imprimir (a documentação do código diz que o Rich não faz isso).
  Streams em modo `--json` são reserializados como NDJSON pelo mesmo motivo.
- **OAuth:** PKCE S256, `state` anti-CSRF, validação do `issuer`, HTTPS obrigatório e sem
  credenciais/fragmento nas URLs descobertas, servidor de callback só em `127.0.0.1`, arquivo de
  sessão com `umask 0o077`.
- **Cadeia de suprimentos das skills:** commit fixo + SHA-256 do tarball + limites de tamanho;
  `npx skills@1.7.4` com versão exata (comentário: "roda com as permissões do usuário").
- **Segredos:** `SecretStr` no LangChain; mas o MCP e o handler de evals enviam a chave também no
  corpo JSON, que pode aparecer em logs de proxy.
- **Superfície de privacidade:** `/feedback` envia `response_delivered` (a resposta final ao usuário) e
  `used_citations`; `X-Human-Id` identifica o usuário. O usuário deve saber disso antes de ativar.
- **Risco do `set-default`:** altera configs globais do Claude Code/Codex para negar `WebSearch` e
  `WebFetch`; é reversível por `unset-default`.

---

## 15. Inferências sobre o backend

Nada abaixo foi confirmado em código.

- **Pipeline provável de `/search`:** consulta → (opcional) `auto_parameters` escolhe
  `topic`/`search_depth`/domínios/`time_range` → recuperação em fontes web → extração do conteúdo →
  *chunking* e reordenação por relevância (`chunks_per_source`, `score`) → (opcional) resposta por LLM
  (`include_answer: basic|advanced`).
- **Cache de páginas:** `cache_fallback`, `max_age_hours`, `fetch_timeout` sugerem páginas em cache
  com tolerância de idade e busca ao vivo.
- **Profundidades:** `ultra-fast`/`fast` priorizam latência; `advanced` faz mais trabalho por
  resultado. Custo (1 crédito basic / 2 advanced) vem de guia de terceiros, não do código.
- **Fonte dos resultados:** o código não revela se há índice próprio ou agregação de buscadores.
  Não afirmo nada.
- **`/research`:** agente multi-etapas com chamadas de ferramenta (`WebSearch`), relatório com
  citações e saída estruturada via JSON Schema; modelos `mini`/`pro`.
- **Camadas:** `api.tavily.com` (REST) e `mcp.tavily.com` (MCP + OAuth 2.1) são serviços distintos; o
  segundo parece ser um gateway sobre o primeiro, com servidor de autorização próprio.
- **Linguagem do backend:** desconhecida. O servidor de autorização lembra o MCP Python SDK (a CLI
  contorna um bug dele no `/revoke`), o que **sugere** Python no gateway MCP, mas não prova nada sobre
  a API principal.

---

## 16. Linguagens e dependências

| Componente | Linguagem | Dependências-chave |
|---|---|---|
| SDK Python | Python | `requests`, `httpx`, `tiktoken` (opcionais: `cohere`, `pymongo`) |
| SDK JS | TypeScript | `axios`, `https-proxy-agent`, `js-tiktoken`, `tsup` |
| MCP | TypeScript (ESM, Node 22) | `@modelcontextprotocol/sdk`, `axios`, `yargs`, `dotenv` |
| CLI | Python | `click`, `rich`, `httpx`, `psutil`, `packaging`, `tavily-python` (build `hatchling`) |
| LangChain | Python | `langchain-core`, `langchain`, `aiohttp`, `requests` (Poetry) |
| Evals | Python | `aiohttp`, `gpt_researcher`, `langchain_openai`, `pandas`, `quotientai` |
| Backend | **desconhecida (fechado)** | — |

Licença: MIT nos clientes.

---

## 17. Lições para o nosso agente Groq

1. O contrato mínimo é `POST /search` com `query`, `search_depth`, `max_results`; dá para chamar com
   `httpx` sem SDK (e sem `tiktoken`, que é pesado).
2. Use `include_answer: false` e deixe o modelo da Groq sintetizar: poupa créditos e dá controle.
3. Guarde `request_id` e `id` dos resultados para citar fontes e, se quiser, enviar `/feedback` (avise
   o usuário que isso envia a resposta final ao Tavily).
4. Trate 429/432/433 como "sem cota" e caia para um buscador gratuito (ex.: DuckDuckGo via `ddgs`).
5. Corte o contexto por tokens (como `get_search_context`) antes de mandar à Groq, por causa do limite
   de tokens do plano gratuito.
6. Para o laço de ferramentas, copie a ideia das descrições-como-prompt do LangChain/MCP, mas
   enxuta: descrição longa gasta tokens a cada chamada.
7. Para pesquisa longa, copie o padrão de `/research`: criar → polling com backoff (2 s ×1,5 até 10 s).
8. Não dependa do modo keyless: limite desconhecido, e `crawl`/`map`/`research` não funcionam nele.
9. Não envie a chave no corpo, só no header `Authorization`.

---

## 18. Lacunas

- Fontes de dados, ranking, preços exatos por endpoint e limites de taxa não estão no código.
- Não li linha a linha: `output.py`, `commands/{init,crawl,map,extract,feedback}.py`, `update.py`
  (só a detecção de instalação), `tavily_{crawl,extract,map,research}.py`, handlers de Exa/Brave/Serper/
  Perplexity/GPTR, testes e as ~220 linhas finais de `types.ts`.
- O servidor MCP remoto e o backend não foram inspecionados; só os clientes que os chamam.
- Nenhum comportamento foi verificado executando o código.
