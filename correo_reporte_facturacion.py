"""
correo_reporte_facturacion.py
-----------------------------
Arma el cuerpo HTML del correo de ejecucion de los RPA de facturacion (fijos y
operaciones) y lo envia por Microsoft Graph, con el PDF del reporte adjunto.

Sigue el estilo visual del resto de correos de SX (header azul, tabla de datos,
boton para reportar incidencias, footer). Es un solo generador que se adapta al
status de fijos o de operaciones (comparten casi todos los campos).

Uso desde el RPA:
    from correo_reporte_facturacion import enviar_reporte_por_correo
    enviar_reporte_por_correo(
        status_global_ejecution,
        titulo="RPA Facturacion - Contratos Fijos",
        ruta_pdf=ruta_pdf,            # o None
        to_email=[...], cc_email="...",
    )
"""

from datetime import datetime

from correo_facturacion import EmailSenderFacturacion

# ── Coleres del estilo SX ────────────────────────────────────────────────────
AZUL = "#0078D7"
VERDE = "#16a34a"
ROJO = "#dc2626"
AMBAR = "#f59e0b"


def _fila(etiqueta, valor, alt=False, acento=None):
    """Una fila de la tabla. alt = fondo gris; acento = barra de color a la izq."""
    fondo = "background-color: #f9f9f9;" if alt else ""
    borde = f"border-left: 3px solid {acento};" if acento else ""
    return (
        f'<tr style="{fondo}{borde}">'
        f'<td style="padding: 8px;">{etiqueta}</td>'
        f'<td style="padding: 8px;"><strong>{valor}</strong></td></tr>'
    )


