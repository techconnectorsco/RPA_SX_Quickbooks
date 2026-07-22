"""
rpa_operaciones.py  —  CODIGO MADRE del RPA de facturacion de OPERACIONES
=========================================================================
Lee las operaciones en estado 'aprobada' de la base, las factura en QuickBooks,
actualiza su estado a 'facturada' (guardando lo que devuelve QBO) y deja un PDF.

Un solo interruptor (ENTORNO) cambia entre SANDBOX y PRODUCCION.

IVA DINAMICO:
  - Cada linea trae su propio porcentaje_iva (cualquier valor) y flag exonerado.
  - El RPA busca en QuickBooks el impuesto cuyo PORCENTAJE coincide (por rate,
    no por nombre) y lo aplica a esa linea.
  - En sandbox, si no existe ese %, lo crea (es nuestro).
  - En produccion, lo BUSCA entre los que ya configuro el contador; NO crea
    impuestos en la contabilidad real (salvo que se habilite a proposito).

MONEDA / TIPO DE CAMBIO (NUEVO):
  - Si la operacion tiene moneda_invertida = true, se factura en la moneda
    CONTRARIA a la de la operacion, resolviendo el tipo de cambio (venta) del
    dia con tipo_cambio.py (doble fuente: API publica + BCCR) y guardando la
    tasa usada en tipo_cambio_usado.
      Ej.: operacion en Dolares con el switch activado -> se factura en Colones
           usando el tipo de cambio de venta de ESE momento.
  - Si no esta invertida, se factura en la moneda propia de la operacion.
  - El tipo de cambio se consulta UNA sola vez por corrida, y solo si hay al
    menos una operacion invertida (para no pegarle al BCCR sin necesidad).

SEGURIDAD:
  - En sandbox la URL es de sandbox y todo va al cliente/item de prueba.
  - Solo toca operaciones 'aprobada' sin factura todavia (idempotente).
  - Cada operacion se confirma por separado: si una falla, no afecta a las demas.
  - QBO SOLO crea la factura (POST /invoice). No la envia al cliente ni a
    Hacienda: la encargada la revisa y le da salida manualmente.

Requiere (una vez):  pip install psycopg2-binary python-dotenv requests fpdf2
                     (y para tipo_cambio real:  pip install bccr pandas)
"""

import os
import sys
import json
import base64
import datetime
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from generarlogpdf import ReporteFacturacionRPA

from tipo_cambio import obtener_tipo_cambio

# ── Metricas / reporte a Supabase (mismo patron que los otros RPAs) ──────────
import time
from global_status import status_global_ejecution
from supabase_manager import (
    verificar_estado_rpa,
    finalizar_y_reportar,
    ID_RPA_OPERACIONES,
)
from teams_notifier import enviar_tarjeta_ejecucion, enviar_tarjeta_simple
import simulacion
from teams_resumen import resumen_operaciones

# ════════════════════════════════════════════════════════════════════════════
#  CONFIGURACION
# ════════════════════════════════════════════════════════════════════════════

# El ENTORNO ya NO se escribe aca: se lee de la variable QBO_ENTORNO del .env de
# CADA maquina. Asi el mismo .py corre en local (sandbox) y en el VPS (produccion)
# sin editar una sola linea. Default 'sandbox' = blindaje: si nadie lo definio,
# se asume sandbox y jamas se toca produccion por accidente.
#   .env local ->  QBO_ENTORNO=sandbox
#   .env VPS   ->  QBO_ENTORNO=produccion
# (el .env NUNCA se copia entre maquinas; cada una manda sobre su propio entorno.)
# --> se define ENTORNO mas abajo, despues de cargar el .env.

LIMITE_FACTURAS = None  # None = todas las aprobadas.  1 = procesar solo UNA.
MONTO_FIJO_PRUEBA = None  # None = monto real.  Ej: 100 = forzar monto chico
# (primer disparo en produccion -> nota de credito).

# Si en PRODUCCION falta un impuesto del % que pide una linea:
#   False (recomendado) -> NO lo crea; marca error y avisa para que el contador
#                          lo configure. Nunca toca la contabilidad real solo.
#   True  -> lo crea (usar solo si sabes lo que haces).
CREAR_IMPUESTOS_FALTANTES = False

# ── Que hacer si el impuesto elegido no tiene un TaxCode ACTIVO en QuickBooks ──
# Pasa con "Exonerado 0%" e "IVA 8%": la TASA existe y esta activa, pero su
# TaxCode figura como inactivo, y la linea de una factura necesita un TaxCode.
#   False (recomendado) -> NO factura esa emision y avisa cual impuesto falta.
#                          Es preferible frenar una factura a emitirla con un
#                          tratamiento tributario distinto al que se eligio.
#   True  -> la factura como "Exento". OJO: ante Hacienda NO es lo mismo que
#            exonerado; activar solo si contabilidad lo aprueba.
USAR_EXENTO_SI_NO_HAY_TAXCODE = False

# ── CORRIDA EN FRIO ──────────────────────────────────────────────────────────
# True = hace TODO el recorrido real (base + QuickBooks, solo lectura) pero NO
# crea ninguna factura, NO escribe en la base y NO reporta a Supabase/Teams.
# Al final imprime que habria pasado con cada emision y deja los payloads en un
# JSON. Sirve para validar contra datos reales sin emitir nada.
SIMULACION = False

# Imprime en consola el JSON completo que se manda a QuickBooks (para depurar).
DEBUG_PAYLOAD = True

# ── CAMBIO DE MONEDA (switch "moneda invertida"): DESCONTINUADO ──────────────
# QuickBooks NO maneja clientes multimoneda: cada cliente tiene UNA moneda fija
# (la de sus cuentas por cobrar) y TODAS sus facturas deben ir en esa moneda.
# Si intentamos facturar en otra, QBO rechaza con el error 6000:
#   "Cambie esta divisa de la transaccion para que coincida con la que usa
#    para sus cuentas por cobrar y por pagar."
# Cuando hay que facturarle a un mismo cliente en las dos monedas, en QBO se
# crea DOS VECES con Ids distintos (ej. SAMESA en USD e SAMESA en CRC).
# Por eso el switch se descontinuo: la moneda la define el CLIENTE elegido en
# QuickBooks, no una conversion nuestra. La webapp filtra los clientes por
# moneda, y el RPA factura SIEMPRE en la moneda de la operacion.
# Para reactivarlo (si algun dia cambia la regla): poner esto en True.
CAMBIO_MONEDA_HABILITADO = False

