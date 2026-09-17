# ORBI — Implantação

Roteiro cronometrado do go-live de um cliente. A meta é **2 a 4 horas** em ERP já
suportado. Implantação artesanal de três dias mata a margem antes do primeiro
problema.

---

## Antes de começar (30 min, uma vez por cliente)

| Item | Quem resolve | Sem isso |
|---|---|---|
| Credencial de leitura no ERP | cliente | não há integração |
| Número de WhatsApp Business (ver abaixo) | cliente **ou** equipe | não há canal |
| Lista de usuários: nome, número, papel | cliente | ninguém consegue perguntar |
| **20 perguntas reais escritas pelo cliente** | cliente | não há critério de aprovação |
| Warm-up do número iniciado | equipe | risco de bloqueio no go-live |

O arquivo das 20 perguntas é gerado com:

```bash
python -c "from pathlib import Path; from orbi.evals.acceptance import write_template; \
  write_template(Path('aceite-cliente.json'))"
```

O cliente preenche com as perguntas **do jeito que a equipe dele fala**. Esse
arquivo vira o critério de aprovação — e o argumento que encerra o "não me
convenceu".

### De quem é o número (D-043)

O cliente escolhe entre duas, e as duas são oferecidas de verdade.

**Opção A — o número é dele.** Ele cria (ou já tem) o Business Portfolio,
verifica o CNPJ, cadastra o número e adiciona o Orbi como parceiro. Nada muda no
ORBI: `orbi tenant set-token` grava o token e o `phone_number_id` do mesmo jeito.

- Sobe em horas **se o portfólio já estiver verificado**. Se não estiver, a
  verificação leva dias a semanas e pede documento — confirme isso *antes* de
  prometer prazo.
- Na saída, ele leva o número e a conversa. Sem migração, sem atrito.

**Opção B — o número é nosso.** Criamos e operamos.

- Sobe imediato. É o que destrava a PoC de quem ainda não tem portfólio.
- **Cada cliente hospedado fica no próprio Business Portfolio.** Não é
  preciosismo: portfólio desabilitado por violação de integridade trava *todas*
  as WABAs dentro dele. Um portfólio para todos significa que a violação de um
  derruba todos.
- ⚠️ **Isso tem teto.** A Meta limita quantos portfólios uma pessoa pode criar —
  as fontes públicas divergem entre 2 e 5, e não conseguimos confirmar na
  documentação oficial. **Confirme o seu limite no Business Manager antes de
  vender a opção B para o terceiro cliente.** Quando o teto chegar, hospedar
  deixa de ser exceção operacional e vira decisão de preço.

Registre a escolha na abertura do cliente. Ela muda o que acontece na saída dele,
e descobrir isso no cancelamento é a pior hora.

---

## Passo a passo (2 a 4 horas)

### 1. Cadastro do cliente (5 min)

```bash
orbi tenant add --tenant construtora-silva \
  --name "Construtora Silva LTDA" \
  --phone "+554330001111" \
  --phone-number-id "<phone_number_id da Cloud API>" \
  --plan time
orbi tenant set-token --tenant construtora-silva --token "<token da Cloud API>"
```

O token fica cifrado no banco (Fernet, chave em `ORBI_SECRET_KEY`).

### 2. Conexão com o ERP (10 min)

```bash
orbi erp add --tenant construtora-silva --adapter odoo \
  --credentials '{"url":"https://erp.cliente.com.br","db":"producao","username":"orbi","api_key":"<chave>"}'
orbi erp test --tenant construtora-silva
```

`orbi erp add` já consulta `capabilities()` e desliga sozinha as tools que aquele
ERP não atende. Nenhuma configuração manual.

### 3. Usuários (10 min)

CSV com `name,phone,role,location`:

```csv
name,phone,role,location
Carlos Souza,+5543999990001,sales_rep,1
Ana Lima,+5543999990002,finance,
João Silva,+5543999990003,admin,
```

