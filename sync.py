"""
sync_clientes.py
----------------
PASO 1 DEL SYNC (solo COMPARA y REPORTA, NO escribe el mapeo todavia).

Que hace:
  1. Refresca SOLO el token de Soportexperto (para que no te pase lo del 401).
  2. Descarga TODOS los clientes de Soportexperto desde QuickBooks (solo lectura).
  3. Lee todos los clientes de tu base de datos (tabla `clientes`, solo lectura).
  4. Empareja cada cliente de la webapp con su cliente en QuickBooks (por nombre).
  5. Escribe un reporte CSV para que lo revises ANTES de guardar nada.

Seguridad:
  - En QuickBooks SOLO hace GET (lee). No crea ni modifica nada.
  - En la base SOLO hace SELECT (lee). No escribe en clientes_qbo todavia.
  - Lo unico que escribe: el reporte CSV y el refresco del token (auth, no datos).

Requiere una sola vez (con tu venv activo):
  pip install psycopg2-binary python-dotenv requests
"""

import os
import csv
import json
import base64
import unicodedata
import difflib
import sys

import requests
import psycopg2
from dotenv import load_dotenv

# ── Configuracion ────────────────────────────────────────────────────────────
load_dotenv()  # carga el .env de ESTA carpeta (QBO_CLIENT_ID, QBO_CLIENT_SECRET)

# Tambien cargamos el .env de la webapp para tomar DATABASE_URL de ahi.
# Si tu ruta es otra, cambiala aqui:
RUTA_ENV_WEBAPP = r"C:\RPA_SX_Quickbooks\.env"
if os.path.exists(RUTA_ENV_WEBAPP):
    load_dotenv(RUTA_ENV_WEBAPP, override=False)

PROD_BASE_URL = "https://quickbooks.api.intuit.com"
TOKENS_FILE = os.path.join("config", "tokens_empresas.json")

# Soportexperto
REALM_SOPORTEXPERTO = "9130355360397996"
EMPRESA_ID_SOPORTEXPERTO = (
    "ec006548-c1a1-4212-aaf5-605041ce7d3e"  # para el PASO 2 (insertar mapeo)
)

CLIENT_ID = os.getenv("QBO_CLIENT_ID")
CLIENT_SECRET = os.getenv("QBO_CLIENT_SECRET")
DATABASE_URL = os.getenv("DATABASE_URL")

REPORTE_CSV = "reporte_match_clientes.csv"


# ── Token: refresco automatico (no dependemos de renovar_token.py) ────────────
def refrescar_token_soportexperto():
    with open(TOKENS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    empresa = data["empresas"][REALM_SOPORTEXPERTO]
    refresh_token = empresa["refresh_token"]

    auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    resp = requests.post(
        "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=30,
    )
    if resp.status_code != 200:
        print(
            f"[ERROR] No se pudo refrescar el token: {resp.status_code} {resp.text[:200]}"
        )
        sys.exit(1)

    nuevos = resp.json()
    empresa["access_token"] = nuevos["access_token"]
    empresa["refresh_token"] = nuevos[
        "refresh_token"
    ]  # Intuit rota el refresh; hay que guardarlo
    with open(TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    print("    Token de Soportexperto refrescado.")
    return nuevos["access_token"]


# ── Descarga de clientes de QuickBooks (solo lectura, paginado) ───────────────
def descargar_clientes_qbo(token):
    clientes = []
    inicio = 1
    lote = 100
    while True:
        sql = f"SELECT * FROM Customer STARTPOSITION {inicio} MAXRESULTS {lote}"
        url = f"{PROD_BASE_URL}/v3/company/{REALM_SOPORTEXPERTO}/query?query={requests.utils.quote(sql)}"
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"[ERROR {resp.status_code}] {resp.text[:300]}")
            sys.exit(1)
        lote_res = resp.json().get("QueryResponse", {}).get("Customer", [])
        if not lote_res:
            break
        clientes.extend(lote_res)
        if len(lote_res) < lote:
            break
        inicio += lote
    return clientes


# ── Lectura de clientes de la base de datos (solo lectura) ────────────────────
def leer_clientes_db():
    if not DATABASE_URL:
        print("[ERROR] No encontre DATABASE_URL.")
        print(
            "Opciones: agregala al .env de esta carpeta, o corrige RUTA_ENV_WEBAPP arriba."
        )
        sys.exit(1)
    conn = psycopg2.connect(DATABASE_URL)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, nombre, cedula_juridica, activo FROM clientes ORDER BY nombre;"
        )
        filas = cur.fetchall()
    finally:
        conn.close()
    return filas  # cada fila: (id, nombre, cedula, activo)


