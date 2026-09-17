# ORBI — A oferta

O que vender, por quanto, e o que dizer. Feito para você abrir na frente do
cliente, não para arquivar.

Tudo aqui é **cumprido pelo código**: o plano define o teto de consultas e o
limite de usuários, e o sistema recusa o sexto usuário num plano de cinco. Não
existe promessa comercial que o software não implemente.

---

# Os planos

| Plano | Usuários | Consultas/mês | Mensal |
|---|---|---|---|
| **Essencial** | até 5 | 3.000 | R$ 490 |
| **Time** | até 15 | 10.000 | R$ 890 |
| **Operação** | até 30 | 20.000 | R$ 1.490 |
| *Fundador* | até 15 | 10.000 | *R$ 350* |

Setup de implantação: R$ 1.500 a 2.500 — **dispensável nos primeiros clientes**
em troca de depoimento.

## Por que só duas coisas mudam entre os planos

**Número de usuários** e **teto de consultas**. Nada mais.

Não muda o modelo de IA, não muda a velocidade, não muda a segurança. Três
razões, e todas resistem a pergunta de cliente:

1. **O cliente consegue verificar.** Ele conta quantos vendedores tem. Ele não
   tem como conferir se está recebendo uma "IA melhor" — e vender o que o
   comprador não pode auditar é como esse tipo de produto perde confiança.
2. **Reflete o custo real.** O que cresce com o tamanho do cliente é **suporte**,
   não token. Dez vendedores geram mais dúvida, mais correção de vocabulário e
   mais conversa que três. O LLM custa entre R$ 2 e R$ 29 por cliente/mês
   (medido — veja [ORBI-MODELOS.md](ORBI-MODELOS.md)).
3. **Mantém o produto sustentável.** Um modelo para todos significa um conjunto
   de evals, um comportamento a depurar, um bug reproduzido uma vez para todos.

## O teto de consultas não é para punir

3.000 consultas para 5 usuários dá **600 por pessoa por mês** — cerca de 27 por
dia útil. Um vendedor ativo faz umas 15. O teto está no **dobro do uso esperado**,
de propósito: ele existe para conter uso claramente anormal, não para o cliente
esbarrar no dia 20.

Se um cliente encostar no teto de forma consistente, isso não é problema — é
**sinal de venda**: ele está usando muito, e é hora de subir de plano.

⚠️ **A partir de 1º de outubro de 2026**, a Meta volta a cobrar as respostas
dentro da janela de 24 horas. Aí o teto deixa de ser só proteção contra abuso e
passa a ser **controle de margem** — cada consulta terá custo de mensagem. O
valor e a conta estão em "Onde a margem fica", no fim deste documento.

---

# Para quem vender, nesta ordem (D-045)

## Agora: distribuidor e atacado

O cliente do MVP tem quatro marcas, e todas importam:

| Marca | Por quê |
|---|---|
| **ERP com API oficial** | sem isso não há integração, e ler o banco direto é proibido |
| **5 a 30 vendedores** que não têm acesso ao ERP | são eles que hoje ligam para o escritório perguntar estoque |
| **Catálogo com nome abreviado e inconsistente** | é onde a resolução de entidade vale mais que uma busca comum |
| **Custo e margem restritos a quem decide preço** | é onde a Field Policy vira diferencial em vez de detalhe |

Material de construção, elétrica, hidráulica, autopeças, embalagens, alimentos.
O vocabulário do produto já está calibrado para isso — "cimento", "tubo PVC",
"saco", "metro" — e é esse acúmulo que fica difícil de copiar.

**Vá fundo aqui até o terceiro cliente pagante.** É quando o `orbi onboard` passa
a rodar em horas de verdade e o vocabulário aprendido começa a valer dinheiro.

## Depois: rastreamento e logística

