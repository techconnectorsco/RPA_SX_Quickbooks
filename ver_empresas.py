import requests
import json
import os
from dotenv import load_dotenv

load_dotenv()

TOKENS_FILE = 'tokens_empresas.json'
BASE_URL = 'https://quickbooks.api.intuit.com'

def ver_todas_las_empresas():
    """Muestra información de las 3 empresas"""
    
    with open(TOKENS_FILE, 'r') as f:
        data = json.load(f)
    
    print("=" * 60)
    print("📊 INFORMACIÓN DE LAS 3 EMPRESAS")
    print("=" * 60)
    
    for realm_id, empresa in data["empresas"].items():
        nombre = empresa["nombre"]
        access_token = empresa["access_token"]
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/json'
        }
        
        url = f"{BASE_URL}/v3/company/{realm_id}/companyinfo/{realm_id}"
        response = requests.get(url, headers=headers)
        
        print(f"\n🏢 {nombre}")
        print(f"   Realm ID: {realm_id}")
        
        if response.status_code == 200:
            company = response.json().get('CompanyInfo', {})
            print(f"   ✅ Conexión exitosa")
            print(f"   País: {company.get('Country', 'N/A')}")
            
            url_customers = f"{BASE_URL}/v3/company/{realm_id}/query?query=SELECT COUNT(*) FROM Customer"
            resp_c = requests.get(url_customers, headers=headers)
            if resp_c.status_code == 200:
                count = resp_c.json().get('QueryResponse', {}).get('totalCount', 0)
                print(f"   Clientes: {count}")
            
            url_invoices = f"{BASE_URL}/v3/company/{realm_id}/query?query=SELECT COUNT(*) FROM Invoice"
            resp_i = requests.get(url_invoices, headers=headers)
            if resp_i.status_code == 200:
                count = resp_i.json().get('QueryResponse', {}).get('totalCount', 0)
                print(f"   Facturas: {count}")
        else:
            print(f"   ❌ Error: {response.status_code}")
            print(f"   Ejecuta: python refrescar_tokens.py")
    
    print("\n" + "=" * 60)

if __name__ == "__main__":
    ver_todas_las_empresas()