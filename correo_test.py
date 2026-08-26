"""
probar_correo_facturacion.py
----------------------------
Prueba de punta a punta del CORREO de reporte, con datos simulados (mock) de una
corrida de facturacion. NO toca la base ni QuickBooks: solo arma el status como
si el RPA hubiera corrido, genera un PDF de muestra y envia el correo real (con
el PDF adjunto) para ver como llega.

Sirve para validar en local:
  - Que el HTML se ve bien (header, tabla general, desglose por empresa).
  - Que el PDF se adjunta y se abre.
  - Que las credenciales de Graph del .env funcionan.

Uso:
    venv/Scripts/python.exe probar_correo_facturacion.py tu-correo@dominio.com

Si no pasas un correo por argumento, usa el DESTINO_PRUEBA de abajo.

Requiere en el .env:  TENANT_ID, CLIENT_ID, VALUE  (credenciales de Azure)
Requiere:  pip install msal requests python-decouple fpdf2
"""

import sys

from correo_reporte_facturacion import enviar_reporte_por_correo

# Cambia esto por tu correo, o pasalo como argumento al ejecutar el script.
DESTINO_PRUEBA = "boot@soportexperto.com"


def crear_pdf_de_muestra(ruta="reporte_muestra.pdf"):
    """Genera un PDF chico solo para probar que el adjunto llega y se abre.
    (El PDF real lo genera el RPA; este es de relleno para la prueba.)"""
    try:
        from fpdf import FPDF
    except ImportError:
        print("[aviso] fpdf2 no esta instalado; se envia el correo SIN adjunto.")
        print("        Para probar el adjunto: pip install fpdf2")
        return None

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, "Reporte de muestra - Prueba de correo", 0, 1, "C")
    pdf.set_font("Arial", "", 11)
    pdf.ln(5)
    pdf.multi_cell(
        0,
        7,
        "Este es un PDF de muestra para probar que el adjunto del correo llega "
        "y se abre correctamente. En una corrida real, aca iria el reporte "
        "completo de facturacion que genera el RPA (detalle de cada emision, "
        "totales, tipo de cambio, etc.).",
    )
    pdf.output(ruta)
    print(f"[ok] PDF de muestra generado: {ruta}")
    return ruta


def status_mock():
    """Arma un status como el que deja el RPA tras una corrida, con las 3
    empresas, para ver el desglose completo en el correo."""
    return {
        "entorno": "produccion",
        "tiempo_ejecucion": "14 seg",
        "tipo_ejecucion": "Prueba (mock)",
        "total_a_facturar": 11,
        "facturadas_ok": 9,
        "con_error": 2,
        "monto_total_crc": 2_146_239.22,
        "monto_total_usd": 344.80,
        # desglose de errores (solo aparece lo que tenga valor > 0)
        "err_sin_cliente": 1,
        "err_iva_invalido": 1,
        "err_sin_empresa": 0,
        "err_sin_lineas": 0,
        "err_token": 0,
        "err_conexion": 0,
        "err_otros": 0,
        # el desglose por empresa que agregamos
        "por_empresa": {
            "Soportexperto.com S.A.": {
                "ok": 5,
                "error": 1,
                "crc": 1_890_500.00,
                "usd": 4.81,
            },
            "Laitcorp": {"ok": 3, "error": 0, "crc": 255_739.22, "usd": 0.0},
            "Hardware y Network S.A.": {"ok": 1, "error": 1, "crc": 0.0, "usd": 339.99},
        },
    }


def main():
    destino = sys.argv[1] if len(sys.argv) > 1 else DESTINO_PRUEBA
    print("=" * 60)
    print("PRUEBA DE CORREO DE FACTURACION (datos mock)")
    print(f"Destino: {destino}")
    print("=" * 60)

    pdf = crear_pdf_de_muestra()
    status = status_mock()

    ok = enviar_reporte_por_correo(
        status,
        titulo="RPA Facturacion - Contratos Fijos (PRUEBA)",
        ruta_pdf=pdf,
        to_email=[destino],
        cc_email=None,
    )

    print("=" * 60)
    if ok:
        print("Correo enviado. Revisa la bandeja (y spam por si acaso).")
    else:
        print("No se pudo enviar. Revisa el error de arriba y las credenciales.")
    print("=" * 60)


if __name__ == "__main__":
    main()