# Cache en memoria del mapa de impuestos por empresa: { realm: { porcentaje: taxCodeId } }
TAX_CODES_CACHE = {}

# ── Entornos ────────────────────────────────────────────────────────────────
# Toda la config sale del .env de LA carpeta de este RPA (una por maquina):
#   DATABASE_URL, QBO_ENTORNO y las llaves QBO. El .env NO se sube al repo y NO
#   se copia entre maquinas: en local apunta a la base/sandbox de pruebas, y en
#   el VPS a la base/produccion. Asi el mismo codigo corre en ambos sitios.
load_dotenv()

# Entorno tomado del .env de la maquina (default sandbox = blindaje).
ENTORNO = os.getenv("QBO_ENTORNO", "sandbox").strip().lower()

ENTORNOS = {
    "sandbox": {
        "base_url": "https://sandbox-quickbooks.api.intuit.com",
        "tokens_file": os.path.join("config", "tokens_sandbox.json"),
        "client_id": os.getenv("QBO_SANDBOX_CLIENT_ID"),
        "client_secret": os.getenv("QBO_SANDBOX_CLIENT_SECRET"),
        "realm_fijo": "9341456664539574",  # en sandbox todo va al unico sandbox
        "cliente_fijo": "58",  # cliente de prueba
        "item_operaciones": "19",  # item de prueba en sandbox
        "usar_moneda_real": False,  # sandbox no tiene multimoneda -> USD
    },
    "produccion": {
        "base_url": "https://quickbooks.api.intuit.com",
        "tokens_file": os.path.join("config", "tokens_empresas.json"),
        "client_id": os.getenv("QBO_CLIENT_ID"),
        "client_secret": os.getenv("QBO_CLIENT_SECRET"),
        "realm_fijo": None,  # usa el realm real de cada empresa
        "cliente_fijo": None,  # usa el qbo_customer_id de la operacion
        # Fase 1: item FIJO por empresa (fallback cuando no hay mapeo dinamico).
        # Fase 2: se busca primero en servicios_quickbooks (cargado al iniciar);
        # si no hay mapeo para ese servicio, se usa este default.
        "item_operaciones_default": {
            "9130355360397996": "4",  # Soportexperto  -> CONTRATOS HORAS ADICIONALES
            "9130355360390096": "157",  # Hardware y Network -> Venta de Servicios y Proyectos
            "9130355360394696": "5",  # Laitcorp       -> CONTRATOS HORAS ADICIONALES
        },
        "usar_moneda_real": True,
    },
}

if ENTORNO not in ENTORNOS:
    sys.exit(f"QBO_ENTORNO invalido: '{ENTORNO}'. Use 'sandbox' o 'produccion'.")

CFG = ENTORNOS[ENTORNO]
DATABASE_URL = os.getenv("DATABASE_URL")

# Candado: realms de produccion (para validar que sandbox nunca los toque)
REALMS_PRODUCCION = {"9130355360397996", "9130355360394696", "9130355360390096"}

# ── Fase 2: Mapeo de Servicios Webapp a Items QBO ────────────────────────────
# Se carga desde la base de datos (tabla servicios_quickbooks) al iniciar
MAPA_SERVICIOS_BD = {}


# ════════════════════════════════════════════════════════════════════════════
#  TOKENS  —  refresca solo, en ambos entornos
# ════════════════════════════════════════════════════════════════════════════


def get_access_token(realm):
    """Refresca y devuelve el access_token del realm dado, segun el entorno."""
    f = CFG["tokens_file"]
    with open(f, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    if ENTORNO == "sandbox":
        nodo = data  # un solo token
    else:
        nodo = data["empresas"][realm]  # token por empresa
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


def leer_operaciones_aprobadas(conn):
    """Operaciones 'aprobada' sin factura todavia, con el realm de su empresa."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT o.id, o.qbo_customer_id, o.compania_facturadora, o.cliente,
                   o.moneda, o.moneda_invertida, o.descripcion_factura, e.realm_id,
                   o.observaciones_internas,
                   cq.email AS cliente_email
            FROM operaciones o
            LEFT JOIN empresas e ON e.nombre = o.compania_facturadora
            -- Correo del cliente desde la tabla espejo de QuickBooks: QBO no lo
            -- hereda solo al crear la factura por API, y sin correo el
            -- Facturador Plus no puede despacharla a Hacienda.
            LEFT JOIN clientes_quickbooks cq
                   ON cq.empresa_id = e.id
                  AND cq.qbo_customer_id = o.qbo_customer_id
            WHERE o.estado = 'aprobada' AND o.qbo_invoice_id IS NULL
            ORDER BY o.creado_en
        """)
        return cur.fetchall()


def leer_lineas(conn, operacion_id):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT lo.descripcion, lo.horas_trabajadas, lo.monto_por_hora,
                   lo.total_linea, lo.porcentaje_iva, lo.exonerado,
                   s.nombre AS servicio,
                   -- Impuesto EXACTO que eligio el operador en la webapp.
                   -- Con esto el RPA ya no adivina por porcentaje: sabe cual es.
                   -- Va LEFT JOIN a proposito: las lineas viejas (anteriores a
                   -- este cambio) no tienen impuesto_id y caen al respaldo por
                   -- porcentaje dentro de resolver_impuesto().
                   imp.qbo_id     AS impuesto_qbo_id,
                   imp.objeto     AS impuesto_objeto,
                   imp.nombre     AS impuesto_nombre,
                   imp.porcentaje AS impuesto_porcentaje
            FROM lineas_operacion lo
            LEFT JOIN servicios s ON s.id = lo.servicio_id
            LEFT JOIN impuestos imp ON imp.id = lo.impuesto_id
            WHERE lo.operacion_id = %s
            ORDER BY lo.orden
        """,
            (operacion_id,),
        )
        return cur.fetchall()


def cargar_mapa_servicios(conn):
    """Carga en memoria el catálogo de servicios (por empresa) para mapear en Fase 2."""
    global MAPA_SERVICIOS_BD
    MAPA_SERVICIOS_BD.clear()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT e.realm_id, s.nombre, s.qbo_item_id
            FROM servicios s
            JOIN empresas e ON e.id = s.empresa_id
            WHERE e.realm_id IS NOT NULL AND s.qbo_item_id IS NOT NULL
        """)
        for realm_id, servicio_nombre, qbo_id in cur.fetchall():
            if realm_id not in MAPA_SERVICIOS_BD:
                MAPA_SERVICIOS_BD[realm_id] = {}
            MAPA_SERVICIOS_BD[realm_id][servicio_nombre] = qbo_id


