# ORBI — Resumo

O que interessa saber. O detalhe está em `ORBI.md`.

---

## O que é

Camada de linguagem natural sobre ERPs. O vendedor pergunta pelo WhatsApp, o Orbi consulta o ERP
e responde — com permissão por pessoa e registro de tudo.

**A regra que define o sistema:**
> O LLM interpreta. O código autoriza, executa e redige.

O LLM só escolhe uma operação de uma lista fechada. Não vê os dados, não decide permissão, não
escreve a resposta final.

---

## Para quem

Três condições, todas ao mesmo tempo:

1. **Usa ERP com interface oficial** — sem isso não dá para integrar
2. **Mais de uma pessoa consulta o ERP** — se é só o dono, o assistente nativo já resolve de graça
3. **Alguém pergunta de fora do escritório** — vendedor na rua, técnico em campo, gestor viajando

Segmento é consequência. Distribuidor e atacadista preenchem as três com folga, mas materiais de
construção, autopeças, food service e representação comercial também.

## Por que sobra espaço

Os principais ERPs em nuvem já dão um assistente de WhatsApp de graça — mas para **um usuário
só**, o dono. Empresa com equipe precisa de papéis separados, e é aí que o Orbi existe.

**Diferenciais:** equipe (não o dono) · permissão por papel e por campo · auditoria · multi-ERP ·
aprende a gíria do cliente · **integração por ERP, não por cliente**.

**Frase de venda:** "Seu ERP te deu um assistente. Ele funciona para você. O Orbi funciona para
seus 12 vendedores — cada um vendo só o que pode, e você vendo tudo que foi perguntado."

## A economia da integração

```
Sob medida:  cliente 1 = semanas · cliente 2 = semanas · cliente 10 = semanas
Orbi:        ERP 1 = semanas · cliente 2 = horas · cliente 10 = horas
```

Cliente novo em ERP já suportado: 2–4 horas, via `orbi onboard`. ERP novo: dias a semanas, é
código.

**Nunca prometa "integração automática"** — é falso. Diga: *"a integração é feita uma vez por ERP,
não uma vez por cliente."*

---

## MVP

**Só WhatsApp. Só leitura. Nenhuma tela.**

Não existe chat Web: o WhatsApp Web já é a mesma conversa no computador. Não existe painel: a
equipe configura por CLI (`orbi user add`, `orbi catalog sync`, `orbi corrections list`) e monitora
por Langfuse + um canal de alertas no WhatsApp.

Quatro tools, em inglês:

```
check_stock(product_term, location_term?)
check_price(product_term, customer_term?, quantity?)
list_open_invoices(customer_term)
get_last_order(customer_term)
```

Não existe tool de busca de produto — a resolução é interna, senão vira duas rodadas de LLM.

---

## Arquitetura — o que importa

```
WhatsApp → identifica (tenant/user/role) → LLM escolhe tool
→ Policy Layer autoriza → resolve a entidade → ERP Adapter
→ filtra campos → template monta a resposta → audit
```

Cinco pontos que não podem ser violados:

1. **O LLM nunca emite ID.** Só palavras. O tipo `EntityTerm` rejeita números e códigos.
2. **Policy Layer é deny-by-default** e roda antes de tocar no ERP.
3. **Field Policy filtra na saída do Adapter.** Vendedor não vê custo — por código, não por prompt.
4. **Resposta é template**, nunca redigida pelo LLM.
5. **Uma tool por turno.** Sem loop, sem agente no caminho da pergunta.

**Resolução de entidade** (cascata, do barato ao caro): alias aprendido → código exato → trigram →
embedding. Em dúvida, **pergunta** — nunca chuta. Toda resposta mostra qual produto usou.

**Adapter** isola cada ERP. `capabilities()` declara o que aquele ERP sabe fazer, e o Core desliga
sozinho as tools que ele não atende. Todo adapter novo só entra se passar no Conformance Kit.

---

## Discovery — o que importa

Roda **offline, uma vez por ERP**, com CrewAI. Não faz parte do caminho da pergunta.

```
Analyst (estuda a API) → Validator (marca o que é duvidoso)
→ Knowledge Model versionado → humano aprova → ativa
```

O humano revisa **só o que ficou abaixo do limiar de confiança** — minutos, não horas.

De brinde: gera as perguntas de teste do eval e o dicionário de abreviações do catálogo.

---

## Segurança — o que importa

> O LLM nunca é a autoridade de segurança.

| Risco | O que resolve |
|---|---|
| Cliente ver dado de outro | RLS + canary tenant que quebra o CI se vazar |
| Vendedor ver custo | Field Policy na saída do Adapter |
| Prompt injection | Saída restrita a lista fechada + resposta por template |
| Dado indo pro exterior | CPF/CNPJ/telefone mascarados antes do prompt |
| "Quem perguntou isso?" | Audit append-only com hash encadeado |
| Número desconhecido | Cadastro prévio obrigatório, recusa genérica |
| Credencial do ERP vazar | Cifrada na aplicação, chave fora do banco |

