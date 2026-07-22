"""
rpa_fijos.py  —  CODIGO MADRE del RPA de facturacion de CONTRATOS FIJOS
========================================================================
Corre TODOS los dias (idealmente al mediodia hora CR). Cada dia:

  1. Busca EMISIONES en estado 'Lista' que aun no tengan qbo_invoice_id.
  2. Solo del MES ACTUAL (si corre en julio, solo emisiones de julio).
  3. Solo si HOY es el dia de emision del contrato (dia_emision), ajustado
     al mes: si el contrato pide 31 y el mes tiene 30, se usa el 30; si pide
     29/30/31 y es febrero, se usa el ultimo dia de febrero.
  4. (Las 'no_facturar' nunca llegan: quedan en estado 'No facturar', no 'Lista'.)

Para cada emision que pasa los 4 filtros, arma la factura segun su tipo:
  - facturar_completo -> todas las lineas del contrato (IVA dinamico por linea).
  - porcentaje        -> UNA linea por el % del total + nota en la factura.
  - monto_parcial     -> UNA linea por el monto exacto + nota en la factura.

Si la emision tiene monedaInvertida = true, factura en la moneda CONTRARIA
del contrato, resolviendo el tipo de cambio (venta) del dia con tipo_cambio.py,
y guarda la tasa usada en tipo_cambio_usado.

En QuickBooks SOLO CREA la factura (no la envia ni la transmite a Hacienda).
La encargada la revisa, la envia al cliente y le da ruta a Hacienda.

Un solo interruptor (ENTORNO) cambia entre SANDBOX y PRODUCCION.

Requiere (una vez):  pip install psycopg2-binary python-dotenv requests fpdf2
                     (y para tipo_cambio real:  pip install bccr pandas)
"""

import os
import sys
import json
import base64
import calendar
import datetime
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

from tipo_cambio import obtener_tipo_cambio
from generarlog_fijos import ReporteContratosFijosRPA

# ── Metricas / reporte a Supabase (mismo patron que los otros RPAs) ──────────
import time
from global_status_fijos import status_global_ejecution
from supabase_manager import (
    verificar_estado_rpa,
    finalizar_y_reportar,
    ID_RPA_FIJOS,
)
from teams_notifier import enviar_tarjeta_ejecucion
from teams_resumen import resumen_contratos_fijos

# ════════════════════════════════════════════════════════════════════════════
#  CONFIGURACION
# ════════════════════════════════════════════════════════════════════════════

# El ENTORNO ya NO se escribe aca: se lee de la variable QBO_ENTORNO del .env de
# CADA maquina. Asi el mismo .py corre en local (sandbox) y en el VPS (produccion)
# sin editar una sola linea. Default 'sandbox' = blindaje.
#   .env local ->  QBO_ENTORNO=sandbox     |     .env VPS -> QBO_ENTORNO=produccion
# --> se define ENTORNO mas abajo, despues de cargar el .env.

LIMITE_FACTURAS = None  # None = todas.  1 = procesar solo UNA (util al pasar a prod).
IGNORAR_DIA_EMISION = (
    False  # True = ignora el filtro del dia (util SOLO para probar en sandbox).
)

# Si en PRODUCCION falta un impuesto del % que pide una linea:
#   False (recomendado) -> NO lo crea; marca error. True -> lo crea.
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

# Imprime en consola el JSON completo que se manda a QuickBooks (para depurar).
DEBUG_PAYLOAD = True

