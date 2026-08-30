# Orbi

**Camada de linguagem natural sobre ERPs.** O vendedor pergunta pelo WhatsApp, o
Orbi consulta o ERP e responde — com permissão por pessoa e registro de tudo.

```
"quanto tem de tubo pvc 100?"
→ Tubo PVC Esgoto 100mm (4471) — 37 un disponíveis
    Matriz 25 · Filial Cambé 12
```

A regra que define o sistema:

> **O LLM interpreta. O código autoriza, executa e redige.**

O LLM só escolhe uma operação de uma lista fechada. Não vê os dados, não decide
permissão e não escreve a resposta final — ela é montada por template a partir de
dado tipado.

---

## Por que existe

Os principais ERPs em nuvem já dão um assistente de WhatsApp de graça — mas para
**um usuário só**, o dono. Empresa com equipe precisa de papéis separados, e é aí
que o Orbi existe: doze vendedores, cada um com o próprio número, cada um vendo
só o que pode ver, e o dono vendo tudo que foi perguntado.

**Diferenciais:** equipe (não o dono) · permissão por papel e por campo ·
auditoria · multi-ERP · aprende a gíria do cliente · **integração por ERP, não
por cliente**.

---

## Como funciona

```
WhatsApp
→ identifica tenant, usuário e papel
→ prompt (PII mascarada, tools filtradas por papel)
→ LLM escolhe UMA tool de um conjunto fechado
→ Policy Layer autoriza (deny-by-default, antes de tocar no ERP)
→ resolve a entidade (alias → código → trigram → vetor)
→ ERP Adapter consulta pela API oficial
→ Field Policy filtra os campos
→ template monta a resposta
→ audit + tracing
```

Cinco pontos que não podem ser violados:

1. **O LLM nunca emite ID.** Só palavras — o tipo `EntityTerm` rejeita códigos.
2. **Policy Layer é deny-by-default** e roda antes de tocar no ERP.
3. **Field Policy filtra na saída do Adapter.** Vendedor não vê custo — por
   código, não por prompt.
4. **Resposta é template**, nunca redigida pelo LLM.
5. **Uma tool por turno.** Sem loop, sem agente no caminho da pergunta.

E duas honestidades que valem o produto: **na dúvida, pergunta** (nunca chuta a
entidade) e **nunca mente sobre a base do número** (disponível é disponível;
físico é declarado como físico).

---

## As quatro operações do MVP

```
check_stock(product_term, location_term?)
check_price(product_term, customer_term?, quantity?)
list_open_invoices(customer_term)
get_last_order(customer_term)
```

Não existe tool de busca de produto — a resolução de entidade é interna ao
Runtime, senão cada pergunta viraria duas rodadas de LLM.

---

## Começando

Requisitos: Docker e Python 3.13.

```bash
# 1. banco
docker compose -f docker/docker-compose.yml up -d

# 2. dependências
python -m venv .venv && .venv/bin/pip install -e ".[dev]"

# 3. configuração
cp .env.example .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# cole o resultado em ORBI_SECRET_KEY

# 4. esquema
.venv/bin/alembic upgrade head
.venv/bin/orbi init
.venv/bin/orbi doctor
```

### Com um ERP de verdade em 5 minutos

```bash
docker compose -f docker/docker-compose.odoo.yml up -d
python scripts/odoo_bootstrap.py          # base de demonstração, pela API oficial

orbi tenant add --tenant demo --name "Distribuidora Demo" --phone "+554330001111"
orbi user add --tenant demo --phone "+5543999990001" --role sales_rep --name "Carlos"
orbi erp add --tenant demo --adapter odoo \
  --credentials '{"url":"http://localhost:8069","db":"orbi","username":"admin","api_key":"admin"}'
orbi catalog sync --tenant demo
orbi maintenance recalibrate --tenant demo --force

orbi ask --tenant demo --phone "+5543999990001" "quanto tem de cimento?"
```

---

## Administração

Não existe painel: a equipe opera por linha de comando.

| Função | Comando |
|---|---|
| Cadastrar usuário | `orbi user add --tenant x --phone y --role sales_rep` |
| Trocar papel | `orbi user set-role --phone y --role finance` |
| Status do catálogo | `orbi catalog status --tenant x` |
| Forçar sync | `orbi catalog sync --tenant x` |
| Ver correções pendentes | `orbi corrections list` |
| Resolver correção | `orbi corrections resolve --id n --entity 4471` |
| **Onboarding completo** | `orbi onboard --tenant <slug>` |
| Resumo diário | `orbi maintenance daily-summary --send` |
| Integridade da auditoria | `orbi maintenance verify-audit` |

`orbi --help` lista tudo.

---

## Stack

Python 3.13 · FastAPI · Pydantic strict · PostgreSQL 17 + pgvector + pg_trgm ·
SQLAlchemy 2 · Alembic · Typer · Jinja2 · Docker.

Três provedores de LLM atrás de um `LLMPort` próprio — Gemini (primário hoje),
Anthropic e OpenAI — com failover automático entre **fabricantes diferentes**,
dentro do orçamento de tempo. Embeddings de 768
dimensões. Sem Redis, sem fila, sem worker — entram quando houver gargalo medido.

---

## Testes

```bash
pytest -q                       # 361 testes (391 com um Odoo vivo)
ruff check src tests && mypy src/orbi

ORBI_ODOO_URL=http://localhost:8069 pytest tests/conformance tests/e2e -q
```