A primeira expansão **não é um mercado novo — é o mesmo cliente comprando a
segunda coisa.** Distribuidor tem frota ou transportadora, e "onde está a carga
4471?" tem exatamente a forma de "quanto tem de cimento?": entidade resolvida,
fato consultado, campo sensível por papel, erro visível.

## O Orbi está preso a ERP?

**Não.** Medindo o código: cerca de **12% está preso ao domínio** (as tools, os
DTOs, o adapter do Odoo, a lista de campos, os templates, os datasets de eval).
Os outros ~88% — canal, identidade, Policy Layer, papéis, resolução, auditoria,
LLM, deadline, RLS, observabilidade — servem a qualquer sistema de registro.

Mas **o código é a parte barata**. O que custa num ramo novo é saber quais são as
quatro perguntas certas, o adapter de referência, os evals, o vocabulário
acumulado e um cliente que dê credibilidade. É 12% do código e praticamente 100%
do conhecimento de mercado.

A razão que decide:

```
2º cliente, mesmo ramo e mesmo ERP   →  horas
2º cliente, ERP diferente            →  dias  (um adapter + Conformance Kit)
1º cliente de um ramo novo           →  semanas, e sem referência nenhuma
```

Enquanto a linha de cima não estiver provada com dinheiro, mudar de direção é
caro. A opcionalidade já está guardada na arquitetura — o erro seria **gastá-la
antes do primeiro cliente**.

## O filtro para qualquer ramo novo

Um ramo serve quando **as seis** valem. Menos que seis, recuse:

| # | Condição | O que quebra se faltar |
|---|---|---|
| 1 | Existe **API oficial** do sistema de registro | não há adapter — ler banco direto é proibido |
| 2 | As perguntas **se repetem** | o vocabulário não acumula e o ativo durável não existe |
| 3 | A resposta é um **fato**, não um julgamento | o template não serve, e o LLM teria que redigir |
| 4 | Quem pergunta **não tem acesso** ao sistema | ele consulta sozinho — não há produto |
| 5 | Há **campos sensíveis por papel** | a Field Policy, que é o diferencial, não vale nada |
| 6 | Erro é **visível e barato** | o custo do erro mata a confiança antes de ela nascer |

### Os ramos já avaliados

| Ramo | Veredito | Onde trava |
|---|---|---|
| **Distribuidor / atacado** | ✅ o MVP | — |
| **Rastreamento / logística** | ✅ a primeira expansão | — |
| **Empréstimo / crédito** | ⚠️ risco alto | **item 4**: quem pergunta costuma ser o próprio devedor, e aí é B2C — o cadastro prévio obrigatório, que é a base da segurança, não escala para milhares de tomadores. E **item 6**: errar saldo devedor não é ticket de suporte, é problema jurídico |
| **Seguros** | ❌ recusar | **item 3**: cobertura de apólice é interpretação de contrato, não campo de banco. Responder exigiria o LLM redigir — a única linha que o produto não cruza. Atender seguros exigiria desmontar o que o diferencia |

O detalhe que vale guardar: seguros é um **não técnico**, não um não de mercado.
O mercado é grande; a arquitetura é que não serve, e mudá-la custaria o produto.

---

# O primeiro cliente: a oferta de fundador

Não venda o plano cheio para o primeiro. Venda isto:

> **Plano Fundador — R$ 350/mês por 12 meses**
>
> Você recebe o mesmo produto do plano Time (até 15 usuários). Em troca:
> ser referência e dar um depoimento quando estiver funcionando.
>
> Antes de assinar: **PoC gratuita de 3 semanas**, com escopo fechado —
> 3 usuários, as 4 consultas, 1 ERP.

## As quatro regras da PoC, e por que cada uma existe

**1. Prazo fixo de 3 semanas, escrito antes de começar.**
Sem isso vira piloto eterno, e piloto eterno vira usuário que nunca paga.

**2. Escopo fechado: 3 usuários, 4 consultas, 1 ERP.**
Pedido fora disso entra na lista do plano pago. Isso não é rigidez — é o que
impede a PoC de virar desenvolvimento sob medida de graça.

