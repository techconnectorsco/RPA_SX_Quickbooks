"""
generarlogpdf.py - Soportexperto
Generación de Log de Control Ejecutivo para el RPA de Facturación de OPERACIONES.

Este módulo separa la lógica de reporteo visual del script principal de ejecución.
Es EXCLUSIVO de operaciones (contratos fijos tiene su propio log aparte).

Tabla (una fila por operación/factura), de izquierda a derecha:
  FACTURA | EMPRESA | DESCRIPCION | HORAS | TOTAL | T.CAMBIO | ESTADO

Decisiones de diseño acordadas:
  - Sin UUID: no le sirve a la encargada; se prioriza el numero de QuickBooks.
  - EMPRESA abreviada: SX / H&N / LAITCORP.
  - DESCRIPCION: concatena la descripcion de CADA linea separada por "; ",
    truncada a un tope fijo de caracteres (con "..." si se pasa).
  - TOTAL: con la MONEDA dinamica de la factura (USD/CRC).
  - T.CAMBIO: la tasa de venta usada si hubo inversion de moneda; si no, "-".
  - ESTADO: "OK" en verde, o un error MAPEADO corto en rojo (si el error no
    esta mapeado, sale "ERROR" a secas para saber que paso algo distinto).
"""

import os
import tempfile
from datetime import datetime
from typing import Dict, List
from fpdf import FPDF

# Intentar importar qrcode para mantener la validación empresarial
try:
    import qrcode

    QR_DISPONIBLE = True
except ImportError:
    QR_DISPONIBLE = False

# =============================================================================
# CONSTANTES Y PALETA DE COLORES DE SOPORTEXPERTO
# =============================================================================
AZUL_CORP = (11, 27, 70)  # Azul oscuro ejecutivo
AZUL_ACCENTO = (40, 120, 200)  # Azul moderno
AZUL_FOOTER = (50, 65, 95)
VERDE_OK = (40, 167, 69)
ROJO_ERR = (220, 53, 69)
GRIS_TEXTO = (90, 90, 90)
GRIS_FONDO = (248, 249, 250)

# Tope FIJO de caracteres para la celda de descripcion. Medido para 152 mm en
# Arial 8: el texto normal entra ~114; se usa 100 como corte seguro y prolijo.
MAX_CHARS_DESCRIPCION = 100

# Abreviaturas de empresa (por si la compania llega con el nombre largo).
# Se hace por "contiene" para tolerar variaciones ("S.A.", tildes, etc.).
ABREVIATURAS_EMPRESA = [
    ("hardware", "H&N"),
    ("network", "H&N"),
    ("soportexperto", "SX"),
    ("latinoamericana", "LAITCORP"),
    ("laitcorp", "LAITCORP"),
]