def construir_html(status, titulo):
    """Arma el HTML del reporte a partir del status del RPA."""
    fecha = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    entorno = str(status.get("entorno", "-")).upper()

    ok = status.get("facturadas_ok", 0)
    err = status.get("con_error", 0)
    total = status.get("total_a_facturar", status.get("total_operaciones", 0))

    # Estado general: verde si no hubo errores, ambar si hubo algunos, rojo si
    # todas fallaron.
    if err == 0:
        color_estado, texto_estado = VERDE, "Ejecucion completada sin errores."
    elif ok == 0 and err > 0:
        color_estado, texto_estado = (
            ROJO,
            "La ejecucion termino con errores en todas las facturas.",
        )
    else:
        color_estado, texto_estado = AMBAR, f"Ejecucion completada con {err} error(es)."

    # ── Bloque de montos ──
    crc = status.get("monto_total_crc", 0) or 0
    usd = status.get("monto_total_usd", 0) or 0

    # ── Filas de la tabla (comunes) ──
    filas = [
        _fila("Entorno", entorno, alt=True),
        _fila("Fecha de ejecucion", fecha),
        _fila("Duracion", status.get("tiempo_ejecucion", "N/A"), alt=True),
        _fila("Tipo de ejecucion", status.get("tipo_ejecucion", "Automatica")),
        _fila("Total a facturar", total, alt=True),
        _fila("Facturadas OK", ok, acento=VERDE),
        _fila("Con error", err, alt=True, acento=ROJO if err else None),
        _fila("Monto total CRC", f"{crc:,.2f}"),
        _fila("Monto total USD", f"{usd:,.2f}", alt=True),
    ]

    # Desglose de errores, solo si hubo alguno (para no llenar de ceros).
    detalle_err = {
        "Sin cliente": status.get("err_sin_cliente", 0),
        "Sin empresa": status.get("err_sin_empresa", 0),
        "Sin lineas": status.get("err_sin_lineas", 0),
        "IVA invalido": status.get("err_iva_invalido", 0),
        "Token": status.get("err_token", 0),
        "Conexion": status.get("err_conexion", 0),
        "Otros": status.get("err_otros", 0),
    }
    filas_err = ""
    if err:
        filas_err = (
            '<h3 style="color:#333; font-size:15px; margin:25px 0 8px;">Desglose de errores</h3>'
            '<table style="width:100%; border-collapse:collapse; font-size:14px;">'
        )
        i = 0
        for etiqueta, n in detalle_err.items():
            if n:
                filas_err += _fila(etiqueta, n, alt=(i % 2 == 0), acento=ROJO)
                i += 1
        filas_err += "</table>"

    # ── Desglose POR EMPRESA (SX, Laitcorp, H&N) ──
    por_empresa = status.get("por_empresa") or {}
    tabla_empresas = ""
    if por_empresa:
        filas_emp = ""
        i = 0
        for empresa, d in por_empresa.items():
            fondo = "background-color: #f9f9f9;" if i % 2 == 0 else ""
            monto_crc = f"{d.get('crc', 0):,.2f}" if d.get("crc") else "-"
            monto_usd = f"{d.get('usd', 0):,.2f}" if d.get("usd") else "-"
            err_emp = d.get("error", 0)
            err_txt = (
                f'<span style="color:{ROJO};">{err_emp}</span>' if err_emp else "0"
            )
            filas_emp += (
                f'<tr style="{fondo}">'
                f'<td style="padding: 8px;"><strong>{empresa}</strong></td>'
                f'<td style="padding: 8px; text-align:center;">{d.get("ok", 0)}</td>'
                f'<td style="padding: 8px; text-align:center;">{err_txt}</td>'
                f'<td style="padding: 8px; text-align:right;">{monto_crc}</td>'
                f'<td style="padding: 8px; text-align:right;">{monto_usd}</td>'
                f"</tr>"
            )
            i += 1
        tabla_empresas = f"""
            <h3 style="color:#333; font-size:15px; margin:28px 0 8px;">Detalle por empresa</h3>
            <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
                <thead>
                    <tr style="background-color: {AZUL}; color: white;">
                        <th style="padding: 10px; text-align: left;">Empresa</th>
                        <th style="padding: 10px; text-align: center;">Facturadas</th>
                        <th style="padding: 10px; text-align: center;">Errores</th>
                        <th style="padding: 10px; text-align: right;">Monto CRC</th>
                        <th style="padding: 10px; text-align: right;">Monto USD</th>
                    </tr>
                </thead>
                <tbody>
                    {filas_emp}
                </tbody>
            </table>
        """

    html = f"""
    <html>
    <body style="font-family: 'Segoe UI', Arial, sans-serif; background-color: #f3f4f6; margin: 0; padding: 30px;">
        <div style="max-width: 700px; margin: auto; background: #ffffff; border-radius: 10px;
                    box-shadow: 0 4px 12px rgba(0,0,0,0.08); padding: 30px;">

            <div style="text-align: center; border-bottom: 3px solid {AZUL}; padding-bottom: 15px; margin-bottom: 25px;">
                <h2 style="color: {AZUL}; margin: 0;">{titulo}</h2>
                <p style="color: #555; font-size: 14px;">Fecha: <strong>{fecha}</strong></p>
            </div>

            <div style="margin: 25px 0; padding: 15px; background-color: #f0f7ff; border-left: 5px solid {color_estado}; border-radius: 6px;">
                <p style="font-size: 15px; color: #333; margin: 0;">
                    <strong>Estado:</strong> {texto_estado}
                </p>
            </div>

            <table style="width: 100%; border-collapse: collapse; font-size: 15px;">
                <thead>
                    <tr style="background-color: {AZUL}; color: white;">
                        <th style="padding: 10px; text-align: left;">Campo</th>
                        <th style="padding: 10px; text-align: left;">Valor</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(filas)}
                </tbody>
            </table>

            {tabla_empresas}

            {filas_err}

            <!-- Boton de reportar incidencia deshabilitado por el momento.
                 Para volver a activarlo, quitar este comentario HTML.
            <div style="text-align: center; margin: 30px 0;">
                <a href="https://forms.gle/974zsGY3jtFRcxVG9" target="_blank"
                   style="background-color: {AZUL}; color: white; text-decoration: none;
                          padding: 12px 25px; border-radius: 6px; font-weight: bold; display: inline-block;">
                    Reportar incidencia o error
                </a>
            </div>
            -->

            <div style="margin-top: 35px; padding-top: 15px; border-top: 1px solid #e5e7eb; text-align: center;">
                <p style="color: #555; font-size: 14px;">
                    Correo generado automaticamente por el RPA de facturacion de <strong>Soportexperto SX</strong>.
                    El detalle completo va en el PDF adjunto.
                </p>
                <p style="font-size: 13px; color: #888;">
                    No responda directamente a este mensaje. Para soporte, use el formulario de arriba.
                </p>
            </div>
        </div>
    </body>
    </html>
    """
    return html


def enviar_reporte_por_correo(
    status, titulo, ruta_pdf=None, to_email=None, cc_email=None
):
    """Arma el HTML y lo envia con el PDF adjunto. Devuelve True/False.

    No lanza excepcion hacia el RPA: si el correo falla, lo reporta y devuelve
    False, para no tumbar la corrida por un problema de correo.
    """
    try:
        html = construir_html(status, titulo)
        sender = EmailSenderFacturacion()
        return sender.enviar(
            to_email=to_email,
            subject=f"{titulo} - {status.get('entorno', '')}".strip(" -"),
            body_html=html,
            cc_email=cc_email,
            adjunto_path=ruta_pdf,
        )
    except Exception as e:
        print(f"[correo] No se pudo enviar el reporte: {e}")
        return False


# ── Prueba local: genera el HTML con datos de ejemplo y lo guarda para verlo ──
if __name__ == "__main__":
    ejemplo = {
        "entorno": "produccion",
        "tiempo_ejecucion": "12 seg",
        "tipo_ejecucion": "Automatica",
        "total_a_facturar": 8,
        "facturadas_ok": 6,
        "con_error": 2,
        "monto_total_crc": 1_346_492.64,
        "monto_total_usd": 341.40,
        "err_sin_cliente": 1,
        "err_iva_invalido": 1,
    }
    html = construir_html(ejemplo, "RPA Facturacion - Contratos Fijos")
    with open("preview_correo.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("HTML de prueba guardado en preview_correo.html (abrilo en el navegador).")
