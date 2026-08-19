"""
rpa_sync_consecutivo.py  —  Sincroniza el CONSECUTIVO REAL de Hacienda
======================================================================
Es el "rpa_fijos al reves": en vez de crear facturas (BD -> QuickBooks),
este LEE de QuickBooks y actualiza la BD (QuickBooks -> BD).

EL PROBLEMA QUE RESUELVE:
  Cuando rpa_fijos crea una factura, QuickBooks devuelve un DocNumber
  preliminar (ej. 75623573) que se guarda en emisiones_cronograma.qbo_doc_number.
  Minutos despues, Mondragon procesa la factura y:
    - Reescribe el DocNumber de QuickBooks al definitivo (ej. FE-004633).
    - Llena un CustomField con el consecutivo largo de Hacienda
      (ej. 00200001010000004633).
  Ese numero preliminar guardado ya NO sirve para buscar por DocNumber (fue
  pisado). El unico puente estable es qbo_invoice_id (el Id interno de QBO,
  que NUNCA cambia).

QUE HACE ESTE RPA:
  1. Lee de la BD las emisiones EMITIDAS, con qbo_invoice_id, que AUN no
     tengan numero_factura (numero_factura IS NULL = pendiente de sincronizar).
  2. Las agrupa por empresa (realm) para usar el token correcto.
  3. Busca cada factura en QBO POR SU Id (no por DocNumber).
  4. Extrae el consecutivo largo del CustomField que corresponde a esa empresa
     (mapa fijo por empresa; si falla, modo patron de respaldo).
  5. Valida que el consecutivo termine en los digitos del DocNumber definitivo.
  6. Actualiza la BD:
       numero_factura  = consecutivo largo         (00200001010000004633)
       qbo_doc_number  = "<preliminar> - <DocNumber definitivo>"
                         (ej. "75623573 - FE-004633")  <- concatenado, NO pisa
  7. Si la factura aun no tiene consecutivo (Mondragon no la proceso todavia),
     la SALTA sin error para la proxima corrida.

IDEMPOTENCIA:
  Solo procesa emisiones con numero_factura IS NULL. Una vez sincronizada, esa
  fila ya no se vuelve a tocar -> la concatenacion de qbo_doc_number NUNCA se
  duplica.

INVOCACION:
  - Programado (9pm CR, programador de tareas) -> corre las 3 empresas.
  - Manual / desde el webapp (boton superadmin) -> igual, sincroniza todo.
  - --dry-run  -> hace TODO el recorrido pero NO escribe en la BD; imprime lo
                  que haria. Util para probar antes de aplicar.

SALIDA:
  - Logs legibles en consola (para la corrida programada y para correrlo a mano).
  - Un bloque JSON de metricas por empresa a stdout al final, entre los
    marcadores ###JSON_INICIO### y ###JSON_FIN###, para que el webapp lo
    capture y muestre el resultado.
  - Tarjeta a Teams (mismo modulo que rpa_fijos).

Requiere:  pip install psycopg2-binary python-dotenv requests
.env:  DATABASE_URL, QBO_ENTORNO, QBO_CLIENT_ID/SECRET (prod) o
       QBO_SANDBOX_CLIENT_ID/SECRET (sandbox), TEAMS_WEBHOOK_URL.
"""

import os
import sys
import json
import time
import base64
import argparse
import datetime
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

from teams_notifier import enviar_tarjeta_ejecucion, enviar_tarjeta_simple

# ════════════════════════════════════════════════════════════════════════════
#  CONFIGURACION
# ════════════════════════════════════════════════════════════════════════════

load_dotenv()

ENTORNO = os.getenv("QBO_ENTORNO", "sandbox").strip().lower()

ENTORNOS = {
    "sandbox": {
        "base_url": "https://sandbox-quickbooks.api.intuit.com",
        "tokens_file": os.path.join("config", "tokens_sandbox.json"),
        "client_id": os.getenv("QBO_SANDBOX_CLIENT_ID"),
        "client_secret": os.getenv("QBO_SANDBOX_CLIENT_SECRET"),
        "realm_fijo": "9341456664539574",
    },
    "produccion": {
        "base_url": "https://quickbooks.api.intuit.com",
        "tokens_file": os.path.join("config", "tokens_empresas.json"),
        "client_id": os.getenv("QBO_CLIENT_ID"),
        "client_secret": os.getenv("QBO_CLIENT_SECRET"),
        "realm_fijo": None,
    },
}

