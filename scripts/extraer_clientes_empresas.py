import pandas as pd

# ============================================================
# CONFIGURACIÓN - Ajusta estas rutas según tu ambiente
# ============================================================
archivo_origen = r"D:\Users\Usuario\Desktop\QuickBooks\Facturación 2024.xlsx"
archivo_destino = r"D:\Users\Usuario\Desktop\QuickBooks\clientes_empresas_unicos.xlsx"

# ============================================================
# PROCESAMIENTO
# ============================================================
print("=" * 60)
print("📖 EXTRACCIÓN DE CLIENTES Y EMPRESAS ÚNICOS")
print("=" * 60)

# Leer el Excel
print("\n📂 Leyendo archivo...")
df = pd.read_excel(archivo_origen)
print(f"   Total filas encontradas: {len(df)}")

# Nombres de columnas (con saltos de línea como están en el Excel)
col_cliente = 'Cliente\n(OP)'
col_empresa = 'Compañía en la que se facturo'

# Verificar que existen las columnas
if col_cliente not in df.columns:
    print(f"❌ No se encontró columna: {col_cliente}")
    print("Columnas disponibles:")
    for c in df.columns:
        print(f"   - {repr(c)}")
    exit(1)

if col_empresa not in df.columns:
    print(f"❌ No se encontró columna: {col_empresa}")
    exit(1)

# Extraer solo esas dos columnas
df_filtrado = df[[col_cliente, col_empresa]].copy()

# Renombrar columnas para facilitar lectura
df_filtrado.columns = ['Cliente', 'Empresa']

# Eliminar filas donde Cliente es NaN
df_filtrado = df_filtrado[df_filtrado['Cliente'].notna()]

# Para cada cliente, obtener la empresa (si tiene en alguna fila)
def obtener_empresa(cliente):
    filas = df_filtrado[df_filtrado['Cliente'] == cliente]
    empresas = filas['Empresa'].dropna().unique()
    if len(empresas) > 0:
        return empresas[0]
    return None

# Obtener clientes únicos
clientes_unicos = df_filtrado['Cliente'].unique()

# Crear DataFrame con clientes únicos y su empresa
data = []
for cliente in clientes_unicos:
    empresa = obtener_empresa(cliente)
    data.append({'Cliente': cliente, 'Empresa': empresa})

df_unicos = pd.DataFrame(data)

# Separar en dos grupos: CON empresa y SIN empresa
con_empresa = df_unicos[df_unicos['Empresa'].notna()].copy()
sin_empresa = df_unicos[df_unicos['Empresa'].isna()].copy()

# Ordenar cada grupo alfabéticamente por cliente
con_empresa = con_empresa.sort_values(by='Cliente')
sin_empresa = sin_empresa.sort_values(by='Cliente')

# Concatenar: PRIMERO los que tienen empresa, LUEGO los que no tienen
df_final = pd.concat([con_empresa, sin_empresa], ignore_index=True)

# Estadísticas
total = len(df_final)
num_con_empresa = len(con_empresa)
num_sin_empresa = len(sin_empresa)

print(f"\n📊 ESTADÍSTICAS:")
print(f"   Total clientes únicos: {total}")
print(f"   ✅ Con empresa asignada: {num_con_empresa}")
print(f"   ⚠️  Sin empresa (vacíos): {num_sin_empresa}")

# Mostrar empresas encontradas
print(f"\n🏢 EMPRESAS ENCONTRADAS:")
empresas_unicas = con_empresa['Empresa'].unique()
for e in empresas_unicas:
    count = (df_final['Empresa'] == e).sum()
    print(f"   - {e}: {count} clientes")

# Guardar nuevo Excel
df_final.to_excel(archivo_destino, index=False)

print(f"\n✅ ARCHIVO GENERADO: {archivo_destino}")
print("=" * 60)
print("\n📝 SIGUIENTE PASO:")
print("   La operadora debe completar la columna 'Empresa'")
print("   para los clientes que aparecen al final sin empresa.")
print("=" * 60)