**3. O cliente escreve as 20 perguntas que quer ver funcionando, antes de começar.**
Esta é a mais importante. Elas viram o critério de aprovação automático
(`orbi onboard --acceptance`). Se passarem, ele não tem argumento para dizer "não
me convenceu" — **o critério foi dele**.

**4. Carta de intenção assinada.**
Uma página: se os 20 casos passarem, contrato de 12 meses a R$ 350. Sem isso, você
entrega três semanas de valor e ouve "vou pensar".

## Por que R$ 350 e não de graça

Cliente que paga pouco continua sendo cliente. Cliente que paga zero é usuário —
e usuário não cancela, ele só some. Você descobre pelo silêncio, tarde demais.

Com R$ 350 ele fica no zero a zero para você (o custo é R$ 250 a 470). Não é para
ganhar dinheiro: é para provar que existe alguém disposto a pagar.

---

# É normal limitar usuários?

Você perguntou se isso é praxe. É — e é o padrão dominante no setor.

Salesforce cobra de **US$ 25 (Starter) a 350 (Unlimited)** por usuário/mês, e
US$ 550 no Agentforce. Jira, Slack e Figma cobram por pessoa do mesmo jeito.
Limitar usuários não é invenção sua — é o formato padrão do setor.

Sobre o **teto de uso**, seja preciso ao citar, porque o cliente pode conferir no
celular durante a reunião:

- **37% das empresas de SaaS B2B usam precificação híbrida** (mensalidade fixa
  **mais** cobrança por consumo);
- **42% oferecem alguma forma de cobrança por uso**, contra 27% em 2023;
- **53% ainda monetizam só por assinatura.**

O Orbi fica entre os dois: assinatura fixa com um **teto** de uso, sem cobrança
por excedente. É mais próximo do modelo de assinatura tradicional que do híbrido
— então **não diga "37% do mercado usa o nosso modelo"**. Diga o que é verdade e
serve melhor: *"cobramos assinatura fixa com um limite generoso, para que sua
conta seja previsível — sem a fatura variável que o modelo por consumo traz."*

Um detalhe de contexto que vale confirmar antes de citar: cliente brasileiro paga
em média cerca de 12% menos que o americano pelo mesmo software. Os preços acima
já estão calibrados para o Brasil.

*(Preços de terceiros e participações de mercado conferidos em 03/09/2026.
Reconfira antes de usar em proposta — tabela de concorrente muda sem aviso.)*

## O que o cliente vai perguntar, e o que responder

**"E se eu tiver 16 vendedores?"**
Sobe para Operação, ou tira alguém que não usa. O sistema conta usuários **ativos**
— desativar quem saiu da empresa libera a vaga.

**"Posso pagar por consulta em vez de por usuário?"**
Não, e por um motivo que favorece você: por consulta, seu custo fica imprevisível
e você acaba evitando usar. Por usuário, você usa à vontade dentro do teto.

**"Por que não tem plano ilimitado?"**
Porque ilimitado é um convite a um único cliente entusiasmado dobrar a conta de
todo mundo. O teto é o dobro do uso esperado — se você encostar nele, a gente
conversa, e provavelmente é hora de subir de plano.

**"O que acontece se eu estourar o teto?"**
As consultas param até o mês virar, e você recebe o aviso antes. Na prática nunca
acontece: o teto é o dobro do uso normal.

---

# A frase que abre a conversa

> "Seu ERP te deu um assistente. Ele funciona para você. O Orbi funciona para
> seus 12 vendedores — cada um vendo só o que pode, e você vendo tudo que foi
> perguntado."

E a oferta que abre a porta:

> "Me dá acesso de leitura ao seu ERP por uma semana. Em 4 horas seus 3
> vendedores estão perguntando estoque e preço pelo WhatsApp. Se não servir,
> desligo e não custou nada."

