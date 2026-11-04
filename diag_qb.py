# -*- coding: utf-8 -*-
"""
diag_qb.py  —  DIAGNOSTICO QUICKBOOKS MULTI-EMPRESA (SOLO LECTURA)

No escribe NADA: ni tokens_empresas.json, ni base de datos, ni facturas.
Refresca tokens en memoria y hace consultas de lectura.

USO
---
  Las tres empresas de una:
      py diag_qb.py

  Solo algunas (por alias o por realm):
      py diag_qb.py sx hyn
      py diag_qb.py 9130355360397996 9130355360390096

  Con un access_token del Playground (solo sirve para UNA empresa):
      py diag_qb.py sx --access-token eyJhb...

  Listado completo de TaxRates y TaxCodes inactivos:
      py diag_qb.py --detalle

CREDENCIALES
------------
QB_CLIENT_ID / QB_CLIENT_SECRET del entorno o del .env del directorio.
(No hacen falta si usas --access-token.)

DONDE CORRERLO
--------------
  VPS   : cualquier modo.
  Local : SOLO con --access-token, para no rotar el refresh_token del VPS.
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

# realm -> (nombre, alias que podes escribir en la linea de comandos)
EMPRESAS = [
    ("9130355360397996", "Soportexperto.com S.A.", ("sx", "soportexperto")),
    ("9130355360390096", "Hardware y Network S.A.", ("hyn", "h&n", "hn", "hardware")),
    (
        "9130355360394696",
        "Corporacion Latinoamericana T.I.",
        ("lc", "laitcorp", "corporacion"),
    ),
]
NOMBRES = {r: n for r, n, _ in EMPRESAS}


# ----------------------------------------------------------------------
# utilidades
# ----------------------------------------------------------------------
def linea(txt=""):
    print(txt)


def titulo(txt, ancho=78):
    print("\n" + "=" * ancho)
    print("  " + txt)
    print("=" * ancho)


def subtitulo(txt, ancho=78):
    print("\n" + "-" * ancho)
    print("  " + txt)
    print("-" * ancho)


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


def resolver_realms(args_realms):
    """Convierte alias o realms sueltos en una lista de realmIds."""
    if not args_realms:
        return [r for r, _, _ in EMPRESAS]

    salida = []
    for token in args_realms:
        t = token.strip().lower()
        encontrado = None
        for realm, _nombre, alias in EMPRESAS:
            if t == realm or t in alias:
                encontrado = realm
                break
        if encontrado is None:
            if t.isdigit():
                encontrado = t  # realm que no esta en la tabla: igual se prueba
            else:
                linea(f"[X] No reconozco '{token}'. Usa sx / hyn / lc o un realmId.")
                sys.exit(1)
        if encontrado not in salida:
            salida.append(encontrado)
    return salida


def buscar_refresh_token(obj, realm):
    """
    Busca recursivamente en el JSON de tokens un refresh_token asociado al realm.
    Tolera distintas estructuras de archivo.
    """
    if isinstance(obj, dict):
        if realm in obj and isinstance(obj[realm], dict):
            rt = obj[realm].get("refresh_token")
            if rt:
                return rt
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
def refrescar(refresh_token, client_id, client_secret, res):
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

    tid = resp.headers.get("intuit_tid", "(no vino)")
    linea(f"    HTTP {resp.status_code}   intuit_tid: {tid}")

    if resp.status_code != 200:
        res["refresh"] = f"FALLO {resp.status_code}"
        res["tid"] = tid
        linea("    [FALLO EL REFRESH] respuesta de Intuit:")
        linea("    " + resp.text[:400])
        return None

    data = resp.json()
    nuevo_rt = data.get("refresh_token", "")
    res["refresh"] = "OK"
    linea("    Refresh OK. (el refresh_token nuevo NO se guarda: solo lectura)")
    if nuevo_rt and nuevo_rt != refresh_token:
        linea(
            "    OJO: Intuit roto el refresh_token. El del VPS sigue siendo el valido."
        )
    return data["access_token"]


# ----------------------------------------------------------------------
# lecturas
# ----------------------------------------------------------------------
def query(access_token, realm, sql, etiqueta):
    url = f"{BASE}/v3/company/{realm}/query"
    resp = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        params={"query": sql, "minorversion": MINORVERSION},
        timeout=60,
    )
    tid = resp.headers.get("intuit_tid", "(no vino)")
    linea(f"    {etiqueta}: HTTP {resp.status_code}   intuit_tid: {tid}")
    if resp.status_code != 200:
        linea("      " + resp.text[:400])
        return None, tid
    return resp.json().get("QueryResponse", {}), tid


def procesar_empresa(realm, args):
    """Devuelve un dict con el resultado resumido de esta empresa."""
    nombre = NOMBRES.get(realm, "(empresa desconocida)")
    res = {
        "realm": realm,
        "nombre": nombre,
        "refresh": "-",
        "company": "-",
        "taxcodes": "-",
        "tid": "",
    }

    subtitulo(f"{nombre}   realm {realm}")

    # ---- token ----
    if args.access_token:
        access_token = args.access_token.strip()
        res["refresh"] = "n/a (Playground)"
        linea("    Usando el access_token que pasaste. No se refresca nada.")
    else:
        refresh_token = (
            buscar_refresh_token(args._tokens, realm) if args._tokens else None
        )
        if not refresh_token:
            res["refresh"] = "SIN TOKEN"
            linea(f"    [X] No hay refresh_token para este realm en {TOKENS_FILE}.")
            return res
        access_token = refrescar(
            refresh_token, args._client_id, args._client_secret, res
        )
        if not access_token:
            return res

    # ---- companyinfo: la prueba de fuego ----
    url = f"{BASE}/v3/company/{realm}/companyinfo/{realm}"
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
    res["tid"] = tid
    linea(f"    companyinfo: HTTP {resp.status_code}   intuit_tid: {tid}")

    if resp.status_code != 200:
        res["company"] = f"HTTP {resp.status_code}"
        linea("      " + resp.text[:400])
        linea("    >>> SIN ACCESO a este realm. Guarda el intuit_tid de arriba.")
        return res

    info = resp.json().get("CompanyInfo", {})
    res["company"] = "OK"
    linea(f"    >>> ACCESO OK: {info.get('CompanyName', '(sin nombre)')}")

    # ---- TaxRates ----
    qr, _ = query(
        access_token, realm, "SELECT * FROM TaxRate MAXRESULTS 200", "TaxRates "
    )
    rates = (qr or {}).get("TaxRate", [])
    linea(f"      {len(rates)} TaxRates")
    if args.detalle and rates:
        for r in sorted(rates, key=lambda x: str(x.get("Name", ""))):
            marca = "" if r.get("Active", True) else "  [INACTIVO]"
            linea(
                f"        Id {str(r.get('Id','?')):>4}  "
                f"{str(r.get('RateValue','?')):>6}%  {r.get('Name','?')}{marca}"
            )

    # ---- TaxCodes ----
    qr, _ = query(
        access_token, realm, "SELECT * FROM TaxCode MAXRESULTS 200", "TaxCodes "
    )
    codes = (qr or {}).get("TaxCode", [])
    activos = [c for c in codes if c.get("Active", True)]
    res["taxcodes"] = f"{len(activos)} act / {len(codes)}"
    linea(f"      {len(codes)} TaxCodes  ({len(activos)} activos)")

    mostrar = codes if args.detalle else activos
    for c in sorted(mostrar, key=lambda x: str(x.get("Name", ""))):
        marca = "" if c.get("Active", True) else "  [INACTIVO]"
        ids = []
        for grupo in (c.get("SalesTaxRateList") or {}).get("TaxRateDetail", []):
            ref = (grupo.get("TaxRateRef") or {}).get("value")
            if ref:
                ids.append(str(ref))
        linea(
            f"        Id {str(c.get('Id','?')):>4}  '{c.get('Name','?')}'{marca}"
            f"   TaxRates: {ids}"
        )

    return res


# ----------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "realms", nargs="*", help="alias (sx, hyn, lc) o realmIds. Vacio = las tres."
    )
    ap.add_argument(
        "--access-token", help="access_token del Playground (una sola empresa)"
    )
    ap.add_argument(
        "--detalle",
        action="store_true",
        help="lista TaxRates completos y TaxCodes inactivos",
    )
    args = ap.parse_args()

    cargar_dotenv()
    realms = resolver_realms(args.realms)

    if args.access_token and len(realms) > 1:
        linea(
            "[X] --access-token sirve para UNA sola empresa "
            "(el token esta atado a esa autorizacion)."
        )
        linea("    Ejemplo:  py diag_qb.py sx --access-token eyJhb...")
        sys.exit(1)

    titulo(f"DIAGNOSTICO QUICKBOOKS  —  {len(realms)} empresa(s)")
    linea("  MODO: SOLO LECTURA. No se escribe ningun archivo ni la base.")

    # credenciales y tokens, una sola vez
    args._tokens = None
    args._client_id = None
    args._client_secret = None

    if not args.access_token:
        if not os.path.exists(TOKENS_FILE):
            linea(
                f"  [X] No encuentro {TOKENS_FILE}. Corre esto en el VPS, o usa --access-token."
            )
            sys.exit(1)
        with open(TOKENS_FILE, "r", encoding="utf-8") as fh:
            args._tokens = json.load(fh)
        linea(f"  Tokens leidos de {TOKENS_FILE} (archivo intacto).")

        args._client_id = os.environ.get("QB_CLIENT_ID")
        args._client_secret = os.environ.get("QB_CLIENT_SECRET")
        if not args._client_id or not args._client_secret:
            linea(
                "  [X] Faltan QB_CLIENT_ID / QB_CLIENT_SECRET en el entorno o el .env."
            )
            sys.exit(1)
        linea(f"  client_id: {args._client_id[:12]}...")

    resultados = []
    for realm in realms:
        try:
            resultados.append(procesar_empresa(realm, args))
        except requests.RequestException as e:
            linea(f"    [X] Error de red: {e}")
            resultados.append(
                {
                    "realm": realm,
                    "nombre": NOMBRES.get(realm, "?"),
                    "refresh": "ERROR RED",
                    "company": "-",
                    "taxcodes": "-",
                    "tid": "",
                }
            )

    # ---- resumen ----
    titulo("RESUMEN")
    linea(f"  {'EMPRESA':<34} {'REFRESH':<16} {'COMPANYINFO':<14} {'TAXCODES':<14}")
    linea("  " + "-" * 76)
    for r in resultados:
        linea(
            f"  {r['nombre'][:33]:<34} {r['refresh']:<16} "
            f"{r['company']:<14} {r['taxcodes']:<14}"
        )

    fallidas = [r for r in resultados if r["company"] != "OK"]
    linea("")
    if not fallidas:
        linea(
            "  >>> Las tres responden. Si los TaxCodes salieron con nombres y activos,"
        )
        linea("      el RPA deberia poder facturar otra vez sin tocar codigo.")
    else:
        linea("  >>> Siguen sin acceso:")
        for r in fallidas:
            linea(
                f"        {r['nombre']}  (realm {r['realm']})   intuit_tid: {r['tid']}"
            )
        linea("      Esos intuit_tid son los que pide soporte de Intuit.")

    linea("\n  Nada fue escrito.")


if __name__ == "__main__":
    main()
