from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4
import json
import os
import re

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv
from google import genai
from google.genai import types
import uvicorn

load_dotenv()

API_KEY_GEMINI = os.getenv("API_KEY_GEMINI", "").strip()
MODEL_AI_GEMINI = os.getenv("MODEL_AI_GEMINI", "gemini-3-flash-preview")

def _load_users() -> Dict[str, str]:
    users_from_json = os.getenv("APP_USERS_JSON", "").strip()
    if users_from_json:
        try:
            parsed = json.loads(users_from_json)
            if not isinstance(parsed, dict):
                raise ValueError("APP_USERS_JSON precisa ser um objeto JSON")

            users: Dict[str, str] = {}
            for raw_user, raw_pass in parsed.items():
                username = str(raw_user).strip().lower()
                password = str(raw_pass)
                if username and password:
                    users[username] = password
            if users:
                return users
        except Exception as exc:
            raise RuntimeError(f"Configuracao invalida em APP_USERS_JSON: {exc}") from exc

    fallback_users: Dict[str, str] = {}
    for idx in range(1, 6):
        username = os.getenv(f"APP_USER_{idx}", "").strip().lower()
        password = os.getenv(f"APP_PASS_{idx}", "")
        if username and password:
            fallback_users[username] = password

    if fallback_users:
        return fallback_users

    return {"admin": "admin123"}


USERS = _load_users()

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

SESSIONS: Dict[str, str] = {}

SYSTEM_PROMPT = (
    "Voce e um nutricionista esportivo especialista em estimativa visual de refeicoes. "
    "Analise a imagem da refeicao e, quando existir, use tambem a descricao textual enviada pelo usuario. "
    "Retorne EXCLUSIVAMENTE um JSON valido com este formato: "
    '{"meal_name":"","confidence":0,"totals":{"calories_kcal":0,"protein_g":0,"carbs_g":0,"fat_g":0,"fiber_g":0,"sodium_mg":0},'
    '"items":[{"name":"","portion":"","calories_kcal":0,"protein_g":0,"carbs_g":0,"fat_g":0}],'
    '"notes":[""],"warnings":[""]}. '
    "Use numeros reais. Se algo for incerto, reduza confidence e explique em notes. "
    "Nunca retorne markdown, apenas JSON puro."
)


def _extract_json(payload: str) -> Dict[str, Any]:
    cleaned = re.sub(r"```(?:json)?", "", payload).strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        raise ValueError("Resposta da IA sem JSON")
    return json.loads(match.group(0))


def _normalize_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0

    if 0 <= confidence <= 1:
        confidence *= 100

    return max(0.0, min(100.0, confidence))


def _normalize_analysis(analysis: Any) -> Dict[str, Any]:
    if not isinstance(analysis, dict):
        analysis = {}

    normalized = dict(analysis)
    normalized["meal_name"] = str(normalized.get("meal_name") or "Refeicao")
    normalized["confidence"] = _normalize_confidence(normalized.get("confidence", 0))

    totals = normalized.get("totals")
    if not isinstance(totals, dict):
        totals = {}
    normalized["totals"] = {
        "calories_kcal": totals.get("calories_kcal", 0),
        "protein_g": totals.get("protein_g", 0),
        "carbs_g": totals.get("carbs_g", 0),
        "fat_g": totals.get("fat_g", 0),
        "fiber_g": totals.get("fiber_g", 0),
        "sodium_mg": totals.get("sodium_mg", 0),
    }

    normalized["items"] = normalized.get("items") if isinstance(normalized.get("items"), list) else []
    normalized["notes"] = normalized.get("notes") if isinstance(normalized.get("notes"), list) else []
    normalized["warnings"] = normalized.get("warnings") if isinstance(normalized.get("warnings"), list) else []

    return normalized


def _user_db_path(username: str) -> Path:
    return DATA_DIR / f"{username}.json"