# ── Normalizacion de nombres para emparejar ──────────────────────────────────
SUFIJOS = [
    " SA",
    " S A",
    " SRL",
    " S R L",
    " LTDA",
    " LIMITADA",
    " SOCIEDAD ANONIMA",
    " DE RESPONSABILIDAD LIMITADA",
    " DOL",
    " COL",
    " CRC",
    " USD",
]


def normalizar(nombre):
    if not nombre:
        return ""
    s = unicodedata.normalize("NFKD", nombre)
    s = "".join(c for c in s if not unicodedata.combining(c))  # quitar acentos
    s = s.upper()
    for ch in ".,;:()/-_\u2014\u2013":  # quitar puntuacion
        s = s.replace(ch, " ")
    s = " ".join(s.split())  # colapsar espacios
    cambiado = True
    while cambiado:  # quitar sufijos societarios
        cambiado = False
        for suf in SUFIJOS:
            if s.endswith(suf):
                s = s[: -len(suf)].strip()
                cambiado = True
    return s


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    if not CLIENT_ID or not CLIENT_SECRET:
        print("[ERROR] Falta QBO_CLIENT_ID / QBO_CLIENT_SECRET en el .env.")
        sys.exit(1)

    print("=" * 60)
    print("SYNC PASO 1 - COMPARACION DE CLIENTES (Soportexperto)")
    print("=" * 60)

    print("\n[1] Refrescando token...")
    token = refrescar_token_soportexperto()

    print("[2] Descargando clientes de QuickBooks (solo lectura)...")
    qbo = descargar_clientes_qbo(token)
    print(f"    {len(qbo)} clientes en QuickBooks.")

    print("[3] Leyendo clientes de la base de datos (solo lectura)...")
    db = leer_clientes_db()
    print(f"    {len(db)} clientes en la base de datos.")

    # indice de QBO por nombre normalizado
    qbo_por_norm = {}
    for c in qbo:
        norm = normalizar(c.get("DisplayName", ""))
        qbo_por_norm.setdefault(norm, []).append(c)
    nombres_norm_qbo = list(qbo_por_norm.keys())

    print("[4] Emparejando...")
    filas_reporte = []
    n_match = n_ambiguo = n_sin = 0
    for cid, nombre, cedula, activo in db:
        norm = normalizar(nombre)
        candidatos = qbo_por_norm.get(norm, [])
        if len(candidatos) == 1:
            c = candidatos[0]
            n_match += 1
            filas_reporte.append(
                [
                    "MATCH",
                    cid,
                    nombre,
                    cedula or "",
                    c["Id"],
                    c.get("DisplayName", ""),
                    "",
                ]
            )
        elif len(candidatos) > 1:
            n_ambiguo += 1
            ids = "; ".join(f"{x['Id']}={x.get('DisplayName','')}" for x in candidatos)
            filas_reporte.append(["AMBIGUO", cid, nombre, cedula or "", "", "", ids])
        else:
            sug = difflib.get_close_matches(norm, nombres_norm_qbo, n=3, cutoff=0.8)
            sugerencias = "; ".join(
                f"{qbo_por_norm[s][0]['Id']}={qbo_por_norm[s][0].get('DisplayName','')}"
                for s in sug
            )
            n_sin += 1
            filas_reporte.append(
                ["SIN_MATCH", cid, nombre, cedula or "", "", "", sugerencias]
            )

    with open(REPORTE_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "estado",
                "cliente_db_id",
                "cliente_db_nombre",
                "cedula",
                "qbo_customer_id",
                "qbo_display_name",
                "sugerencias_o_candidatos",
            ]
        )
        w.writerows(filas_reporte)

    print("\n" + "=" * 60)
    print("RESUMEN")
    print(f"  MATCH     (1 a 1)      : {n_match}")
    print(f"  AMBIGUO   (varios QBO) : {n_ambiguo}")
    print(f"  SIN_MATCH (revisar)    : {n_sin}")
    print(f"\nReporte escrito en: {REPORTE_CSV}")
    print("Revisalo antes de que insertemos nada en clientes_qbo.")
    print("=" * 60)


if __name__ == "__main__":
    main()
