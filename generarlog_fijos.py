"""
generarlog_fijos.py - Soportexperto
Generación de Log de Control Ejecutivo para el RPA de CONTRATOS FIJOS.

Archivo separado del de operaciones (comparten estilo, pero son entregables
distintos). La tabla, de izquierda a derecha:
  FACTURA | EMPRESA | TIPO | DESCRIPCION | TOTAL | T.CAMBIO | ESTADO

Diferencias respecto al de operaciones:
  - Columna TIPO: modo de emision del contrato (completo / porcentaje / parcial).
  - Sin columna HORAS: en contratos la unidad no siempre son horas; el espacio
    se le da a la descripcion, que es lo que la encargada revisa.
  - Titulo del listado: "CONTRATOS FIJOS" (para distinguirlo de operaciones).
"""

import os
import tempfile
from datetime import datetime
from typing import Dict, List
from fpdf import FPDF

try:
    import qrcode

    QR_DISPONIBLE = True
except ImportError:
    QR_DISPONIBLE = False

# =============================================================================
# PALETA DE COLORES DE SOPORTEXPERTO
# =============================================================================
AZUL_CORP = (11, 27, 70)
AZUL_ACCENTO = (40, 120, 200)
AZUL_FOOTER = (50, 65, 95)
VERDE_OK = (40, 167, 69)
ROJO_ERR = (220, 53, 69)
GRIS_TEXTO = (90, 90, 90)
GRIS_FONDO = (248, 249, 250)

# Tope FIJO de caracteres para la descripcion. La celda es mas ancha que en
# operaciones (no hay columna HORAS), asi que admite mas texto.
MAX_CHARS_DESCRIPCION = 135

ABREVIATURAS_EMPRESA = [
    ("hardware", "H&N"),
    ("network", "H&N"),
    ("soportexperto", "SX"),
    ("latinoamericana", "LAITCORP"),
    ("laitcorp", "LAITCORP"),
]

# Modo de emision -> etiqueta corta y clara para la columna TIPO.
TIPO_EMISION = {
    "facturar_completo": "Completo",
    "porcentaje": "Porcentaje",
    "monto_parcial": "Parcial",
}

# Mapa de errores -> texto corto (busca por "contiene", en minusculas).
ERRORES_MAPEADOS = [
    ("qbo_customer_id", "Sin cliente QB"),
    ("sin cliente", "Sin cliente QB"),
    ("realm", "Sin empresa QB"),
    ("sin lineas", "Sin lineas"),
    ("line is missing", "Sin lineas"),
    ("2020", "Sin lineas"),
    ("no tiene configurado un impuesto", "Falta IVA"),
    ("impuesto", "Falta IVA"),
    ("item", "Falta item QB"),
    ("timeout", "Error conexion"),
    ("timed out", "Error conexion"),
    ("connection", "Error conexion"),
    ("connect", "Error conexion"),
    ("max retries", "Error conexion"),
    ("name resolution", "Error conexion"),
    ("token", "Error token"),
    ("401", "Error token"),
    ("unauthorized", "Error token"),
    ("tipo de cambio", "Sin T.Cambio"),
    ("bccr", "Sin T.Cambio"),
    ("qbo 4", "Rechazo QB"),
    ("qbo 5", "Rechazo QB"),
    ("400", "Rechazo QB"),
    ("500", "Rechazo QB"),
]


def formato_moneda(valor: float) -> str:
    if valor is None:
        valor = 0.0
    return f"{valor:,.2f}"


def _limpiar_latin1(texto: str) -> str:
    return (texto or "").encode("latin-1", "replace").decode("latin-1")


def abreviar_empresa(nombre: str) -> str:
    base = (nombre or "").lower()
    for clave, sigla in ABREVIATURAS_EMPRESA:
        if clave in base:
            return sigla
    return (nombre or "-")[:12]


def etiqueta_tipo(tipo: str) -> str:
    return TIPO_EMISION.get((tipo or "").strip(), (tipo or "-"))


