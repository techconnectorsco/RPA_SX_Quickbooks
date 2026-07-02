"""
tipo_cambio.py
--------------
Obtiene el tipo de cambio del colon (CRC) del dia, con DOBLE fuente de
verificacion, copiando la logica del bot SXTCAMBIO:

  1. API publica (tipodecambio.paginasweb.cr)
  2. BCCR (libreria bccr)

Reglas de decision:
  - Si las dos fuentes responden y coinciden (dentro de una tolerancia) -> usa la API.
  - Si discrepan mas que la tolerancia -> usa BCCR (fuente oficial).
  - Si solo una responde -> usa esa.
  - Si ninguna responde -> lanza error (el RPA NO debe facturar con una tasa inventada).

Uso:
    from tipo_cambio import obtener_tipo_cambio
    tc = obtener_tipo_cambio()
    # tc = {"compra": 700.12, "venta": 707.45, "fecha": "01/07/2026", "fuente": "API publica"}

Requiere:  pip install requests bccr pandas
"""

import warnings

warnings.filterwarnings("ignore")

import requests

# La libreria BCCR es opcional: si no esta instalada, el modulo sigue
# funcionando solo con la API publica.
try:
    from bccr import SW
    import pandas as pd
    from datetime import date

    _BCCR_DISPONIBLE = True
except Exception:
    _BCCR_DISPONIBLE = False


TOLERANCIA = 0.5  # colones de diferencia permitida entre las dos fuentes


def _api_publica():
    """Fuente 1: API publica. Devuelve (compra, venta, fecha) o (None, None, None)."""
    try:
        r = requests.get("https://tipodecambio.paginasweb.cr/api", timeout=5)
        r.raise_for_status()
        d = r.json()
        return float(d["compra"]), float(d["venta"]), d["fecha"]
    except Exception as e:
        print(f"[tipo_cambio] API publica no disponible: {e}")
        return None, None, None


def _bccr():
    """Fuente 2: BCCR. Devuelve (compra, venta, fecha) o (None, None, None)."""
    if not _BCCR_DISPONIBLE:
        return None, None, None
    try:
        df = SW(compra=317, venta=318)
        if pd.api.types.is_period_dtype(df.index.dtype):
            df.index = df.index.to_timestamp()
        else:
            df.index = pd.to_datetime(df.index)
        hoy = date.today()
        df_hoy = df[df.index.date == hoy]
        if df_hoy.empty:
            print(f"[tipo_cambio] BCCR sin datos para hoy ({hoy})")
            return None, None, None
        row = df_hoy.iloc[0]
        return float(row["compra"]), float(row["venta"]), hoy.strftime("%d/%m/%Y")
    except Exception as e:
        print(f"[tipo_cambio] BCCR no disponible: {e}")
        return None, None, None


def obtener_tipo_cambio():
    """
    Devuelve un dict {compra, venta, fecha, fuente} con la tasa del dia,
    verificada entre las dos fuentes. Lanza RuntimeError si ninguna responde
    (para que el RPA NO facture con una tasa inventada).
    """
    api_c, api_v, api_f = _api_publica()
    bccr_c, bccr_v, bccr_f = _bccr()

    # Ninguna fuente: no inventamos tasa.
    if (api_v is None) and (bccr_v is None):
        raise RuntimeError(
            "No se pudo obtener el tipo de cambio de ninguna fuente (API ni BCCR). "
            "No se factura para no usar una tasa incorrecta."
        )

    # Solo una fuente disponible.
    if bccr_v is None:
        return {
            "compra": api_c,
            "venta": api_v,
            "fecha": api_f,
            "fuente": "API publica",
        }
    if api_v is None:
        return {"compra": bccr_c, "venta": bccr_v, "fecha": bccr_f, "fuente": "BCCR"}

    # Las dos disponibles: si coinciden dentro de la tolerancia, usa API;
    # si discrepan, usa BCCR (oficial).
    if abs(api_c - bccr_c) <= TOLERANCIA and abs(api_v - bccr_v) <= TOLERANCIA:
        return {
            "compra": api_c,
            "venta": api_v,
            "fecha": api_f,
            "fuente": "API publica",
        }
    else:
        print(
            f"[tipo_cambio] Fuentes discrepan (API venta {api_v} vs BCCR {bccr_v}); uso BCCR."
        )
        return {"compra": bccr_c, "venta": bccr_v, "fecha": bccr_f, "fuente": "BCCR"}


if __name__ == "__main__":
    # Prueba rapida
    try:
        tc = obtener_tipo_cambio()
        print(
            f"Tipo de cambio del dia: venta {tc['venta']} (compra {tc['compra']}) "
            f"| fuente: {tc['fuente']} | fecha: {tc['fecha']}"
        )
    except RuntimeError as e:
        print(f"ERROR: {e}")