# ── CAMBIO DE MONEDA (switch "moneda invertida"): DESCONTINUADO ──────────────
# QuickBooks NO maneja clientes multimoneda: cada cliente tiene UNA moneda fija
# (la de sus cuentas por cobrar) y TODAS sus facturas deben ir en esa moneda.
# Ademas, esa moneda ya no se puede cambiar una vez que el cliente tiene
# movimientos. Si intentamos facturar en otra, QBO rechaza con el error 6000:
#   "Cambie esta divisa de la transaccion para que coincida con la que usa
#    para sus cuentas por cobrar y por pagar."
#
# En la practica, cuando hay que facturarle a un mismo cliente en las dos
# monedas, en QuickBooks se crea DOS VECES con Ids distintos. Ejemplo real:
#   Id 17 | USD | AGENCIA ADUANAL SAMESA S.A.
#   Id 18 | CRC | AGENCIA ADUANAL SAMESA SOCIEDAD ANONIMA
#
# Por eso el switch se descontinuo: la moneda de la factura la define el
# CLIENTE elegido en QuickBooks, no una conversion nuestra. La webapp filtra
# los clientes por moneda al armar el contrato, y el RPA factura SIEMPRE en la
# moneda del contrato (que es la del cliente que se eligio).
# Se deja el codigo de conversion por si alguna vez cambia la regla; para
# reactivarlo basta poner esto en True.
CAMBIO_MONEDA_HABILITADO = False

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
        "realm_fijo": "9341456664539574",
        "cliente_fijo": "58",
        "item_operaciones": "19",
        "usar_moneda_real": False,
    },
    "produccion": {
        "base_url": "https://quickbooks.api.intuit.com",
        "tokens_file": os.path.join("config", "tokens_empresas.json"),
        "client_id": os.getenv("QBO_CLIENT_ID"),
        "client_secret": os.getenv("QBO_CLIENT_SECRET"),
        "realm_fijo": None,
        "cliente_fijo": None,
        # El item por defecto a usar si no se encuentra mapeo
        "item_operaciones_default": {
            "9130355360397996": "4",  # Soportexperto  -> CONTRATOS HORAS ADICIONALES
            "9130355360390096": "157",  # Hardware y Network -> Venta de Servicios y Proyectos
            "9130355360394696": "5",  # Laitcorp       -> CONTRATOS HORAS ADICIONALES
        },
        "usar_moneda_real": True,
    },
}

# ── Fase 2: Mapeo de Servicios Webapp a Items QBO ─────────────────────────────
# Se carga desde la base de datos (tabla servicios_quickbooks) al iniciar
MAPA_SERVICIOS_BD = {}

if ENTORNO not in ENTORNOS:
    sys.exit(f"QBO_ENTORNO invalido: '{ENTORNO}'. Use 'sandbox' o 'produccion'.")

CFG = ENTORNOS[ENTORNO]
DATABASE_URL = os.getenv("DATABASE_URL")
REALMS_PRODUCCION = {"9130355360397996", "9130355360394696", "9130355360390096"}
MONEDA_QBO = {"Dólares": "USD", "Dolares": "USD", "Colones": "CRC"}


# ════════════════════════════════════════════════════════════════════════════
#  TOKENS
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


def leer_emisiones_listas(conn, periodo):
    """
    Emisiones en 'Lista' del mes indicado (periodo 'YYYY-MM'), sin factura aun,
    con los datos del contrato que el RPA necesita.
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                em.id                AS emision_id,
                em.mes_facturado,
                em.estado_emision,
                em.porcentaje_real,
                em.monto_real,
                em.descripcion_factura AS emision_descripcion,
                em.moneda_invertida,
                em.observaciones_internas,
                c.id                 AS contrato_id,
                c.qbo_customer_id,
                c.nombre_cliente,
                c.compania_facturadora,
                c.moneda,
                c.dia_emision,
                c.descripcion_factura AS contrato_descripcion,
                e.realm_id,
                cq.email             AS cliente_email
            FROM emisiones_cronograma em
            JOIN contratos_cronograma c ON c.id = em.contrato_id
            LEFT JOIN empresas e ON e.nombre = c.compania_facturadora
            -- Correo del cliente desde la tabla espejo de QuickBooks: QBO no lo
            -- hereda solo al crear la factura por API, y sin correo el
            -- Facturador Plus no puede despacharla a Hacienda.
            LEFT JOIN clientes_quickbooks cq
                   ON cq.empresa_id = e.id
                  AND cq.qbo_customer_id = c.qbo_customer_id
            WHERE em.estado = 'Lista'
              AND em.qbo_invoice_id IS NULL
              AND em.mes_facturado = %s
            ORDER BY c.compania_facturadora
        """,
            (periodo,),
        )
        return cur.fetchall()


