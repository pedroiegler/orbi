# ORBI — Incidentes

O que fazer quando algo quebra. Cada seção tem sintoma, diagnóstico e ação.

**A regra que vale para todo incidente:** é melhor não responder do que responder
errado. Nenhum procedimento aqui autoriza servir número de estoque desatualizado.

---

## Alerta: "circuito aberto"

**O que significa:** cinco falhas em 30 segundos naquela operação daquele ERP. O
Orbi parou de tentar por 60 segundos e responde honestamente ao usuário.

**Diagnóstico:**

```bash
orbi erp test --tenant <slug>          # o ERP responde?
orbi maintenance daily-summary --tenant <slug>
```

**Ação:**

1. ERP do cliente fora do ar → avisar o cliente; o circuito fecha sozinho.
2. ERP respondendo mas lento → medir com `orbi ask` e verificar limites de
   requisição da API do cliente.
3. Credencial expirada → `orbi erp add` com a nova credencial.
4. Depois de resolver, `orbi erp reset-breaker` fecha o circuito na hora.

**O que não fazer:** aumentar o timeout do ERP para "parar de dar erro". O
orçamento de 10 s do turno existe para o usuário não ficar esperando.

---

## Alerta: "sync abortado"

**O que significa:** o sync aplicaria mudança em mais de 30% do catálogo e parou.
Isso é proteção, não falha.

**Diagnóstico:**

```bash
orbi catalog status --tenant <slug>
```

Compare o total do índice com o total no ERP. Quase sempre é uma destas três:

- credencial apontando para outra base do ERP;
- API devolvendo página vazia (filtro ou permissão);
- o cliente realmente renomeou ou trocou o catálogo.

**Ação:** confirme a causa **antes** de forçar. Sendo mudança legítima:

```bash
orbi catalog sync --tenant <slug> --force
orbi maintenance recalibrate --tenant <slug> --force
```

---

## Alerta: "provedores de LLM indisponíveis"

**O que significa:** primário e fallback falharam no mesmo turno.

**Diagnóstico:** verifique a página de status dos dois fabricantes. Se só um caiu,
o failover deveria ter funcionado — confira `ORBI_LLM_FALLBACK` no `.env` e rode
`orbi doctor`.

**Ação:**

1. Fallback ausente ou do mesmo fabricante do primário: corrigir e reiniciar.
2. Os dois fora do ar: o Orbi responde falha e alerta. Nada a fazer além de
   avisar os clientes em piloto.
3. Chave revogada: trocar a chave e reiniciar.

**O que não fazer:** apontar primário e fallback para o mesmo fabricante. A queda
seria correlacionada, e o failover viraria enfeite (D-011).

---

## Alerta: "número desconhecido tentando acesso"

**O que significa:** alguém mandou mensagem para o número de um cliente sem estar
cadastrado. A resposta foi genérica e nada foi revelado.

**Ação:**

1. Um alerta isolado costuma ser usuário novo que ninguém cadastrou →
   `orbi user add`.
2. Vários alertas do mesmo número em minutos → é varredura. O rate limit já
   silenciou; nada a fazer.
3. Vários números diferentes no mesmo cliente → avisar o cliente: o número dele
   vazou em algum lugar.

---

## Suspeita de vazamento entre clientes

**Trate como incidente grave.** Sintoma típico: usuário relata ter visto produto
ou cliente que não é da empresa dele.

**Diagnóstico, nesta ordem:**

```bash
orbi maintenance verify-audit                    # a auditoria está íntegra?
pytest tests/integration/test_rls.py -q          # o RLS ainda vale?
pytest tests/unit/test_architecture.py -q        # alguém usou o engine direto?
```

O tenant canário e a fixture global do CI detectam vazamento antes do merge; se
os três passarem, quase sempre o relato é outra coisa — dois clientes com nome de
produto parecido, ou usuário cadastrado no tenant errado:

```bash
orbi user list --tenant <slug>
```

**Se houver vazamento real:** suspenda os tenants afetados
(`orbi tenant deactivate`), preserve `audit_logs` (é append-only, não apague nada)
e reconstitua o caminho pelo `trace_id` relatado.

---

## Resposta errada (usuário marcou 👎)

Não é incidente de sistema; é a matéria-prima do produto.

```bash
orbi corrections list
```

Classifique conforme a causa:

| Causa | Comando | Efeito |
|---|---|---|
| Entidade errada | `orbi corrections resolve --id N --entity <erp_id>` | o termo vira alias do cliente |
| Tool errada | `orbi corrections resolve --id N --kind tool` | acrescente o caso em `src/orbi/evals/datasets/` |
| Dado errado no ERP | `orbi corrections resolve --id N --kind erp_data` | conversa com o cliente |
| Expectativa incorreta | `orbi corrections discard --id N` | nada muda no sistema |

---

## Banco fora do ar

**Sintoma:** `/health` devolve 503 e nenhuma pergunta é respondida.

**Ação:**

1. `docker compose -f docker/docker-compose.app.yml ps`
2. Se o Postgres não sobe, olhe o log antes de qualquer restauração: quase sempre
   é disco cheio.
3. Perda de dados confirmada: restaure pelo procedimento testado
   (`scripts/restore_test.sh` prova mensalmente que ele funciona). **RPO 15 min,
   RTO 4h.**

---

## Corrente de auditoria quebrada

**O que significa:** alguém alterou `audit_logs` por fora da aplicação. A
aplicação não tem permissão de `UPDATE` nem `DELETE`, e o gatilho do banco recusa
qualquer campo coberto pela corrente — só um acesso privilegiado consegue.

**Ação:** trate como incidente de segurança. Identifique o ponto pelo
`orbi maintenance verify-audit`, preserve a partição do mês e investigue quem tem
acesso de superusuário ao banco.

---

## Contatos e escalada

| Situação | Prazo |
|---|---|
| ERP do cliente fora do ar | avisar o cliente no mesmo dia |
| LLM fora do ar | avisar clientes em piloto |
| Suspeita de vazamento | imediato, com suspensão preventiva |
| Corrente de auditoria quebrada | imediato |
| 👎 de usuário | próximo dia útil |
