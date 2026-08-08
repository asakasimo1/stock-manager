from dotenv import load_dotenv
load_dotenv()
import os, httpx
token = os.environ.get('GH_TOKEN')
headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json'}
r = httpx.get('https://api.github.com/repos/asakasimo1/stock-trader/actions/workflows', headers=headers)
for wf in r.json().get('workflows', []):
    print(wf['id'], wf['name'], wf['path'], wf['state'])