def leer_lineas_contrato(conn, contrato_id):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT lc.descripcion, lc.cantidad, lc.monto_por_unidad, lc.total_linea,
                   lc.porcentaje_iva, lc.exonerado,
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
            FROM lineas_contrato lc
            LEFT JOIN servicios s ON s.id = lc.servicio_id
            LEFT JOIN impuestos imp ON imp.id = lc.impuesto_id
            WHERE lc.contrato_id = %s
            ORDER BY lc.orden
        """,
            (contrato_id,),
        )
        return cur.fetchall()


def marcar_emitida(conn, emision_id, inv, tipo_cambio_usado):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE emisiones_cronograma
            SET estado = 'Emitida',
                qbo_invoice_id = %s,
                qbo_doc_number = %s,
                facturado_en = now(),
                fecha_emision = %s,
                tipo_cambio_usado = %s,
                qbo_sync_error = NULL
            WHERE id = %s AND estado = 'Lista'
        """,
            (
                inv["Id"],
                inv.get("DocNumber"),
                datetime.date.today().isoformat(),
                tipo_cambio_usado,
                emision_id,
            ),
        )
    conn.commit()


def marcar_error(conn, emision_id, error):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE emisiones_cronograma SET qbo_sync_error = %s WHERE id = %s",
            (str(error)[:500], emision_id),
        )
    conn.commit()


# ════════════════════════════════════════════════════════════════════════════
#  DIA DE EMISION  —  ajustado al mes
# ════════════════════════════════════════════════════════════════════════════


def dia_objetivo_este_mes(dia_emision, hoy):
    """
    Ajusta el dia_emision del contrato al mes actual:
    si el dia pedido no existe este mes (31 en un mes de 30, o 29/30/31 en
    febrero), devuelve el ultimo dia real del mes. Si no hay dia_emision,
    devuelve None.
    """
    if not dia_emision:
        return None
    ultimo_dia = calendar.monthrange(hoy.year, hoy.month)[1]
    return min(int(dia_emision), ultimo_dia)


# ════════════════════════════════════════════════════════════════════════════
#  IMPUESTOS DINAMICOS  (mismo criterio que operaciones: match por rate)
# ════════════════════════════════════════════════════════════════════════════


def _query_qbo(realm, token, sql):
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
#  CONSTRUIR Y ENVIAR LA FACTURA
# ════════════════════════════════════════════════════════════════════════════


def item_para(realm, nombre_servicio=None):
    # Fase 2 implementada: buscamos el ID exacto segun el servicio webapp en la BD
    mapeo_empresa = MAPA_SERVICIOS_BD.get(realm, {})

    print(
        f"\n[DEBUG ITEM_PARA] realm: {realm}, nombre_servicio webapp: '{nombre_servicio}'"
    )
    print(f"[DEBUG ITEM_PARA] mapeo_empresa para {realm}: {mapeo_empresa}")

    if nombre_servicio and nombre_servicio in mapeo_empresa:
        print(
            f"[DEBUG ITEM_PARA] -> Encontrado! ID QBO: {mapeo_empresa[nombre_servicio]}\n"
        )
        return mapeo_empresa[nombre_servicio]

    # Si el servicio no esta en el mapa, usamos el default de la empresa
    defaults = CFG.get("item_operaciones_default", {})
    id_default = defaults.get(realm)
    print(f"[DEBUG ITEM_PARA] -> NO encontrado. Usando default: {id_default}\n")
    return id_default


def convertir_monto(monto, moneda_contrato, invertir, tc_venta):
    """
    Devuelve el monto en la moneda en que se va a facturar.
    Si no se invierte, el monto queda igual (moneda del contrato).
    Si se invierte:
      - contrato USD -> factura CRC: multiplica por venta.
      - contrato CRC -> factura USD: divide por venta.
    """
    if not invertir or not tc_venta:
        return round(float(monto), 2)
    if moneda_contrato in ("Dólares", "Dolares"):  # USD -> CRC
        return round(float(monto) * tc_venta, 2)
    else:  # CRC -> USD
        return round(float(monto) / tc_venta, 2)


def moneda_factura(moneda_contrato, invertir):
    """Moneda (USD/CRC) en la que se emite la factura."""
    base = MONEDA_QBO.get(moneda_contrato, "USD")
    if not invertir:
        return base
    return "CRC" if base == "USD" else "USD"


