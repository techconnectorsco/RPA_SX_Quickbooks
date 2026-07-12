"""
global_status.py - RPA Operaciones (QuickBooks)
Estructura de metricas de una corrida, para reportar a Supabase (tabla
'ejecuciones', campo 'metricas'). Mismo patron que los otros RPAs.

El main va llenando estos campos durante la ejecucion y al final se envia
completo a Supabase con finalizar_y_reportar().
"""

from datetime import datetime

status_global_ejecution = {
    "fecha_ejecucion": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    "tiempo_ejecucion": None,
    "entorno": None,  # 'sandbox' o 'produccion' (para no mezclar en los graficos)
    # Embudo de facturacion
    "total_operaciones": 0,  # operaciones 'aprobada' que se intentaron facturar
    "facturadas_ok": 0,  # facturas creadas en QuickBooks con exito
    "con_error": 0,  # operaciones que no se pudieron facturar
    # Desglose de errores (para saber QUE falla, no solo cuanto)
    "err_sin_lineas": 0,  # operacion sin lineas para facturar
    "err_sin_cliente": 0,  # sin qbo_customer_id
    "err_sin_empresa": 0,  # empresa sin realm_id
    "err_iva_invalido": 0,  # % de IVA no valido / no configurado como venta
    "err_conexion": 0,  # timeout / conexion / red
    "err_token": 0,  # token vencido / 401
    "err_otros": 0,  # cualquier otro no clasificado
    # Montos por moneda (NO se mezclan; salen del TotalAmt real de QuickBooks)
    "monto_total_usd": 0.00,
    "monto_total_crc": 0.00,
    "facturas_con_cambio_moneda": 0,  # cuantas se facturaron con moneda invertida
    "tipo_ejecucion": "Automatica",  # Automatica / Prueba / Manual
    "fuente": "QuickBooks-Operaciones",
}