def marcar_facturada(
    conn, operacion_id, qbo_invoice_id, qbo_doc_number, tipo_cambio_usado
):
    """Marca la operacion como facturada y guarda la tasa usada (si hubo inversion).

    tipo_cambio_usado va None cuando la operacion NO invierte moneda; en ese caso
    la columna queda en NULL, que es lo correcto (no se uso ningun tipo de cambio).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE operaciones
            SET estado = 'facturada', qbo_invoice_id = %s, qbo_doc_number = %s,
                facturado_en = now(), tipo_cambio_usado = %s, qbo_sync_error = NULL
            WHERE id = %s AND estado = 'aprobada'
        """,
            (qbo_invoice_id, qbo_doc_number, tipo_cambio_usado, operacion_id),
        )
    conn.commit()


def marcar_error(conn, operacion_id, error):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE operaciones SET qbo_sync_error = %s WHERE id = %s",
            (str(error)[:500], operacion_id),
        )
    conn.commit()


# ════════════════════════════════════════════════════════════════════════════
#  IMPUESTOS DINAMICOS  —  match por PORCENTAJE (rate), no por nombre
# ════════════════════════════════════════════════════════════════════════════


def _query_qbo(realm, token, sql):
    """GET a /query con la consulta correctamente codificada."""
    url = (
        f"{CFG['base_url']}/v3/company/{realm}/query"
        f"?query={requests.utils.quote(sql)}&minorversion=75"
    )
    return requests.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=30,
    )


def _cargar_resolvedor_impuestos(realm, token):
    """Lee los impuestos de QuickBooks y arma un RESOLVEDOR para esta empresa.

    Devuelve un dict con:
      "rate_a_taxcode": { TaxRate Id -> TaxCode Id }   <- la traduccion clave
      "taxcode_a_rate": { TaxCode Id -> TaxRate Id }
      "rate_pct":       { TaxRate Id -> porcentaje }
      "taxcodes":       set de TaxCode Ids activos y usables
      "pct_a_taxcode":  { porcentaje -> TaxCode Id }   <- solo para lineas viejas
      "exento":         TaxCode Id del "Exento" (0%), o None

    POR QUE HACE FALTA TRADUCIR:
      La webapp guarda en cada linea el impuesto EXACTO que eligio el operador
      (tabla `impuestos`), y lo que guarda son objetos TaxRate. Pero la linea de
      una factura en QuickBooks referencia un TaxCode, NO un TaxRate, y los Id
      no son intercambiables: en la misma empresa el TaxRate 13 es "EE" y el
      TaxCode 13 es "2% R". Mandar el Id de un TaxRate como TaxCodeRef factura
      con el impuesto equivocado sin dar error.
      Por eso se busca el TaxCode ACTIVO que contiene ese TaxRate exacto.

    EL DESEMPATE (lo que arreglo el bug del Id 27):
      Una misma tasa puede estar dentro de varios TaxCode. Ej. en Soportexperto
      el TaxRate "IVA 13% (Ventas)" esta en el TaxCode "IVA 13%" (una sola tasa)
      y tambien en "Exonerado al 100%" (que combina +13% y -13%).
      Se prefiere SIEMPRE el TaxCode "puro" (el que tiene UNA sola tasa de
      venta). Los compuestos solo rellenan huecos. El criterio es un DATO
      (cuantas tasas tiene el codigo), no el nombre ni el orden en que
      QuickBooks los devuelva: antes ganaba "el primero que apareciera" y por
      eso se colaba "Exonerado al 100%" y las facturas salian exoneradas.

    Cache por realm (una sola consulta por corrida).
    """
    if realm in TAX_CODES_CACHE:
        return TAX_CODES_CACHE[realm]

    # ── 1) TaxRate Id -> porcentaje y nombre ──
    rate_pct, rate_nombre = {}, {}
    r = _query_qbo(realm, token, "SELECT * FROM TaxRate")
    if r.status_code == 200:
        for tr in r.json().get("QueryResponse", {}).get("TaxRate", []):
            try:
                pct = round(float(tr.get("RateValue", 0)), 2)
            except (TypeError, ValueError):
                continue
            rid = str(tr["Id"])
            rate_pct[rid] = pct
            rate_nombre[rid] = tr.get("Name") or ""

    def es_rate_de_venta(nombre_rate):
        """Descarta tasas de compra y de retencion (no se facturan al cliente)."""
        n = (nombre_rate or "").lower()
        if "(compras)" in n or "compra" in n:
            return False
        if n.startswith(("rs-", "rp-", "rri-", "rs ", "rp ", "rri ")):
            return False
        return True

    rate_a_taxcode, taxcode_a_rate, pct_a_taxcode = {}, {}, {}
    taxcodes = set()
    exento = None
    puros, compuestos = [], []  # (taxcode_id, [rate_ids de venta])

    # ── 2) TaxCode activos: se clasifican en "puros" (1 tasa) y compuestos ──
    r = _query_qbo(realm, token, "SELECT * FROM TaxCode")
    if r.status_code == 200:
        for tc in r.json().get("QueryResponse", {}).get("TaxCode", []):
            if not tc.get("Active", True):
                continue
            tcid = str(tc["Id"])
            nombre_tc = " ".join((tc.get("Name") or "").lower().split())
            if nombre_tc.endswith(" r") or "retenc" in nombre_tc:
                continue

            taxcodes.add(tcid)
            if nombre_tc == "exento":
                exento = tcid

            detalles = (tc.get("SalesTaxRateList") or {}).get("TaxRateDetail", [])
            rids = []
            for det in detalles:
                rid = str((det.get("TaxRateRef") or {}).get("value") or "")
                if rid and rid in rate_pct and es_rate_de_venta(rate_nombre.get(rid)):
                    rids.append(rid)
            if not rids:
                continue
            (puros if len(rids) == 1 else compuestos).append((tcid, rids))

    # Primero los puros: son los que ganan el desempate.
    for tcid, rids in puros:
        rid = rids[0]
        rate_a_taxcode.setdefault(rid, tcid)
        taxcode_a_rate.setdefault(tcid, rid)
        pct = rate_pct.get(rid, 0.0)
        if pct > 0:
            pct_a_taxcode.setdefault(pct, tcid)

    # Los compuestos solo rellenan huecos; nunca pisan a un TaxCode puro.
    for tcid, rids in compuestos:
        for rid in rids:
            rate_a_taxcode.setdefault(rid, tcid)
        taxcode_a_rate.setdefault(tcid, rids[0])

    resolvedor = {
        "rate_a_taxcode": rate_a_taxcode,
        "taxcode_a_rate": taxcode_a_rate,
        "rate_pct": rate_pct,
        "taxcodes": taxcodes,
        "pct_a_taxcode": pct_a_taxcode,
        "exento": exento,
    }
    TAX_CODES_CACHE[realm] = resolvedor
    return resolvedor


