# 🚀 Quick Start - Muscle Track

## Instalação Rápida (3 minutos)

### 1. Instale as dependências
```bash
pip install -r requirements.txt
```

### 2. Configure o banco de dados
```bash
python manage.py migrate
python populate_db.py
```

### 3. Inicie o servidor
```bash
python manage.py runserver
```

### 4. Acesse a aplicação
Abra seu navegador em: **http://localhost:8000**

---

## 🔑 Login

### Como Administrador:
- Email: `admin@muscletrack.com`
- Senha: `admin123`
- Acesso completo ao sistema

### Como Aluno:
- Email: `joao@example.com`
- Senha: `senha123`
- Visualizar e executar treinos

---

## 📋 O que você pode fazer?

### Administrador:
1. Cadastrar exercícios (Admin > Exercícios)
2. Criar treinos (Admin > Treinos)
3. Cadastrar alunos (Admin > Alunos)
4. Atribuir treinos aos alunos
5. Ver histórico e evolução

### Aluno:
1. Ver treinos disponíveis (Meus Treinos)
2. Iniciar treino com timer
3. Marcar exercícios concluídos
4. Registrar carga utilizada
5. Ver histórico pessoal

---

## 🤖 Geração de Treinos com IA (Opcional)

Para usar ChatGPT:
1. Obtenha uma chave em: https://platform.openai.com/api-keys
2. Edite o arquivo `.env`:
   ```
   OPENAI_API_KEY=sua-chave-aqui
   ```
3. Acesse: Treinos > Gerar com IA

---

## 🎯 Recursos Principais

✅ Login com Google, Facebook, GitHub (SSO)
✅ Timer automático de treino
✅ Controle de carga e repetições
✅ Histórico de evolução
✅ Design responsivo (mobile/desktop)
✅ 21 exercícios pré-cadastrados
✅ 4 treinos de exemplo (A, B, C, D)

---

## 🆘 Problemas?

**Erro ao migrar:** Execute `rm db.sqlite3` e rode novamente
**Porta 8000 ocupada:** Use `python manage.py runserver 8080`

Veja o [README.md](README.md) completo para mais detalhes.

---

**Pronto para treinar!** 💪