def _ensure_user_db(username: str) -> None:
    path = _user_db_path(username)
    if path.exists():
        return
    payload = {
        "user": username,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "entries": [],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_user_db(username: str) -> Dict[str, Any]:
    _ensure_user_db(username)
    path = _user_db_path(username)
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_user_db(username: str, data: Dict[str, Any]) -> None:
    path = _user_db_path(username)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _token_to_user(authorization: Optional[str]) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Token ausente")

    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Formato de token invalido")

    token = authorization.split(" ", 1)[1].strip()
    username = SESSIONS.get(token)
    if not username:
        raise HTTPException(status_code=401, detail="Sessao invalida ou expirada")
    return username


def _analyze_meal_with_ai(image_bytes: bytes, mime_type: str, note: str) -> Dict[str, Any]:
    try:
        if not API_KEY_GEMINI:
            raise RuntimeError("API_KEY_GEMINI nao configurada")

        client = genai.Client(api_key=API_KEY_GEMINI)

        extra_context = ""
        if note:
            extra_context = f"Descricao textual do usuario: {note.strip()}"

        response = client.models.generate_content(
            model=MODEL_AI_GEMINI,
            contents=[
                types.Part.from_text(text=SYSTEM_PROMPT),
                types.Part.from_text(text=extra_context),
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            ],
            config=types.GenerateContentConfig(
                temperature=0.2,
                response_mime_type="application/json",
            ),
        )

        try:
            parsed = json.loads(response.text)
        except Exception:
            parsed = _extract_json(response.text)

        return parsed
    except Exception as exc:
        raise RuntimeError(f"Falha ao consultar IA: {exc}") from exc


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")

for username in USERS:
    _ensure_user_db(username)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/login")
async def login(payload: Dict[str, str]):
    username = (payload.get("username") or "").strip().lower()
    password = payload.get("password") or ""

    if username not in USERS or USERS[username] != password:
        raise HTTPException(status_code=401, detail="Usuario ou senha invalidos")

    token = uuid4().hex
    SESSIONS[token] = username

    return {
        "success": True,
        "token": token,
        "user": username,
    }


@app.post("/api/logout")
async def logout(authorization: Optional[str] = Header(default=None)):
    username = _token_to_user(authorization)
    token = authorization.split(" ", 1)[1].strip()
    SESSIONS.pop(token, None)
    return {"success": True, "user": username}


@app.get("/api/history")
async def history(authorization: Optional[str] = Header(default=None)):
    username = _token_to_user(authorization)
    db = _read_user_db(username)

    entries = db.get("entries", [])
    changed = False
    for entry in entries:
        if "image_preview_base64" in entry:
            entry.pop("image_preview_base64", None)
            changed = True

        previous_analysis = entry.get("analysis")
        normalized_analysis = _normalize_analysis(previous_analysis)
        if normalized_analysis != previous_analysis:
            entry["analysis"] = normalized_analysis
            changed = True

    if changed:
        _write_user_db(username, db)

    ordered_entries = sorted(entries, key=lambda e: e.get("created_at", ""), reverse=True)

    return {
        "success": True,
        "user": username,
        "entries": ordered_entries,
    }


@app.post("/api/analyze-meal")
async def analyze_meal(
    image: UploadFile = File(...),
    note: str = Form(default=""),
    authorization: Optional[str] = Header(default=None),
):
    username = _token_to_user(authorization)

    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Envie uma imagem valida")

    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Arquivo de imagem vazio")

    try:
        analysis = _normalize_analysis(_analyze_meal_with_ai(
            image_bytes=image_bytes,
            mime_type=image.content_type,
            note=note,
        ))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    entry = {
        "id": uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "image_filename": image.filename,
        "note": note,
        "analysis": analysis,
    }

    db = _read_user_db(username)
    db.setdefault("entries", []).append(entry)
    _write_user_db(username, db)

    return {
        "success": True,
        "user": username,
        "entry": entry,
    }


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=5600, reload=False)

