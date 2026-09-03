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

**Os dois provedores rodam no CI.** Failover que nunca foi avaliado é failover
que degrada silenciosamente a qualidade exatamente no dia do incidente.

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