Três papéis: `sales_rep`, `finance`, `admin`.

**LGPD:** você é operador, o cliente é controlador. Precisa de DPA no contrato e retenção zero
contratada com o provedor de LLM.

---

## Integração com ERP

> Só interface oficial. Sem banco, sem agente no servidor, sem scraping.

O primeiro ERP de produção é escolhido por critério, não por marca: API REST documentada, base
grande de PME brasileira, módulo financeiro relevante e limites de requisição compatíveis com a
meta de latência.

ERP sem API oficial não é atendido — é critério de qualificação de cliente, não exceção a
negociar.

---

## Stack

Python · FastAPI · Pydantic strict · PostgreSQL + pgvector + pg_trgm · Alembic · CrewAI (só
Discovery) · Langfuse · WhatsApp Cloud API direta, um número por tenant · Docker · VPS em São Paulo.

**Dois provedores de LLM, de fabricantes diferentes**, atrás de um `LLMPort` próprio. O primário é
escolhido por custo por *acerto*, não por preço de token.

Sem Redis. Sem filas. Sem workers.

**Backup** em provedor diferente do da aplicação, com restore testado todo mês.

---

## Performance

Meta 2–4s. Orçamento: canal 200ms · LLM 800ms · policy 5ms · resolução 60ms · ERP 500–2000ms ·
render 50ms.

Um `Deadline` desce por todas as camadas, senão os timeouts se somam. Passou de 2,5s, manda
"consultando...".

Circuito abre depois de 5 falhas. **Nunca serve estoque em cache** — melhor não responder do que
responder errado sobre quantidade.

---

## Banco

Um PostgreSQL, RLS forçado. **Toda tabela que guarda dado de um cliente tem
`tenant_id` e política de RLS.** São globais de propósito, e por isso sem
`tenant_id`: `tenants`, `roles`, `capabilities`, `tools`, `role_tools`,
`role_capabilities` (o catálogo do produto, igual para todos), `eval_runs` e
`rate_limit_counters` (dados de operação, escritos só pela CLI e pelo Runtime,
sem caminho de leitura por cliente).

As que costumam ser esquecidas: `erp_connections` (credenciais cifradas), `entity_aliases` (a
gíria aprendida), `user_identities`, `pending_resolutions`, `catalog_sync_runs`, `tenant_settings`.

O contexto guarda **slots resolvidos**, não só mensagens — é o que faz "e o preço dele?" funcionar.

Catálogo: não vetoriza nome cru. `"TB PVC ESG 100MM"` vira `"tubo pvc esgoto 100 mm"` antes de
gerar o embedding. Re-embedda só o que mudou de nome.

---

## Observabilidade (é a interface de operação)

`trace_id` único, compartilhado entre Langfuse e audit.

**Alertas no WhatsApp de ops:** ERP caiu, circuito abriu, sync falhou, 👎 de usuário, tenant
inativo 24h.

**Resumo diário:** volume, taxa de ambiguidade, p95, custo, e os 5 termos mais buscados sem
resultado — essa lista é o trabalho do dia.

**Evals em 5 camadas** rodando no CI, bloqueando merge em regressão.

---

## Documentação (obrigatória para vibe coding)

Prefixo `ORBI-` em tudo. Os dois que mais importam:

- **`ORBI-CONVENCOES.md`** — proibições escritas como regra. Sem isso o modelo cria tool de busca,
  deixa o LLM redigir e cacheia estoque.
- **`ORBI-DECISOES.md`** — cada decisão em 3 linhas: contexto, escolha, motivo. Evita rediscutir a
  mesma coisa toda semana.

---

## Trajetória

```
FASE 1  →  perguntar e responder · WhatsApp · leitura
           ↳ é aqui que o produto vive, cresce e amadurece

Futuro  →  Plantão · Analista · novos canais
```

**A Fase 1 é o produto, não um degrau.** Enquanto houver cliente a conquistar e tool a adicionar,
aprofundar rende mais que abrir frente nova. Em ordem: mais clientes no mesmo ERP (custo marginal
quase zero) · mais tools puxadas pelo uso real · segundo ERP · melhor resolução de entidade · novos
domínios.

**Plantão só entra quando:** Fase 1 estável com 8–10 tools · 10+ clientes pagando · churn perto de
zero · clientes pedindo aviso proativo · custo de mensagem proativa conhecido.

> **Agentes ganham tempo, nunca privilégio.** Sempre com identidade de pessoa real, sob a mesma
> Policy Layer.

Futuro: Slack, Telegram, Discord · Console interno · painel self-service · webhooks · MCP · novos
ERPs.

**Fora do horizonte:** escrita no ERP. Leitura errada gera uma pergunta a mais; escrita errada gera
um pedido errado no sistema do cliente.

---

## Plano de execução