def construir_factura(em, lineas, realm, token, tc_venta, correo_cliente=None):
    customer = CFG["cliente_fijo"] or em["qbo_customer_id"]
    if not customer:
        raise RuntimeError(
            "Falta configurar el cliente de contratos para esta empresa."
        )

    invertir = bool(em.get("moneda_invertida")) and CAMBIO_MONEDA_HABILITADO
    moneda_contrato = em.get("moneda") or "Dólares"
    tipo = em["estado_emision"]
    desc = (
        em.get("emision_descripcion")
        or em.get("contrato_descripcion")
        or "Servicios contratados"
    )

    lineas_qbo = []
    nota_factura = None
    codigos_gravados = set()

    # Variables para calculo manual de IVA en produccion
    tax_lines = {}
    total_tax = 0.0

    def ref_impuesto(linea, amount):
        """Devuelve el TaxCode que va en la linea y acumula el impuesto del
        TxnTaxDetail. Recibe la LINEA completa porque el impuesto ya viene
        elegido desde la webapp (impuesto_qbo_id); ya no se adivina por %."""
        nonlocal total_tax
        code, rate_id, pct = resolver_impuesto(linea, realm, token)

        if code not in ("NON", "TAX"):
            codigos_gravados.add(code)

        # QuickBooks (edicion global) NO autocalcula el impuesto por API: hay
        # que mandarle el TxnTaxDetail con la tasa y el monto. Solo suma si la
        # tasa es > 0 (una linea exenta/exonerada aporta 0 y no debe sumar).
        if ENTORNO == "produccion" and rate_id and pct > 0:
            tax_amt = round(amount * (pct / 100.0), 2)
            total_tax += tax_amt
            if rate_id not in tax_lines:
                tax_lines[rate_id] = {"amount": 0.0, "net": 0.0, "pct": pct}
            tax_lines[rate_id]["amount"] += tax_amt
            tax_lines[rate_id]["net"] += amount

        # En sandbox la linea va "TAX"/"NON" y el impuesto se inyecta global.
        if ENTORNO == "sandbox":
            return "NON" if code == "NON" else "TAX"
        return str(code)

    if tipo == "facturar_completo":
        for ln in lineas:
            qty = float(ln["cantidad"])
            unit_price = convertir_monto(
                ln["monto_por_unidad"], moneda_contrato, invertir, tc_venta
            )
            amount = round(qty * unit_price, 2)
            lineas_qbo.append(
                {
                    "DetailType": "SalesItemLineDetail",
                    "Amount": amount,
                    "Description": ln["descripcion"],
                    "SalesItemLineDetail": {
                        "ItemRef": {"value": item_para(realm, ln.get("servicio"))},
                        "Qty": qty,
                        "UnitPrice": unit_price,
                        "TaxCodeRef": {"value": ref_impuesto(ln, amount)},
                    },
                }
            )
    else:
        # porcentaje o monto_parcial -> UNA sola linea + nota explicativa
        base_total = sum(
            float(l["total_linea"]) for l in lineas
        )  # subtotal del contrato
        if tipo == "porcentaje":
            pct = float(em.get("porcentaje_real") or 0)
            monto_base = base_total * pct / 100.0
            nota_factura = (
                f"Facturacion parcial: {pct:g}% del contrato mensual "
                f"segun acuerdo con el cliente."
            )
        else:  # monto_parcial
            monto_base = float(em.get("monto_real") or 0)
            nota_factura = (
                "Facturacion parcial por monto acordado con el cliente "
                "para este periodo."
            )

        # IVA del cobro parcial: la factura lleva UNA sola linea, asi que se toma
        # como referencia el impuesto de la primera linea GRAVADA del contrato
        # (si todas son exentas, el de la primera linea). Con IVA mezclado esto
        # es una aproximacion; con un solo IVA (el caso normal) es exacto.
        linea_ref = next((l for l in lineas if not l["exonerado"]), None) or lineas[0]

        # Tomamos el primer servicio de la lista para la factura colapsada
        primer_servicio = lineas[0].get("servicio") if lineas else None

        # Qty = 1, asi que Amount y UnitPrice son el mismo numero: cuadra solo.
        unit_price = convertir_monto(monto_base, moneda_contrato, invertir, tc_venta)
        amount = round(1 * unit_price, 2)
        lineas_qbo.append(
            {
                "DetailType": "SalesItemLineDetail",
                "Amount": amount,
                "Description": desc,
                "SalesItemLineDetail": {
                    "ItemRef": {"value": item_para(realm, primer_servicio)},
                    "Qty": 1,
                    "UnitPrice": unit_price,
                    "TaxCodeRef": {"value": ref_impuesto(linea_ref, amount)},
                },
            }
        )

    factura = {"Line": lineas_qbo, "CustomerRef": {"value": str(customer)}}

    # Los montos que mandamos son SIN IVA: QuickBooks debe sumarlo encima usando
    # el TaxCodeRef de cada linea. Sin esta directiva, algunas facturas salian
    # como EXENTAS aunque sus lineas llevaran IVA. Solo aplica a produccion: el
    # sandbox es una empresa gringa y no maneja este calculo global.
    if ENTORNO == "produccion":
        factura["GlobalTaxCalculation"] = "TaxExcluded"

        # QuickBooks API NO autocalcula el IVA en empresas Global (fuera de EEUU).
        # Es obligatorio mandar el TxnTaxDetail con el TotalTax y el detalle por tasa,
        # de lo contrario asume que el impuesto es 0 y descuadra con Hacienda/Mondragon.
        if total_tax > 0:
            tax_details = []
            for rate_id, data in tax_lines.items():
                tax_details.append(
                    {
                        "Amount": round(data["amount"], 2),
                        "DetailType": "TaxLineDetail",
                        "TaxLineDetail": {
                            "TaxRateRef": {"value": str(rate_id)},
                            "PercentBased": True,
                            "TaxPercent": data["pct"],
                            "NetAmountTaxable": round(data["net"], 2),
                        },
                    }
                )
            factura["TxnTaxDetail"] = {
                "TotalTax": round(total_tax, 2),
                "TaxLine": tax_details,
            }

    # Correo del cliente: QuickBooks NO lo hereda del perfil al crear la factura
    # por API, y sin correo el Facturador Plus no puede despacharla a Hacienda.
    # Sale de la tabla espejo clientes_quickbooks (la llena sync_clientes.py).
    if correo_cliente:
        factura["BillEmail"] = {"Address": correo_cliente}

    # Nota visible en la factura (para porcentaje / parcial)
    if nota_factura:
        factura["CustomerMemo"] = {"value": nota_factura}

    # Nota privada interna
    if em.get("observaciones_internas"):
        factura["PrivateNote"] = em["observaciones_internas"]

    # Sandbox: impuesto global (limitacion del sandbox gringo)
    if ENTORNO == "sandbox" and codigos_gravados:
        factura["TxnTaxDetail"] = {
            "TxnTaxCodeRef": {"value": str(next(iter(codigos_gravados)))}
        }
        if len(codigos_gravados) > 1:
            print(
                "    [aviso sandbox] mezcla de % de IVA; en sandbox se aplica uno solo."
            )

    # Moneda: en produccion mandamos la moneda de la factura (invertida o no)
    if CFG["usar_moneda_real"]:
        factura["CurrencyRef"] = {"value": moneda_factura(moneda_contrato, invertir)}

    # Debug al FINAL: aca la factura ya esta completa (impuestos, correo, notas
    # y moneda). Antes se imprimia apenas creada y parecia que faltaban campos.
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
#  MAIN
# ════════════════════════════════════════════════════════════════════════════


