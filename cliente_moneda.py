"""
clientes_moneda.py
------------------
Reporte INVESTIGATIVO de la MONEDA de cada cliente en QuickBooks, y cruce
contra los contratos activos para detectar los que NO van a poder facturarse.

POR QUE EXISTE:
  En QuickBooks (con multimoneda activa) cada cliente tiene UNA moneda fija:
  la de sus cuentas por cobrar. TODAS sus facturas deben ir en esa moneda, y
  una vez que el cliente tiene movimientos, esa moneda YA NO SE PUEDE CAMBIAR.
  No existe el "cliente multimoneda".

  Por eso, la moneda de la factura NO la decide el contrato: la decide el
  cliente en QuickBooks. Si el contrato esta en colones y el cliente en QBO
  es dolares, esa factura se rechaza con:
    "Cambie esta divisa de la transaccion para que coincida con la que usa
     para sus cuentas por cobrar y por pagar."  (codigo 6000)

  La unica salida es emitir en la moneda del cliente: para eso sirve el switch
  de "moneda invertida" (convierte del contrato a la moneda del cliente con el
  tipo de cambio del dia). No es una preferencia comercial, es una obligacion
  de QuickBooks.

QUE HACE:
  1) Lista todos los clientes de cada empresa con su moneda.
  2) Cruza los contratos ACTIVOS contra esa moneda y marca:
       [OK]      -> la moneda del contrato coincide con la del cliente.
       [SWITCH]  -> NO coincide: hay que activar "moneda invertida" en la
                    emision, o la factura sera rechazada por QuickBooks.
       [REVISAR] -> el cliente no se encontro en QuickBooks (o sin moneda).

*** SOLO SE EJECUTA EN EL VPS ***
  Consulta PRODUCCION y refresca el token de produccion. Exige
  QBO_ENTORNO=produccion para arrancar (blindaje).

SEGURIDAD:
  - En QuickBooks SOLO lee (GET). No crea ni modifica NADA.
  - En la base SOLO lee (SELECT). No escribe nada.

Requiere:  pip install requests python-dotenv psycopg2-binary
"""

import os
import sys
import json
import base64

import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# ── Configuracion ────────────────────────────────────────────────────────────
load_dotenv()

ENTORNO = os.getenv("QBO_ENTORNO", "sandbox").strip().lower()

PROD_BASE_URL = "https://quickbooks.api.intuit.com"
TOKENS_FILE = os.path.join("config", "tokens_empresas.json")

CLIENT_ID = os.getenv("QBO_CLIENT_ID")
CLIENT_SECRET = os.getenv("QBO_CLIENT_SECRET")
DATABASE_URL = os.getenv("DATABASE_URL")

EMPRESAS = [
    {"nombre": "Soportexperto.com S.A.", "realm": "9130355360397996"},
    {"nombre": "Hardware y Network S.A.", "realm": "9130355360390096"},
    {"nombre": "Corporacion Latinoamericana T.I.", "realm": "9130355360394696"},
]

# Como se guarda la moneda en nuestra base -> como la llama QuickBooks
MONEDA_QBO = {"Dólares": "USD", "Dolares": "USD", "Colones": "CRC"}


def refrescar_token(realm):
    """Refresca el token de una empresa y lo guarda. Devuelve el access_token."""
    with open(TOKENS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    empresa = data["empresas"][realm]
    auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    resp = requests.post(
        "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        data={"grant_type": "refresh_token", "refresh_token": empresa["refresh_token"]},
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"  [ERROR refresco] {resp.status_code} {resp.text[:200]}")
        return None
    nuevos = resp.json()
    empresa["access_token"] = nuevos["access_token"]
    empresa["refresh_token"] = nuevos["refresh_token"]  # Intuit rota el refresh
    with open(TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return nuevos["access_token"]


def descargar_clientes(realm, token):
    """Baja TODOS los clientes de una empresa con su moneda (solo lectura)."""
    clientes, inicio, lote = [], 1, 100
    while True:
        sql = f"SELECT * FROM Customer STARTPOSITION {inicio} MAXRESULTS {lote}"
        url = (
            f"{PROD_BASE_URL}/v3/company/{realm}/query"
            f"?query={requests.utils.quote(sql)}&minorversion=75"
        )
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"  [ERROR {resp.status_code}] {resp.text[:200]}")
            break
        lote_res = resp.json().get("QueryResponse", {}).get("Customer", [])
        if not lote_res:
            break
        clientes.extend(lote_res)
        if len(lote_res) < lote:
            break
        inicio += lote
    return clientes


def moneda_de(cliente):
    """Devuelve la moneda del cliente ('USD'/'CRC') o None si no viene."""
    return (cliente.get("CurrencyRef") or {}).get("value")


def leer_contratos_activos(conn):
    """Contratos activos con su moneda y el realm de su empresa (solo lectura)."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT c.id, c.nombre_cliente, c.moneda, c.qbo_customer_id,
                   c.compania_facturadora, e.realm_id
            FROM contratos_cronograma c
            LEFT JOIN empresas e ON e.nombre = c.compania_facturadora
            WHERE c.esta_activo = true
            ORDER BY c.compania_facturadora, c.nombre_cliente
        """)
        return cur.fetchall()


