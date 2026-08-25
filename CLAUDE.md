## Escopo

- ORBI é a fonte da verdade — código, decisões e documentação sempre coerentes
- Não implementar nada fora do escopo atual: WhatsApp, somente leitura, tools definidas
- Não existe frontend — a administração é pela CLI (`orbi ...`)
- Não adicionar tecnologia sem um problema real que justifique
- Não antecipar roadmap, exceto os pontos de extensão já previstos na arquitetura

## LLM

- O LLM interpreta; o código autoriza, executa e monta a resposta
- O LLM nunca emite IDs ou códigos, nunca acessa o banco, nunca gera SQL
- Respostas por template do código — nunca redigidas pelo LLM
- Uma tool por turno — sem loops ou agentes no caminho da pergunta
- Enviar só as tools permitidas ao papel do usuário
- Mascarar CPF, CNPJ, telefone e e-mail antes de qualquer envio ao LLM
- Toda integração com provedor passa pelo LLMPort

## Tools e resolução

- Não existe tool de busca de produto — a resolução é interna ao Runtime
- Nomes de tools, argumentos, classes e arquivos em inglês
- Tool declarada uma única vez no Tool Registry
- Na dúvida ou ambiguidade, perguntar — nunca chutar
- Toda resposta mostra qual entidade foi usada

## Segurança

- Policy Layer deny-by-default, antes do acesso ao ERP
- Field Policy controla campos visíveis — nunca depender do prompt
- Isolamento total entre tenants: RLS, sessão sempre pela dependency que define o tenant
- Canary tenant no CI — vazamento entre tenants falha o build
- Credenciais de ERP cifradas; segredos nunca no repositório

## ERP

- Somente APIs ou interfaces oficiais — nunca banco direto ou scraping
- Cada ERP em seu próprio Adapter; sem condicional por tenant ou ERP no Core
- Adapter novo só entra depois do Conformance Kit
- Odoo é o ERP de referência permanente — validar tudo nele antes de cliente real
- Nunca responder estoque de cache; ERP fora do ar, informar a falha
- Nunca mentir sobre a base do número (físico vs disponível)

## Qualidade

- trace_id compartilhado entre observabilidade e auditoria
- Mudança de comportamento passa pelos evals correspondentes
- Rodar os testes ao final e corrigir falhas antes de concluir
- Reutilizar em vez de duplicar; sem código morto, redundante ou fallback desnecessário
- Seguir o padrão do código existente

## Git e docs

- Mensagens de commit em inglês, sem citar ferramentas de IA
- Atualizar a documentação ao mudar contratos, arquitetura ou decisões
- Sem analogias — literal e técnico