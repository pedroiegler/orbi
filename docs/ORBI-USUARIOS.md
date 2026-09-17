# ORBI — Usuários

Cadastro, papel, vínculo de número e re-verificação.

**Cadastro prévio é obrigatório.** Número desconhecido recebe recusa genérica,
entra em rate limit agressivo e gera alerta — e nunca fica sabendo se existe
cadastro naquele número.

---

## Papéis

Todo cliente começa com três, que servem para a maioria:

```
sales_rep  → check_stock, check_price, get_last_order
finance    → list_open_invoices, get_last_order, check_price (com custo)
admin      → tudo
```

Mas **os papéis são do cliente, não do Orbi** (D-040). Cada empresa tem processo
diferente: há distribuidor onde o vendedor negocia margem e precisa ver custo, e
há onde ele não pode ver preço de tabela sem aprovação. Um papel próprio se cria
sem tocar em código e sem deploy:

```bash
# o que existe para compor um papel
orbi role capabilities

# um papel que só esse cliente tem
orbi role set --tenant construtora-silva --role gerente \
  --name "Gerente Comercial" --caps "stock:read,price:read,price:read_cost,invoice:read"

# o que vale hoje nesse cliente, e de onde cada papel vem
orbi role list --tenant construtora-silva
orbi role show --tenant construtora-silva --role gerente

# voltar ao padrão do código
orbi role reset --tenant construtora-silva --role gerente
```

### Três regras que valem conhecer antes de customizar

**A lista substitui, não soma.** `orbi role set` define o papel inteiro. Não há
herança do padrão. Herança silenciosa é como uma permissão sobrevive a uma
remoção: alguém tira `price:read_cost` e o custo continua aparecendo porque o
padrão o reintroduziu.

**Papel vazio não consulta nada.** Quem esquecer de preencher as permissões fica
sem acesso, não com acesso total. O mesmo vale para papel que ninguém criou: um
`--role` desconhecido não concede acesso, ele remove todo o acesso.

**Permissão inventada é recusada no cadastro.** Erro de digitação para alto, na
CLI, e não vira acesso.

### O que decide o custo é a permissão, não o nome do papel

O que separa `check_price` de `check_price com custo` é a Field Policy, e ela é
indexada por **permissão** (`price:read_cost`) — tanto faz se o papel se chama
`finance`, `gerente_comercial` ou `diretoria`. É isso que permite cada cliente
ter os próprios nomes sem que ninguém precise mexer no código.

Papéis próprios de um cliente **não existem** para outro. O nome do papel sozinho
já contaria como a operação do vizinho é organizada.

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

`--role` aceita tanto os três padrões quanto qualquer papel próprio do cliente.

A troca vale no próximo turno: as tools são filtradas pelas permissões do papel
**na montagem do prompt**, então o modelo nem chega a ver a tool que o novo papel
não pode chamar.

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
