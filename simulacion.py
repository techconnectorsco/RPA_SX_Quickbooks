"""
simulacion.py  —  CORRIDA EN FRIO de los RPA de facturacion
============================================================
Permite ejecutar los RPA contra datos REALES (base y QuickBooks) haciendo todo
el recorrido completo, pero SIN crear ni una sola factura y SIN escribir en la
base.

QUE SI HACE la corrida en frio:
  - Lee la base de produccion (emisiones/operaciones, lineas, impuestos, correos).
  - Refresca los tokens y consulta QuickBooks (solo lectura).
  - Resuelve los items y los TaxCode reales de cada empresa.
  - Arma el payload COMPLETO de cada factura (montos, IVA, correo, notas).
  - Verifica la matematica y la moneda del cliente.

QUE NO HACE:
  - NO manda POST /invoice: no se crea ninguna factura.
  - NO escribe en la base (ni marcar_emitida ni marcar_error).
  - NO reporta a Supabase ni notifica a Teams.

POR QUE HACE FALTA:
  El sandbox de QuickBooks es una empresa de EE.UU.: no tiene multimoneda, ni
  los impuestos de Costa Rica, ni los clientes reales. Casi nada de lo que hay
  que validar (resolucion de TaxCode, Exento, TxnTaxDetail, correo del cliente)
  se puede ejercitar alla. Por eso la prueba tiene que ser contra produccion,
  pero sin emitir.

LIMITE HONESTO:
  Esto NO prueba que QuickBooks acepte el payload. Verifica que los numeros
  cuadren y que las referencias existan, pero si QBO rechaza algo por un
  detalle de formato, eso solo se sabe posteando. Por eso despues de una
  simulacion limpia conviene una unica factura real (LIMITE_FACTURAS = 1 con
  MONTO_FIJO_PRUEBA) que finanzas anula con nota de credito.

Uso desde el RPA:
    import simulacion
    simulacion.registrar(nombre, factura, avisos)
    simulacion.resumen()
    simulacion.guardar_json("logs/simulacion_fijos.json")
"""

import os
import json
import datetime

# Todo lo que se fue simulando en esta corrida
_REGISTROS = []


# ════════════════════════════════════════════════════════════════════════════
#  VERIFICACIONES
# ════════════════════════════════════════════════════════════════════════════


def verificar_matematica(factura):
    """Comprueba lo mismo que valida QuickBooks antes de aceptar una factura.

    QBO rechaza con el error 6070 ("El monto no es equivalente al precio
    unitario por la cantidad") si Amount != UnitPrice x Qty, aunque la
    diferencia sea de un centimo. Fue lo que tumbo a COOPELOT y a HABLA
    BEBIDAS: el total guardado en la base no coincidia con la multiplicacion.

    Tambien revisa que el TotalTax del TxnTaxDetail cuadre con la suma de sus
    lineas de impuesto, que es la otra cuenta que QBO verifica.

    Devuelve una lista de problemas (vacia = todo bien).
    """
    problemas = []

    for i, linea in enumerate(factura.get("Line", []), 1):
        det = linea.get("SalesItemLineDetail") or {}
        amount = linea.get("Amount")
        qty = det.get("Qty")
        unit = det.get("UnitPrice")
        if amount is None or qty is None or unit is None:
            problemas.append(f"L{i}: falta Amount, Qty o UnitPrice")
            continue
        esperado = round(float(qty) * float(unit), 2)
        if abs(float(amount) - esperado) > 0.001:
            problemas.append(
                f"L{i}: Amount {amount} != Qty x UnitPrice ({qty} x {unit} = "
                f"{esperado}) -> QuickBooks lo rechazaria con el error 6070"
            )
        if not (det.get("TaxCodeRef") or {}).get("value"):
            problemas.append(f"L{i}: sin TaxCodeRef (QBO exige impuesto en cada linea)")
        if not (det.get("ItemRef") or {}).get("value"):
            problemas.append(f"L{i}: sin ItemRef")

    detalle = factura.get("TxnTaxDetail") or {}
    if "TotalTax" in detalle:
        suma = sum(float(t.get("Amount", 0)) for t in detalle.get("TaxLine", []))
        if abs(float(detalle["TotalTax"]) - round(suma, 2)) > 0.011:
            problemas.append(
                f"TxnTaxDetail: TotalTax {detalle['TotalTax']} != suma de las "
                f"lineas de impuesto ({round(suma, 2)})"
            )

    return problemas


def verificar_moneda_cliente(query_qbo, realm, token, customer_id, moneda_factura):
    """Compara la moneda de la factura contra la del cliente en QuickBooks.

    En QBO cada cliente tiene UNA moneda fija (la de sus cuentas por cobrar) y
    todas sus facturas deben ir en esa moneda. Si no coinciden, QBO rechaza con
    el error 6000 ("Cambie esta divisa de la transaccion..."). Fue lo que paso
    con AGENCIA ADUANAL. En frio se detecta antes, de una sola pasada, en vez
    de descubrirlo factura por factura.

    Devuelve un aviso (str) o None si esta todo bien.
    """
    if not customer_id or not moneda_factura:
        return None
    try:
        sql = f"SELECT * FROM Customer WHERE Id = '{customer_id}'"
        r = query_qbo(realm, token, sql)
        if r.status_code != 200:
            return f"no se pudo leer el cliente {customer_id} en QBO ({r.status_code})"
        clientes = r.json().get("QueryResponse", {}).get("Customer", [])
        if not clientes:
            return f"el cliente {customer_id} no existe en QuickBooks"
        moneda_cliente = (clientes[0].get("CurrencyRef") or {}).get("value")
        if moneda_cliente and moneda_cliente != moneda_factura:
            return (
                f"MONEDA: la factura sale en {moneda_factura} pero el cliente "
                f"'{clientes[0].get('DisplayName', '')}' esta en {moneda_cliente} "
                f"en QuickBooks -> seria rechazada (error 6000). Hay que apuntar "
                f"el contrato al cliente de la moneda correcta."
            )
    except Exception as e:
        return f"no se pudo verificar la moneda del cliente: {e}"
    return None


