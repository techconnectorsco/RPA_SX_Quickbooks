import requests
import json
import base64
import os
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("QBO_CLIENT_ID")
CLIENT_SECRET = os.getenv("QBO_CLIENT_SECRET")

TOKENS_FILE = os.path.join("config", "tokens_empresas.json")


def refrescar_todos_los_tokens():
    """Refresca los tokens de todas las empresas"""

    with open(TOKENS_FILE, "r") as f:
        data = json.load(f)

    auth_string = f"{CLIENT_ID}:{CLIENT_SECRET}"
    auth_base64 = base64.b64encode(auth_string.encode()).decode()

    print("=" * 60)
    print("🔄 REFRESCANDO TOKENS DE TODAS LAS EMPRESAS")
    print("=" * 60)

    for realm_id, empresa in data["empresas"].items():
        nombre = empresa["nombre"]
        refresh_token = empresa["refresh_token"]

        print(f"\n📍 {nombre}...")

        response = requests.post(
            "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
            headers={
                "Authorization": f"Basic {auth_base64}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        )

        if response.status_code == 200:
            new_tokens = response.json()
            data["empresas"][realm_id]["access_token"] = new_tokens["access_token"]
            data["empresas"][realm_id]["refresh_token"] = new_tokens["refresh_token"]
            print(f"   ✅ Token refrescado correctamente")
        else:
            print(f"   ❌ Error: {response.status_code}")
            print(f"   {response.text}")

    with open(TOKENS_FILE, "w") as f:
        json.dump(data, f, indent=2)

    print("\n" + "=" * 60)
    print("✅ TODOS LOS TOKENS ACTUALIZADOS")
    print("=" * 60)


if __name__ == "__main__":
    refrescar_todos_los_tokens()
