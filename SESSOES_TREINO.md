# Sistema de Sessões de Treino - Correções Implementadas

## Problemas Corrigidos

### 1. ✅ Dados da Sessão Atual Não Eram Salvos
**Antes:** Carga e status de conclusão eram salvos diretamente em `PersonRoutineExercise`, sobrescrevendo dados de sessões anteriores.

**Agora:** Criado modelo `SessionExercise` que rastreia cada exercício em cada sessão individualmente.

### 2. ✅ Dados de Sessões Anteriores Não Eram Mostrados
**Antes:** A view buscava dados de `PersonRoutineExercise.weight_used` que não refletia histórico.

**Agora:** A view busca dados da última sessão completada via `SessionExercise`.

### 3. ✅ Sessão Não Era Encerrada Automaticamente Após 120 Minutos
**Antes:** Apenas verificação no backend ao carregar a página.

**Agora:**
- Verificação no backend (mantida)
- Timer no frontend que redireciona automaticamente após 120 minutos

---

## Mudanças Implementadas

### 1. Novo Modelo: `SessionExercise`
```python
class SessionExercise(models.Model):
    """Rastreia execução individual de exercícios em cada sessão"""
    session = ForeignKey(WorkoutSession)
    person_routine_exercise = ForeignKey(PersonRoutineExercise)
    weight_used = DecimalField(...)
    is_completed = BooleanField(...)
    completed_at = DateTimeField(...)
```

**Benefícios:**
- Histórico completo de todas as sessões
- Dados de cada sessão são preservados
- Possibilita análise de progresso ao longo do tempo

### 2. View `workout_execute` Atualizada

**Mudanças:**
- Cria `SessionExercise` entries automaticamente para sessão ativa
- Busca dados da sessão anterior (última completada)
- Passa dados corretos para o template:
  - `current_session_id`: ID do SessionExercise (para APIs)
  - `current_weight`: Carga da sessão atual
  - `current_is_completed`: Status da sessão atual
  - `previous_date`: Data da última sessão
  - `previous_weight`: Carga da última sessão

### 3. APIs Atualizadas

#### `exercise_toggle_completion`
- Agora trabalha com `SessionExercise.id` em vez de `PersonRoutineExercise.id`
- Marca exercício como concluído apenas na sessão ativa

#### `exercise_update_weight`
- Salva peso em `SessionExercise` da sessão ativa
- Preserva histórico de pesos de sessões anteriores

### 4. Template `workout_execute.html`

**Mudanças:**
- Checkbox usa `current_session_id` e é marcado se já completado
- Input de carga mostra valor da sessão atual se existir
- Placeholder mostra valor da sessão anterior como referência
- Timer com auto-close após 120 minutos:
  ```javascript
  if (totalMinutes >= MAX_SESSION_MINUTES && !sessionClosed) {
      sessionClosed = true;
      alert('Sessão encerrada automaticamente...');
      window.location.href = '/my-workouts/';
  }
  ```

### 5. Admin Interface

**Adicionado:**
- `SessionExerciseAdmin`: Listagem de execuções por sessão
- `SessionExerciseInline`: Visualização inline em `WorkoutSessionAdmin`

---

## Como Funciona Agora

### Fluxo de uma Sessão de Treino

1. **Início da Sessão**
   - Usuário clica em "Iniciar Treino"
   - `WorkoutSession` é criada
   - Ao carregar `workout_execute`, `SessionExercise` entries são criadas automaticamente

2. **Durante a Sessão**
   - Usuário marca exercícios como concluídos → salvo em `SessionExercise`
   - Usuário adiciona carga → salva em `SessionExercise`
   - Timer conta até 120 minutos
   - Dados da sessão anterior são mostrados como referência

3. **Após 120 Minutos**
   - Backend: Encerra sessão automaticamente se usuário tentar acessar
   - Frontend: Redireciona automaticamente para "Meus Treinos"

4. **Finalização Manual**
   - Usuário clica em "Finalizar Treino"
   - `WorkoutSession.end_session()` é chamado
   - Duração é calculada e sessão marcada como completa

5. **Próxima Sessão**
   - Nova `WorkoutSession` é criada
   - Dados da sessão anterior (a que acabou de ser completada) são mostrados
   - Usuário vê o progresso em relação ao último treino

---

## Exemplo de Dados

### Sessão 1 (Segunda-feira)
```
PersonRoutine: Treino A
WorkoutSession: ID=1, start_time=10:00
  SessionExercise: Supino Reto, weight=60kg, completed ✓
  SessionExercise: Supino Inclinado, weight=50kg, completed ✓
```

### Sessão 2 (Quarta-feira)
```
PersonRoutine: Treino A (mesmo treino)
WorkoutSession: ID=2, start_time=10:00
  SessionExercise: Supino Reto, weight=62.5kg, completed ✓ (mostra 60kg da sessão anterior)
  SessionExercise: Supino Inclinado, weight=52.5kg, completed ✓ (mostra 50kg da sessão anterior)
```

---

## Migração

A migração `0007_sessionexercise.py` foi criada e aplicada.

Para sessões existentes, os `SessionExercise` serão criados automaticamente na primeira vez que o usuário acessar a página de execução.

---

## Próximos Passos (Opcionais)

1. **Gráficos de Progresso**: Usar histórico de `SessionExercise` para mostrar evolução
2. **Relatórios**: Análise de frequência, volume total, etc.
3. **Sugestões Automáticas**: Sugerir peso com base em sessões anteriores
4. **Notificações**: Alertar quando próximo dos 120 minutos

---

## Arquivos Modificados

- ✅ `core/models.py` - Adicionado `SessionExercise`
- ✅ `core/admin.py` - Registrado `SessionExercise` e inline
- ✅ `core/views.py` - Atualizado `workout_execute`, APIs
- ✅ `templates/core/workout_execute.html` - Atualizado IDs e timer
- ✅ `core/migrations/0007_sessionexercise.py` - Nova migração