Funciona porque é verdade: `orbi onboard` roda em horas.

## A justificativa de valor

Um distribuidor com 10 vendedores que economizam 20 minutos por dia deixando de
ligar para o escritório recupera **mais de 60 horas por mês**. Cobrar R$ 890 por
isso é conversa fácil.

---

# O que **não** dizer

**Nunca prometa "integração automática".** É falso e quebra na primeira semana.
A formulação correta:

> "A integração é feita uma vez por ERP, não uma vez por cliente. Do segundo
> cliente em diante, colocar no ar leva horas."

**Nunca prometa que a IA nunca erra.** O que o Orbi promete é diferente e melhor:
quando está em dúvida, ele **pergunta** em vez de chutar. E toda resposta mostra
qual produto foi usado, então erro vira visível e corrigível.

**Nunca diga que o estoque é em tempo real sem qualificar.** O Orbi consulta o ERP
a cada pergunta — não há cache. Mas se o ERP do cliente estiver desatualizado, a
resposta reflete isso. A fonte da verdade continua sendo o ERP dele.

**Nunca omita o que o número de WhatsApp custa.** A partir de outubro de 2026 as
mensagens dentro da janela de 24 h voltam a ter custo. Dizer isso na venda evita
a conversa ruim em novembro.

### As duas origens de número (D-043)

O cliente escolhe, e as duas são oferecidas de verdade:

| | **Número dele** | **Número nosso** |
|---|---|---|
| Como | ele verifica o CNPJ no Business Portfolio e libera nosso acesso | criamos e operamos para ele |
| Prazo para subir | horas, **se** o portfólio já estiver verificado | imediato |
| Se não estiver verificado | dias a semanas, com documento | — |
| Na saída | ele leva o número e a conversa | precisa migrar |
| Risco de portfólio | dele | **nosso** |

A frase para a venda: *"Você prefere que o número fique na sua conta da Meta ou
na nossa? Na sua, você tem o controle e leva o número se um dia sair. Na nossa,
sobe hoje."*

O que **não** dizer: que tanto faz. Portfólio da Meta desabilitado trava todas as
WABAs dentro dele — então cada cliente que hospedarmos deve ficar em portfólio
separado, e essa é uma conta que cresce. Quando hospedar deixar de ser exceção,
ela precisa de preço próprio.

---

# Os comandos que sustentam a oferta

```bash
# criar cliente já com o plano e o teto certos
orbi tenant add --tenant construtora-silva --name "Construtora Silva LTDA" \
  --phone "+554330001111" --plan fundador

# o limite de usuários é cumprido: o sexto num plano de cinco é recusado
orbi user add --tenant construtora-silva --phone "+5543999990001" --role sales_rep

# subir de plano quando ele crescer
orbi tenant set-plan --tenant construtora-silva --plan time

# ver consumo contra o limite
orbi tenant show --tenant construtora-silva

# o critério de aprovação escrito pelo próprio cliente
orbi onboard --tenant construtora-silva --acceptance aceite-cliente.json
```

O roteiro completo do go-live está em [ORBI-IMPLANTACAO.md](ORBI-IMPLANTACAO.md).

---

# Onde a margem fica

## De onde vem o custo

Não aceite o total sem a conta. São três parcelas, e elas se comportam de forma
muito diferente:

| Parcela | Comportamento | Valor | Origem do número |
|---|---|---|---|
| VPS, Postgres e backup | **fixo** — não cresce com o cliente | R$ 150 a 400/mês no total | ⚠️ **estimativa, sem cotação** — peça o preço real antes de decidir |
| LLM | por pergunta | R$ 6 a 30 por cliente | **medido**: 890 tokens de entrada, 24 de saída, contra a API real ([ORBI-MODELOS.md](ORBI-MODELOS.md)) |
| Mensagens do WhatsApp | por mensagem **acima de 1.000/mês por número**, a partir de out/2026 | ≈ US$ 0,0098 ≈ **R$ 0,053** por mensagem | ⚠️ fonte secundária — SendPulse, 02/09/2026, citando a documentação da Meta; **não confirmado na página oficial em 16/09/2026**, que referencia o rate card em arquivo externo |