```bash
orbi user import --tenant construtora-silva --file usuarios.csv
orbi user list --tenant construtora-silva
```

### 4. Onboarding completo (1 a 3 horas, quase tudo esperando)

```bash
orbi onboard --tenant construtora-silva \
  --users usuarios.csv \
  --acceptance aceite-cliente.json
```

O comando executa, em ordem:

1. valida credenciais e consulta `capabilities()`;
2. sync inicial do catálogo, com relatório;
3. importa usuários e papéis do CSV;
4. **calibra os limiares de resolução** contra o catálogo real do cliente;
5. roda o acceptance eval com as 20 perguntas dele;
6. emite o relatório de aprovação.

O relatório final é o artefato que se mostra ao cliente para dizer "está pronto".

### 5. Verificação manual (15 min)

```bash
orbi ask --tenant construtora-silva --phone "+5543999990001" "quanto tem de <produto que ele vende muito>?"
orbi ask --tenant construtora-silva --phone "+5543999990002" "a <cliente conhecido> tem títulos em aberto?"
orbi ask --tenant construtora-silva --phone "+5543999990001" "a <cliente conhecido> tem títulos em aberto?"  # precisa recusar
```

A terceira pergunta é a mais importante: o vendedor **não pode** ver título.

### 6. Piloto com código de rastreio (primeira semana)

```bash
orbi tenant debug --tenant construtora-silva --on
```

A resposta passa a trazer o código curto do trace no rodapé:

```
CIM CP-II 50KG (CIMCP2) — 575 Units disponíveis

_ref T6XLNJ_
```

O cliente cita o código, e o comando devolve o turno inteiro:

```bash
orbi trace show T6XLNJ --tenant construtora-silva
```

Quem perguntou, a pergunta literal, a tool escolhida, **qual produto foi
resolvido e por qual etapa da cascata**, a decisão da policy, a latência por
etapa e o hash do que o ERP devolveu. A busca corre dentro da sessão do cliente,
então o código de um cliente não encontra turno de outro.

Desligue ao fim do piloto.

---

## O que falha na prática, e o que fazer

| Sintoma | Causa quase sempre | Ação |
|---|---|---|
| `orbi erp test` recusa credencial | usuário do ERP sem permissão de leitura em algum módulo | pedir permissão de leitura em estoque, vendas e financeiro |
| Sync aborta com "mudaria X% do catálogo" | credencial apontando para outra base, ou API devolvendo página vazia | conferir a base antes de usar `--force` |
| Muita resposta "qual desses?" | catálogo com nomes quase idênticos | `orbi catalog abbreviations --add` e recalibrar |
| Acceptance eval falha em 2 ou 3 casos | termo que o cliente usa e o ERP não tem | vira alias: `orbi corrections resolve --entity <id>` |
| Resposta correta mas lenta (> 4s) | ERP do cliente lento | medir com `orbi ask`; se for o ERP, informar ao cliente |

---

## Warm-up do número

Número novo de WhatsApp que dispara 40 mensagens no primeiro dia é número
bloqueado. O warm-up é **etapa cronometrada do onboarding**, não surpresa da
semana do go-live:

| Dia | Volume | O que fazer |
|---|---|---|
| 1–2 | 5–10 msg/dia | equipe interna conversando pelo número |
| 3–4 | 20–30 msg/dia | dois ou três usuários do cliente |
| 5–7 | 50+ msg/dia | equipe toda |

---

## Critério de conclusão

A implantação está pronta quando **todos** valerem:

- `orbi erp test` responde;
- `orbi catalog status` mostra produtos e clientes ativos com vetor;
- limiares calibrados com erro silencioso ≤ 1%;
- as 20 perguntas do cliente passam;
- um vendedor real perguntou pelo WhatsApp e recebeu a resposta certa;
- um vendedor real tentou uma consulta financeira e foi recusado.