| Suíte | O que garante |
|---|---|
| `tests/unit` | contratos, policy, templates e as proibições do projeto |
| `tests/integration` | RLS, auditoria encadeada, resolução, calibração |
| `tests/conformance` | **Adapter Conformance Kit** — todo ERP novo passa por ele |
| `tests/e2e` | o turno inteiro, inclusive contra um Odoo real |
| `tests/evals` | L1–L5; L5 (adversarial) exige 100% |

Uma fixture global mantém um **tenant canário** com linhas envenenadas: qualquer
linha dele que apareça em qualquer query quebra o build. Não é preciso lembrar de
escrever o teste de vazamento — todo teste é um.

---

## Documentação

O detalhe não cabe aqui. Cada assunto tem seu arquivo em [`docs/`](docs/):

| Arquivo | Para quê |
|---|---|
| [ORBI.md](docs/ORBI.md) | o documento essencial — o que o projeto é e por quê |
| [**ORBI-ENTENDENDO.md**](docs/ORBI-ENTENDENDO.md) | **comece por aqui** — cada peça explicada, termo por termo, e o que ficou de fora |
| [ORBI-RESUMO.md](docs/ORBI-RESUMO.md) | versão curta, para abrir sessão de desenvolvimento |
| [ORBI-ARQUITETURA.md](docs/ORBI-ARQUITETURA.md) | mapa do código: onde cada decisão mora |
| [ORBI-CONFIGURACAO.md](docs/ORBI-CONFIGURACAO.md) | cada variável do `.env`, cotas medidas do Gemini e receitas prontas |
| [ORBI-MODELOS.md](docs/ORBI-MODELOS.md) | estudo de modelos: latência medida, custo por acerto e qual usar em cada fase |
| [ORBI-CONVENCOES.md](docs/ORBI-CONVENCOES.md) | nomenclatura e **as proibições escritas como regra** |
| [ORBI-DECISOES.md](docs/ORBI-DECISOES.md) | cada decisão em três linhas: contexto, escolha, motivo |
| [ORBI-IMPLANTACAO.md](docs/ORBI-IMPLANTACAO.md) | roteiro cronometrado do go-live |
| [ORBI-TENANT.md](docs/ORBI-TENANT.md) | criar, suspender e encerrar cliente |
| [ORBI-USUARIOS.md](docs/ORBI-USUARIOS.md) | cadastro, papéis, re-verificação, LGPD |
| [ORBI-CATALOGO.md](docs/ORBI-CATALOGO.md) | sync, nome canônico, alias, limiares |
| [ORBI-ERP.md](docs/ORBI-ERP.md) | credenciais, capabilities, novo adapter |
| [ORBI-OBSERVABILIDADE.md](docs/ORBI-OBSERVABILIDADE.md) | alertas, resumo diário, evals |
| [ORBI-INCIDENTES.md](docs/ORBI-INCIDENTES.md) | o que fazer quando algo quebra |

Para entender o produto sem tê-lo construído, comece por **ORBI-ENTENDENDO.md**.
Antes de mexer no código, leia **ORBI-CONVENCOES.md** (o que não pode ser feito) e
**ORBI-DECISOES.md** (por que cada coisa é como é).

---

## Segurança, em uma tabela

| Risco | O que resolve |
|---|---|
| Cliente ver dado de outro | RLS forçado + tenant canário que quebra o CI |
| Vendedor ver custo | Field Policy na saída do Adapter, em qualquer profundidade |
| Prompt injection | saída restrita a lista fechada + resposta por template + content firewall |
| Dado indo pro exterior | CPF, CNPJ, telefone e e-mail mascarados antes do prompt |
| "Quem perguntou isso?" | auditoria append-only com hash encadeado |
| Número desconhecido | cadastro prévio obrigatório, recusa genérica, rate limit |
| Credencial do ERP vazar | cifrada na aplicação, chave fora do banco |
| ERP fora do ar | responde a falha — **nunca estoque de cache** |

---

## Trajetória

```
FASE 1  →  perguntar e responder · WhatsApp · somente leitura
           ↳ é aqui que o produto vive, cresce e amadurece

Futuro  →  Plantão · Modo Analista · novos canais
```

A Fase 1 é o produto, não um degrau. Enquanto houver cliente a conquistar e tool
a adicionar, aprofundar rende mais que abrir frente nova.

> **Não adicionar tecnologia apenas porque ela existe. Adicionar quando houver um
> problema real que justifique a complexidade.**

---

## Resumo

O Orbi é uma camada de acesso a ERPs por linguagem natural, com segurança e
auditoria **no código**, feita para equipes — não para o dono sozinho, que o
assistente nativo do próprio ERP já atende.

O LLM entende a linguagem, e só isso. O código controla autorização, execução e
redação. O pgvector identifica entidades a partir de nomes canônicos, não de
nomes crus. O Adapter traduz a operação para cada ERP, pela porta da frente, e
declara o que sabe fazer. O ERP fornece o dado atual. O PostgreSQL guarda
configuração, isolamento, auditoria, contexto, vocabulário aprendido e o índice
de resolução.

O projeto começa pequeno e deliberadamente estreito: **um canal, um ERP, quatro
operações, três papéis, nenhuma tela.** O crescimento acontece dentro da Fase 1 —
mais clientes, mais tools, mais ERPs — sempre puxado por problema real e nunca
por tecnologia disponível.

**A primeira meta continua simples: fazer uma pergunta real, consultar o ERP
correto e devolver uma resposta correta, segura, rápida e rastreável.**