A parcela fixa é o que faz a margem melhorar com escala: o décimo cliente divide
a mesma VPS que o segundo.

## A conta, hoje (sem a tarifa da Meta)

| Situação | Receita | Custo | Margem |
|---|---|---|---|
| 1 cliente fundador (R$ 350) | R$ 350 | R$ 156–430 | negativa a 55% |
| 1 cliente pagante (R$ 890) | R$ 890 | R$ 156–430 | 52–82% |
| 3 clientes | ~R$ 2.200 | R$ 168–490 | 78–92% |
| 10 clientes | ~R$ 7.500 | R$ 210–700 | 91–97% |

O primeiro cliente não fecha a conta, e não precisa: ele existe para provar. **O
segundo cliente a preço cheio já deixa a operação no azul** — porque o custo
marginal do décimo é praticamente o mesmo do segundo.

## A mesma conta a partir de outubro de 2026

O que se sabe da tarifa, e de onde vem — porque este é o único número do
documento que ainda não está confirmado na fonte primária:

- **Cobrança a partir de 1º de outubro de 2026** para respostas dentro da janela
  de 24 h (a página oficial da Meta confirma "pricing updates launching October 1,
  2026").
- **1.000 mensagens de serviço grátis por número, por mês**; cobra-se da 1.001ª em
  diante, sem acumular (fonte secundária — SendPulse, 02/09/2026, citando a documentação da Meta; **não confirmado na página oficial em 16/09/2026**, que referencia o rate card em arquivo externo).
- **Mensagem de serviço custa o mesmo que template de utilidade** no mesmo país;
  no Brasil, **≈ US$ 0,0098 ≈ R$ 0,053** ao dólar de R$ 5,40 (mesma fonte).
- Cada turno é **uma** mensagem cobrada. Quando o turno passa de 2,5 s, o aviso
  "consultando o sistema da empresa..." é uma **segunda** mensagem cobrada — hoje
  raro (turno medido em 1,2 s), mas é custo que aparece no dia em que o ERP do
  cliente ficar lento.

**Reconfira na página oficial antes de fechar proposta**, e refaça esta tabela se
o valor mudar.

| Situação | Turnos/mês | Cobradas (−1.000) | Custo Meta | Custo total | Margem |
|---|---|---|---|---|---|
| Essencial, uso esperado | ~1.650 | 650 | ≈ R$ 34 | R$ 190–464 | 5–61% |
| Essencial, **no teto** | 3.000 | 2.000 | ≈ R$ 106 | R$ 262–536 | −9% a 47% |
| Time, uso esperado | ~4.000 | 3.000 | ≈ R$ 159 | R$ 315–589 | 34–65% |
| Time, **no teto** | 10.000 | 9.000 | ≈ R$ 477 | R$ 633–907 | **−2% a 29%** |
| Operação, **no teto** | 20.000 | 19.000 | ≈ R$ 1.007 | R$ 1.163–1.437 | 4–22% |
| 10 clientes Time, uso esperado | ~40.000 | 30.000 | ≈ R$ 1.590 | R$ 1.800–2.290 | 74–80% |

Três leituras dessa tabela:

1. **Cliente pequeno quase não paga Meta.** A franquia de 1.000 absorve a maior
   parte do uso esperado do Essencial.
2. **Um cliente Time que viva encostado no teto fica perto do zero a zero.** É
   isto que "o teto vira controle de margem" quer dizer, com número: encostar no
   teto de forma consistente é sinal para subir de plano, e a conta prova por quê.
3. **A parcela da Meta não amortiza** — cresce por turno, para sempre. A infra
   amortiza; o LLM é pequeno; a Meta é a linha que decide a margem em escala.
