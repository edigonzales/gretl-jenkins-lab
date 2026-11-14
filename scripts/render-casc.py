#!/usr/bin/env python3
"""Render laboratory roles and the GRETL agent for the selected controller."""
import argparse
from pathlib import Path

import yaml

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("platform", choices=["k3d", "standalone"])
parser.add_argument("image", nargs="?")
parser.add_argument("--label", default="gretl-j4")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
config = yaml.safe_load((root / "jenkins/casc.yaml").read_text())
jenkins = config["jenkins"]
jenkins["authorizationStrategy"] = {"projectMatrix": {"entries": [
    {"user": {"name": "${chart-admin-username}", "permissions": ["Overall/Administer"]}},
    {"group": {"name": "authenticated", "permissions": ["Overall/Read"]}},
]}}
for user in ("operator", "reviewer", "viewer", "outsider", "dual-role"):
    jenkins["securityRealm"]["local"]["users"].append(
        {"id": user, "password": "${jenkins-j2-users-" + user + "}"})

if args.platform == "standalone":
    del jenkins["clouds"]
    agent = jenkins["nodes"][0]["permanent"]
    agent.update(name="gretl-j4", labelString=args.label, remoteFS="/work/agent")
else:
    if not args.image:
        parser.error("the k3d agent image is required")
    templates = jenkins["clouds"][0]["kubernetes"]["templates"]
    # Kubernetes Remoting reconnects using the template ID, including across CasC reloads.
    for template in templates:
        template["id"] = template["name"]
    templates.append({
        "id": "gretl-j4", "name": "gretl-j4", "label": args.label, "nodeUsageMode": "EXCLUSIVE",
        "instanceCap": 2, "idleMinutes": 0, "agentContainer": "gretl", "agentInjection": True,
        "yaml": yaml.safe_dump({
            "apiVersion": "v1", "kind": "Pod",
            "spec": {
                "automountServiceAccountToken": False,
                "restartPolicy": "Never",
                "securityContext": {"runAsNonRoot": True, "runAsUser": 1000, "fsGroup": 0,
                                    "seccompProfile": {"type": "RuntimeDefault"}},
                "containers": [{
                    "name": "gretl", "image": args.image, "imagePullPolicy": "Never",
                    "workingDir": "/work/agent",
                    "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
                    "resources": {
                        "requests": {"cpu": "200m", "memory": "512Mi"},
                        "limits": {"cpu": "1", "memory": "3584Mi"},
                    },
                }],
            },
        }, sort_keys=False),
    })
print(yaml.safe_dump(config, sort_keys=False), end="")
