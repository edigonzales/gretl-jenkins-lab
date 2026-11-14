"""Obtain the dedicated inbound agent credential from the loopback controller."""
import importlib.util
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("j2_e2e", root / "scripts/j2-e2e.py")
e2e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e2e)

passwords = dict(line.split("=", 1) for line in (root / ".local/standalone/users.env").read_text().splitlines())
admin = e2e.Jenkins(int(sys.argv[1]), passwords["chart-admin-username"], passwords["chart-admin-password"])
agent = "gretl-j4"
def agent_config():
    body, _ = admin.call(f"/computer/{agent}/jenkins-agent.jnlp", expected=(200, 403, 404))
    if b"Failed to initialize Jenkins" in body or b"Jenkins failed to load" in body:
        raise RuntimeError("Standalone Jenkins failed to initialize")
    if not body.lstrip().startswith(b"<?xml") and not body.lstrip().startswith(b"<jnlp"):
        return None
    return ET.fromstring(body)

secret = e2e.poll(agent_config, seconds=240).findtext(".//argument")
if not secret or len(secret) != 64:
    raise ValueError("Unexpected Jenkins inbound agent credential")
print("JENKINS_SECRET=" + secret)
print("JENKINS_AGENT_NAME=" + agent)
print("JENKINS_WEB_SOCKET=true")
