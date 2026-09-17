# ORBI — Mapa do código

Onde cada decisão mora. Para abrir sessão de desenvolvimento sem reler tudo.

---

## O caminho da pergunta

```
tests/e2e/test_turn.py  ← comece por aqui para entender o sistema

src/orbi/api/app.py               webhook da Meta: verifica assinatura, responde 200
src/orbi/api/dependencies.py      despacha o evento, responde pelo canal, trata 👍/👎
src/orbi/runtime/pipeline.py      ★ o turno inteiro, single-shot
src/orbi/identity/resolver.py     tenant pelo número de destino, usuário pelo de origem
src/orbi/llm/prompt.py            prefixo estático (cacheável) + sufixo dinâmico
src/orbi/llm/router.py            primário, fallback de outro fabricante, uma retentativa
src/orbi/policy/engine.py         deny-by-default, ordenada, decisão tipada
src/orbi/policy/roles.py          papéis próprios de cada cliente (D-040)
src/orbi/policy/field_policy.py   whitelist por (permissão, tool) — custo some aqui
src/orbi/resolution/resolver.py   ★ cascata alias → código → trigram → vetor
src/orbi/erp/gateway.py           deadline, circuit breaker, bulkhead — e nenhum cache
src/orbi/erp/adapters/odoo.py     adapter de referência (XML-RPC)
src/orbi/render/renderer.py       template Jinja a partir de dado tipado
src/orbi/audit/logger.py          append-only com hash encadeado
```

As duas estrelas são onde vive a maior parte da inteligência do produto.

## Fora do caminho da pergunta

```
src/orbi/catalog/sync.py          índice de resolução: nome canônico, re-embedding
src/orbi/resolution/calibration.py limiares por cliente, erro silencioso ≤ 1%
src/orbi/discovery/               offline: Analyst, Validator, Knowledge Model
src/orbi/evals/runner.py          L1–L5
src/orbi/cli/                     toda a administração
src/orbi/cli/trace.py             achar o turno pelo código citado (D-042)
src/orbi/observability/           métricas, canal de ops, resumo diário
```

## Fundação

```
src/orbi/core/settings.py    configuração, com validação para produção
src/orbi/core/deadline.py    orçamento único do turno
src/orbi/core/resilience.py  breaker, bulkhead, retry
src/orbi/core/crypto.py      credenciais cifradas
src/orbi/db/session.py       ★ a única porta para o banco (SET LOCAL app.tenant_id)
src/orbi/db/models.py        o esquema
src/orbi/tools/registry.py   ★ cada tool declarada uma vez, cinco artefatos
migrations/                  RLS, partições e gatilhos versionados
```

---

## Regra de dependência

```
api / cli  →  runtime  →  policy · resolution · erp · render · audit
                       →  llm · channel
core e db não importam nada de camadas acima.
erp/adapters/* só é importado pela factory em erp/registry.py.
```

`tests/unit/test_architecture.py` falha se isso for violado.

---

## Onde está cada proibição, como teste

| Proibição | Teste |
|---|---|
| P1 sem tool de busca | `test_architecture.py::test_no_entity_search_tool_can_be_registered` |
| P2 LLM não redige | `test_architecture.py::test_the_renderer_does_not_import_the_llm` |
| P3 sem cache de estoque | `test_erp_gateway.py::test_no_stock_cache` |
| P4 LLM não emite id | `test_tools.py::test_entity_term_rejects_identifiers` |
| P5 só API oficial | `test_adapter_conformance.py::test_integration_mode_is_official_api` |
| P6 só a sessão com tenant | `test_architecture.py::test_only_the_session_module_touches_the_engine` |
| P7 Field Policy no código | `test_policy.py::test_no_sensitive_field_survives_at_any_depth_for_sales_rep` |
| P8 uma tool por turno | `test_single_shot.py` (conta as chamadas reais) + `test_architecture.py::test_the_erp_result_never_goes_back_to_the_llm` |
| P9 PII mascarada | `test_llm.py::test_pii_is_masked_before_the_request_is_built` |
| P10 sem `if erp == x` | `test_architecture.py::test_no_module_branches_on_the_erp_name` |
| P11 base do estoque declarada | `test_renderer.py::test_physical_basis_is_declared_explicitly` |
| P12 somente leitura | `test_architecture.py::test_no_adapter_exposes_a_write_method` |
| P14 sem payload bruto | `test_audit.py::test_audit_never_stores_the_raw_erp_payload` |
| Isolamento entre clientes | `tests/conftest.py` (canário global) + `test_rls.py` |
| P16 sem `if tenant == x` | `test_architecture.py::test_no_module_branches_on_the_tenant` |
| P17 RLS em toda tabela de cliente | `test_rls.py::test_toda_tabela_com_tenant_id_tem_rls_forcado` |

---

## Como rodar

```bash
docker compose -f docker/docker-compose.yml up -d   # Postgres com pgvector
alembic upgrade head
pytest -q                                            # a suite inteira
ruff check src tests && mypy src/orbi

docker compose -f docker/docker-compose.odoo.yml up -d
python scripts/odoo_bootstrap.py
ORBI_ODOO_URL=http://localhost:8069 pytest tests/conformance tests/e2e -q
```