def _crear_taxcode(porcentaje, realm, token):
    """Crea un TaxCode con ese porcentaje. SOLO se usa en sandbox, para no
    frenar las pruebas. En produccion el RPA nunca crea impuestos."""
    nombre = f"IVA {porcentaje}%"
    url = f"{CFG['base_url']}/v3/company/{realm}/taxservice/taxcode?minorversion=75"
    payload = {
        "TaxCode": nombre,
        "TaxRateDetails": [
            {
                "TaxRateName": nombre,
                "RateValue": porcentaje,
                "TaxAgencyId": "1",
                "TaxApplicableOn": "Sales",
            }
        ],
    }
    r = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    if r.status_code in (200, 201):
        return str(r.json().get("TaxCodeId"))
    raise RuntimeError(
        f"No se pudo crear impuesto {porcentaje}%: {r.status_code} {r.text[:200]}"
    )


def resolver_impuesto(linea, realm, token):
    """Devuelve (taxcode_id, rate_id, porcentaje) para UNA linea de factura.

    CAMINO NORMAL (linea nueva):
      La linea trae el impuesto que el operador eligio en la webapp
      (impuesto_qbo_id + impuesto_objeto, desde la tabla `impuestos`). No se
      adivina nada: se traduce ese TaxRate al TaxCode que lo contiene.

    CAMINO DE RESPALDO (linea vieja, sin impuesto_id):
      Se busca por porcentaje_iva / exonerado, como antes del cambio. Las
      lineas creadas antes de que existiera `impuesto_id` siguen facturando.

    rate_id se devuelve porque QuickBooks (edicion global) NO autocalcula el
    impuesto: hay que mandarle el TxnTaxDetail con el TaxRate y el monto.
    """
    qbo_id = linea.get("impuesto_qbo_id")
    qbo_id = str(qbo_id).strip() if qbo_id else None
    objeto = (linea.get("impuesto_objeto") or "").strip()
    nombre = linea.get("impuesto_nombre") or "?"
    pct_linea = linea.get("porcentaje_iva")
    exonerado = bool(linea.get("exonerado"))

    # ── SANDBOX ──
    # La empresa de pruebas es de EE.UU. y no tiene los impuestos de Costa Rica,
    # asi que los Id de produccion no existen alla. Se mantiene el comportamiento
    # anterior (match por porcentaje, creando el impuesto si falta).
    if ENTORNO == "sandbox":
        if exonerado or pct_linea is None or float(pct_linea) == 0:
            return ("NON", None, 0.0)
        pct = round(float(pct_linea), 2)
        res = _cargar_resolvedor_impuestos(realm, token)
        tcid = res["pct_a_taxcode"].get(pct)
        if not tcid:
            print(f"    impuesto {pct}% no existe en sandbox; creandolo...")
            tcid = _crear_taxcode(pct, realm, token)
            res["pct_a_taxcode"][pct] = tcid
        return (tcid, None, pct)

    res = _cargar_resolvedor_impuestos(realm, token)

    # ── 0) El check "exonerado" MANDA sobre el impuesto del dropdown ──
    # Si la linea viene marcada, se factura EXENTA aunque en el dropdown haya
    # quedado seleccionado un IVA (ej. 13%). El check es la decision explicita
    # del operador sobre esa linea y pisa cualquier otra seleccion.
    # El codigo "Exento" se busca por empresa (su Id cambia en cada una: en
    # Soportexperto es el 23, en Hardware y Network el 22, en Laitcorp el 23),
    # asi que no hay nada clavado: se resuelve solo contra QuickBooks.
    if exonerado:
        if res["exento"]:
            return (res["exento"], None, 0.0)
        raise RuntimeError(
            "IVA no valido: la linea esta marcada como exonerada pero la empresa "
            "no tiene un impuesto de venta 'Exento' activo en QuickBooks."
        )

    # ── 1) Camino normal: la linea trae el impuesto elegido en la webapp ──
    if qbo_id:
        if objeto == "TaxCode":
            if qbo_id in res["taxcodes"]:
                rid = res["taxcode_a_rate"].get(qbo_id)
                pct = res["rate_pct"].get(rid, 0.0) if rid else 0.0
                return (qbo_id, rid, pct)
            raise RuntimeError(
                f"IVA no valido: el TaxCode '{nombre}' (Id {qbo_id}) no esta "
                f"activo en QuickBooks para esta empresa."
            )

        # objeto == 'TaxRate' (es lo que guarda hoy la webapp)
        tcid = res["rate_a_taxcode"].get(qbo_id)
        if tcid:
            return (tcid, qbo_id, res["rate_pct"].get(qbo_id, 0.0))

        # No hay NINGUN TaxCode activo que contenga esa tasa. Pasa con
        # "Exonerado 0%" y con "IVA 8%": la TASA existe y esta activa, pero su
        # TaxCode figura como inactivo en QuickBooks, y la linea de una factura
        # necesita un TaxCode. Se resuelve reactivando ese codigo en QBO.
        if USAR_EXENTO_SI_NO_HAY_TAXCODE and res["exento"]:
            print(
                f"    [aviso] '{nombre}' no tiene TaxCode activo en QuickBooks; "
                f"se factura como Exento (revisar con contabilidad)."
            )
            return (res["exento"], None, 0.0)
        raise RuntimeError(
            f"IVA no valido: '{nombre}' (TaxRate {qbo_id}) no tiene un TaxCode "
            f"ACTIVO en QuickBooks para esta empresa. Hay que activarlo o "
            f"crearlo alla antes de poder facturar con ese impuesto."
        )

    # ── 2) Respaldo: linea vieja, sin impuesto_id ──
    if pct_linea is None or float(pct_linea) == 0:
        if res["exento"]:
            return (res["exento"], None, 0.0)
        raise RuntimeError(
            "IVA no valido: la empresa no tiene un impuesto de venta 'Exento' "
            "configurado en QuickBooks."
        )
    pct = round(float(pct_linea), 2)
    tcid = res["pct_a_taxcode"].get(pct)
    if tcid:
        return (tcid, res["taxcode_a_rate"].get(tcid), pct)
    raise RuntimeError(
        f"IVA no valido: {pct}% no esta configurado como impuesto de venta "
        f"para esta empresa en QuickBooks."
    )