# ════════════════════════════════════════════════════════════════════════════
#  REGISTRO Y REPORTE
# ════════════════════════════════════════════════════════════════════════════


def registrar(etiqueta, factura, avisos=None):
    """Guarda una factura simulada. Devuelve el total que habria tenido."""
    lineas = factura.get("Line", [])
    subtotal = round(sum(float(l.get("Amount", 0)) for l in lineas), 2)
    impuesto = float((factura.get("TxnTaxDetail") or {}).get("TotalTax", 0) or 0)
    total = round(subtotal + impuesto, 2)

    _REGISTROS.append(
        {
            "etiqueta": etiqueta,
            "lineas": len(lineas),
            "subtotal": subtotal,
            "impuesto": round(impuesto, 2),
            "total": total,
            "moneda": (factura.get("CurrencyRef") or {}).get("value", "-"),
            "correo": (factura.get("BillEmail") or {}).get("Address"),
            "nota_interna": factura.get("PrivateNote"),
            "nota_visible": (factura.get("CustomerMemo") or {}).get("value"),
            "taxcodes": [
                (l.get("SalesItemLineDetail") or {}).get("TaxCodeRef", {}).get("value")
                for l in lineas
            ],
            "problemas": verificar_matematica(factura),
            "avisos": [a for a in (avisos or []) if a],
            "payload": factura,
        }
    )
    return total


def factura_simulada(factura):
    """Devuelve un objeto con la misma forma que responde QuickBooks, para que
    el resto del RPA (reporte PDF, metricas) siga funcionando igual."""
    lineas = factura.get("Line", [])
    subtotal = sum(float(l.get("Amount", 0)) for l in lineas)
    impuesto = float((factura.get("TxnTaxDetail") or {}).get("TotalTax", 0) or 0)
    return {
        "Id": "SIMULADA",
        "DocNumber": "SIM",
        "TotalAmt": round(subtotal + impuesto, 2),
    }


def hubo_problemas():
    """True si alguna factura simulada tuvo problemas o avisos."""
    return any(r["problemas"] or r["avisos"] for r in _REGISTROS)


def resumen():
    """Imprime el resultado de la corrida en frio."""
    print("\n" + "=" * 72)
    print("CORRIDA EN FRIO — NO se creo ninguna factura ni se escribio en la base")
    print("=" * 72)

    if not _REGISTROS:
        print("  (no hubo nada que simular)")
        return

    con_problemas = 0
    for r in _REGISTROS:
        print(f"\n  {r['etiqueta']}")
        print(
            f"    Lineas {r['lineas']}  |  Subtotal {r['subtotal']:,.2f}  "
            f"IVA {r['impuesto']:,.2f}  ->  TOTAL {r['total']:,.2f} {r['moneda']}"
        )
        print(f"    TaxCodes por linea : {', '.join(str(t) for t in r['taxcodes'])}")
        print(f"    Correo             : {r['correo'] or '(SIN CORREO)'}")
        if r["nota_interna"]:
            print(f"    Nota interna       : {r['nota_interna'][:60]}")
        if r["nota_visible"]:
            print(f"    Nota visible       : {r['nota_visible'][:60]}")
        for p in r["problemas"]:
            print(f"    [PROBLEMA] {p}")
        for a in r["avisos"]:
            print(f"    [AVISO]    {a}")
        if r["problemas"] or r["avisos"]:
            con_problemas += 1

    print("\n" + "-" * 72)
    print(
        f"  Facturas simuladas: {len(_REGISTROS)}   Con problemas o avisos: {con_problemas}"
    )
    sin_correo = sum(1 for r in _REGISTROS if not r["correo"])
    if sin_correo:
        print(
            f"  Sin correo del cliente: {sin_correo}  (el Facturador Plus las rechaza)"
        )
    print("-" * 72)
    print("  Recorda: esto NO prueba que QuickBooks acepte el payload. Cuando la")
    print("  simulacion salga limpia, el siguiente paso es UNA factura real")
    print("  (LIMITE_FACTURAS = 1 con MONTO_FIJO_PRUEBA) y anularla.")
    print("=" * 72)


def guardar_json(ruta):
    """Vuelca los payloads completos a un JSON, para revisarlos con calma."""
    if not _REGISTROS:
        return None
    os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(
            {
                "generado": datetime.datetime.now().isoformat(timespec="seconds"),
                "facturas": _REGISTROS,
            },
            f,
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    print(f"  Payloads completos: {ruta}")
    return ruta


def limpiar():
    _REGISTROS.clear()