def validar_entorno():
    if ENTORNO not in ENTORNOS:
        sys.exit(f"ENTORNO invalido: {ENTORNO}")
    if ENTORNO == "sandbox" and "sandbox" not in CFG["base_url"]:
        sys.exit("BLINDAJE: en sandbox la URL debe ser de sandbox.")
    if not CFG["client_id"] or not CFG["client_secret"]:
        sys.exit("Faltan las llaves (client_id/secret) en el .env para este entorno.")
    if not DATABASE_URL:
        sys.exit("Falta DATABASE_URL.")


def realm_de(em):
    if CFG["realm_fijo"]:
        return CFG["realm_fijo"]
    return em.get("realm_id")


def clasificar_error_metricas(mensaje):
    """Suma el error al contador correcto del status_global (metricas)."""
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


def _contar_tipo_emision(tipo):
    """Suma la emision OK a su casilla de tipo (completo/porcentaje/parcial)."""
    t = (tipo or "").strip().lower()
    if "completo" in t:
        status_global_ejecution["tipo_completo"] += 1
    elif "porcentaje" in t:
        status_global_ejecution["tipo_porcentaje"] += 1
    elif "parcial" in t:
        status_global_ejecution["tipo_parcial"] += 1


def main():
    validar_entorno()

    # Interruptor remoto (por ahora siempre True; queda listo para el futuro)
    if not verificar_estado_rpa():
        print("RPA desactivado administrativamente en Supabase. No se ejecuta.")
        return

    inicio = time.time()  # cronometro para tiempo_ejecucion
    hoy = datetime.date.today()
    periodo = f"{hoy.year}-{hoy.month:02d}"

    print("=" * 60)
    print(f"RPA CONTRATOS FIJOS  —  ENTORNO: {ENTORNO.upper()}")
    print(f"Fecha: {hoy.isoformat()}   Periodo: {periodo}")
    if LIMITE_FACTURAS is not None:
        print(f"   *** LIMITE: {LIMITE_FACTURAS} factura(s) ***")
    if IGNORAR_DIA_EMISION:
        print("   *** IGNORANDO dia_emision (modo prueba) ***")
    print("=" * 60)

    # Orquestador de reporte PDF (contratos fijos, archivo aparte)
    reporte = ReporteContratosFijosRPA(entorno=ENTORNO)

    conn = psycopg2.connect(DATABASE_URL)
    tc_dia = None  # se consulta solo si hace falta (alguna emision invertida)

    try:
        emisiones = leer_emisiones_listas(conn, periodo)

        cargar_mapa_servicios(conn)

        print(f"\nEmisiones 'Lista' de {periodo}: {len(emisiones)}")

        # Filtro por dia de emision
        a_procesar = []
        for em in emisiones:
            objetivo = dia_objetivo_este_mes(em.get("dia_emision"), hoy)
            if IGNORAR_DIA_EMISION or (objetivo is not None and hoy.day == objetivo):
                a_procesar.append(em)
            else:
                status_global_ejecution["emisiones_en_espera"] += 1
                print(
                    f"  [espera] {em['compania_facturadora']}: dia objetivo {objetivo}, hoy {hoy.day}"
                )

        status_global_ejecution["emisiones_listas"] = len(emisiones)

        if LIMITE_FACTURAS is not None:
            a_procesar = a_procesar[:LIMITE_FACTURAS]

        status_global_ejecution["total_a_facturar"] = len(a_procesar)
        print(f"A facturar hoy: {len(a_procesar)}\n")

        tokens = {}
        for em in a_procesar:
            eid = em["emision_id"]
            realm = realm_de(em)
            cliente_lbl = em.get("compania_facturadora", "")
            cliente_nom = (
                em.get("nombre_cliente", "") or "-"
            )  # cliente al que se factura
            tipo = em["estado_emision"]
            desc_fact = (
                em.get("emision_descripcion") or em.get("contrato_descripcion") or "-"
            )

            if not realm or realm == "TODO":
                marcar_error(conn, eid, "Empresa sin realm_id")
                status_global_ejecution["con_error"] += 1
                status_global_ejecution["err_sin_empresa"] += 1
                reporte.registrar_emision(
                    compania=cliente_lbl,
                    cliente=cliente_nom,
                    factura_num="-",
                    tipo=tipo,
                    lineas=[],
                    status="ERR",
                    error_msg="Empresa sin realm_id",
                    descripcion_factura=desc_fact,
                )
                print(f"  [SKIP] {cliente_lbl}: empresa sin realm")
                continue
            if ENTORNO == "sandbox" and realm in REALMS_PRODUCCION:
                sys.exit(
                    "BLINDAJE: en sandbox aparecio un realm de produccion. Abortando."
                )
            customer = CFG["cliente_fijo"] or em["qbo_customer_id"]
            if not customer:
                marcar_error(conn, eid, "Contrato sin qbo_customer_id")
                status_global_ejecution["con_error"] += 1
                status_global_ejecution["err_sin_cliente"] += 1
                reporte.registrar_emision(
                    compania=cliente_lbl,
                    cliente=cliente_nom,
                    factura_num="-",
                    tipo=tipo,
                    lineas=[],
                    status="ERR",
                    error_msg="Sin qbo_customer_id",
                    descripcion_factura=desc_fact,
                )
                print(f"  [SKIP] {cliente_lbl}: sin qbo_customer_id")
                continue

            try:
                # Tipo de cambio: solo si esta emision invierte moneda
                tc_venta = None
                if em.get("moneda_invertida") and CAMBIO_MONEDA_HABILITADO:
                    if tc_dia is None:
                        tc_dia = obtener_tipo_cambio()
                        print(
                            f"  Tipo de cambio del dia: venta {tc_dia['venta']} ({tc_dia['fuente']})"
                        )
                    tc_venta = tc_dia["venta"]

                if realm not in tokens:
                    tokens[realm] = get_access_token(realm)
                lineas = leer_lineas_contrato(conn, em["contrato_id"])

                # Sin lineas no hay nada que facturar: QBO rechazaria la factura
                # con "Line is missing". Lo detectamos antes para dejar un estado
                # claro en el log y no gastar la llamada.
                if not lineas:
                    raise RuntimeError("Contrato sin lineas para facturar")

                factura = construir_factura(
                    em,
                    lineas,
                    realm,
                    tokens[realm],
                    tc_venta,
                    correo_cliente=em.get("cliente_email"),
                )
                inv = enviar_factura(realm, tokens[realm], factura)

                marcar_emitida(conn, eid, inv, tc_venta)

                # Moneda con la que realmente se emitio la factura (dinamica)
                # Misma condicion que usa construir_factura: si el cambio de
                # moneda esta descontinuado, la factura sale en la moneda del
                # contrato aunque la fila tenga moneda_invertida=true. El
                # reporte y las metricas tienen que decir lo mismo que se
                # facturo, no lo que dice el flag viejo de la base.
                moneda_emitida = moneda_factura(
                    em.get("moneda") or "Dólares",
                    bool(em.get("moneda_invertida")) and CAMBIO_MONEDA_HABILITADO,
                )

                # --- Metricas de exito ---
                status_global_ejecution["facturadas_ok"] += 1
                _contar_tipo_emision(tipo)
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

                reporte.registrar_emision(
                    compania=cliente_lbl,
                    cliente=cliente_nom,
                    factura_num=inv.get("DocNumber", "-"),
                    tipo=tipo,
                    lineas=lineas,
                    status="OK",
                    descripcion_factura=desc_fact,
                    moneda=moneda_emitida,
                    tipo_cambio_usado=tc_venta,
                    total_qb=inv.get("TotalAmt"),
                )
                print(
                    f"  [OK]   {cliente_lbl} ({tipo}): factura {inv.get('DocNumber')} total {inv.get('TotalAmt')}"
                )
            except Exception as e:
                marcar_error(conn, eid, e)
                status_global_ejecution["con_error"] += 1
                clasificar_error_metricas(str(e))
                reporte.registrar_emision(
                    compania=cliente_lbl,
                    cliente=cliente_nom,
                    factura_num="-",
                    tipo=tipo,
                    lineas=[],
                    status="ERR",
                    error_msg=str(e),
                    descripcion_factura=desc_fact,
                )
                print(f"  [ERR]  {cliente_lbl}: {e}")
    finally:
        conn.close()

    ruta_pdf = reporte.exportar_pdf()
    print("\n" + "=" * 60)
    print(f"Listo. Facturadas: {reporte.exitosas}   Errores: {reporte.errores}")
    if ruta_pdf:
        print(f"Log PDF: {ruta_pdf}")
    print("=" * 60)

    # ── Reporte de metricas a Supabase (mismo patron que los otros RPAs) ──
    duracion = int(time.time() - inicio)
    status_global_ejecution["tiempo_ejecucion"] = f"{duracion // 60}m {duracion % 60}s"
    status_global_ejecution["entorno"] = ENTORNO
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
            automatizacion_id=ID_RPA_FIJOS,
            subcarpeta="contratos_fijos",
        )
        if isinstance(resultado, dict):
            url_pdf = resultado.get("url_pdf")
    except Exception as e_sup:
        print(f"[aviso] No se pudo reportar a Supabase: {e_sup}")

    # ── Notificacion a Microsoft Teams (tarjeta + boton al PDF) ──
    try:
        hechos, texto_pie = resumen_contratos_fijos(status_global_ejecution)
        enviar_tarjeta_ejecucion(
            webhook_url=os.getenv("TEAMS_WEBHOOK_URL"),
            nombre_proceso="RPA Facturacion - Contratos Fijos",
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
