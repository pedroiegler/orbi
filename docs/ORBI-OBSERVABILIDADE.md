# ORBI — Observabilidade

Como não há painel, **a observabilidade é a interface de operação**.

---

## trace_id

Um por turno, propagado por `contextvars`, compartilhado entre Langfuse e
`audit_logs`. Um problema relatado no WhatsApp vira investigação em segundos.

Spans: `channel → llm → policy → resolution → erp → render`.

Para clientes em piloto, o **modo debug** acrescenta o código curto do trace no
rodapé da resposta:

```bash
orbi tenant debug --tenant <slug> --on      # o código passa a sair no rodapé
orbi trace show T6XLNJ --tenant <slug>      # e o código encontra o turno
```

O usuário reclama citando o código e a investigação já começa pronta.

---

## Orçamento por etapa

**O que o código impõe** (`ORBI_*` no `.env`, aplicado pelo `Deadline`):

| Constante | Valor | O que é |
|---|---|---|
| `deadline_total_ms` | 10.000 | orçamento do turno inteiro; toda etapa consulta o que sobrou |
| `llm_timeout_ms` | 4.000 | teto pedido ao provedor por chamada |
| `erp_timeout_ms` | 6.000 | teto pedido ao ERP |
| `MIN_LLM_BUDGET_MS` | 300 | abaixo disso não vale tentar o fallback |
| `slow_reply_threshold_ms` | 2.500 | dispara "consultando o sistema da empresa..." |

**O perfil observado**, que é alvo e não configuração — não existe coluna para
mudar estes números:

```
channel      200 ms
llm          800 ms
policy         5 ms
resolution    60 ms
erp        500–2000 ms
render        50 ms
```

Meta de **2 a 4 segundos**, acompanhando p50 e p95. É meta de engenharia a ser
medida, não promessa. Acima de 2,5 s o Orbi manda "consultando o sistema da
empresa..." — percepção de velocidade vale tanto quanto o número, e a mensagem
ainda mantém a janela de conversa ativa.

Latência é medida **por etapa**, não só total: sem isso, "está lento" não diz se
o problema é o ERP do cliente ou o provedor de LLM.

---

## Alertas imediatos

Chegam no número interno de WhatsApp, com throttle de 10 minutos por assunto e
**sem nenhum dado de cliente**:

| Alerta | O que significa |
|---|---|
| ERP fora do ar / circuito aberto | ver `ORBI-INCIDENTES.md` |
| sync falhou ou variou mais de 30% | credencial errada ou mudança real de catálogo |
| 👎 de usuário | entrou na fila de correções |
| tenant sem atividade há 24h | risco de churn silencioso |
| número desconhecido tentando acesso | usuário novo não cadastrado, ou varredura |
| provedores de LLM indisponíveis | os dois fabricantes falharam no mesmo turno |
| corrente de auditoria quebrada | incidente de segurança |

---

## Resumo diário

```bash
orbi maintenance daily-summary --tenant <slug>     # imprime
orbi maintenance daily-summary --send              # envia ao canal de ops
```

Traz volume por cliente, taxa de ambiguidade, taxa de sem-resultado, p50 e p95,
custo do dia, usuários ativos na semana e **os cinco termos mais buscados sem
resultado**.

Essa última lista é a lista de trabalho do dia: cada termo vira um alias ou uma
correção de nome canônico.

O cron envia às 7h de São Paulo.

---

## Evals em camadas

| Camada | O que mede | Mínimo |
|---|---|---|
| L1 | seleção de tool | 90% |
| L2 | argumentos | 85% |
| L3 | resolução de entidade | 80% |
| L4 | end-to-end com ERP mockado | 90% |
| L5 | adversarial: injeção, escalada, fora de escopo | **100%** |

```bash
orbi evals --layer all --tenant <slug>
pytest tests/evals -q
```

L5 é 100% de propósito: injeção e escalada de privilégio não têm nota de corte.

Cada execução grava `prompt_version`, provedor, modelo e score em `eval_runs` —
a tabela de regressão histórica. Sem ela, comparar semanas vira opinião.

### O que roda no CI, hoje

O CI roda **em cada merge para `main`**, em PR para `main` e sob demanda — não em
push para `development`. A verificação é por entrega, não por commit (D-046).
Depois de um merge, confira o resultado em `github.com/pedroiegler/orbi/actions`:
CI vermelho manda e-mail; CI que ninguém olha não protege nada.

O build normal roda os evals com o provedor **`rule_based`** — determinístico,
sem rede, sem cota. Isso prova a lógica do turno, e **não prova o failover**.

Existe um job separado (`provedores-reais`) que avalia os dois fabricantes
contra a API de verdade, em `main` ou sob demanda. Ele roda **quando os segredos
`GEMINI_API_KEY`, `OPENAI_API_KEY` ou `ANTHROPIC_API_KEY` estiverem
configurados** no repositório; sem eles, emite um aviso e pula.

⚠️ **Enquanto esse job estiver pulando, o failover não foi avaliado contra API
real.** Failover que nunca foi exercitado degrada silenciosamente exatamente no
dia do incidente — configure os segredos antes do primeiro cliente pagante.

---

## Loop de feedback

A reação 👍/👎 chega por webhook e é gravada contra o `trace_id`. O negativo abre
uma correção:

```bash
orbi corrections list
orbi corrections resolve --id 12 --entity 4471
```

Erro de resolução vira alias. Erro de tool vira caso no eval set. Erro de dado
vira conversa com o cliente.

---

## Auditoria

```bash
orbi maintenance verify-audit                 # todos os clientes
orbi maintenance verify-audit --tenant <slug>
```

Percorre a corrente de hash e diz se alguém mexeu. Roda no cron toda segunda.

Cada linha registra `trace_id`, cliente, usuário, texto original, tool,
argumentos, entidade resolvida, decisão da policy, `policy_version_hash`,
`prompt_version`, latências por etapa, status, **hash** do payload do ERP e
apenas os campos-chave. Payload completo é passivo de LGPD sem contrapartida.

---

## Retenção

| Dado | Prazo | Como expurga |
|---|---|---|
| `audit_logs` | 12 meses | `DROP PARTITION` |
| traces (Langfuse) | 90 dias | configuração do Langfuse |
| contexto de conversa | 30 dias | `orbi maintenance purge --apply` |

```bash
orbi maintenance purge            # simulação
orbi maintenance purge --apply    # executa
orbi maintenance partitions       # garante as partições dos próximos meses
```

---

## Critério de validação do produto

- 3 clientes pagantes;
- ≥ 60% dos usuários cadastrados ativos por semana;
- ≥ 85% das perguntas resolvidas sem intervenção humana;
- p95 < 6 s;
- zero vazamento entre clientes, no CI e em produção;
- nenhum churn em 3 meses.

Os três primeiros saem do resumo diário; o quarto, das latências por etapa; o
quinto, do tenant canário no CI.
