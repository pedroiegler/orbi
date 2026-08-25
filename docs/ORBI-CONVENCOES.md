# ORBI — Convenções

Nomenclatura, estrutura e **proibições escritas como regra**. Este arquivo tem autoridade sobre
preferência pessoal e sobre o que "pareceu melhor na hora".

---

## 1. Proibições (regras, não preferências)

Cada linha abaixo é um `NÃO`. Se um código violar qualquer uma delas, o código está errado —
mesmo que funcione.

| # | Proibição | Onde o teste garante |
|---|---|---|
| P1 | **Não criar tool de busca de produto/cliente.** A resolução de entidade é interna ao Runtime. | `tests/unit/test_tool_registry.py::test_no_search_tool` |
| P2 | **Não deixar o LLM redigir a resposta final.** Toda saída passa pelo `ResultRenderer`. | `tests/unit/test_renderer.py` |
| P3 | **Não cachear estoque.** ERP fora do ar responde falha, nunca número velho. | `tests/unit/test_erp_gateway.py::test_no_stock_cache` |
| P4 | **Não permitir que o LLM emita identificadores.** `EntityTerm` rejeita. | `tests/unit/test_entity_term.py` |
| P5 | **Não acessar o banco do ERP, não instalar agente, não fazer scraping.** Só `integration_mode="official_api"`. | Conformance Kit |
| P6 | **Não usar o engine do SQLAlchemy diretamente.** Só a dependency que emite `SET LOCAL app.tenant_id`. | `tests/unit/test_import_lint.py` |
| P7 | **Não esconder campo por instrução de prompt.** Só Field Policy na saída do Adapter. | `tests/unit/test_field_policy.py` |
| P8 | **Não fazer mais de uma chamada de tool por turno.** Sem loop de agente no Runtime. | `tests/unit/test_runtime_single_shot.py` |
| P9 | **Não enviar PII ao LLM.** `PIIRedactor` antes de qualquer envio. | `tests/unit/test_pii.py` |
| P10 | **Não colocar `if erp == "x"` no Core.** Diferença de ERP vive no Adapter + `capabilities()`. | `tests/unit/test_import_lint.py` |
| P11 | **Não mentir sobre a base do número de estoque.** `basis` sempre declarado no texto. | `tests/unit/test_renderer.py` |
| P12 | **Não escrever no ERP.** MVP é somente leitura; o Protocol não tem método de escrita. | `tests/unit/test_erp_port.py` |
| P13 | **Não editar migration já aplicada.** Nova migration sempre. | revisão humana |
| P14 | **Não guardar payload bruto do ERP na auditoria.** Só hash + campos-chave. | `tests/unit/test_audit.py` |
| P15 | **Não adicionar Redis, fila, worker ou serviço novo** sem um gatilho medido (ver ORBI.md §18). | revisão humana |

---

## 2. Idioma

- **Código, nomes de tools, argumentos, classes, arquivos, tabelas, colunas, commits: inglês.**
- **Documentação, templates de resposta ao usuário final e comentários de negócio: português.**
- Sem analogia, sem metáfora: literal e técnico.

---

## 3. Estrutura de pastas

```
src/orbi/
  api/            FastAPI (webhook do canal, health)
  audit/          log append-only com hash encadeado
  catalog/        sync, nome canônico, content firewall
  channel/        ChannelPort + WhatsApp Cloud API + console (dev)
  cli/            comandos `orbi ...` (Typer)
  core/           deadline, trace, erros, cripto, resiliência
  db/             engine, sessão por tenant (RLS), modelos SQLAlchemy
  discovery/      offline: analyst, validator, knowledge model
  erp/            ErpAdapter (Protocol), DTOs, exceções, gateway, adapters/
  evals/          L1..L5, runner, datasets
  identity/       tenant + user + role a partir do canal
  llm/            LLMPort, provedores, router com failover, prompt, PII
  observability/  trace_id, métricas, Langfuse, canal de ops
  policy/         policy layer, field policy, rate limit, versão
  render/         ResultRenderer + templates Jinja
  resolution/     cascata de resolução, embeddings, calibração
  runtime/        orquestração do turno (single-shot)
  tools/          ToolSpec, registry, EntityTerm
migrations/       Alembic
tests/
  unit/ integration/ e2e/ conformance/ evals/
docker/           compose de dev (postgres+pgvector, odoo, langfuse)
docs/             ORBI-*.md
```

**Regra de dependência (uma direção só):**

```
api / cli  →  runtime  →  policy · resolution · erp · render · audit
                       →  llm · channel
core e db não importam nada de camadas acima.
erp/adapters/* não é importado pelo Core: só pela factory em erp/registry.py.
```

---

## 4. Nomenclatura

| Coisa | Padrão | Exemplo |
|---|---|---|
| Módulo/arquivo | `snake_case.py` | `field_policy.py` |
| Classe | `PascalCase` | `ResultRenderer` |
| Função/variável | `snake_case` | `resolve_entity` |
| Constante | `UPPER_SNAKE` | `DEFAULT_DEADLINE_MS` |
| Tool | `verb_noun` em inglês | `check_stock` |
| Argumento de tool que é termo humano | sufixo `_term` | `product_term` |
| Tabela | plural, `snake_case` | `entity_aliases` |
| ID do ERP | `erp_entity_id` (string) | `"4471"` |
| Enum de motivo de negação | `UPPER_SNAKE` | `TOOL_NOT_ALLOWED_FOR_ROLE` |

---

## 5. Tipos e validação

- Pydantic v2 em todas as fronteiras, com `strict=True` e `extra="forbid"`.
- Dinheiro e quantidade: `Decimal`, nunca `float`.
- Datas: `datetime` com timezone (UTC no banco, `America/Sao_Paulo` na renderização).
- Nada de `dict[str, Any]` cruzando camada — DTO tipado.

---

## 6. Testes

- **TDD:** o teste vem antes ou junto da implementação; nenhum bloco é dado como pronto sem teste.
- `tests/unit` sem I/O. `tests/integration` com Postgres real. `tests/e2e` com o turno inteiro.
- `tests/conformance` é o **Adapter Conformance Kit**: roda para todo adapter registrado.
- Fixture global de **canary tenant**: qualquer linha do tenant canário que apareça em qualquer
  query falha o build.
- Evals L1–L5 rodam no CI e bloqueiam merge em regressão.

---

## 7. Erros

- Exceções do ERP são normalizadas em `ErpTimeout`, `ErpUnavailable`, `ErpAuthError`,
  `ErpRateLimited`, `ErpNotFound`. Payload nativo nunca cruza a fronteira.
- Erro de sistema vira mensagem determinística ao usuário + alerta no canal de ops. Nunca
  stacktrace no WhatsApp.
- `reason_code` tipado em toda negação de policy.

---

## 8. Git

- Branch principal `main`; desenvolvimento em `development`.
- Commits em inglês, no imperativo, com prefixo: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`,
  `chore:`, `perf:`.
- Commits incrementais por bloco lógico. Nunca um commit gigante no final.
- Sem citar ferramentas de IA na mensagem.

---

## 9. Segredos

- Nunca no repositório. `.env` é local e está no `.gitignore`; `.env.example` documenta as chaves
  sem valores.
- Credencial de ERP é cifrada na aplicação (Fernet), chave em `ORBI_SECRET_KEY`, fora do banco.
- Log e auditoria nunca registram credencial, token ou payload bruto.
