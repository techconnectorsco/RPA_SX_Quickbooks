# diag_token.py  — solo lectura, una sola empresa
import os, json, base64, requests
from dotenv import load_dotenv

load_dotenv()

REALM = "9130355360397996"  # Soportexperto
CID, CSEC = os.getenv("QBO_CLIENT_ID"), os.getenv("QBO_CLIENT_SECRET")
print(
    f"client_id en uso: ...{CID[-8:]}  (comparalo con las llaves PRODUCTION del portal)"
)

with open(os.path.join("config", "tokens_empresas.json"), encoding="utf-8") as f:
    data = json.load(f)
emp = data["empresas"][REALM]

auth = base64.b64encode(f"{CID}:{CSEC}".encode()).decode()
r = requests.post(
    "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
    headers={
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    },
    data={"grant_type": "refresh_token", "refresh_token": emp["refresh_token"]},
    timeout=30,
)
print("refresh:", r.status_code, r.text[:150] if r.status_code != 200 else "OK")
if r.status_code != 200:
    raise SystemExit
tok = r.json()["access_token"]
emp["access_token"] = tok
emp["refresh_token"] = r.json()["refresh_token"]
with open(os.path.join("config", "tokens_empresas.json"), "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)

h = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
for nombre, url in [
    (
        "CompanyInfo",
        f"https://quickbooks.api.intuit.com/v3/company/{REALM}/companyinfo/{REALM}?minorversion=75",
    ),
    (
        "Preferences",
        f"https://quickbooks.api.intuit.com/v3/company/{REALM}/preferences?minorversion=75",
    ),
]:
    resp = requests.get(url, headers=h, timeout=30)
    print(
        f"{nombre}: {resp.status_code} {'' if resp.status_code == 200 else resp.text[:150]}"
    )
