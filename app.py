from flask import Flask, redirect, request, session, url_for
from intuitlib.client import AuthClient
from intuitlib.enums import Scopes
from dotenv import load_dotenv
import requests
import json
import os

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'clave-secreta-temporal')

# Configuración QuickBooks
CLIENT_ID = os.getenv('QBO_CLIENT_ID')
CLIENT_SECRET = os.getenv('QBO_CLIENT_SECRET')
REDIRECT_URI = os.getenv('QBO_REDIRECT_URI')
ENVIRONMENT = os.getenv('QBO_ENVIRONMENT', 'production')

# Cliente OAuth
auth_client = AuthClient(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    redirect_uri=REDIRECT_URI,
    environment=ENVIRONMENT
)

# Archivo para guardar tokens de todas las empresas
TOKENS_FILE = 'tokens_empresas.json'

def cargar_tokens():
    """Carga los tokens guardados"""
    if os.path.exists(TOKENS_FILE):
        with open(TOKENS_FILE, 'r') as f:
            return json.load(f)
    return {"empresas": {}}

def guardar_tokens(tokens):
    """Guarda los tokens"""
    with open(TOKENS_FILE, 'w') as f:
        json.dump(tokens, f, indent=2)

@app.route('/')
def home():
    """Página principal - muestra estado de las 3 empresas"""
    tokens = cargar_tokens()
    empresas_conectadas = tokens.get("empresas", {})
    
    html = '<h1>QuickBooks API - Producción</h1>'
    html += '<h2>Empresas Conectadas:</h2>'
    
    if empresas_conectadas:
        html += '<ul>'
        for realm_id, data in empresas_conectadas.items():
            nombre = data.get('nombre', 'Sin nombre')
            html += f'<li><strong>{nombre}</strong> (ID: {realm_id}) '
            html += f'<a href="/empresa/{realm_id}">Ver datos</a></li>'
        html += '</ul>'
    else:
        html += '<p>No hay empresas conectadas aún.</p>'
    
    html += '<hr>'
    html += f'<p><strong>Empresas conectadas:</strong> {len(empresas_conectadas)}/3</p>'
    html += '<p><a href="/authorize">🔐 Conectar otra empresa</a></p>'
    html += '<p><a href="/ver-todas">📊 Ver resumen de todas las empresas</a></p>'
    
    return html

@app.route('/authorize')
def authorize():
    """Inicia el flujo OAuth2"""
    scopes = [Scopes.ACCOUNTING]
    auth_url = auth_client.get_authorization_url(scopes)
    return redirect(auth_url)

@app.route('/callback')
def callback():
    """Callback de OAuth2 - recibe el código de autorización"""
    auth_code = request.args.get('code')
    realm_id = request.args.get('realmId')
    
    if not auth_code:
        return "Error: No se recibió código de autorización", 400
    
    # Intercambiar código por tokens
    auth_client.get_bearer_token(auth_code, realm_id=realm_id)
    
    # Obtener nombre de la empresa
    base_url = 'https://quickbooks.api.intuit.com' if ENVIRONMENT == 'production' else 'https://sandbox-quickbooks.api.intuit.com'
    url = f"{base_url}/v3/company/{realm_id}/companyinfo/{realm_id}"
    
    headers = {
        'Authorization': f"Bearer {auth_client.access_token}",
        'Accept': 'application/json'
    }
    
    response = requests.get(url, headers=headers)
    nombre_empresa = "Empresa sin nombre"
    if response.status_code == 200:
        data = response.json()
        nombre_empresa = data.get('CompanyInfo', {}).get('CompanyName', 'Sin nombre')
    
    # Cargar tokens existentes y agregar/actualizar esta empresa
    tokens = cargar_tokens()
    tokens["empresas"][realm_id] = {
        "nombre": nombre_empresa,
        "access_token": auth_client.access_token,
        "refresh_token": auth_client.refresh_token
    }
    guardar_tokens(tokens)
    
    return f'''
    <h1>✅ Empresa conectada!</h1>
    <p><strong>{nombre_empresa}</strong></p>
    <p>Realm ID: {realm_id}</p>
    <p><a href="/">← Volver al inicio</a></p>
    '''