def concatenar_descripcion(lineas: List[Dict], descripcion_factura: str = "") -> str:
    """Une la descripcion de cada linea con '; ' y trunca a un tope fijo."""
    partes = []
    for ln in lineas or []:
        d = (ln.get("descripcion") or "").strip()
        if d:
            partes.append(d)

    texto = "; ".join(partes) if partes else (descripcion_factura or "-")

    if len(texto) > MAX_CHARS_DESCRIPCION:
        texto = texto[: MAX_CHARS_DESCRIPCION - 3].rstrip() + "..."
    return _limpiar_latin1(texto)


def estado_corto(status: str, error_msg: str = "") -> str:
    if status == "OK":
        return "OK"
    base = (error_msg or "").lower()
    for clave, corto in ERRORES_MAPEADOS:
        if clave in base:
            return corto
    return "ERROR"


def generar_qr_verificacion(stats: Dict, fecha: str, hora: str) -> str:
    if not QR_DISPONIBLE:
        return None

    verificacion_code = f"SE-RPA-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    contenido = [
        "==============================",
        "      SOPORTEXPERTO SX",
        "   Log de Contratos Fijos RPA",
        "==============================",
        f"Fecha: {fecha} | {hora}",
        "",
        f"Procesadas OK: {stats.get('exitosas', 0)}",
        f"Errores: {stats.get('errores', 0)}",
        f"Total Facturado: {formato_moneda(stats.get('monto_total', 0.0))}",
        "",
        "==============================",
        f"Verificación: {verificacion_code}",
    ]

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data("\n".join(contenido))
    qr.make(fit=True)

    img_qr = qr.make_image(fill_color="black", back_color="white")
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    img_qr.save(temp_file.name)
    temp_file.close()
    return temp_file.name


