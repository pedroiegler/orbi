# ORBI — Catálogo

O Orbi **não copia o ERP**: mantém um índice de resolução — o mínimo para
entender a pergunta e descobrir de qual entidade a pessoa falou.

```
catalog(tenant_id, erp_entity_id, entity_type, name, code, canonical_name,
        embedding vector(768), search_text, name_hash, active, flagged)
```

---

## Nome canônico: onde está o ganho

Não se vetoriza o nome cru.

```
"TB PVC ESG 100MM BR"  →  "tubo pvc esgoto 100 mm branco"
```

Expansão de abreviações, normalização de unidades e remoção do código interno.
**A mesma tradução é aplicada à pergunta**, senão estaríamos comparando coisas
diferentes.

O ganho de qualidade vem daqui, não de dobrar a dimensão do vetor: catálogo de
PME brasileira é abreviado e inconsistente, e é exatamente isso que quebra a
busca semântica.

### Dicionário do cliente

```bash
orbi catalog abbreviations --tenant construtora-silva
orbi catalog abbreviations --tenant construtora-silva --add "tb=tubo"
orbi catalog sync --tenant construtora-silva   # aplica a mudança
```

O Discovery propõe candidatos (tokens curtos e frequentes que o dicionário base
não cobre) — mas **não inventa a expansão**: isso é decisão humana.

---

## Sincronização

```bash
orbi catalog sync --tenant construtora-silva                      # full
orbi catalog sync --tenant construtora-silva --mode incremental   # últimas 4h
orbi catalog status --tenant construtora-silva
```

Pelo cron: full às 3h e incremental a cada 4 horas.

Quatro regras que o job segue:

- **Re-embedding só quando o `name_hash` muda.** Sem isso, paga-se embedding do
  catálogo inteiro toda noite sem motivo.
- **Produto ausente vira `active=false`, nunca DELETE** — a auditoria referencia
  esses ids.
- **Sync que mudaria mais de 30% do catálogo para e alerta.** Quase sempre é
  credencial errada ou API devolvendo página vazia.
- **Job idempotente e retomável**, com a corrida registrada em
  `catalog_sync_runs`.

### Quando o abort é legítimo

Renomeação em massa e troca de catálogo acontecem. Confirme a causa e só então:

```bash
orbi catalog sync --tenant construtora-silva --force
orbi maintenance recalibrate --tenant construtora-silva --force
```

---

## Content firewall

Nome de produto é o único texto de terceiro que circula pelo sistema. Nomes com
padrão de instrução (`ignore as instruções`, `system:`, delimitadores, menção a
credencial) entram **sinalizados** e ficam fora do índice até revisão:

```bash
orbi catalog status --tenant construtora-silva   # coluna "sinalizados"
```

É a terceira barreira contra injeção. As duas primeiras — saída restrita a um
conjunto fechado de tools e rendering determinístico — já resolvem o caso.

---

## Vocabulário aprendido

```bash
orbi catalog aliases --tenant construtora-silva
```

Cada desambiguação resolvida vira alias daquele cliente. O alias **não** nasce
confirmado: entra com `confidence=low` e só é promovido depois de dois usos sem
correção. Um toque errado não pode envenenar a resolução do cliente para sempre.

Esse acúmulo é dado proprietário e é a defesa competitiva mais durável do
produto: não se copia, se acumula.

---

## Limiares de resolução

Calibrados por cliente, nunca fixos no código:

```bash
orbi maintenance recalibrate --tenant construtora-silva
orbi tenant show --tenant construtora-silva     # mostra os limiares em uso
```

A calibração gera um conjunto de teste sintético a partir do catálogo real (nome
truncado, abreviado, com erro de digitação, sem a medida) e varre os pares de
limiar maximizando o acerto sujeito a **erro silencioso ≤ 1%**.

> Responder a entidade errada com confiança é um erro grave.
> Perguntar "qual desses?" é um custo pequeno.

Recalibre quando o catálogo mudar mais de 20% — o comando verifica isso sozinho.

---

## Catálogo travado

Sintoma: muita resposta "não encontrei" ou "qual desses?".

1. `orbi catalog status` — os itens estão ativos e com vetor?
2. `orbi catalog aliases` — o termo que o cliente usa já virou alias?
3. Resumo diário — os cinco termos mais buscados sem resultado são a lista de
   trabalho do dia: cada um vira um alias ou uma correção de nome canônico.
4. Persistindo, é o dicionário de abreviações do cliente que está incompleto.
