# NutriLens IA

Aplicacao web com FastAPI para login de usuarios, upload de foto de refeicao e analise nutricional com Gemini.

> Este projeto foi pensado como um MVP pequeno e simples.
> Os dados sao armazenados localmente em arquivos JSON (pasta `data/`), sem banco de dados.
> A ideia e facilitar estudo, validacao rapida e publicacao open source com baixa complexidade.

## Requisitos

- Python 3.10+
- Chave de API do Gemini (`API_KEY_GEMINI`)

## Como rodar

1. Crie e ative um ambiente virtual.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. Instale as dependencias Python.

```powershell
pip install -r requirements_python.txt
```

3. Configure as variaveis de ambiente.

```powershell
Copy-Item .env.example .env
```

Edite o arquivo `.env` e preencha:

- `API_KEY_GEMINI`: sua chave da API Gemini
- `MODEL_AI_GEMINI`: modelo (padrao `gemini-3-flash-preview`)
- Usuarios de login:
  - Opcao A (recomendada): `APP_USERS_JSON` em formato JSON
  - Opcao B: `APP_USER_1`/`APP_PASS_1`, `APP_USER_2`/`APP_PASS_2`, etc.

Exemplo usando JSON:

```env
APP_USERS_JSON={"admin":"admin123","analista":"senha123"}
```

4. Execute a aplicacao.

```powershell
python main.py
```

5. Acesse no navegador:

- http://localhost:5600

## Estrutura

- `main.py`: API FastAPI e regras de autenticacao
- `templates/index.html`: interface web
- `data/`: historico por usuario (arquivos JSON)

## Observacoes

- O login aceita os usuarios definidos no `.env`.
- Cada usuario possui historico proprio em `data/<usuario>.json`.
- O armazenamento e **100% local em JSON** por escolha de simplicidade do MVP.
- Nao ha banco de dados, migracoes ou infraestrutura externa para persistencia.