# =============================================================================
# CLASE PDF
# =============================================================================
class LogContratosFijosPDF(FPDF):

    def __init__(self, entorno: str, logo_path: str = "img/SX.png"):
        super().__init__(orientation="L", unit="mm", format="Legal")
        self.entorno = entorno.upper()
        self.logo_path = logo_path
        self.qr_path = None
        self.datos_proceso = {}
        self.set_auto_page_break(auto=True, margin=25)
        self.set_margins(10, 10, 10)

    def set_qr_path(self, qr_path: str):
        self.qr_path = qr_path

    def header(self):
        if self.logo_path and os.path.exists(self.logo_path):
            self.image(self.logo_path, 15, 8, 40)

        self.set_font("Arial", "B", 16)
        self.set_text_color(*AZUL_CORP)
        self.set_y(10)
        self.cell(0, 8, "SOPORTEXPERTO", 0, 1, "C")

        self.set_font("Arial", "B", 12)
        self.set_text_color(*AZUL_ACCENTO)
        self.cell(
            0,
            6,
            f"LOG DE CONTROL DE CONTRATOS FIJOS RPA - ENTORNO: {self.entorno}",
            0,
            1,
            "C",
        )

        self.set_font("Arial", "", 10)
        self.set_text_color(*GRIS_TEXTO)
        fecha = self.datos_proceso.get("fecha", datetime.now().strftime("%d/%m/%Y"))
        hora = self.datos_proceso.get("hora", datetime.now().strftime("%I:%M %p"))
        self.set_xy(-110, 10)
        self.cell(60, 5, f"Fecha Ejecución: {fecha}", 0, 1, "R")
        self.set_xy(-110, 15)
        self.cell(60, 5, f"Hora Ejecución: {hora}", 0, 1, "R")

        if self.qr_path and os.path.exists(self.qr_path):
            self.image(self.qr_path, self.w - 42, 5, 30)
            self.set_font("Arial", "I", 6)
            self.set_text_color(*GRIS_TEXTO)
            self.set_xy(self.w - 44, 36)
            self.cell(34, 3, "Validación Interna RPA", 0, 0, "C")

        self.set_draw_color(*AZUL_ACCENTO)
        self.set_line_width(0.6)
        self.line(10, 41, self.w - 10, 41)
        self.set_y(45)

    def footer(self):
        self.set_y(-23)
        self.set_fill_color(*AZUL_ACCENTO)
        self.rect(0, self.h - 24, self.w, 2, "F")
        self.set_fill_color(*AZUL_FOOTER)
        self.rect(0, self.h - 22, self.w, 22, "F")

        self.set_text_color(255, 255, 255)
        self.set_font("Arial", "B", 10)
        self.set_y(-17)
        self.cell(0, 5, "Oficina de Transformación Digital | SoporteXperto", 0, 1, "C")
        self.set_font("Arial", "I", 8)
        self.cell(
            0, 5, "SOPORTEXPERTO.COM | Monitoreo de Procesos QuickBooks", 0, 1, "C"
        )

        self.set_font("Arial", "I", 9)
        self.set_xy(-25, -13)
        self.cell(15, 5, f"Pág {self.page_no()}", 0, 0, "R")

    def agregar_resumen(self, stats: Dict):
        self.set_font("Arial", "B", 11)
        self.set_text_color(*AZUL_CORP)
        self.cell(0, 6, "1. RESUMEN DE COMPROBACIÓN EJECUTIVA", 0, 1, "L")
        self.ln(1)

        y_pos = self.get_y()
        self.set_fill_color(*GRIS_FONDO)
        self.rect(10, y_pos, self.w - 20, 20, "F")
        self.set_fill_color(*AZUL_CORP)
        self.rect(10, y_pos, 2, 20, "F")

        self.set_xy(15, y_pos + 3)
        self.set_font("Arial", "B", 10)
        self.set_text_color(0, 0, 0)

        self.cell(65, 6, f"Total Emisiones: {stats.get('total', 0)}", 0, 0)
        self.set_text_color(*VERDE_OK)
        self.cell(65, 6, f"Facturadas [OK]: {stats.get('exitosas', 0)}", 0, 0)
        self.set_text_color(*ROJO_ERR)
        self.cell(65, 6, f"Con Error [ERR]: {stats.get('errores', 0)}", 0, 0)
        self.set_text_color(0, 0, 0)
        self.cell(
            0,
            6,
            f"Monto Total Procesado: {formato_moneda(stats.get('monto_total', 0))}",
            0,
            1,
        )

        self.set_x(15)
        self.set_font("Arial", "I", 9)
        self.set_text_color(*GRIS_TEXTO)
        self.cell(0, 5, f"Duración de corrida: {stats.get('duracion', 'N/A')}", 0, 1)
        self.ln(6)

    def agregar_detalle(self, emisiones: List[Dict]):
        self.set_font("Arial", "B", 11)
        self.set_text_color(*AZUL_CORP)
        self.cell(
            0,
            6,
            "2. DETALLE DE COMPROBACIÓN DE FACTURAS - CONTRATOS FIJOS",
            0,
            1,
            "L",
        )
        self.ln(1)

        # Anchos (mm). Suman ~335. Sin HORAS; la DESCRIPCION se lleva ese espacio.
        # FACTURA  EMPRESA  TIPO  DESCRIPCION  TOTAL  T.CAMBIO  ESTADO
        anchos = [26, 28, 28, 165, 40, 24, 24]
        headers = [
            "FACTURA",
            "EMPRESA",
            "TIPO",
            "DESCRIPCIÓN",
            "TOTAL",
            "T. CAMBIO",
            "ESTADO",
        ]

        def imprimir_headers():
            self.set_font("Arial", "B", 9)
            self.set_fill_color(230, 235, 245)
            self.set_text_color(*AZUL_CORP)
            for i, h in enumerate(headers):
                self.cell(anchos[i], 6, h, 1, 0, "C", True)
            self.ln()

        imprimir_headers()
        self.set_font("Arial", "", 8)
        self.set_text_color(0, 0, 0)

        for em in emisiones:
            if self.get_y() > self.h - 25:
                self.add_page()
                imprimir_headers()

            self.set_x(10)

            lineas = em.get("lineas", [])
            total_factura = sum(float(ln.get("total_linea", 0)) for ln in lineas)
            moneda = em.get("moneda") or ("USD" if em.get("status") == "OK" else "")
            tc = em.get("tipo_cambio_usado")

            # 1. FACTURA
            self.set_font("Arial", "B", 8)
            self.cell(anchos[0], 6, str(em.get("factura", "-")), 1, 0, "C")

            # 2. EMPRESA
            self.set_font("Arial", "", 8)
            self.cell(anchos[1], 6, abreviar_empresa(em.get("compania")), 1, 0, "L")

            # 3. TIPO (modo de emision)
            self.cell(
                anchos[2], 6, _limpiar_latin1(etiqueta_tipo(em.get("tipo"))), 1, 0, "C"
            )

            # 4. DESCRIPCIÓN (concatenada, truncada fija)
            desc = concatenar_descripcion(lineas, em.get("descripcion_factura"))
            self.cell(anchos[3], 6, desc, 1, 0, "L")

            # 5. TOTAL (moneda dinamica)
            total_txt = f"{formato_moneda(total_factura)} {moneda}".strip()
            self.cell(anchos[4], 6, total_txt, 1, 0, "R")

            # 6. T. CAMBIO
            tc_txt = f"{float(tc):,.2f}" if tc else "-"
            self.cell(anchos[5], 6, tc_txt, 1, 0, "C")

            # 7. ESTADO
            es_ok = em.get("status") == "OK"
            self.set_text_color(*(VERDE_OK if es_ok else ROJO_ERR))
            self.set_font("Arial", "B", 8)
            txt_status = _limpiar_latin1(
                estado_corto(em.get("status"), em.get("error_msg", ""))
            )
            self.cell(anchos[6], 6, txt_status, 1, 1, "C")
            self.set_text_color(0, 0, 0)
            self.set_font("Arial", "", 8)