def main():
    if ENTORNO != "produccion":
        sys.exit(
            "BLINDAJE: este script es SOLO para produccion (VPS).\n"
            f"Detectado QBO_ENTORNO='{ENTORNO}'. Correrlo en local rotaria los\n"
            "tokens del VPS. Solo corre con QBO_ENTORNO=produccion."
        )
    if not (CLIENT_ID and CLIENT_SECRET):
        sys.exit("[ERROR] Falta QBO_CLIENT_ID / QBO_CLIENT_SECRET en el .env.")
    if not os.path.exists(TOKENS_FILE):
        sys.exit(f"[ERROR] No encuentro {TOKENS_FILE}.")

    print("=" * 78)
    print("MONEDA DE LOS CLIENTES EN QUICKBOOKS (PRODUCCION, SOLO LECTURA)")
    print("=" * 78)

    # mapa[(realm, customer_id)] = (nombre, moneda)
    mapa = {}

    for emp in EMPRESAS:
        print(f"\n=== {emp['nombre']}  (realm {emp['realm']}) ===")
        print("  Refrescando token...")
        token = refrescar_token(emp["realm"])
        if not token:
            print("  [SALTADA] no se pudo refrescar el token.")
            continue
        print("  Descargando clientes (solo lectura)...\n")
        clientes = descargar_clientes(emp["realm"], token)
        if not clientes:
            print("  (sin clientes o no se pudieron leer)")
            continue

        # Conteo por moneda, para ver el panorama de la empresa
        conteo = {}
        for c in clientes:
            m = moneda_de(c) or "(sin moneda)"
            conteo[m] = conteo.get(m, 0) + 1
            mapa[(emp["realm"], str(c.get("Id")))] = (
                c.get("DisplayName", ""), moneda_de(c)
            )

        resumen = "  ".join(f"{m}: {n}" for m, n in sorted(conteo.items()))
        print(f"  Total {len(clientes)} clientes  ->  {resumen}")

        # Listado detallado
        for c in sorted(clientes, key=lambda x: (x.get("DisplayName") or "").upper()):
            activo = "activo" if c.get("Active", True) else "INACTIVO"
            m = moneda_de(c) or "?"
            print(f"    Id {str(c.get('Id')):<5} | {m:<4} | {activo:<8} | {c.get('DisplayName','')}")

    # ── Cruce con los contratos activos ──
    if not DATABASE_URL:
        print("\n[aviso] Sin DATABASE_URL: no se puede cruzar con los contratos.")
        return

    print("\n" + "=" * 78)
    print("CRUCE: CONTRATOS ACTIVOS vs MONEDA DEL CLIENTE EN QUICKBOOKS")
    print("=" * 78)
    print("  [OK]     la moneda del contrato coincide con la del cliente en QBO.")
    print("  [SWITCH] NO coincide -> hay que activar 'moneda invertida' en la")
    print("           emision, o QuickBooks rechazara la factura.")
    print("  [REVISAR] el cliente no se encontro en QBO o no tiene moneda.")
    print("-" * 78)

    conn = psycopg2.connect(DATABASE_URL)
    try:
        contratos = leer_contratos_activos(conn)
    finally:
        conn.close()

    n_ok = n_switch = n_revisar = 0
    for ct in contratos:
        realm = ct.get("realm_id")
        cid = str(ct.get("qbo_customer_id") or "")
        moneda_contrato = MONEDA_QBO.get(ct.get("moneda"), "?")

        info = mapa.get((realm, cid))
        if not info or not info[1]:
            estado = "[REVISAR]"
            moneda_cliente = "?"
            n_revisar += 1
        else:
            moneda_cliente = info[1]
            if moneda_cliente == moneda_contrato:
                estado = "[OK]     "
                n_ok += 1
            else:
                estado = "[SWITCH] "
                n_switch += 1

        nombre = (ct.get("nombre_cliente") or "")[:38]
        print(
            f"  {estado} {nombre:<38} | contrato: {moneda_contrato:<4} "
            f"| cliente QBO: {moneda_cliente:<4}"
        )

    print("-" * 78)
    print(f"  Total contratos activos: {len(contratos)}")
    print(f"    OK (coinciden):        {n_ok}")
    print(f"    Necesitan SWITCH:      {n_switch}")
    print(f"    A revisar:             {n_revisar}")
    print("=" * 78)


if __name__ == "__main__":
    main()