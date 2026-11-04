# -*- coding: utf-8 -*-
"""
diag_qb.py  —  DIAGNOSTICO QUICKBOOKS (SOLO LECTURA)

No escribe NADA: ni tokens_empresas.json, ni base de datos, ni facturas.
Solo refresca un token en memoria y hace 3 consultas de lectura.

USO
---
  1) Usando el refresh_token que ya tenes en config/tokens_empresas.json:
        py diag_qb.py 9130355360397996

  2) Usando un access_token fresco del OAuth Playground (dura ~1 hora):
        py diag_qb.py 9130355360397996 --access-token eyJhb...

  3) Pasando el refresh_token a mano:
        py diag_qb.py 9130355360397996 --refresh-token AB11...

CREDENCIALES
------------
Toma QBO_CLIENT_ID / QBO_CLIENT_SECRET (o QB_CLIENT_ID / QB_CLIENT_SECRET)
del entorno o del .env del directorio.
(No hacen falta si usas --access-token.)
"""

import os
import sys
import json
import base64
import argparse

import requests

BASE = "https://quickbooks.api.intuit.com"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
MINORVERSION = "75"
TOKENS_FILE = os.path.join("config", "tokens_empresas.json")


# ----------------------------------------------------------------------
# utilidades
# ----------------------------------------------------------------------
def linea(txt=""):
    print(txt)


def titulo(txt):
    print("\n" + "=" * 74)
    print("  " + txt)
    print("=" * 74)


def cargar_dotenv():
    """Lee .env del directorio actual sin dependencias externas."""
    if not os.path.exists(".env"):
        return
    with open(".env", "r", encoding="utf-8", errors="ignore") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            k, v = raw.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def buscar_refresh_token(obj, realm):
    """
    Busca recursivamente en el JSON de tokens un dict que contenga el realm
    y devuelve su refresh_token. Tolera distintas estructuras de archivo.
    """
    if isinstance(obj, dict):
        # caso: la clave del dict es el realm
        if realm in obj and isinstance(obj[realm], dict):
            rt = obj[realm].get("refresh_token")
            if rt:
                return rt
        # caso: el realm es un valor dentro del dict
        valores = {str(v) for v in obj.values() if isinstance(v, (str, int))}
        if realm in valores and obj.get("refresh_token"):
            return obj["refresh_token"]
        for v in obj.values():
            r = buscar_refresh_token(v, realm)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = buscar_refresh_token(v, realm)
            if r:
                return r
    return None


# ----------------------------------------------------------------------
# OAuth
# ----------------------------------------------------------------------
def refrescar(refresh_token, client_id, client_secret):
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode(
        "utf-8"
    )

    resp = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": f"Basic {basic}",
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=30,
    )

    linea(f"  HTTP {resp.status_code}")
    linea(f"  intuit_tid: {resp.headers.get('intuit_tid', '(no vino)')}")

    if resp.status_code != 200:
        linea("  [FALLO EL REFRESH] respuesta de Intuit:")
        linea("  " + resp.text[:500])
        return None

    data = resp.json()
    nuevo_rt = data.get("refresh_token", "")
    linea("  Refresh OK.")
    linea(
        f"  access_token  : {data['access_token'][:25]}... (len {len(data['access_token'])})"
    )
    linea(f"  refresh_token : {nuevo_rt[:12]}...  <-- NO se guardo (solo lectura)")
    if nuevo_rt and nuevo_rt != refresh_token:
        linea("  OJO: Intuit roto el refresh_token. El del VPS sigue siendo el valido.")
    return data["access_token"]


