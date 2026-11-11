# Estilo do canal @rendaextraparatodos

Guia para os próximos lotes de `renda/posts.json`. Saiu da leitura dos posts do canal em 10/11/2026.

## O que mais teve visualização no canal
- Texto que começa com uma história curta ("Nem toda renda extra começa com uma ideia genial…"): 305 views.
- Aplicativo que paga por algo simples: 263.
- Cursos grátis do MEC, Etec e Instituto Federal: 140, 96 e 48.
- Listas do tipo "3 sites que pagam…": 90.

## Regras
- **Dicas honestas.** Nada de prometer valor por dia, nada de aposta, cassino, pirâmide ou cripto.
- **Sem link de afiliado** por enquanto (decisão do Manoel em 10/11/2026). O foco é crescer o canal.
- **Arte própria**, feita pelo `renda/arte.py`:
  - 4:5, verde-escuro com dourado;
  - um ícone grande do tema e um ícone colorido em cada item, nada seco;
  - o texto da arte sai do campo `arte` do posts.json.
- **Cada post traz:**
  - título com emoji;
  - uma abertura que conversa com a pessoa;
  - uma lista de 4 a 7 itens;
  - um bloco "👀 Toque para ver a dica de ouro";
  - uma chamada para salvar ou compartilhar;
  - de 3 a 5 hashtags, sempre com `#rendaextra`.
- **Mistura do lote:** cerca de 80% listas e 20% quizzes. Quizzes sobre golpes rendem bem.
- **Notícias:** o `renda.py` garimpa o Google Notícias sozinho, com 1 por dia às 15h. A prioridade vai para:
  - cursos grátis online;
  - vagas remotas de empresas grandes.
  Notícias locais de uma cidade só ficam de fora.

## Como gerar um lote
1. Acrescente os itens no `renda/posts.json`, com os campos `id`, `tipo`, `titulo`, `imagens`, `arte` (com `icone` e `icones`), `corpo` e `tags`.
2. Rode `python renda/arte.py` para desenhar só as artes novas.
3. Rode `python renda.py previa` para ver tudo no privado antes de subir.
