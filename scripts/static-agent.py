import base64
import json
import sys
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

from bench import connection, kube

if kube('config', 'current-context').strip() != 'k3d-gretl-jenkins-lab':
    raise ValueError('Unexpected laboratory context')

root = Path(__file__).resolve().parents[1]
call = connection(f'http://127.0.0.1:{int(sys.argv[1])}')
document, _ = call('/computer/gretl-static/jenkins-agent.jnlp')
secret = ET.fromstring(document).findtext('.//argument')
if not secret or len(secret) != 64:
    raise ValueError('Unexpected inbound agent secret')
resource = {
    'apiVersion': 'v1', 'kind': 'Secret',
    'metadata': {'name': 'gretl-static'},
    'data': {'secret': base64.b64encode(secret.encode()).decode()},
}
command = ['kubectl', '--kubeconfig', str(root / '.local/kubeconfig'), '--context',
           'k3d-gretl-jenkins-lab', '-n', 'gretl-jenkins-lab', 'apply', '--server-side', '-f', '-']
subprocess.run(command, input=json.dumps(resource), text=True, check=True)
