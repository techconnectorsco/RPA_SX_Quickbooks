"""
teams_notifier.py - Notificaciones a Microsoft Teams (RPA QuickBooks)
=====================================================================
Envia una tarjeta visual (Adaptive Card) a un canal de Teams con el resumen
de cada ejecucion de los RPA de facturacion, mas un boton que abre el PDF del
reporte (alojado en Supabase Storage).

DISENADO PARA SER MODULAR:
  - No sabe nada de operaciones ni de fijos en particular. Recibe un resumen
    generico (titulo, metricas, color, url del pdf) y arma la tarjeta.
  - Cada RPA construye su propio resumen y llama a enviar_tarjeta_ejecucion().
  - La URL del webhook/flujo se pasa desde afuera (sale del .env), NUNCA se
    escribe aca. Asi se cambia el canal (el tuyo de prueba -> el real) sin
    tocar codigo.

SOBRE EL ENVIO:
  - Se usa el flujo de Power Automate (workflow) cuya URL entrega el admin del
    tenant. Power Automate recibe el JSON y publica la Adaptive Card en el canal.
  - Si mas adelante habilitan SharePoint/Graph para adjuntar el PDF "pegado"
    al canal, solo habria que cambiar este modulo; los RPA no se enteran.

SEGURIDAD:
  - Un fallo al notificar a Teams NUNCA debe tumbar la facturacion. Por eso
    todo va dentro de try/except y solo se avisa por consola.

Requiere:  pip install requests
.env:  TEAMS_WEBHOOK_URL = <url del flujo de Power Automate>
"""

import os
import requests
from datetime import datetime

# ── Colores por estado (para la franja/acento de la tarjeta) ─────────────────
COLOR_OK = "Good"  # verde  (Adaptive Cards: Good / Warning / Attention)
COLOR_ADVERTENCIA = "Warning"  # naranja: se facturo pero hubo algun error
COLOR_ERROR = "Attention"  # rojo: no se facturo nada / fallo critico


def _color_segun_resultado(facturadas_ok, con_error):
    """Verde si todo OK, naranja si hubo facturas pero con algun error,
    rojo si no se facturo nada."""
    if facturadas_ok > 0 and con_error == 0:
        return COLOR_OK
    if facturadas_ok > 0 and con_error > 0:
        return COLOR_ADVERTENCIA
    return COLOR_ERROR


def _fact(nombre, valor):
    """Atajo para un par etiqueta/valor de la tabla de hechos de la tarjeta."""
    return {"title": nombre, "value": str(valor)}


def construir_adaptive_card(
    titulo,
    subtitulo,
    hechos,
    color="Good",
    texto_pie=None,
    url_pdf=None,
    etiqueta_boton="Ver reporte PDF",
):
    """Arma la Adaptive Card (dict) lista para enviar.

    titulo        : encabezado grande (ej. 'RPA Facturacion - Operaciones').
    subtitulo     : linea secundaria (ej. 'PRODUCCION - 12/07/2026 13:00').
    hechos        : lista de dicts {title, value} (la tabla de datos del resumen).
    color         : 'Good' | 'Warning' | 'Attention' (acento visual).
    texto_pie     : texto opcional al final (ej. desglose de errores).
    url_pdf       : si viene, agrega un boton que la abre.
    etiqueta_boton: texto del boton.
    """
    body = [
        {
            "type": "TextBlock",
            "text": titulo,
            "weight": "Bolder",
            "size": "Large",
            "wrap": True,
        },
        {
            "type": "TextBlock",
            "text": subtitulo,
            "isSubtle": True,
            "spacing": "None",
            "wrap": True,
        },
        # Barra de color de acento (contenedor con estilo segun resultado)
        {
            "type": "Container",
            "style": (
                "good"
                if color == "Good"
                else "warning" if color == "Warning" else "attention"
            ),
            "bleed": True,
            "items": [
                {
                    "type": "TextBlock",
                    "text": "Resumen de la ejecucion",
                    "weight": "Bolder",
                    "wrap": True,
                }
            ],
        },
        {"type": "FactSet", "facts": hechos},
    ]

    if texto_pie:
        body.append(
            {
                "type": "TextBlock",
                "text": texto_pie,
                "wrap": True,
                "spacing": "Medium",
                "isSubtle": True,
            }
        )

    card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body,
    }

    if url_pdf:
        card["actions"] = [
            {
                "type": "Action.OpenUrl",
                "title": etiqueta_boton,
                "url": url_pdf,
            }
        ]

    return card