# ----------------------------------------------------------------------
# llamadas de lectura
# ----------------------------------------------------------------------
def consultar(access_token, realm, query, etiqueta):
    url = f"{BASE}/v3/company/{realm}/query"
    linea(f"\n  --> {etiqueta}")
    linea(f"      GET {url}?minorversion={MINORVERSION}")
    linea(f"      query: {query}")

    resp = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        params={"query": query, "minorversion": MINORVERSION},
        timeout=60,
    )

    tid = resp.headers.get("intuit_tid", "(no vino)")
    linea(f"      HTTP {resp.status_code}   intuit_tid: {tid}")

    if resp.status_code != 200:
        linea("      [ERROR] cuerpo de la respuesta:")
        linea("      " + resp.text[:700])
        return None

    return resp.json().get("QueryResponse", {})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("realm", help="realmId de la empresa")
    ap.add_argument("--access-token", help="access_token fresco del Playground")
    ap.add_argument("--refresh-token", help="refresh_token a mano")
    args = ap.parse_args()

    realm = args.realm.strip()
    cargar_dotenv()

    titulo(f"DIAGNOSTICO QUICKBOOKS  —  realm {realm}")
    linea("  MODO: SOLO LECTURA. No se escribe ningun archivo ni la base.")

    # ---- 1. conseguir access_token ----
    titulo("1) TOKEN")

    if args.access_token:
        access_token = args.access_token.strip()
        linea("  Usando el access_token que pasaste (Playground). No se refresca nada.")
    else:
        refresh_token = args.refresh_token
        if not refresh_token:
            if not os.path.exists(TOKENS_FILE):
                linea(f"  [X] No encuentro {TOKENS_FILE} y no pasaste --refresh-token.")
                sys.exit(1)
            with open(TOKENS_FILE, "r", encoding="utf-8") as fh:
                tokens = json.load(fh)
            refresh_token = buscar_refresh_token(tokens, realm)
            if not refresh_token:
                linea(
                    f"  [X] No hay refresh_token para el realm {realm} en {TOKENS_FILE}."
                )
                sys.exit(1)
            linea(f"  refresh_token leido de {TOKENS_FILE} (archivo intacto).")

        client_id = os.environ.get("QBO_CLIENT_ID") or os.environ.get("QB_CLIENT_ID")
        client_secret = os.environ.get("QBO_CLIENT_SECRET") or os.environ.get(
            "QB_CLIENT_SECRET"
        )

        if not client_id or not client_secret:
            linea(
                "  [X] Faltan QBO_CLIENT_ID / QBO_CLIENT_SECRET en el entorno o el .env."
            )
            sys.exit(1)
        linea(f"  client_id: {client_id[:12]}...")

        access_token = refrescar(refresh_token, client_id, client_secret)
        if not access_token:
            linea(
                "\n  >>> El refresh fallo. El problema esta en las credenciales de la app,"
            )
            linea("      no en el permiso sobre la empresa. Mira el cuerpo de arriba.")
            sys.exit(1)

    # ---- 2. companyinfo: la prueba de fuego ----
    titulo("2) COMPANYINFO  (si esto da 200, tenes acceso al realm)")

    url = f"{BASE}/v3/company/{realm}/companyinfo/{realm}"
    linea(f"  GET {url}?minorversion={MINORVERSION}")
    resp = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        params={"minorversion": MINORVERSION},
        timeout=60,
    )
    tid = resp.headers.get("intuit_tid", "(no vino)")
    linea(f"  HTTP {resp.status_code}   intuit_tid: {tid}")

    if resp.status_code != 200:
        linea("  [ERROR] cuerpo de la respuesta:")
        linea("  " + resp.text[:700])
        linea("")
        linea("  >>> Seguis SIN acceso a este realm. Guarda el intuit_tid de arriba:")
        linea("      es lo que pide soporte de Intuit.")
        sys.exit(1)

    info = resp.json().get("CompanyInfo", {})
    linea("")
    linea(f"  >>> ACCESO OK: {info.get('CompanyName', '(sin nombre)')}")
    linea(
        f"      Pais: {info.get('Country', '?')}   Moneda: "
        f"{(info.get('Currency') or {}).get('value', '?')}"
    )

    # ---- 3. impuestos ----
    titulo("3) IMPUESTOS  (lo que el RPA no logra leer)")

    qr = consultar(
        access_token, realm, "SELECT * FROM TaxRate MAXRESULTS 200", "TaxRates"
    )
    if qr is not None:
        rates = qr.get("TaxRate", [])
        linea(f"      {len(rates)} TaxRates")
        for r in sorted(rates, key=lambda x: str(x.get("Name", ""))):
            activo = "" if r.get("Active", True) else "  [INACTIVO]"
            linea(
                f"        Id {str(r.get('Id','?')):>4}  "
                f"{str(r.get('RateValue','?')):>6}%  {r.get('Name','?')}{activo}"
            )

    qr = consultar(
        access_token, realm, "SELECT * FROM TaxCode MAXRESULTS 200", "TaxCodes"
    )
    if qr is not None:
        codes = qr.get("TaxCode", [])
        linea(f"      {len(codes)} TaxCodes")
        for c in sorted(codes, key=lambda x: str(x.get("Name", ""))):
            activo = "" if c.get("Active", True) else "  [INACTIVO]"
            ids = []
            for grupo in (c.get("SalesTaxRateList") or {}).get("TaxRateDetail", []):
                ref = (grupo.get("TaxRateRef") or {}).get("value")
                if ref:
                    ids.append(str(ref))
            linea(
                f"        Id {str(c.get('Id','?')):>4}  '{c.get('Name','?')}'{activo}"
                f"   TaxRates: {ids}"
            )

    titulo("LISTO")
    linea("  Nada fue escrito. Si los TaxCodes de arriba salieron con nombres y")
    linea("  sin [INACTIVO], el RPA deberia poder facturar otra vez.")


if __name__ == "__main__":
    main()