# ════════════════════════════════════════════════════════════════════════════
#  MONEDA / TIPO DE CAMBIO  —  mismo criterio que rpa_fijos.py
# ════════════════════════════════════════════════════════════════════════════

MONEDA_QBO = {"Dólares": "USD", "Dolares": "USD", "Colones": "CRC"}


def convertir_monto(monto, moneda_operacion, invertir, tc_venta):
    """
    Devuelve el monto en la moneda en que se va a facturar.
    Si no se invierte, el monto queda igual (moneda de la operacion).
    Si se invierte:
      - operacion USD -> factura CRC: multiplica por venta.
      - operacion CRC -> factura USD: divide por venta.
    """
    if not invertir or not tc_venta:
        return round(float(monto), 2)
    if moneda_operacion in ("Dólares", "Dolares"):  # USD -> CRC
        return round(float(monto) * tc_venta, 2)
    else:  # CRC -> USD
        return round(float(monto) / tc_venta, 2)


def moneda_factura(moneda_operacion, invertir):
    """Moneda (USD/CRC) en la que se emite la factura."""
    base = MONEDA_QBO.get(moneda_operacion, "USD")
    if not invertir:
        return base
    return "CRC" if base == "USD" else "USD"


# ════════════════════════════════════════════════════════════════════════════
#  CONSTRUIR Y ENVIAR LA FACTURA
# ════════════════════════════════════════════════════════════════════════════


def item_para(realm, nombre_servicio=None):
    mapeo_empresa = MAPA_SERVICIOS_BD.get(realm, {})

    print(
        f"\n[DEBUG ITEM_PARA OPERACIONES] realm: {realm}, nombre_servicio webapp: '{nombre_servicio}'"
    )
    print(f"[DEBUG ITEM_PARA OPERACIONES] mapeo_empresa para {realm}: {mapeo_empresa}")

    if nombre_servicio and nombre_servicio in mapeo_empresa:
        print(
            f"[DEBUG ITEM_PARA OPERACIONES] -> Encontrado! ID QBO: {mapeo_empresa[nombre_servicio]}\n"
        )
        return mapeo_empresa[nombre_servicio]

    # Si el servicio no esta en el mapa, usamos el default de la empresa
    defaults = CFG.get("item_operaciones_default", {})
    id_default = defaults.get(realm)
    print(
        f"[DEBUG ITEM_PARA OPERACIONES] -> NO encontrado. Usando default: {id_default}\n"
    )
    return id_default


def construir_factura(op, lineas, realm, token, tc_venta, correo_cliente=None):
    customer = CFG["cliente_fijo"] or op["qbo_customer_id"]

    moneda_op = op.get("moneda") or "Dólares"
    # El switch quedo descontinuado (ver CAMBIO_MONEDA_HABILITADO arriba): la
    # moneda la define el cliente elegido en QuickBooks, no una conversion.
    invertir = bool(op.get("moneda_invertida")) and CAMBIO_MONEDA_HABILITADO

    # El monto fijo de prueba es un valor pequeno y reversible (primer disparo
    # en produccion -> nota de credito). NO se invierte, para que el numero y la
    # moneda de la factura sean predecibles y el ejercicio sea 100% controlado.
    if MONTO_FIJO_PRUEBA is not None:
        invertir = False

    lineas_qbo = []
    codigos_gravados = set()  # taxcode ids usados (para el global del sandbox)

    # Acumuladores del calculo manual de IVA en produccion (ver TxnTaxDetail
    # mas abajo): QuickBooks en edicion global no lo autocalcula por API.
    tax_lines = {}
    total_tax = 0.0

    def agregar(linea, qty, unit, desc, servicio=None):
        """Agrega una linea a la factura. `linea` es el registro de la base:
        de ahi sale el impuesto que eligio el operador en la webapp."""
        nonlocal total_tax

        code, rate_id, pct = resolver_impuesto(linea, realm, token)
        if code not in ("NON", "TAX"):
            codigos_gravados.add(code)

        # En produccion la linea lleva el Id real del TaxCode.
        # En sandbox la linea va "TAX"/"NON" y el impuesto se inyecta global.
        if ENTORNO == "sandbox":
            line_ref = "NON" if code == "NON" else "TAX"
        else:
            line_ref = str(code)

        # QuickBooks VALIDA que Amount == UnitPrice x Qty y rechaza la factura
        # si no cuadra al centimo (error 6070: "El monto no es equivalente al
        # precio unitario por la cantidad").
        # Por eso el Amount NO se toma del total guardado ni se calcula aparte:
        # se DERIVA del precio unitario ya redondeado. Asi nunca se descuadra,
        # ni por redondeos ni por datos con centimos de mas en la base.
        # El Qty (horas) NO se convierte: solo cambian los montos.
        qty_f = float(qty)
        unit_final = convertir_monto(unit, moneda_op, invertir, tc_venta)
        amount_final = round(qty_f * unit_final, 2)

        # QuickBooks (edicion global) NO autocalcula el impuesto por API: hay que
        # mandarle el TxnTaxDetail con la tasa y el monto. Solo suma si la tasa
        # es > 0 (una linea exenta/exonerada aporta 0 y no debe sumar).
        if ENTORNO == "produccion" and rate_id and pct > 0:
            tax_amt = round(amount_final * (pct / 100.0), 2)
            total_tax += tax_amt
            if rate_id not in tax_lines:
                tax_lines[rate_id] = {"amount": 0.0, "net": 0.0, "pct": pct}
            tax_lines[rate_id]["amount"] += tax_amt
            tax_lines[rate_id]["net"] += amount_final

        lineas_qbo.append(
            {
                "DetailType": "SalesItemLineDetail",
                "Amount": amount_final,
                "Description": desc,
                "SalesItemLineDetail": {
                    "ItemRef": {"value": item_para(realm, servicio)},
                    "Qty": qty_f,
                    "UnitPrice": unit_final,
                    "TaxCodeRef": {"value": line_ref},
                },
            }
        )

    if MONTO_FIJO_PRUEBA is not None:
        # Linea sintetica: no viene de la base, asi que no trae impuesto_id y
        # resolver_impuesto() la resuelve por porcentaje (camino de respaldo).
        agregar(
            {"porcentaje_iva": 13, "exonerado": False},
            1,
            MONTO_FIJO_PRUEBA,
            "FACTURA DE PRUEBA - anular con nota de credito",
        )
    else:
        for ln in lineas:
            agregar(
                ln,
                ln["horas_trabajadas"],
                ln["monto_por_hora"],
                ln["descripcion"],
                servicio=ln.get("servicio"),
            )

    factura = {"Line": lineas_qbo, "CustomerRef": {"value": str(customer)}}

    # Los montos que mandamos son SIN IVA: QuickBooks debe sumarlo encima usando
    # el TaxCodeRef de cada linea. Sin esta directiva, algunas facturas salian
    # como EXENTAS aunque sus lineas llevaran IVA. Solo aplica a produccion: el
    # sandbox es una empresa gringa y no maneja este calculo global.
    if ENTORNO == "produccion":
        factura["GlobalTaxCalculation"] = "TaxExcluded"

        # QuickBooks en edicion global (fuera de EE.UU.) NO calcula el IVA solo
        # cuando la factura entra por API: si no se manda el TxnTaxDetail, asume
        # impuesto 0 y la factura sale exenta aunque las lineas tengan IVA.
        if total_tax > 0:
            factura["TxnTaxDetail"] = {
                "TotalTax": round(total_tax, 2),
                "TaxLine": [
                    {
                        "Amount": round(d["amount"], 2),
                        "DetailType": "TaxLineDetail",
                        "TaxLineDetail": {
                            "TaxRateRef": {"value": str(rate_id)},
                            "PercentBased": True,
                            "TaxPercent": d["pct"],
                            "NetAmountTaxable": round(d["net"], 2),
                        },
                    }
                    for rate_id, d in tax_lines.items()
                ],
            }

    # Correo del cliente: QuickBooks NO lo hereda del perfil al crear la factura
    # por API, y sin correo el Facturador Plus no puede despacharla a Hacienda.
    # Sale de la tabla espejo clientes_quickbooks (la llena sync_clientes.py).
    if correo_cliente:
        factura["BillEmail"] = {"Address": correo_cliente}

    # Nota interna: va como PrivateNote, que en QuickBooks es la nota privada
    # (la ve el equipo, NO el cliente en el PDF de la factura). Mismo criterio
    # que en contratos fijos.
    if op.get("observaciones_internas"):
        factura["PrivateNote"] = op["observaciones_internas"]

    # SANDBOX: aplica UN impuesto global (limitacion del sandbox gringo).
    #   - 1 solo porcentaje  -> exacto.
    #   - varios porcentajes -> usa uno (en sandbox no se puede mas; en
    #     produccion cada linea lleva el suyo y QBO calcula bien).
    if ENTORNO == "sandbox" and codigos_gravados:
        factura["TxnTaxDetail"] = {
            "TxnTaxCodeRef": {"value": str(next(iter(codigos_gravados)))}
        }
        if len(codigos_gravados) > 1:
            print(
                "    [aviso sandbox] la operacion mezcla varios % de IVA; en sandbox "
                "se aplica uno solo. En produccion sale correcto."
            )

    # Moneda: en produccion mandamos la moneda de la factura (invertida o no).
    # En sandbox usar_moneda_real es False -> no se manda CurrencyRef (el
    # sandbox gringo no tiene multimoneda), pero los MONTOS ya salen convertidos,
    # asi se puede verificar la aritmetica del tipo de cambio en las pruebas.
    if CFG["usar_moneda_real"]:
        factura["CurrencyRef"] = {"value": moneda_factura(moneda_op, invertir)}

    # Debug al FINAL: aca la factura ya esta completa (impuestos, correo, moneda).
    if DEBUG_PAYLOAD:
        print("PAYLOAD FACTURA DEBUG:")
        print(json.dumps(factura, indent=2, ensure_ascii=False))

    return factura