| Etapa | Duração | Custo/mês | Resultado |
|---|---|---|---|
| 1. Odoo + fundação | 2–3 sem | R$ 200–400 | Runtime completo contra ERP de teste |
| 2. Adapter do 1º ERP | 1–2 sem | R$ 200–400 | Passa no Conformance Kit |
| 3. PoC com cliente | 3 sem | R$ 200–370 | 20 casos escritos por ele, passando |
| 4. Primeiro contrato | — | R$ 250–470 | R$ 350/mês, preço de fundador |
| 5. Do 2º em diante | 4h cada | +R$ 120–250 | R$ 890/mês, preço cheio |

**Reserva:** R$ 2.500–4.000 cobre 8 meses de infraestrutura.

### Odoo antes do cliente

Odoo Community em Docker com os dados de demo — **2 a 3 dias, não mais**. Não se constrói um ERP.

O `OdooAdapter` fica permanente: é contra ele que o Conformance Kit roda no CI. E como a API do
Odoo é XML-RPC e a dos ERPs em nuvem é REST, construir os dois testa a abstração **antes** do
primeiro cliente, quando corrigir ainda é barato.

### PoC gratuita — 4 regras

1. Prazo fixo de 3 semanas
2. Escopo fechado: 3 usuários, 4 tools, 1 ERP
3. **O cliente escreve as 20 perguntas** que quer ver funcionando, antes de começar
4. Carta de intenção assinada: se passar, contrata por 12 meses a R$ X

Sem a regra 4, você entrega 3 semanas de valor e ouve "vou pensar".

---

## Custo e preço

**Infra por fase:** R$ 200–400 (dev) · R$ 250–470 (1 cliente) · R$ 1.200–2.500 (10 clientes).
**Custo por cliente em escala: R$ 120–250/mês.**

**O número é do cliente** — melhor para o bolso e para a LGPD.

⚠️ **Atenção:** até setembro de 2026 as respostas dentro da janela de 24h são gratuitas. **A
partir de outubro de 2026 a Meta volta a cobrá-las**, à tarifa de template de utilidade. As tarifas
do Brasil saem até setembro — o custo por cliente precisa ser recalculado então. Isso dá mais peso
ao teto de consultas por plano e aos canais que não cobram por mensagem (Telegram, Slack).

| Plano | Usuários | Mensal |
|---|---|---|
| Essencial | até 5 | R$ 490 |
| Time | até 15 | R$ 890 |
| Operação | até 30 | R$ 1.490 |

Cobra-se por usuário, não por consulta — o custo real é suporte, não token. Mas com teto de
consultas, para um cliente entusiasmado não dobrar a conta de LLM.

**Margem:** 1 cliente fundador fica no zero · 1 cliente pagante já dá 47–72% · 10 clientes dão
67–84%. O segundo cliente a preço cheio já deixa a operação no azul.

**Gatilhos de gasto — nada sobe por antecipação:** VPS maior só se o p95 passar de 6s por CPU ·
Postgres gerenciado só acima de 50 GB · Redis só se o contexto virar gargalo medido · Console só
se a CLI custar mais de 4h/mês.

---

## Como conseguir clientes

O Google Maps dá a lista bruta, mas não mostra qual ERP a empresa usa — que é a condição nº 1.
Qualifique por outro caminho:

1. **Contador local** — enxerga o ERP de dezenas de empresas. Comissão de 10–15% do primeiro ano
2. Parceiros e integradores dos ERPs em nuvem — já vendem, travam onde você resolve
3. Associação comercial e sindicatos do atacado
4. LinkedIn por cargo: gerente e supervisor comercial
5. Prospecção pelo próprio WhatsApp — é demonstração, não coincidência

**A oferta que abre porta:**
> "Me dá acesso de leitura por uma semana. Em 4 horas seus 3 vendedores estão perguntando pelo
> WhatsApp. Se não servir, desligo e não custou nada."

---

## Os 8 princípios que resolvem 90% das dúvidas

1. LLM interpreta; código autoriza, executa e redige
2. LLM nunca emite identificador
3. Resposta é template, não texto gerado
4. Na dúvida, pergunta — nunca chuta
5. Segurança não depende de prompt
6. Só interface oficial de ERP
7. Agentes ganham tempo, nunca privilégio
8. Não adicionar tecnologia sem problema real

---

## Como saber que deu certo

3 clientes pagando · 60% dos usuários ativos por semana · 85% das perguntas resolvidas sozinhas ·
p95 abaixo de 6s · zero vazamento entre clientes · nenhum churn em 3 meses.

---

## Ainda em aberto

Não são decisões — são coisas a medir: **tarifa de mensagem de serviço do WhatsApp (imediato)** ·
limites da API do 1º ERP · retenção zero no provedor de LLM · bake-off dos modelos · limiares
calibrados no 1º cliente · p95 real · DPA com advogado.

Adiados por escolha: **preço** e **primeiro cliente**.
