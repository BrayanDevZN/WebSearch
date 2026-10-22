# WebSearch

Camada de busca na web com contrato equivalente ao do Tavily (`search`, `extract`, `map`, `crawl`),
feita só com componentes gratuitos (ddgs/DuckDuckGo, SearXNG opcional). Pensada para ser a ferramenta
de pesquisa de um agente de IA, que ainda **não** faz parte deste repositório.

> **Status: etapas 1–11 do [plano](docs/WEB_SEARCH_PLAN.md) concluídas e revisadas etapa por etapa
> com casos adversariais.** 361 testes offline passam. Os 6 testes com internet real
> (`pytest -m network`) passam, exceto que o ddgs (serviço externo, instável) pode devolver "sem
> resultados" para consultas com `site:`; nesse caso o teste é pulado, não falha.

Referência de contrato: [`TAVILY_ARCHITECTURE.md`](TAVILY_ARCHITECTURE.md).

## Uso

```bash
pip install -e ".[dev]"
cp .env.example .env     # preencha API_SEARCH_KEY
python -m websearch      # sobe em http://127.0.0.1:8000
pytest                   # testes offline; `pytest -m network` usa a internet
```

Atrás de um proxy com inspeção TLS (rede corporativa), aponte `FETCH_CA_BUNDLE` (ou `SSL_CERT_FILE`) para
o PEM das CAs confiáveis; sem isso, o download de páginas falha com `connect`.

Todas as rotas (exceto `/v1/health`) exigem `Authorization: Bearer <API_SEARCH_KEY>` ou `X-API-Key`.

```bash
curl -s localhost:8000/v1/search -H "Authorization: Bearer $API_SEARCH_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"query": "o que é BM25?", "search_depth": "advanced", "max_results": 5}'
```

## Rotas

| Rota | O que faz |
|---|---|
| `POST /v1/search` | `query`, `search_depth` (`ultra-fast`/`fast` só snippets; `basic` lê as 3 melhores páginas; `advanced` lê todas e devolve `chunks_per_source` trechos), `max_results` (1–20), `topic`, `time_range` ou `start_date`/`end_date`, `include_domains`/`exclude_domains`, `include_raw_content`, `include_images`, `include_favicon`. |
| `POST /v1/extract` | `urls` (até 20), `query` opcional (devolve só os melhores trechos), `extract_depth`, `format`. Sucesso parcial: `results` + `failed_results`. |
| `POST /v1/map` | Descobre URLs de um site (BFS): `max_depth`, `max_breadth`, `limit`, `select_paths`/`exclude_paths` (regex), `select_domains`/`exclude_domains`, `allow_external` (padrão **false**). |
| `POST /v1/crawl` | Igual ao `map`, mas devolve o conteúdo de cada página. |
| `GET /v1/health` | Sem autenticação. |

Respostas trazem `request_id` e `response_time`. Erros: `{"error": {"code", "message"}, "request_id"}` —
400/422 parâmetros inválidos (tipos estritos: `"5"` não vale como inteiro), 401 chave, 404/405 rota, 413 corpo grande (inclusive sem `Content-Length`), 429 limite (`Retry-After`), 502 nenhum provedor de busca respondeu.

Diferenças em relação ao Tavily: **sem `answer`** (não há LLM aqui; `include_answer=true` retorna 400);
o `score` vem de BM25 local, não do ranking do Tavily; o `crawl` não tem `instructions`.

## Como funciona

```
consulta → provedores (ddgs, SearXNG; backoff + circuit breaker) → filtros/dedupe
        → [basic/advanced] baixa páginas em paralelo (Fetcher seguro) → extrai texto (trafilatura/PDF)
        → divide em trechos → BM25 contra a consulta → melhores trechos + score [0,1]
```

Código em `src/websearch/`: `config.py` (.env), `net/` (validação de URL e fetcher), `extract/`, `rank/`,
`search/`, `tools/` (as quatro ferramentas), `api/` (FastAPI).

## Segurança

- **SSRF:** toda URL (e cada redirecionamento) é validada; IPs privados/loopback/metadata de nuvem
  bloqueados; a conexão vai ao IP já validado (anti DNS rebinding); sem proxies nem cookies do ambiente.
- **Recursos:** limite de bytes (após descompressão), tempo, redirecionamentos, concorrência, corpo da
  requisição (64 KB) e requisições por minuto.
- **Chave:** comparação em tempo constante; nunca aparece em log, erro ou `repr`. Docs/OpenAPI desligados.
  Requisições sem chave não consomem o limite de taxa.
- **robots.txt** respeitado no `map`/`crawl` (interpretador próprio: `*`, `$`, regra mais longa vence — igual em qualquer versão do Python).
- Chave, limite de taxa e tamanho do corpo são checados num middleware **antes** de ler/validar o corpo (sem chave: sempre 401).
- **Limitação conhecida:** regex de `select_paths` etc. vêm do cliente autenticado; têm tamanho máximo
  mas não proteção contra regex patológicas (ReDoS). Use só com clientes confiáveis.
- O limite de taxa é por processo (memória) e único para a chave; HTTPS/TLS fica a cargo de um proxy reverso.

## O que não existe (de propósito)

O agente com LLM (loop de planejar → buscar → ler → responder com citações) descrito na primeira versão
deste README não foi implementado; o plano atual constrói antes a camada de busca que ele usaria.
Essa versão antiga está no histórico do git.