def _envolver_para_powerautomate(card):
    """Power Automate (flujo 'publicar tarjeta') suele esperar la card dentro de
    un attachment estilo Teams. Se envia el sobre estandar de Adaptive Card."""
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": card,
            }
        ],
    }


def enviar_a_teams(webhook_url, card, timeout=15):
    """Envia una Adaptive Card ya construida al flujo de Teams.
    Devuelve True si se envio bien; nunca lanza excepcion (solo avisa)."""
    if not webhook_url:
        print("[Teams] No hay TEAMS_WEBHOOK_URL configurada; no se notifica.")
        return False

    payload = _envolver_para_powerautomate(card)
    try:
        resp = requests.post(webhook_url, json=payload, timeout=timeout)
        if resp.ok:  # 2xx (Power Automate suele responder 202)
            print(f"[Teams] Notificacion enviada ({resp.status_code}).")
            return True
        print(f"[Teams] Error al enviar: {resp.status_code} {resp.text[:200]}")
        return False
    except Exception as e:
        print(f"[Teams] Error de conexion: {e}")
        return False


def enviar_tarjeta_ejecucion(
    webhook_url,
    nombre_proceso,
    entorno,
    metricas,
    hechos_resumen,
    url_pdf=None,
    texto_pie=None,
):
    """Punto de entrada de alto nivel que usan los RPA.

    nombre_proceso : ej. 'RPA Facturacion - Operaciones'.
    entorno        : 'sandbox' / 'produccion' (para el subtitulo).
    metricas       : el status_global (se usa para elegir el color).
    hechos_resumen : lista de {title, value} con los datos a mostrar.
    url_pdf        : URL publica del PDF (boton). Opcional.
    texto_pie      : desglose de errores u otra nota. Opcional.
    """
    color = _color_segun_resultado(
        int(metricas.get("facturadas_ok", 0)),
        int(metricas.get("con_error", 0)),
    )
    marca_tiempo = datetime.now().strftime("%d/%m/%Y %H:%M")
    subtitulo = f"{str(entorno).upper()}  -  {marca_tiempo}"

    card = construir_adaptive_card(
        titulo=nombre_proceso,
        subtitulo=subtitulo,
        hechos=hechos_resumen,
        color=color,
        texto_pie=texto_pie,
        url_pdf=url_pdf,
    )
    return enviar_a_teams(webhook_url, card)


def enviar_tarjeta_simple(webhook_url, titulo, subtitulo, mensaje):
    """Tarjeta MINIMA de latido: solo avisa que el RPA se ejecuto y que no
    habia nada que hacer.

    A diferencia de enviar_tarjeta_ejecucion(), no lleva tabla de datos, ni
    barra de color, ni boton al PDF: es un aviso de una linea. La idea es que
    el equipo sepa que el robot esta encendido y paso por ahi, sin llenar el
    canal con tarjetas de ceros los dias sin emisiones.
    """
    card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": [
            {
                "type": "TextBlock",
                "text": titulo,
                "weight": "Bolder",
                "size": "Medium",
                "wrap": True,
            },
            {
                "type": "TextBlock",
                "text": subtitulo,
                "isSubtle": True,
                "spacing": "None",
                "wrap": True,
            },
            {
                "type": "TextBlock",
                "text": mensaje,
                "wrap": True,
                "spacing": "Small",
            },
        ],
    }
    return enviar_a_teams(webhook_url, card)