@app.route('/empresa/<realm_id>')
def ver_empresa(realm_id):
    """Muestra información de una empresa específica"""
    tokens = cargar_tokens()
    empresa = tokens.get("empresas", {}).get(realm_id)
    
    if not empresa:
        return "Empresa no encontrada. <a href='/'>Volver</a>", 404
    
    access_token = empresa.get('access_token')
    nombre = empresa.get('nombre')
    
    base_url = 'https://quickbooks.api.intuit.com' if ENVIRONMENT == 'production' else 'https://sandbox-quickbooks.api.intuit.com'
    
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json'
    }
    
    # Obtener info de empresa
    url_company = f"{base_url}/v3/company/{realm_id}/companyinfo/{realm_id}"
    resp_company = requests.get(url_company, headers=headers)
    
    # Obtener clientes
    url_customers = f"{base_url}/v3/company/{realm_id}/query?query=SELECT * FROM Customer MAXRESULTS 5"
    resp_customers = requests.get(url_customers, headers=headers)
    
    # Obtener facturas
    url_invoices = f"{base_url}/v3/company/{realm_id}/query?query=SELECT * FROM Invoice MAXRESULTS 5"
    resp_invoices = requests.get(url_invoices, headers=headers)
    
    html = f'<h1>{nombre}</h1>'
    html += f'<p>Realm ID: {realm_id}</p>'
    
    # Mostrar info empresa
    if resp_company.status_code == 200:
        company = resp_company.json().get('CompanyInfo', {})
        html += '<h2>Información de la Empresa</h2>'
        html += f"<p><strong>País:</strong> {company.get('Country', 'N/A')}</p>"
        html += f"<p><strong>Email:</strong> {company.get('Email', {}).get('Address', 'N/A')}</p>"
    else:
        html += f'<p>Error al obtener info: {resp_company.status_code}</p>'
    
    # Mostrar clientes
    html += '<h2>Clientes (primeros 5)</h2>'
    if resp_customers.status_code == 200:
        customers = resp_customers.json().get('QueryResponse', {}).get('Customer', [])
        if customers:
            html += '<ul>'
            for c in customers:
                email = c.get('PrimaryEmailAddr', {}).get('Address', 'Sin email') if c.get('PrimaryEmailAddr') else 'Sin email'
                html += f"<li>{c.get('DisplayName', 'Sin nombre')} - {email}</li>"
            html += '</ul>'
        else:
            html += '<p>No hay clientes.</p>'
    else:
        html += f'<p>Error al obtener clientes: {resp_customers.status_code}</p>'
    
    # Mostrar facturas
    html += '<h2>Facturas (primeras 5)</h2>'
    if resp_invoices.status_code == 200:
        invoices = resp_invoices.json().get('QueryResponse', {}).get('Invoice', [])
        if invoices:
            html += '<ul>'
            for inv in invoices:
                customer_name = inv.get('CustomerRef', {}).get('name', 'Sin cliente')
                html += f"<li>#{inv.get('DocNumber', 'N/A')} - ${inv.get('TotalAmt', 0)} - {customer_name}</li>"
            html += '</ul>'
        else:
            html += '<p>No hay facturas.</p>'
    else:
        html += f'<p>Error al obtener facturas: {resp_invoices.status_code}</p>'
    
    html += '<p><a href="/">← Volver al inicio</a></p>'
    return html

@app.route('/ver-todas')
def ver_todas():
    """Muestra resumen de todas las empresas conectadas"""
    tokens = cargar_tokens()
    empresas = tokens.get("empresas", {})
    
    if not empresas:
        return 'No hay empresas conectadas. <a href="/authorize">Conectar empresa</a>'
    
    html = '<h1>📊 Resumen de Todas las Empresas</h1>'
    
    base_url = 'https://quickbooks.api.intuit.com' if ENVIRONMENT == 'production' else 'https://sandbox-quickbooks.api.intuit.com'
    
    for realm_id, data in empresas.items():
        nombre = data.get('nombre')
        access_token = data.get('access_token')
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/json'
        }
        
        # Contar clientes
        url_customers = f"{base_url}/v3/company/{realm_id}/query?query=SELECT COUNT(*) FROM Customer"
        resp_customers = requests.get(url_customers, headers=headers)
        
        # Contar facturas
        url_invoices = f"{base_url}/v3/company/{realm_id}/query?query=SELECT COUNT(*) FROM Invoice"
        resp_invoices = requests.get(url_invoices, headers=headers)
        
        html += f'<h2>{nombre}</h2>'
        html += f'<p>Realm ID: {realm_id}</p>'
        
        if resp_customers.status_code == 200:
            count = resp_customers.json().get('QueryResponse', {}).get('totalCount', 0)
            html += f'<p>Total clientes: {count}</p>'
        else:
            html += f'<p>Error obteniendo clientes: {resp_customers.status_code}</p>'
        
        if resp_invoices.status_code == 200:
            count = resp_invoices.json().get('QueryResponse', {}).get('totalCount', 0)
            html += f'<p>Total facturas: {count}</p>'
        else:
            html += f'<p>Error obteniendo facturas: {resp_invoices.status_code}</p>'
        
        html += '<hr>'
    
    html += '<p><a href="/">← Volver al inicio</a></p>'
    return html

@app.route('/logout')
def logout():
    """Cierra la sesión"""
    session.clear()
    return redirect('/')

if __name__ == '__main__':
    print("\n" + "="*50)
    print("🚀 Servidor iniciado - PRODUCCIÓN")
    print("👉 Abre: http://localhost:5000")
    print("="*50 + "\n")
    app.run(debug=True, port=5000)