def enviar_factura(realm, token, factura):
    url = f"{CFG['base_url']}/v3/company/{realm}/invoice?minorversion=75"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    r = requests.post(url, headers=headers, json=factura, timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"QBO {r.status_code}: {r.text[:300]}")
    return r.json()["Invoice"]


# ════════════════════════════════════════════════════════════════════════════
#  LOG EN PDF
# ════════════════════════════════════════════════════════════════════════════


def generar_pdf(resultados):
    try:
        from fpdf import FPDF
    except ImportError:
        print("[aviso] fpdf2 no instalado; salto el PDF. (pip install fpdf2)")
        return None

    os.makedirs("logs", exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    ruta = os.path.join("logs", f"facturacion_{ENTORNO}_{ts}.pdf")

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, f"RPA Operaciones - Log de facturacion ({ENTORNO})", ln=1)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 6, f"Fecha: {datetime.datetime.now():%Y-%m-%d %H:%M:%S}", ln=1)
    ok = sum(1 for r in resultados if r["ok"])
    pdf.cell(
        0,
        6,
        f"Total: {len(resultados)}   Facturadas: {ok}   Errores: {len(resultados)-ok}",
        ln=1,
    )
    pdf.ln(3)

    for r in resultados:
        pdf.set_font("Helvetica", "B", 9)
        estado = "OK" if r["ok"] else "ERROR"
        pdf.multi_cell(
            0, 5, f"[{estado}] Operacion {r['operacion_id']}  ({r['cliente']})"
        )
        pdf.set_font("Helvetica", "", 8)
        if r["ok"]:
            pdf.multi_cell(
                0,
                5,
                f"   Factura QBO Id {r['qbo_invoice_id']} / DocNumber {r['qbo_doc_number']} / Total {r['total']}",
            )
        else:
            pdf.multi_cell(0, 5, f"   {r['error']}")
        pdf.ln(1)

    pdf.output(ruta)
    return ruta


# ════════════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════════════


def clasificar_error_metricas(mensaje):
    """Suma el error al contador correcto del status_global (metricas).
    Mismo criterio que el mapeo de errores del PDF."""
    m = (mensaje or "").lower()
    if "sin lineas" in m or "line is missing" in m:
        status_global_ejecution["err_sin_lineas"] += 1
    elif "customer_id" in m or "sin cliente" in m:
        status_global_ejecution["err_sin_cliente"] += 1
    elif "realm" in m or "sin empresa" in m:
        status_global_ejecution["err_sin_empresa"] += 1
    elif "iva no valido" in m or "impuesto" in m:
        status_global_ejecution["err_iva_invalido"] += 1
    elif any(
        k in m
        for k in (
            "timeout",
            "timed out",
            "connection",
            "connect",
            "max retries",
            "name resolution",
        )
    ):
        status_global_ejecution["err_conexion"] += 1
    elif "token" in m or "401" in m or "unauthorized" in m:
        status_global_ejecution["err_token"] += 1
    else:
        status_global_ejecution["err_otros"] += 1