# =============================================================================
# ORQUESTADOR
# =============================================================================
class ReporteContratosFijosRPA:

    def __init__(self, entorno: str):
        self.entorno = entorno
        self.inicio_time = datetime.now()
        self.emisiones_procesadas = []
        self.monto_acumulado = 0.0
        self.exitosas = 0
        self.errores = 0

    def registrar_emision(
        self,
        compania: str,
        factura_num: str,
        tipo: str,
        lineas: List[Dict],
        status: str,
        error_msg: str = "",
        descripcion_factura: str = "",
        moneda: str = "",
        tipo_cambio_usado=None,
    ):
        """Agrega una emision de contrato fijo al lote del reporte.

        tipo   : modo de emision ('facturar_completo' / 'porcentaje' / 'monto_parcial').
        moneda : 'USD' / 'CRC' con que se emitio la factura.
        """
        total = (
            sum(float(ln.get("total_linea", 0) or 0.0) for ln in lineas)
            if lineas
            else 0.0
        )

        if status == "OK":
            self.exitosas += 1
            self.monto_acumulado += total
        else:
            self.errores += 1

        self.emisiones_procesadas.append(
            {
                "compania": compania,
                "factura": factura_num,
                "tipo": tipo,
                "lineas": lineas,
                "status": status,
                "error_msg": error_msg,
                "descripcion_factura": descripcion_factura,
                "moneda": moneda,
                "tipo_cambio_usado": tipo_cambio_usado,
            }
        )

    def exportar_pdf(self, directorio_salida: str = "logs") -> str:
        fin_time = datetime.now()
        duracion = fin_time - self.inicio_time
        duracion_str = f"{int(duracion.total_seconds() // 60)}m {int(duracion.total_seconds() % 60)}s"

        stats = {
            "total": len(self.emisiones_procesadas),
            "exitosas": self.exitosas,
            "errores": self.errores,
            "monto_total": self.monto_acumulado,
            "duracion": duracion_str,
        }

        qr_file = generar_qr_verificacion(
            stats,
            self.inicio_time.strftime("%d/%m/%Y"),
            self.inicio_time.strftime("%I:%M %p"),
        )

        pdf = LogContratosFijosPDF(entorno=self.entorno)
        pdf.datos_proceso = {
            "fecha": self.inicio_time.strftime("%d/%m/%Y"),
            "hora": self.inicio_time.strftime("%I:%M %p"),
        }
        if qr_file:
            pdf.set_qr_path(qr_file)

        pdf.add_page()
        pdf.agregar_resumen(stats)
        pdf.agregar_detalle(self.emisiones_procesadas)

        timestamp = self.inicio_time.strftime("%Y%m%d_%H%M%S")
        os.makedirs(directorio_salida, exist_ok=True)
        nombre_archivo = f"contratos_fijos_{self.entorno.lower()}_{timestamp}.pdf"
        ruta_completa = os.path.join(directorio_salida, nombre_archivo)

        pdf.output(ruta_completa)

        if qr_file and os.path.exists(qr_file):
            try:
                os.remove(qr_file)
            except Exception:
                pass

        return ruta_completa
