"""
oauth.py — OAuth 2.1 + PKCE + Dynamic Client Registration mínimo,
auto-aprobado, para un servidor MCP de un solo usuario (tú).

Claude.ai siempre intenta un handshake OAuth al conectar un conector
personalizado, incluso si el servidor no lo necesita. Este módulo
implementa lo mínimo para que ese handshake tenga éxito, sin pantalla
de login real: el servidor ya está protegido por ser privado (URL +
variables de entorno en Render), así que la autorización se
auto-aprueba en el momento.
"""

import time
import uuid
import base64
import hashlib
import secrets
from urllib.parse import urlencode

from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route
from starlette.requests import Request

_CODE_TTL = 5 * 60                    # 5 minutos para intercambiar el code
_TOKEN_TTL = 365 * 24 * 60 * 60       # 1 año, servidor personal

_clients: dict = {}
_auth_codes: dict = {}
_tokens: dict = {}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def is_valid_token(token: str) -> bool:
    entry = _tokens.get(token)
    return bool(entry) and not entry.get("is_refresh") and entry["expires"] > time.time()


def build_oauth_routes(public_url: str) -> list[Route]:
    async def authorization_server_metadata(request: Request):
        return JSONResponse({
            "issuer": public_url,
            "authorization_endpoint": f"{public_url}/oauth/authorize",
            "token_endpoint": f"{public_url}/oauth/token",
            "registration_endpoint": f"{public_url}/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
        })

    async def protected_resource_metadata(request: Request):
        return JSONResponse({
            "resource": f"{public_url}/mcp",
            "authorization_servers": [public_url],
            "bearer_methods_supported": ["header"],
        })

    async def register(request: Request):
        body = await request.json()
        client_id = str(uuid.uuid4())
        _clients[client_id] = {
            "redirect_uris": body.get("redirect_uris", []),
            "client_name": body.get("client_name", "claude"),
        }
        return JSONResponse({
            "client_id": client_id,
            "client_id_issued_at": int(time.time()),
            "redirect_uris": _clients[client_id]["redirect_uris"],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        }, status_code=201)

    async def authorize(request: Request):
        params = request.query_params
        client_id = params.get("client_id")
        redirect_uri = params.get("redirect_uri")
        state = params.get("state")
        code_challenge = params.get("code_challenge")

        if client_id not in _clients:
            return JSONResponse({"error": "invalid_client"}, status_code=400)

        code = secrets.token_urlsafe(32)
        _auth_codes[code] = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "expires": time.time() + _CODE_TTL,
        }

        redirect_params = {"code": code}
        if state:
            redirect_params["state"] = state
        return RedirectResponse(f"{redirect_uri}?{urlencode(redirect_params)}", status_code=302)

    async def token(request: Request):
        form = await request.form()
        grant_type = form.get("grant_type")

        if grant_type == "authorization_code":
            code = form.get("code")
            code_verifier = form.get("code_verifier")
            entry = _auth_codes.get(code)
            if not entry or entry["expires"] < time.time():
                return JSONResponse({"error": "invalid_grant"}, status_code=400)

            if entry.get("code_challenge"):
                expected = _b64url(hashlib.sha256((code_verifier or "").encode()).digest())
                if expected != entry["code_challenge"]:
                    return JSONResponse(
                        {"error": "invalid_grant", "error_description": "PKCE inválido"},
                        status_code=400,
                    )

            del _auth_codes[code]
            access_token = secrets.token_urlsafe(32)
            refresh_token = secrets.token_urlsafe(32)
            _tokens[access_token] = {"expires": time.time() + _TOKEN_TTL, "is_refresh": False}
            _tokens[refresh_token] = {"expires": time.time() + _TOKEN_TTL, "is_refresh": True}

            return JSONResponse({
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": _TOKEN_TTL,
                "refresh_token": refresh_token,
            })

        if grant_type == "refresh_token":
            refresh_token = form.get("refresh_token")
            entry = _tokens.get(refresh_token)
            if not entry or not entry.get("is_refresh") or entry["expires"] < time.time():
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            access_token = secrets.token_urlsafe(32)
            _tokens[access_token] = {"expires": time.time() + _TOKEN_TTL, "is_refresh": False}
            return JSONResponse({
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": _TOKEN_TTL,
            })

        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    return [
        Route("/.well-known/oauth-authorization-server", authorization_server_metadata, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource", protected_resource_metadata, methods=["GET"]),
        Route("/oauth/register", register, methods=["POST"]),
        Route("/oauth/authorize", authorize, methods=["GET"]),
        Route("/oauth/token", token, methods=["POST"]),
    ]
