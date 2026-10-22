# Plano: ferramenta de web search (clone funcional do Tavily)

Objetivo: construir **antes do agente** a camada de busca na web que o agente vai usar. Ela imita o
contrato do Tavily (`search`, `extract`, `map`, `crawl`) usando só componentes gratuitos, sem
depender da API do Tavily. Referência de contrato: [`TAVILY_ARCHITECTURE.md`](../TAVILY_ARCHITECTURE.md).

## 1. Como o Tavily funciona (o que se sabe e o que não se sabe)

**Documentado publicamente** (docs e central de ajuda do Tavily):
- `/search` agrega até 20 sites por chamada, filtra e ranqueia as fontes e devolve trechos densos.
- Retorna `content` (trecho relevante), opcionalmente `raw_content` (página inteira), `score`,
  `published_date`, `favicon`, imagens e, se pedido, uma `answer` sintetizada por LLM.
- `/extract` baixa e limpa páginas de URLs que você já tem; `/map` e `/crawl` descobrem e (no crawl)
  extraem páginas de um site.
- `search_depth`: `ultra-fast`/`fast` priorizam latência, `basic` é o padrão, `advanced` faz mais
  trabalho por resultado e devolve vários trechos por fonte (`chunks_per_source`).

**Não documentado** (por isso é projeto nosso, não cópia): de onde vêm os resultados, o modelo de
ranking, o tamanho dos trechos, o cache. Não vou afirmar nada disso sobre o Tavily.

**Pipeline que vamos implementar** (equivalente funcional, escolhido por nós):

```
consulta ─► provedor(es) de busca (DuckDuckGo/ddgs, SearXNG) ─► dedupe + filtros de domínio/data
         ─► [basic] snippet do provedor, enriquecido com a página dos melhores resultados
            [advanced] baixa as páginas (paralelo, seguro) ─► extrai texto principal
                       ─► divide em trechos ─► ranqueia por BM25 contra a consulta
                       ─► content = melhores `chunks_per_source` trechos, score normalizado
         ─► resposta no formato Tavily {query, results[], response_time, request_id}
```

Pontos de projeto herdados da leitura dos clientes do Tavily:
- parâmetros opcionais ausentes = não enviados; regras como "`start_date` + `time_range` conflitam";
- `extract` devolve `results` e `failed_results` separados;
- `map`/`crawl` com `max_depth`, `max_breadth`, `limit`, `select_paths`/`exclude_paths` (regex),
  `select_domains`/`exclude_domains`, `allow_external`;
- erros padronizados e `request_id` em tudo.

**Fontes dos resultados:** `ddgs` (metabuscador gratuito: DuckDuckGo, Bing, Brave, Google…; pode sofrer
limite de taxa, então há reserva e *backoff*) e SearXNG (opcional, auto-hospedado).

## 2. Etapas

Cada etapa termina com: testes em `tests/` passando → commit na `main` → tentativa de push.

| # | Etapa | Entrega | Testes |
|---|---|---|---|
| 1 | Scaffold e configuração | `pyproject.toml`, pacote `websearch`, `config.py` com a validação do `.env`, pytest | validação do `.env` |
| 2 | Segurança de URL (SSRF) | `net/urlsafe.py`: normalização, esquemas/portas, bloqueio de IPs internos, DNS injetável | IPs privados, esquemas, IDN, userinfo |
| 3 | Fetcher HTTP seguro | `net/fetcher.py`: limite de tamanho/tempo, redirecionamentos revalidados, charset | `MockTransport`, redirecionamento para interno |
| 4 | Extração de conteúdo | `extract/html.py`: título, texto principal (markdown/texto), data, links, imagens, favicon | HTML de exemplo |
| 5 | Trechos e ranking | `extract/chunk.py`, `rank/bm25.py`: divisão, BM25, seleção dos melhores trechos | ordenação, trechos, scores |
| 6 | Provedores de busca | `search/base.py`, `ddg.py`, `searxng.py`, `fallback.py`: interface, filtros, dedupe, *backoff* | provedores falsos, filtros |
| 7 | Pipeline `search` | `tools/search.py`: profundidades, formato Tavily, paralelismo, orçamento | pipeline completo com falsos |
| 8 | `extract` | `tools/extract.py`: várias URLs, `failed_results`, `query`, `chunks_per_source` | sucesso/falha parcial |
| 9 | `map` e `crawl` | `tools/map.py`, `tools/crawl.py`: BFS, regex, robots.txt, limites | site falso em memória |
| 10 | API HTTP + chave | FastAPI: `/v1/search|extract|map|crawl|health`, `API_SEARCH_KEY` obrigatória | autenticação e contrato |
| 11 | Fechamento | teste real opcional (rede), README/status, revisão de segurança | marcado `@network` |

## 3. Convenções

- Código em `src/websearch/`; testes em `tests/` na raiz, espelhando a estrutura.
- Rede real só em testes marcados `network` (desligados por padrão).
- DNS, relógio e transporte HTTP são injetáveis para teste determinístico.
- Nada de segredo em log nem em exceção.
