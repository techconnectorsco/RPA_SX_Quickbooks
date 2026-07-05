"""
renovar_token.py  —  Renovador de tokens OAuth de QuickBooks (sandbox / produccion)
====================================================================================
Refresca los tokens de acceso de QuickBooks. El ENTORNO NO se decide en el
codigo: se lee de la variable de entorno QBO_ENTORNO del .env de cada maquina.

  - En LOCAL:  el .env NO trae QBO_ENTORNO (o trae 'sandbox') -> renueva SOLO el
               token de sandbox (config/tokens_sandbox.json). NUNCA toca produccion.
  - En el VPS: el .env trae  QBO_ENTORNO=produccion  -> renueva los tokens de las
               empresas reales (config/tokens_empresas.json).

Asi el MISMO archivo .py funciona en las dos maquinas sin editar nada: cada
maquina se comporta segun su propio .env. Y como el DEFAULT es 'sandbox',
correrlo por accidente en local jamas invalida los tokens del VPS.

Por que importa:
  El refresh_token de QuickBooks es de UN SOLO USO: cada refresco entrega uno
  nuevo y mata el anterior en todo el mundo. Por eso un mismo token no puede
  vivir en dos maquinas. Regla de oro: produccion se renueva SOLO en el VPS.

Requiere:  pip install requests python-dotenv
"""

import os
import sys
import json
import base64

import requests
from dotenv import load_dotenv

# ── Carga de variables de entorno ────────────────────────────────────────────
load_dotenv()  # .env de esta carpeta (llaves QBO y, opcional, QBO_ENTORNO)
RUTA_ENV_WEBAPP = r"D:\Users\Usuario\Desktop\SX-Ecosystem\SX-Ecosystem\.env"
if os.path.exists(RUTA_ENV_WEBAPP):
    load_dotenv(RUTA_ENV_WEBAPP, override=False)

# ENTORNO desde el .env de LA MAQUINA. Default 'sandbox' = blindaje: si nadie
# lo puso explicitamente, se asume sandbox y NO se toca produccion.
ENTORNO = os.getenv("QBO_ENTORNO", "sandbox").strip().lower()

ENTORNOS = {
    "sandbox": {
        "tokens_file": os.path.join("config", "tokens_sandbox.json"),
        "client_id": os.getenv("QBO_SANDBOX_CLIENT_ID"),
        "client_secret": os.getenv("QBO_SANDBOX_CLIENT_SECRET"),
        "multi_empresa": False,  # un solo token al nivel raiz del JSON
    },
    "produccion": {
        "tokens_file": os.path.join("config", "tokens_empresas.json"),
        "client_id": os.getenv("QBO_CLIENT_ID"),
        "client_secret": os.getenv("QBO_CLIENT_SECRET"),
        "multi_empresa": True,  # un token por empresa bajo data["empresas"]
    },
}


def _auth_header(client_id, client_secret):
    raw = f"{client_id}:{client_secret}".encode()
    return base64.b64encode(raw).decode()


def _refrescar_nodo(nodo, auth_base64):
    """Refresca un nodo {access_token, refresh_token} in-place.
    Devuelve (True, 'OK') o (False, 'motivo')."""
    resp = requests.post(
        "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
        headers={
            "Authorization": f"Basic {auth_base64}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        data={"grant_type": "refresh_token", "refresh_token": nodo["refresh_token"]},
        timeout=30,
    )
    if resp.status_code == 200:
        nuevos = resp.json()
        nodo["access_token"] = nuevos["access_token"]
        nodo["refresh_token"] = nuevos[
            "refresh_token"
        ]  # Intuit rota; hay que guardarlo
        return True, "OK"
    return False, f"{resp.status_code} {resp.text[:200]}"


def main():
    if ENTORNO not in ENTORNOS:
        sys.exit(f"QBO_ENTORNO invalido: '{ENTORNO}'. Use 'sandbox' o 'produccion'.")

    cfg = ENTORNOS[ENTORNO]

    if not (cfg["client_id"] and cfg["client_secret"]):
        sys.exit(
            f"Faltan las llaves (client_id/secret) para '{ENTORNO}' en el .env.\n"
            f"  sandbox   -> QBO_SANDBOX_CLIENT_ID / QBO_SANDBOX_CLIENT_SECRET\n"
            f"  produccion-> QBO_CLIENT_ID / QBO_CLIENT_SECRET"
        )

    f = cfg["tokens_file"]
    if not os.path.exists(f):
        sys.exit(f"No encuentro el archivo de tokens: {f}")

    with open(f, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    auth = _auth_header(cfg["client_id"], cfg["client_secret"])

    print("=" * 60)
    print(f"RENOVAR TOKENS  —  ENTORNO: {ENTORNO.upper()}")
    print(f"Archivo: {f}")
    if ENTORNO == "produccion":
        print("*** OJO: estas renovando PRODUCCION (esto solo se hace en el VPS) ***")
    print("=" * 60)

    ok_total = 0
    if cfg["multi_empresa"]:
        # Produccion: un token por empresa
        for realm, empresa in data["empresas"].items():
            nombre = empresa.get("nombre", realm)
            print(f"\n  {nombre} (realm {realm})...")
            ok, msg = _refrescar_nodo(empresa, auth)
            if ok:
                print("   [OK] token refrescado")
                ok_total += 1
            else:
                print(f"   [ERROR] {msg}")
    else:
        # Sandbox: un solo token al nivel raiz del JSON
        print("\n  Sandbox (token unico)...")
        ok, msg = _refrescar_nodo(data, auth)
        if ok:
            print("   [OK] token refrescado")
            ok_total += 1
        else:
            print(f"   [ERROR] {msg}")

    # Guardamos SIEMPRE (Intuit ya roto el/los refresh; hay que persistirlos)
    with open(f, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)

    print("\n" + "=" * 60)
    print(f"Listo. Tokens refrescados correctamente: {ok_total}")
    print(f"Guardado en: {f}")
    print("=" * 60)


if __name__ == "__main__":
    main()
