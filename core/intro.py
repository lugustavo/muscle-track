"""First-access tour of the student: three short screens on how a workout works (shown once on "Hoje", and again
whenever the student opens "Como usar o app" in the profile)."""

STEPS = (
    {'icon': 'bi-check-circle', 'title': 'Marque cada série',
     'text': 'Preencha a carga e as repetições e toque no ✓ ao terminar a série. Os botões − e + ajustam a carga '
             'sem precisar digitar.'},
    {'icon': 'bi-hourglass-split', 'title': 'Descanso automático',
     'text': 'Ao marcar uma série, o cronômetro de descanso começa sozinho. Dá para somar ou tirar 15 segundos, '
             'ou pular quando já estiver pronto.'},
    {'icon': 'bi-chat-left-text', 'title': 'Conte como foi',
     'text': 'Cada exercício tem um campo para anotar dor, ajuste ou sensação, e no fim o treino pergunta '
             '"Como foi?". Sua personal lê tudo e ajusta o próximo treino.'},
)
