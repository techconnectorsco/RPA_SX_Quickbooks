"""
global_status_fijos.py - RPA Contratos Fijos (QuickBooks)
Estructura de metricas de una corrida, para reportar a Supabase (tabla
'ejecuciones', campo 'metricas'). Mismo patron que operaciones, con los
campos propios de contratos fijos (tipos de emision, en espera).
"""

from datetime import datetime

status_global_ejecution = {
    "fecha_ejecucion": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    "tiempo_ejecucion": None,
    "entorno": None,  # 'sandbox' o 'produccion'
    # Embudo de facturacion
    "emisiones_listas": 0,  # emisiones 'Lista' del periodo encontradas
    "emisiones_en_espera": 0,  # 'Lista' pero hoy NO es su dia de emision
    "total_a_facturar": 0,  # las que si tocaba facturar hoy (se intentaron)
    "facturadas_ok": 0,
    "con_error": 0,
    # Desglose por TIPO de emision (solo las facturadas OK)
    "tipo_completo": 0,
    "tipo_porcentaje": 0,
    "tipo_parcial": 0,
    # Desglose de errores (para saber QUE falla)
    "err_sin_lineas": 0,
    "err_sin_cliente": 0,
    "err_sin_empresa": 0,
    "err_iva_invalido": 0,
    "err_conexion": 0,
    "err_token": 0,
    "err_otros": 0,
    # Montos por moneda (del TotalAmt real de QuickBooks)
    "monto_total_usd": 0.00,
    "monto_total_crc": 0.00,
    "facturas_con_cambio_moneda": 0,
    "tipo_ejecucion": "Automatica",
    "fuente": "QuickBooks-Contratos-Fijos",
}