# Mapa de errores -> texto corto. Se busca por "contiene" (en minusculas).
# Lo que NO matchee cae en "ERROR" generico (para detectar lo no previsto).
ERRORES_MAPEADOS = [
    ("qbo_customer_id", "Sin cliente QB"),
    ("sin cliente", "Sin cliente QB"),
    ("realm", "Sin empresa QB"),
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
    """Formato estándar con comas para miles y puntos para decimales."""
    if valor is None:
        valor = 0.0
    return f"{valor:,.2f}"


def _limpiar_latin1(texto: str) -> str:
    """Deja el texto seguro para las fuentes core de fpdf (latin-1)."""
    return (texto or "").encode("latin-1", "replace").decode("latin-1")


def abreviar_empresa(nombre: str) -> str:
    """Convierte el nombre largo de la compania en su sigla corta."""
    base = (nombre or "").lower()
    for clave, sigla in ABREVIATURAS_EMPRESA:
        if clave in base:
            return sigla
    # Si no la reconocemos, devolvemos el nombre recortado para no romper el ancho.
    return (nombre or "-")[:12]


def concatenar_descripcion(lineas: List[Dict], descripcion_factura: str = "") -> str:
    """Une la descripcion de cada linea con '; ' y trunca a un tope fijo.

    Si no hay lineas (p. ej. en errores), cae a la descripcion de la factura.
    """
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
    """Devuelve 'OK' o un texto de error corto y mapeado."""
    if status == "OK":
        return "OK"
    base = (error_msg or "").lower()
    for clave, corto in ERRORES_MAPEADOS:
        if clave in base:
            return corto
    return "ERROR"


def generar_qr_verificacion(stats: Dict, fecha: str, hora: str) -> str:
    """Genera un código QR temporal con el resumen de la corrida del RPA."""
    if not QR_DISPONIBLE:
        return None

    verificacion_code = f"SE-RPA-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    contenido = [
        "==============================",
        "      SOPORTEXPERTO SX",
        "    Log de Operaciones RPA",
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
# CLASE PDF - DISEÑO Y LAYOUT VISUAL (Horizontal / Legal para máximo detalle)
# =============================================================================
class LogFacturacionPDF(FPDF):

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

        # Título Corporativo
        self.set_font("Arial", "B", 16)
        self.set_text_color(*AZUL_CORP)
        self.set_y(10)
        self.cell(0, 8, "SOPORTEXPERTO", 0, 1, "C")

        self.set_font("Arial", "B", 12)
        self.set_text_color(*AZUL_ACCENTO)
        self.cell(
            0,
            6,
            f"LOG DE CONTROL DE FACTURACIÓN RPA - ENTORNO: {self.entorno}",
            0,
            1,
            "C",
        )

        # Fechas y metadatos
        self.set_font("Arial", "", 10)
        self.set_text_color(*GRIS_TEXTO)
        fecha = self.datos_proceso.get("fecha", datetime.now().strftime("%d/%m/%Y"))
        hora = self.datos_proceso.get("hora", datetime.now().strftime("%I:%M %p"))
        self.set_xy(-110, 10)
        self.cell(60, 5, f"Fecha Ejecución: {fecha}", 0, 1, "R")
        self.set_xy(-110, 15)
        self.cell(60, 5, f"Hora Ejecución: {hora}", 0, 1, "R")

        # QR de Validación
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
        """Bloque superior tipo Dashboard Ejecutivo."""
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

        self.cell(65, 6, f"Total Solicitudes: {stats.get('total', 0)}", 0, 0)
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

    def agregar_detalle_operaciones(self, operaciones: List[Dict]):
        """Genera la tabla consolidada de una sola fila por operación/factura."""
        self.set_font("Arial", "B", 11)
        self.set_text_color(*AZUL_CORP)
        self.cell(
            0, 6, "2. DETALLE DE COMPROBACIÓN DE FACTURAS - OPERACIONES", 0, 1, "L"
        )
        self.ln(1)

        # Anchos (mm). Suman ~335 (util de Legal horizontal con margenes 10+10).
        # FACTURA  EMPRESA  CLIENTE  DESCRIPCION  HORAS  TOTAL  T.CAMBIO  ESTADO
        anchos = [24, 25, 50, 137, 20, 35, 22, 22]
        headers = [
            "FACTURA",
            "EMPRESA",
            "CLIENTE",
            "DESCRIPCIÓN",
            "HORAS",
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

        for op in operaciones:
            # Salto preventivo por página si se agota el espacio
            if self.get_y() > self.h - 25:
                self.add_page()
                imprimir_headers()

            self.set_x(10)

            # Consolidamos los datos numéricos de las líneas para la fila única
            lineas = op.get("lineas", [])
            total_horas = sum(float(ln.get("horas_trabajadas", 0)) for ln in lineas)

            # TOTAL: el monto REAL que QuickBooks registro en la factura
            # (ya convertido de moneda si hubo inversion, ya con el %/parcial
            #  aplicado y con el IVA incluido). Es la unica fuente confiable.
            # Si por algun motivo no viniera (ej. filas de error), queda en 0.
            total_qb = op.get("total_qb")
            total_factura = float(total_qb) if total_qb is not None else 0.0

            moneda = op.get("moneda") or ("USD" if op.get("status") == "OK" else "")
            tc = op.get("tipo_cambio_usado")

            # 1. FACTURA (numero de QuickBooks)
            self.set_font("Arial", "B", 8)
            self.cell(anchos[0], 6, str(op.get("factura", "-")), 1, 0, "C")

            # 2. EMPRESA (sigla facturadora)
            self.set_font("Arial", "", 8)
            self.cell(anchos[1], 6, abreviar_empresa(op.get("compania")), 1, 0, "L")

            # 3. CLIENTE (a quien se factura; truncado al ancho de la celda)
            cliente_txt = _limpiar_latin1((op.get("cliente") or "-"))[:23]
            self.cell(anchos[2], 6, cliente_txt, 1, 0, "L")

            # 4. DESCRIPCIÓN (concatenada de las lineas, truncada fija)
            desc = concatenar_descripcion(lineas, op.get("descripcion_factura"))
            self.cell(anchos[3], 6, desc, 1, 0, "L")

            # 5. HORAS (suma)
            self.cell(
                anchos[4],
                6,
                f"{total_horas:,.2f}" if total_horas > 0 else "0.00",
                1,
                0,
                "C",
            )

            # 6. TOTAL (monto real de QBO + moneda dinamica)
            total_txt = f"{formato_moneda(total_factura)} {moneda}".strip()
            self.cell(anchos[5], 6, total_txt, 1, 0, "R")

            # 7. T. CAMBIO (venta usada, o "-" si no hubo inversion)
            if tc:
                tc_txt = f"{float(tc):,.2f}"
            else:
                tc_txt = "-"
            self.cell(anchos[6], 6, tc_txt, 1, 0, "C")

            # 8. ESTADO (OK verde / error corto rojo)
            es_ok = op.get("status") == "OK"
            self.set_text_color(*(VERDE_OK if es_ok else ROJO_ERR))
            self.set_font("Arial", "B", 8)
            txt_status = _limpiar_latin1(
                estado_corto(op.get("status"), op.get("error_msg", ""))
            )
            self.cell(anchos[7], 6, txt_status, 1, 1, "C")
            self.set_text_color(0, 0, 0)
            self.set_font("Arial", "", 8)


# =============================================================================
# MANEJADOR / COLECTOR ORQUESTADOR
# =============================================================================
class ReporteFacturacionRPA:

    def __init__(self, entorno: str):
        self.entorno = entorno
        self.inicio_time = datetime.now()
        self.operaciones_procesadas = []
        self.monto_acumulado = 0.0
        self.exitosas = 0
        self.errores = 0

    def registrar_operacion(
        self,
        op_id: str,
        compania: str,
        factura_num: str,
        lineas: List[Dict],
        status: str,
        error_msg: str = "",
        descripcion_factura: str = "",
        moneda: str = "",
        tipo_cambio_usado=None,
        cliente: str = "",
        total_qb=None,
    ):
        """Agrega los datos recolectados de una operación al lote del reporte.

        cliente           : nombre del cliente al que se le factura (SAMESA, etc.).
        moneda            : 'USD' / 'CRC' con que se emitio la factura (dinamica).
        tipo_cambio_usado : tasa de venta usada si hubo inversion; None si no hubo.
        total_qb          : TotalAmt real que devolvio QuickBooks (ya convertido,
                            con %/parcial aplicado y con IVA). Fuente del total.
        """
        # Para el acumulado usamos el total REAL de QBO cuando existe.
        if total_qb is not None:
            total_op = float(total_qb)
        else:
            total_op = (
                sum(float(ln.get("total_linea", 0) or 0.0) for ln in lineas)
                if lineas
                else 0.0
            )

        if status == "OK":
            self.exitosas += 1
            self.monto_acumulado += total_op
        else:
            self.errores += 1

        self.operaciones_procesadas.append(
            {
                "id": op_id,
                "compania": compania,
                "cliente": cliente,
                "factura": factura_num,
                "lineas": lineas,
                "status": status,
                "error_msg": error_msg,
                "descripcion_factura": descripcion_factura,
                "moneda": moneda,
                "tipo_cambio_usado": tipo_cambio_usado,
                "total_qb": total_qb,
            }
        )

    def exportar_pdf(self, directorio_salida: str = "logs") -> str:
        """Compila toda la información recolectada y genera el archivo físico PDF."""
        fin_time = datetime.now()
        duracion = fin_time - self.inicio_time
        duracion_str = f"{int(duracion.total_seconds() // 60)}m {int(duracion.total_seconds() % 60)}s"

        stats = {
            "total": len(self.operaciones_procesadas),
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

        pdf = LogFacturacionPDF(entorno=self.entorno)
        pdf.datos_proceso = {
            "fecha": self.inicio_time.strftime("%d/%m/%Y"),
            "hora": self.inicio_time.strftime("%I:%M %p"),
        }

        if qr_file:
            pdf.set_qr_path(qr_file)

        pdf.add_page()
        pdf.agregar_resumen(stats)
        pdf.agregar_detalle_operaciones(self.operaciones_procesadas)

        timestamp = self.inicio_time.strftime("%Y%m%d_%H%M%S")
        os.makedirs(directorio_salida, exist_ok=True)
        nombre_archivo = f"facturacion_{self.entorno.lower()}_{timestamp}.pdf"
        ruta_completa = os.path.join(directorio_salida, nombre_archivo)

        pdf.output(ruta_completa)

        if qr_file and os.path.exists(qr_file):
            try:
                os.remove(qr_file)
            except Exception:
                pass

        return ruta_completa