def validar_entorno():
    if ENTORNO not in ENTORNOS:
        sys.exit(f"ENTORNO invalido: {ENTORNO}")
    if ENTORNO == "sandbox" and "sandbox" not in CFG["base_url"]:
        sys.exit("BLINDAJE: en sandbox la URL debe ser de sandbox.")
    if not CFG["client_id"] or not CFG["client_secret"]:
        sys.exit("Faltan las llaves (client_id/secret) en el .env para este entorno.")
    if not DATABASE_URL:
        sys.exit("Falta DATABASE_URL.")


def realm_de(op):
    if CFG["realm_fijo"]:
        return CFG["realm_fijo"]
    return op.get("realm_id")


def main():
    validar_entorno()

    # Interruptor remoto (por ahora siempre True; queda listo para el futuro)
    if not verificar_estado_rpa():
        print("RPA desactivado administrativamente en Supabase. No se ejecuta.")
        return

    inicio = time.time()  # cronometro para tiempo_ejecucion

    print("=" * 60)
    print(f"RPA OPERACIONES  —  ENTORNO: {ENTORNO.upper()}")
    if MONTO_FIJO_PRUEBA is not None:
        print(f"   *** MODO PRUEBA: monto fijo {MONTO_FIJO_PRUEBA} ***")
    if LIMITE_FACTURAS is not None:
        print(f"   *** LIMITE: {LIMITE_FACTURAS} factura(s) ***")
    if SIMULACION:
        print("   *** CORRIDA EN FRIO: no se creara ninguna factura ***")
    print("=" * 60)

    # Inicializamos tu orquestador de reportes
    reporte = ReporteFacturacionRPA(entorno=ENTORNO)

    conn = None
    tc_dia = None  # se consulta solo si hace falta (alguna operacion invertida)
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cargar_mapa_servicios(conn)
        operaciones = leer_operaciones_aprobadas(conn)

        if LIMITE_FACTURAS is not None:
            operaciones = operaciones[:LIMITE_FACTURAS]
        print(f"\nOperaciones a facturar: {len(operaciones)}\n")

        tokens = {}

        for op in operaciones:
            oid = op["id"]
            realm = realm_de(op)
            cliente_lbl = op.get("compania_facturadora", "")
            cliente_nom = op.get("cliente", "") or "-"  # cliente al que se factura

            status_global_ejecution["total_operaciones"] += 1

            # 1. Error: Empresa sin Realm
            if not realm or realm == "TODO":
                marcar_error(conn, oid, "Empresa sin realm_id (no factura por QBO)")
                status_global_ejecution["con_error"] += 1
                status_global_ejecution["err_sin_empresa"] += 1

                # >>> REGISTRO CORREGIDO CON DESCRIPCIÓN <<<
                reporte.registrar_operacion(
                    op_id=oid,
                    compania=cliente_lbl,
                    cliente=cliente_nom,
                    factura_num="-",
                    lineas=[],
                    status="ERR",
                    error_msg="Empresa sin realm_id",
                    descripcion_factura=op.get(
                        "descripcion_factura", "-"
                    ),  # <--- Agregado aquí
                )
                print(f"  [SKIP] {oid}: empresa sin realm")
                continue

            # Blindaje
            if ENTORNO == "sandbox" and realm in REALMS_PRODUCCION:
                sys.exit(
                    "BLINDAJE: en sandbox apareció un realm de produccion. Abortando."
                )

            # 2. Error: Sin cliente QBO
            customer = CFG["cliente_fijo"] or op["qbo_customer_id"]
            if not customer:
                marcar_error(conn, oid, "Operacion sin qbo_customer_id")
                status_global_ejecution["con_error"] += 1
                status_global_ejecution["err_sin_cliente"] += 1

                # >>> REGISTRO CORREGIDO CON DESCRIPCIÓN <<<
                reporte.registrar_operacion(
                    op_id=oid,
                    compania=cliente_lbl,
                    cliente=cliente_nom,
                    factura_num="-",
                    lineas=[],
                    status="ERR",
                    error_msg="Sin qbo_customer_id",
                    descripcion_factura=op.get(
                        "descripcion_factura", "-"
                    ),  # <--- Agregado aquí
                )
                print(f"  [SKIP] {oid}: sin qbo_customer_id")
                continue

            # Proceso de facturación activa
            try:
                # Tipo de cambio: solo si ESTA operacion invierte moneda.
                # (Con MONTO_FIJO_PRUEBA no se invierte, asi que ni se consulta:
                #  el ejercicio de prueba no debe depender de que el BCCR responda.)
                tc_venta = None
                if (
                    op.get("moneda_invertida")
                    and CAMBIO_MONEDA_HABILITADO
                    and MONTO_FIJO_PRUEBA is None
                ):
                    if tc_dia is None:
                        tc_dia = obtener_tipo_cambio()
                        print(
                            f"  Tipo de cambio del dia: venta {tc_dia['venta']} "
                            f"({tc_dia['fuente']})"
                        )
                    tc_venta = tc_dia["venta"]

                if realm not in tokens:
                    tokens[realm] = get_access_token(realm)

                lineas = leer_lineas(conn, oid)

                # Sin lineas no hay nada que facturar: QBO rechazaria con
                # "Line is missing". Lo detectamos antes para un estado claro.
                if not lineas:
                    raise RuntimeError("Operacion sin lineas para facturar")

                factura = construir_factura(
                    op,
                    lineas,
                    realm,
                    tokens[realm],
                    tc_venta,
                    correo_cliente=op.get("cliente_email"),
                )
                if SIMULACION:
                    # Corrida en frio: se verifica la factura pero NO se envia
                    # a QuickBooks ni se toca la base.
                    avisos = [
                        simulacion.verificar_moneda_cliente(
                            _query_qbo,
                            realm,
                            tokens[realm],
                            op.get("qbo_customer_id"),
                            (factura.get("CurrencyRef") or {}).get("value"),
                        )
                    ]
                    simulacion.registrar(
                        f"{cliente_lbl} | {cliente_nom}", factura, avisos
                    )
                    inv = simulacion.factura_simulada(factura)
                else:
                    inv = enviar_factura(realm, tokens[realm], factura)
                    marcar_facturada(
                        conn, oid, inv["Id"], inv.get("DocNumber"), tc_venta
                    )

                # Moneda con la que realmente se emitio la factura (dinamica):
                # si la operacion invierte, es la contraria a la de la operacion.
                # Misma condicion que usa construir_factura: si el cambio de
                # moneda esta descontinuado, la factura sale en la moneda del
                # contrato aunque la fila tenga moneda_invertida=true. El
                # reporte y las metricas tienen que decir lo mismo que se
                # facturo, no lo que dice el flag viejo de la base.
                moneda_emitida = moneda_factura(
                    op.get("moneda") or "Dólares",
                    bool(op.get("moneda_invertida")) and CAMBIO_MONEDA_HABILITADO,
                )

                # --- Metricas de exito (monto real de QBO, por moneda) ---
                status_global_ejecution["facturadas_ok"] += 1
                try:
                    _total = float(inv.get("TotalAmt") or 0)
                except (TypeError, ValueError):
                    _total = 0.0
                if moneda_emitida == "CRC":
                    status_global_ejecution["monto_total_crc"] += _total
                else:
                    status_global_ejecution["monto_total_usd"] += _total
                if tc_venta:
                    status_global_ejecution["facturas_con_cambio_moneda"] += 1

                # 3. ÉXITO: Registramos la operación (con moneda y tipo de cambio)
                reporte.registrar_operacion(
                    op_id=oid,
                    compania=cliente_lbl,
                    cliente=cliente_nom,
                    factura_num=inv.get("DocNumber", "-"),
                    lineas=lineas,
                    status="OK",
                    descripcion_factura=op.get("descripcion_factura", "-"),
                    moneda=moneda_emitida,
                    tipo_cambio_usado=tc_venta,
                    total_qb=inv.get("TotalAmt"),
                )
                tc_txt = f" TC {tc_venta}" if tc_venta else ""
                print(
                    f"  [OK]   {oid}: factura {inv.get('DocNumber')} "
                    f"total {inv.get('TotalAmt')}{tc_txt}"
                )

            except Exception as e:
                if not SIMULACION:
                    marcar_error(conn, oid, e)
                status_global_ejecution["con_error"] += 1
                clasificar_error_metricas(str(e))

                # 4. Error dinámico en el proceso de envío (ESTÁ PERFECTO)
                # NOTA: Pasamos lineas=[] para que en errores muestre 0.00 en montos, lo cual es correcto.
                reporte.registrar_operacion(
                    op_id=oid,
                    compania=cliente_lbl,
                    cliente=cliente_nom,
                    factura_num="-",
                    lineas=[],
                    status="ERR",
                    error_msg=str(e),
                    descripcion_factura=op.get("descripcion_factura", "-"),
                )
                print(f"  [ERR]  {oid}: {e}")

    except Exception as e_db:
        print(f"Error crítico en la conexión o lectura de la Base de Datos: {e_db}")

    finally:
        if conn is not None:
            conn.close()

    # Si no habia ninguna operacion aprobada, no tiene sentido generar un PDF
    # en blanco: no hay nada que reportar.
    hubo_trabajo = status_global_ejecution["total_operaciones"] > 0

    ruta_pdf = reporte.exportar_pdf() if hubo_trabajo else None

    print("\n" + "=" * 60)
    if hubo_trabajo:
        print(f"Listo. Facturadas: {reporte.exitosas}   Errores: {reporte.errores}")
        if ruta_pdf:
            print(f"Log PDF: {ruta_pdf}")
    else:
        print("No habia operaciones aprobadas (sin PDF y sin aviso a Teams).")
    print("=" * 60)

    if SIMULACION:
        simulacion.resumen()
        simulacion.guardar_json(
            os.path.join(
                "logs",
                f"simulacion_operaciones_{datetime.date.today().isoformat()}.json",
            )
        )
        print("\n(Corrida en frio: no se reporta a Supabase ni a Teams.)")
        return

    # ── Reporte de metricas a Supabase (mismo patron que los otros RPAs) ──
    duracion = int(time.time() - inicio)
    status_global_ejecution["tiempo_ejecucion"] = f"{duracion // 60}m {duracion % 60}s"
    status_global_ejecution["entorno"] = ENTORNO
    if MONTO_FIJO_PRUEBA is not None:
        status_global_ejecution["tipo_ejecucion"] = "Prueba"
    elif not hubo_trabajo:
        status_global_ejecution["tipo_ejecucion"] = "Sin operaciones aprobadas"
    # Redondeo de montos para dejarlos limpios
    status_global_ejecution["monto_total_usd"] = round(
        status_global_ejecution["monto_total_usd"], 2
    )
    status_global_ejecution["monto_total_crc"] = round(
        status_global_ejecution["monto_total_crc"], 2
    )
    url_pdf = None
    try:
        resultado = finalizar_y_reportar(
            status_global_ejecution,
            ruta_pdf_local=ruta_pdf,
            automatizacion_id=ID_RPA_OPERACIONES,
            subcarpeta="operaciones",
        )
        if isinstance(resultado, dict):
            url_pdf = resultado.get("url_pdf")
    except Exception as e_sup:
        # Un fallo al reportar metricas NUNCA debe tumbar la corrida de facturacion
        print(f"[aviso] No se pudo reportar a Supabase: {e_sup}")

    # ── Notificacion a Microsoft Teams ──
    # Sin operaciones aprobadas se manda una tarjeta MINIMA en vez de la
    # completa: una tarjeta con todo en cero es ruido, pero el silencio total
    # tampoco sirve (no se distingue "no habia nada" de "el robot no corrio").
    if not hubo_trabajo:
        try:
            enviar_tarjeta_simple(
                webhook_url=os.getenv("TEAMS_WEBHOOK_URL"),
                titulo="RPA Facturacion - Operaciones",
                subtitulo=(
                    f"{ENTORNO.upper()}  -  "
                    f"{datetime.date.today().strftime('%d/%m/%Y')}"
                ),
                mensaje="Ejecucion correcta. No habia operaciones aprobadas para facturar.",
            )
        except Exception as e_teams:
            print(f"[aviso] No se pudo notificar a Teams: {e_teams}")
        return

    # La URL del flujo sale del .env; si no esta, el modulo no notifica y sigue.
    try:
        hechos, texto_pie = resumen_operaciones(status_global_ejecution)
        enviar_tarjeta_ejecucion(
            webhook_url=os.getenv("TEAMS_WEBHOOK_URL"),
            nombre_proceso="RPA Facturacion - Operaciones",
            entorno=ENTORNO,
            metricas=status_global_ejecution,
            hechos_resumen=hechos,
            url_pdf=url_pdf,
            texto_pie=texto_pie,
        )
    except Exception as e_teams:
        print(f"[aviso] No se pudo notificar a Teams: {e_teams}")


if __name__ == "__main__":
    main()
