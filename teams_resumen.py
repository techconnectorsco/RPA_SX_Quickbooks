"""
teams_resumen.py - Construccion del resumen para la tarjeta de Teams
====================================================================
Traduce el status_global de cada RPA a la lista de "hechos" (datos) que se
muestran en la Adaptive Card, mas el texto de pie con el desglose de errores.

Esta separado de teams_notifier.py a proposito:
  - teams_notifier.py  -> generico: sabe ARMAR y ENVIAR una tarjeta.
  - teams_resumen.py    -> especifico: sabe QUE datos de facturacion mostrar.
Asi, si manana cambian las metricas o quieren mostrar otra cosa, se toca solo
este archivo y no el del envio.
"""


def _fmt_monto(valor):
    """Formatea un monto con separador de miles y 2 decimales."""
    try:
        return f"{float(valor):,.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _texto_desglose_errores(m, mapa):
    """Arma un texto corto con el desglose de errores, solo los que ocurrieron.
    mapa: lista de (clave_en_metricas, etiqueta_legible)."""
    partes = []
    for clave, etiqueta in mapa:
        n = int(m.get(clave, 0) or 0)
        if n > 0:
            partes.append(f"{n} {etiqueta}")
    if not partes:
        return None
    return "Errores: " + ", ".join(partes)


# ── Mapa de errores -> etiqueta legible (compartido) ─────────────────────────
_ERRORES = [
    ("err_sin_lineas", "sin lineas"),
    ("err_sin_cliente", "sin cliente"),
    ("err_sin_empresa", "sin empresa"),
    ("err_iva_invalido", "IVA invalido"),
    ("err_conexion", "conexion"),
    ("err_token", "token"),
    ("err_otros", "otros"),
]


def resumen_operaciones(m):
    """Devuelve (hechos, texto_pie) para el RPA de operaciones."""
    hechos = [
        {"title": "Operaciones", "value": str(m.get("total_operaciones", 0))},
        {"title": "Facturadas OK", "value": str(m.get("facturadas_ok", 0))},
        {"title": "Con error", "value": str(m.get("con_error", 0))},
        {"title": "Total USD", "value": _fmt_monto(m.get("monto_total_usd"))},
        {"title": "Total CRC", "value": _fmt_monto(m.get("monto_total_crc"))},
    ]
    if int(m.get("facturas_con_cambio_moneda", 0) or 0) > 0:
        hechos.append(
            {
                "title": "Con cambio de moneda",
                "value": str(m.get("facturas_con_cambio_moneda")),
            }
        )
    hechos.append({"title": "Duracion", "value": str(m.get("tiempo_ejecucion", "-"))})

    texto_pie = _texto_desglose_errores(m, _ERRORES)
    return hechos, texto_pie


def resumen_contratos_fijos(m):
    """Devuelve (hechos, texto_pie) para el RPA de contratos fijos."""
    hechos = [
        {"title": "Emisiones del dia", "value": str(m.get("total_a_facturar", 0))},
        {"title": "Facturadas OK", "value": str(m.get("facturadas_ok", 0))},
        {"title": "Con error", "value": str(m.get("con_error", 0))},
        {"title": "Total USD", "value": _fmt_monto(m.get("monto_total_usd"))},
        {"title": "Total CRC", "value": _fmt_monto(m.get("monto_total_crc"))},
    ]
    # Desglose por tipo solo si hubo facturadas
    tipos = []
    if int(m.get("tipo_completo", 0) or 0):
        tipos.append(f"{m['tipo_completo']} completo")
    if int(m.get("tipo_porcentaje", 0) or 0):
        tipos.append(f"{m['tipo_porcentaje']} porcentaje")
    if int(m.get("tipo_parcial", 0) or 0):
        tipos.append(f"{m['tipo_parcial']} parcial")
    if tipos:
        hechos.append({"title": "Por tipo", "value": ", ".join(tipos)})

    if int(m.get("emisiones_en_espera", 0) or 0) > 0:
        hechos.append(
            {
                "title": "En espera (otro dia)",
                "value": str(m.get("emisiones_en_espera")),
            }
        )
    hechos.append({"title": "Duracion", "value": str(m.get("tiempo_ejecucion", "-"))})

    texto_pie = _texto_desglose_errores(m, _ERRORES)
    return hechos, texto_pie


def resumen_sync_clientes(m):
    """Devuelve (hechos, texto_pie) para el RPA de sincronizacion de clientes.

    Es mas simple que los de facturacion: no hay montos ni facturas, solo el
    conteo de clientes sincronizados y el estado por empresa.
    """
    hechos = [
        {
            "title": "Clientes sincronizados",
            "value": str(m.get("total_sincronizados", 0)),
        },
        {
            "title": "Empresas procesadas",
            "value": f"{m.get('empresas_ok', 0)} de {m.get('empresas_total', 0)}",
        },
    ]

    # Detalle por empresa (cuantos clientes de cada una)
    detalle = m.get("detalle_por_empresa") or []
    for d in detalle:
        hechos.append(
            {"title": d.get("nombre", "-"), "value": f"{d.get('clientes', 0)} clientes"}
        )

    hechos.append({"title": "Duracion", "value": str(m.get("tiempo_ejecucion", "-"))})

    # Pie: si alguna empresa se salto, se detalla el motivo
    saltadas = m.get("empresas_saltadas") or []
    texto_pie = None
    if saltadas:
        partes = [
            f"{s.get('nombre', '-')} ({s.get('motivo', 'error')})" for s in saltadas
        ]
        texto_pie = "No sincronizadas: " + "; ".join(partes)
    return hechos, texto_pie