if ENTORNO not in ENTORNOS:
    sys.exit(f"QBO_ENTORNO invalido: '{ENTORNO}'. Use 'sandbox' o 'produccion'.")

CFG = ENTORNOS[ENTORNO]
DATABASE_URL = os.getenv("DATABASE_URL")
REALMS_PRODUCCION = {"9130355360397996", "9130355360394696", "9130355360390096"}

# ── Mapa: nombre de empresa por realm (para logs y metricas legibles) ────────
NOMBRE_EMPRESA = {
    "9130355360397996": "Soportexperto",
    "9130355360390096": "Hardware y Network",
    "9130355360394696": "Laitcorp",
}

# ── CustomField donde vive el consecutivo, POR EMPRESA ───────────────────────
# Descubierto con investigar_consecutivo.py: el campo cambia de nombre entre
# empresas. En Soportexperto el consecutivo quedo en "COTIZACION" (¡no en
# "No. Consecutivo", que esta vacio!); en Hardware y Laitcorp en "No. Consecutivo".
# Los nombres se comparan normalizados (sin tildes, sin espacios de sobra,
# minusculas) para no depender de como los devuelva QBO.
CAMPO_CONSECUTIVO_POR_REALM = {
    "9130355360397996": "cotizacion",  # Soportexperto
    "9130355360390096": "no. consecutivo",  # Hardware y Network
    "9130355360394696": "no. consecutivo",  # Laitcorp
}

TEAMS_WEBHOOK_URL = os.getenv("TEAMS_WEBHOOK_URL")


# ════════════════════════════════════════════════════════════════════════════
#  TOKENS  (identico a rpa_fijos)
# ════════════════════════════════════════════════════════════════════════════


