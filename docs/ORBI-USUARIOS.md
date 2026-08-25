# ORBI — Usuários

Cadastro, papel, vínculo de número e re-verificação.

**Cadastro prévio é obrigatório.** Número desconhecido recebe recusa genérica,
entra em rate limit agressivo e gera alerta — e nunca fica sabendo se existe
cadastro naquele número.

---

## Papéis

```
sales_rep  → check_stock, check_price, get_last_order
finance    → list_open_invoices, get_last_order, check_price (com custo)
admin      → tudo
```

O cliente enxerga três nomes. Internamente cada papel é um preset sobre
capabilities (`stock:read`, `price:read`, `price:read_cost`, `invoice:read`,
`customer:read`).

**O que separa `check_price` de `check_price com custo` é a Field Policy, não uma
tool diferente.** Quando um cliente pedir "meu gerente vê tudo menos custo", isso
é uma linha de configuração em `ROLE_CAPABILITIES` e na `FIELD_POLICY` — não um
deploy de emergência nem uma tool nova.

---

## Cadastrar

```bash
orbi user add --tenant construtora-silva \
  --phone "+5543999990001" --role sales_rep --name "Carlos Souza"
```

Com depósito padrão (a resposta lidera pelo depósito do próprio vendedor):

```bash
orbi user add --tenant construtora-silva \
  --phone "+5543999990004" --role sales_rep --name "Marina" --location 2
```

Em lote, a partir de CSV com `name,phone,role,location`:

```bash
orbi user import --tenant construtora-silva --file usuarios.csv
```

## Listar e trocar papel

```bash
orbi user list --tenant construtora-silva
orbi user set-role --phone "+5543999990001" --role finance
```

A troca vale no próximo turno: as tools são filtradas por papel **na montagem do
prompt**, então o modelo nem chega a ver a tool que o novo papel não pode chamar.

## Desativar

```bash
orbi user deactivate --phone "+5543999990001"
```

Usuário desativado é negado na segunda regra da Policy Layer, antes de qualquer
acesso ao ERP.

---

## Re-verificação de número

Duas situações exigem confirmar o número de novo:

- **reciclagem** — o número mudou de dono (comum no Brasil);
- **inatividade de 90 dias**.

```bash
orbi user verify --phone "+5543999990001"              # gera o código
orbi user verify --phone "+5543999990001" --code 123456  # confirma
```

O código vale 15 minutos e é guardado como hash. Enquanto não confirmar, o
usuário recebe a mensagem de re-verificação em vez da resposta.

---

## LGPD: direito ao esquecimento

```bash
orbi user erase --tenant construtora-silva --phone "+5543999990001"
```

Anonimiza o texto original das perguntas e **preserva as métricas**: atende à
LGPD sem destruir a base de evals. A corrente de hash da auditoria continua
íntegra, porque o `row_hash` não cobre o texto da mensagem.
