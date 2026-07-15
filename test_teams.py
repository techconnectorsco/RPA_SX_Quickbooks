"""
test_teams.py - Prueba del webhook de Teams (canal nuevo)
=========================================================
Envia una tarjeta de prueba usando teams_notifier.py + teams_resumen.py,
exactamente igual que lo hacen los RPA reales. Si esta prueba pasa,
los RPA funcionaran sin tocar nada mas que el .env.

Uso:  python test_teams.py
"""

import os
from dotenv import load_dotenv

from teams_notifier import enviar_tarjeta_ejecucion
from teams_resumen import resumen_operaciones

# Carga el .env de la carpeta del proyecto
load_dotenv()

WEBHOOK_URL = os.getenv("TEAMS_WEBHOOK_URL")

if not WEBHOOK_URL:
    print("ERROR: No se encontro TEAMS_WEBHOOK_URL en el .env")
    raise SystemExit(1)

print(f"Webhook cargado (primeros 60 chars): {WEBHOOK_URL[:60]}...")

# ── Metricas falsas simulando una ejecucion real del RPA ─────────────
metricas_prueba = {
    "total_operaciones": 5,
    "facturadas_ok": 4,
    "con_error": 1,
    "monto_total_usd": 1250.50,
    "monto_total_crc": 645000.00,
    "facturas_con_cambio_moneda": 1,
    "tiempo_ejecucion": "0:02:15",
    "err_conexion": 1,
}

hechos, texto_pie = resumen_operaciones(metricas_prueba)

ok = enviar_tarjeta_ejecucion(
    webhook_url=WEBHOOK_URL,
    nombre_proceso="PRUEBA - RPA Facturacion (canal nuevo)",
    entorno="test",
    metricas=metricas_prueba,
    hechos_resumen=hechos,
    texto_pie=texto_pie,
    url_pdf="https://www.example.com",  # boton de prueba
)

if ok:
    print("\n✅ Prueba exitosa. Revisa el canal de Teams.")
    print("   Si ves la tarjeta, solo cambia TEAMS_WEBHOOK_URL en el .env y listo.")
else:
    print("\n❌ Fallo el envio. Revisa el mensaje de error arriba.")