def get_access_token(realm):
    f = CFG["tokens_file"]
    with open(f, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    nodo = data if ENTORNO == "sandbox" else data["empresas"][realm]
    refresh_token = nodo["refresh_token"]

    auth = base64.b64encode(
        f"{CFG['client_id']}:{CFG['client_secret']}".encode()
    ).decode()
    r = requests.post(
        "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(
            f"No se pudo refrescar token ({r.status_code}): {r.text[:200]}"
        )
    nuevos = r.json()
    nodo["access_token"] = nuevos["access_token"]
    nodo["refresh_token"] = nuevos["refresh_token"]
    with open(f, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return nuevos["access_token"]


# ════════════════════════════════════════════════════════════════════════════
#  BASE DE DATOS
# ════════════════════════════════════════════════════════════════════════════


def leer_emisiones_pendientes(conn):
    """
    Emisiones EMITIDAS, con qbo_invoice_id, SIN numero_factura todavia.
    numero_factura IS NULL = aun no se le sincronizo el consecutivo.
    Se trae el realm de la empresa (por el JOIN) para agrupar por token.
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT
                em.id              AS emision_id,
                em.qbo_invoice_id,
                em.qbo_doc_number,
                em.numero_factura,
                c.compania_facturadora,
                e.realm_id
            FROM emisiones_cronograma em
            JOIN contratos_cronograma c ON c.id = em.contrato_id
            LEFT JOIN empresas e ON e.nombre = c.compania_facturadora
            WHERE em.estado = 'Emitida'
              AND em.qbo_invoice_id IS NOT NULL
              AND em.numero_factura IS NULL
            ORDER BY e.realm_id, em.facturado_en
        """)
        return cur.fetchall()


def actualizar_consecutivo(conn, emision_id, numero_factura, qbo_doc_number_nuevo):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE emisiones_cronograma
            SET numero_factura = %s,
                qbo_doc_number = %s,
                updated_at     = now()
            WHERE id = %s AND numero_factura IS NULL
        """,
            (numero_factura, qbo_doc_number_nuevo, emision_id),
        )
    conn.commit()


# ════════════════════════════════════════════════════════════════════════════
#  QUICKBOOKS  —  lectura de la factura por Id
# ════════════════════════════════════════════════════════════════════════════


def leer_factura_qbo(realm, token, invoice_id):
    """Trae una factura de QBO por su Id interno (estable, no cambia).
    Devuelve el dict de la factura o None si no existe."""
    sql = f"SELECT * FROM Invoice WHERE Id = '{invoice_id}'"
    url = (
        f"{CFG['base_url']}/v3/company/{realm}/query"
        f"?query={requests.utils.quote(sql)}&minorversion=75"
    )
    r = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"QBO {r.status_code}: {r.text[:200]}")
    facturas = r.json().get("QueryResponse", {}).get("Invoice", [])
    return facturas[0] if facturas else None


# ════════════════════════════════════════════════════════════════════════════
#  EXTRACCION DEL CONSECUTIVO
# ════════════════════════════════════════════════════════════════════════════


def _normalizar(txt):
    """minusculas, sin tildes, sin espacios de sobra — para comparar nombres
    de CustomField sin depender de como los escriba QBO."""
    if not txt:
        return ""
    t = txt.strip().lower()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")):
        t = t.replace(a, b)
    return " ".join(t.split())


def _es_consecutivo_valido(valor, docnumber):
    """El consecutivo de Hacienda son ~20 digitos y SIEMPRE termina en los
    digitos del DocNumber definitivo (ej. FE-004633 -> ...0000004633).
    Se valida ambas cosas para no traer basura de otro CustomField."""
    if not valor:
        return False
    v = valor.strip()
    if not v.isdigit() or len(v) < 15:
        return False
    # Digitos del DocNumber (FE-004633 -> "004633")
    digitos_doc = "".join(ch for ch in (docnumber or "") if ch.isdigit())
    if not digitos_doc:
        return False
    return v.endswith(digitos_doc)


def extraer_consecutivo(factura, realm):
    """Devuelve el consecutivo largo de la factura, o None si aun no esta.

    Estrategia (como pidió Irving): PRIMERO el campo fijo por empresa; si ese
    viene vacio o no valida, SEGUNDO modo patron (recorre todos los CustomField
    y agarra el que tenga pinta de consecutivo Y termine en el DocNumber).
    """
    docnumber = factura.get("DocNumber") or ""
    campos = factura.get("CustomField", []) or []

    # ── 1) Campo fijo por empresa ──
    esperado = CAMPO_CONSECUTIVO_POR_REALM.get(str(realm))
    if esperado:
        for cf in campos:
            if _normalizar(cf.get("Name")) == esperado:
                val = (cf.get("StringValue") or "").strip()
                if _es_consecutivo_valido(val, docnumber):
                    return val
                break  # el campo existe pero aun vacio/invalido -> intentar patron

    # ── 2) Modo patron (respaldo) ──
    # Recorre TODOS los CustomField y toma el primero que valide contra el
    # DocNumber. Asi, si Mondragon cambiara el campo, el RPA no se rompe.
    for cf in campos:
        val = (cf.get("StringValue") or "").strip()
        if _es_consecutivo_valido(val, docnumber):
            return val

    return None


# ════════════════════════════════════════════════════════════════════════════
#  PROCESO PRINCIPAL
# ════════════════════════════════════════════════════════════════════════════


def _metricas_empresa():
    return {"sincronizadas": 0, "sin_consecutivo": 0, "con_error": 0}


def sincronizar(dry_run=False):
    inicio = time.time()
    print("=" * 64)
    print(f"RPA SYNC CONSECUTIVO  —  ENTORNO: {ENTORNO.upper()}")
    print(f"Fecha: {datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
    if dry_run:
        print("   *** DRY-RUN: no se escribe en la base de datos ***")
    print("=" * 64)

    # Metricas por empresa + acumulado global
    por_empresa = {}  # realm -> dict de conteos
    total = _metricas_empresa()
    errores_detalle = []

    conn = psycopg2.connect(DATABASE_URL)
    tokens = {}
    try:
        pendientes = leer_emisiones_pendientes(conn)
        print(f"\nEmisiones pendientes de sincronizar: {len(pendientes)}\n")

        for em in pendientes:
            eid = em["emision_id"]
            realm = em.get("realm_id")
            empresa_lbl = NOMBRE_EMPRESA.get(
                str(realm), em.get("compania_facturadora", "?")
            )
            invoice_id = em.get("qbo_invoice_id")
            preliminar = em.get("qbo_doc_number") or ""

            if realm not in por_empresa:
                por_empresa[realm] = _metricas_empresa()
            m = por_empresa[realm]

            # Blindaje: en sandbox jamas un realm de produccion
            if ENTORNO == "sandbox" and str(realm) in REALMS_PRODUCCION:
                sys.exit(
                    "BLINDAJE: en sandbox aparecio un realm de produccion. Abortando."
                )

            if not realm:
                m["con_error"] += 1
                total["con_error"] += 1
                errores_detalle.append(f"{empresa_lbl}: emision sin realm")
                print(f"  [ERR]  {empresa_lbl}: emision sin realm ({eid})")
                continue

            try:
                if realm not in tokens:
                    # En sandbox el token es unico; en prod, por realm
                    tokens[realm] = get_access_token(
                        realm if ENTORNO == "produccion" else realm
                    )

                factura = leer_factura_qbo(realm, tokens[realm], invoice_id)
                if not factura:
                    m["con_error"] += 1
                    total["con_error"] += 1
                    errores_detalle.append(
                        f"{empresa_lbl}: factura Id {invoice_id} no existe en QBO"
                    )
                    print(
                        f"  [ERR]  {empresa_lbl}: factura Id {invoice_id} no existe en QBO"
                    )
                    continue

                docnumber_def = factura.get("DocNumber") or ""
                consecutivo = extraer_consecutivo(factura, realm)

                if not consecutivo:
                    # Mondragon aun no la proceso -> se salta para otra corrida
                    m["sin_consecutivo"] += 1
                    total["sin_consecutivo"] += 1
                    print(
                        f"  [ESPERA] {empresa_lbl}: factura {docnumber_def or invoice_id} "
                        f"aun sin consecutivo (se reintenta luego)"
                    )
                    continue

                # qbo_doc_number nuevo = concatenacion preliminar - definitivo,
                # para conservar el numero con que nacio + el FE que se ve en QBO.
                doc_concatenado = f"{preliminar} - {docnumber_def}".strip(" -")

                if dry_run:
                    print(
                        f"  [DRY]  {empresa_lbl}: {docnumber_def}  ->  "
                        f"numero_factura={consecutivo} | qbo_doc_number='{doc_concatenado}'"
                    )
                else:
                    actualizar_consecutivo(conn, eid, consecutivo, doc_concatenado)
                    print(
                        f"  [OK]   {empresa_lbl}: {docnumber_def}  ->  "
                        f"consecutivo {consecutivo}"
                    )

                m["sincronizadas"] += 1
                total["sincronizadas"] += 1

            except Exception as e:
                m["con_error"] += 1
                total["con_error"] += 1
                errores_detalle.append(f"{empresa_lbl}: {e}")
                print(f"  [ERR]  {empresa_lbl}: {e}")
    finally:
        conn.close()

    duracion = int(time.time() - inicio)
    total["duracion"] = f"{duracion // 60}m {duracion % 60}s"

    # ── Resumen en consola ──
    print("\n" + "=" * 64)
    print("RESUMEN POR EMPRESA")
    for realm, m in por_empresa.items():
        nombre = NOMBRE_EMPRESA.get(str(realm), str(realm))
        print(
            f"  {nombre:<20} sincronizadas: {m['sincronizadas']:<3} "
            f"en espera: {m['sin_consecutivo']:<3} errores: {m['con_error']}"
        )
    print(
        f"  {'TOTAL':<20} sincronizadas: {total['sincronizadas']:<3} "
        f"en espera: {total['sin_consecutivo']:<3} errores: {total['con_error']}"
    )
    if dry_run:
        print("\n  (DRY-RUN: no se escribio nada en la base de datos)")
    print("=" * 64)

    # ── Payload de metricas para el webapp / Teams ──
    resultado = {
        "entorno": ENTORNO,
        "dry_run": dry_run,
        "fecha": datetime.datetime.now().strftime("%d/%m/%Y %H:%M"),
        "duracion": total["duracion"],
        "total": {
            "sincronizadas": total["sincronizadas"],
            "sin_consecutivo": total["sin_consecutivo"],
            "con_error": total["con_error"],
        },
        "por_empresa": [
            {
                "realm": str(realm),
                "nombre": NOMBRE_EMPRESA.get(str(realm), str(realm)),
                "sincronizadas": m["sincronizadas"],
                "sin_consecutivo": m["sin_consecutivo"],
                "con_error": m["con_error"],
            }
            for realm, m in por_empresa.items()
        ],
        "errores": errores_detalle,
    }

    return resultado


# ════════════════════════════════════════════════════════════════════════════
#  TEAMS
# ════════════════════════════════════════════════════════════════════════════


def notificar_teams(resultado):
    """Manda la tarjeta a Teams reutilizando el modulo de rpa_fijos.

    El modulo colorea la tarjeta con las llaves 'facturadas_ok' y 'con_error',
    asi que se mapean: sincronizadas -> facturadas_ok, con_error -> con_error.
    """
    if not TEAMS_WEBHOOK_URL:
        print("[Teams] Sin TEAMS_WEBHOOK_URL; no se notifica.")
        return

    t = resultado["total"]
    metricas = {
        "facturadas_ok": t["sincronizadas"],
        "con_error": t["con_error"],
    }

    # Si no hubo NADA que sincronizar ni errores -> tarjeta minima (latido).
    if t["sincronizadas"] == 0 and t["con_error"] == 0 and t["sin_consecutivo"] == 0:
        try:
            enviar_tarjeta_simple(
                webhook_url=TEAMS_WEBHOOK_URL,
                titulo="RPA Sync Consecutivo",
                subtitulo=f"{resultado['entorno'].upper()}  -  {resultado['fecha']}",
                mensaje="Ejecucion correcta. No habia facturas pendientes de sincronizar.",
            )
        except Exception as e:
            print(f"[aviso] No se pudo notificar a Teams: {e}")
        return

    # Hechos por empresa + total
    hechos = [
        {"title": "Sincronizadas", "value": str(t["sincronizadas"])},
        {"title": "En espera (sin consecutivo)", "value": str(t["sin_consecutivo"])},
        {"title": "Con error", "value": str(t["con_error"])},
    ]
    for e in resultado["por_empresa"]:
        hechos.append(
            {
                "title": e["nombre"],
                "value": (
                    f"{e['sincronizadas']} ok / {e['sin_consecutivo']} espera / "
                    f"{e['con_error']} err"
                ),
            }
        )
    hechos.append({"title": "Duracion", "value": resultado["duracion"]})

    texto_pie = None
    if resultado["errores"]:
        texto_pie = "Errores: " + "; ".join(resultado["errores"][:5])
        if len(resultado["errores"]) > 5:
            texto_pie += f"  (+{len(resultado['errores']) - 5} mas)"

    titulo = "RPA Sync Consecutivo"
    if resultado["dry_run"]:
        titulo += " (DRY-RUN)"

    try:
        enviar_tarjeta_ejecucion(
            webhook_url=TEAMS_WEBHOOK_URL,
            nombre_proceso=titulo,
            entorno=resultado["entorno"],
            metricas=metricas,
            hechos_resumen=hechos,
            url_pdf=None,
            texto_pie=texto_pie,
        )
    except Exception as e:
        print(f"[aviso] No se pudo notificar a Teams: {e}")


# ════════════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(
        description="Sincroniza el consecutivo real de Hacienda desde QuickBooks a la BD."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Recorre todo pero NO escribe en la BD; imprime lo que haria.",
    )
    parser.add_argument(
        "--no-teams",
        action="store_true",
        help="No envia la tarjeta a Teams (util al probar a mano).",
    )
    args = parser.parse_args()

    if not DATABASE_URL:
        sys.exit("Falta DATABASE_URL en el .env.")
    if ENTORNO == "sandbox" and "sandbox" not in CFG["base_url"]:
        sys.exit("BLINDAJE: en sandbox la URL debe ser de sandbox.")
    if not CFG["client_id"] or not CFG["client_secret"]:
        sys.exit(
            "Faltan las llaves QBO (client_id/secret) en el .env para este entorno."
        )

    resultado = sincronizar(dry_run=args.dry_run)

    if not args.no_teams:
        notificar_teams(resultado)

    # ── JSON de metricas a stdout (para que el webapp lo capture) ──
    # Entre marcadores para que el frontend lo extraiga aunque haya logs arriba.
    print("\n###JSON_INICIO###")
    print(json.dumps(resultado, ensure_ascii=False))
    print("###JSON_FIN###")


if __name__ == "__main__":
    main()
