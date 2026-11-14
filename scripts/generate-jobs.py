#!/usr/bin/env python3
"""Validate the committed V2 catalog before emitting Jenkins Job DSL."""
import argparse
import base64
from datetime import date
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from zoneinfo import ZoneInfo

import yaml

ID = r"[a-z0-9]+(?:-[a-z0-9]+)*"
FIELD = r"[A-Za-z][A-Za-z0-9_]*"
PATH = r"[A-Za-z0-9_-][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_-][A-Za-z0-9_.-]*)*"
TYPES = {"text", "multiline", "integer", "boolean", "enum", "date", "file"}
# Jenkins exports parameter names as environment variables. Reserve an adapter
# prefix, while retaining the logical V2 names for GRETL and flow bindings.
def build_parameter(name):
    return "J2_" + name


LOCAL_GROUPS = {
    "console-viewers": ["viewer"],
    "console-operators": ["operator", "dual-role"],
    "console-reviewers": ["reviewer", "dual-role"],
}
ROOT = Path(__file__).resolve().parents[1]


class CatalogLoader(yaml.SafeLoader):
    # V2 uses YAML 1.2. Keep dates and words such as "on" as strings.
    yaml_implicit_resolvers = {
        key: [(tag, regex) for tag, regex in values
              if tag not in {"tag:yaml.org,2002:timestamp", "tag:yaml.org,2002:bool"}]
        for key, values in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }

    def construct_mapping(self, node, deep=False):
        keys = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            require(isinstance(key, str) and key not in keys, f"invalid or duplicate key {key!r}")
            keys.add(key)
        return super().construct_mapping(node, deep=deep)


CatalogLoader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$", re.I), "tTfF")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def matches(value, pattern, limit):
    return isinstance(value, str) and len(value) <= limit and bool(re.fullmatch(pattern, value))


def fields(value, required, optional, label):
    require(isinstance(value, dict), f"{label}: expected object")
    unknown = value.keys() - set((required + " " + optional).split())
    missing = set(required.split()) - value.keys()
    require(not (unknown or missing), f"{label}: unexpected {sorted(unknown)}, missing {sorted(missing)}")


def sequence(value, limit, label, minimum=0):
    require(isinstance(value, list) and minimum <= len(value) <= limit, f"{label}: invalid list length")
    return value


def string(value):
    return isinstance(value, str) and bool(value) and "\0" not in value


def flag(value, key):
    require(type(value[key]) is bool, f"{key}: expected boolean")


def check_value(p, value):
    kind = p["type"]
    valid = False
    if kind in {"text", "multiline"}:
        valid = (isinstance(value, str) and len(value.encode("utf-16-le")) <= 8192
                 and "\0" not in value and (not p["required"] or bool(value.strip())))
    elif kind == "integer":
        valid = type(value) is int and -(2**31) <= value < 2**31
    elif kind == "boolean":
        valid = type(value) is bool
    elif kind == "enum":
        valid = value in [option["value"] for option in p["options"]]
    elif kind == "date" and matches(value, r"[0-9]{4}-[0-9]{2}-[0-9]{2}", 10):
        try:
            date.fromisoformat(value)
            valid = True
        except ValueError:
            pass
    require(valid, f'{p["name"]}: invalid {kind} value')


def parameters(items, limit=32):
    result = {}
    for p in sequence(items, limit, "parameters"):
        fields(p, "name label type", "required default options accept", "parameter")
        name, kind = p["name"], p["type"]
        require(matches(name, FIELD, 48) and name not in {"constructor", "prototype", "GRETL_APPROVER", "GRETL_APPROVAL_COMMENT"}
                and name not in result, f"invalid or duplicate parameter {name!r}")
        require(string(p["label"]) and kind in TYPES, f"{name}: invalid label or type")
        require(name.upper() not in {key.upper() for key in result}, f"case-insensitive parameter collision: {name}")
        p.setdefault("required", False)
        flag(p, "required")
        require((kind == "file") == ("accept" in p) and (kind == "enum") == ("options" in p),
                f"{name}: invalid options or accept")
        if kind == "file":
            require(isinstance(p["accept"], list) and p["accept"]
                    and all(matches(ext, r"\.[A-Za-z0-9]+", 48) for ext in p["accept"]), f"{name}: invalid accept")
        if kind == "enum":
            values = set()
            require(isinstance(p["options"], list) and p["options"], f"{name}: expected options")
            for option in p["options"]:
                fields(option, "value label", "", name)
                value = option["value"]
                require(isinstance(value, str) and len(value.encode("utf-16-le")) <= 8192
                        and "\0" not in value and "\n" not in value and "\r" not in value
                        and value not in values and string(option["label"]), f"{name}: invalid or duplicate option")
                values.add(value)
        if "default" in p:
            check_value(p, p["default"])
        result[name] = p
    upload_metadata = {(name + "_FILENAME").upper() for name, p in result.items() if p["type"] == "file"}
    require(not (upload_metadata & {name.upper() for name in result}), "parameter collides with upload metadata")
    return result


def validate_definition(d, flow, files, path):
    required = "schemaVersion id name description"
    optional = "access parameters execution schedule"
    fields(d, required + (" steps" if flow else " runtime script"),
           optional + ("" if flow else " exposed resources outputs onSuccess"), "definition")
    require(type(d["schemaVersion"]) is int and d["schemaVersion"] == 2, "schemaVersion must be 2")
    require(matches(d["id"], ID, 40) and d["id"] not in {"reports", "outputs", "inputs", "source", "work"}, "invalid or reserved id")
    require(string(d["name"]) and string(d["description"]), "invalid name or description")
    d["parameters"] = parameters(d.get("parameters", []))
    access = d.setdefault("access", {})
    fields(access, "", "view run approve manage", "access")
    for action, grant in access.items():
        fields(grant, "", "users groups", f"access.{action}")
        for key, limit in (("users", 1024), ("groups", 256)):
            require(isinstance(grant.get(key, []), list) and all(
                string(item) and item == item.strip() and len(item) <= limit
                and not any(c in item for c in ",\n\r") for item in grant.get(key, [])),
                f"access.{action}.{key}: invalid identity")
    execution = d.setdefault("execution", {})
    fields(execution, "", "serial timeoutSeconds", "execution")
    execution.setdefault("serial", True)
    timeout = execution.setdefault("timeoutSeconds", 604800 if flow else 3600)
    flag(execution, "serial")
    require(type(timeout) is int and 1 <= timeout <= 604800, "invalid execution.timeoutSeconds")
    if "schedule" in d:
        schedule = d["schedule"]
        fields(schedule, "cron", "timeZone", "schedule")
        schedule.setdefault("timeZone", "Europe/Zurich")
        ZoneInfo(schedule["timeZone"])
        cron = schedule["cron"]
        require(isinstance(cron, str) and len(cron.split()) == 5
                and re.fullmatch(r"[A-Za-z0-9*/(),?\- \t]+", cron), "invalid schedule.cron")
        aliases = ("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(),
                   "SUN MON TUE WED THU FRI SAT".split())
        cron_fields = cron.upper().replace("?", "*").split()
        for index, names in enumerate(aliases, start=3):
            for number, alias in enumerate(names, start=1 if index == 3 else 0):
                cron_fields[index] = cron_fields[index].replace(alias, str(number))
        schedule["cron"] = " ".join(cron_fields)
        validate_cron(schedule["cron"])
        require(all(p["type"] != "file" and (not p["required"] or "default" in p)
                    for p in d["parameters"].values()), "schedule requires defaults and cannot upload files")
    if flow:
        return
    d.setdefault("exposed", True)
    flag(d, "exposed")
    require(d["runtime"] == "gretl" and matches(d["script"], r"[A-Za-z0-9_-]+\.groovy", 240)
            and str(PurePosixPath(path).parent / d["script"]) in files, "invalid runtime or missing script")
    if "resources" in d:
        resources = d["resources"]
        fields(resources, "cpu memory", "", "resources")
        for key, pattern, limit in (("cpu", r"[1-9][0-9]{0,3}m|[12]", 2000),
                                    ("memory", r"[1-9][0-9]{0,3}Mi|[12]Gi", 2048)):
            value = resources[key]
            require(matches(value, pattern, 6)
                    and (not value.endswith(("m", "Mi")) or int(value.rstrip("mMi")) <= limit),
                    f"invalid resources.{key}")
    if "onSuccess" in d:
        fields(d["onSuccess"], "job", "", "onSuccess")
        require(matches(d["onSuccess"]["job"], ID, 40), "invalid onSuccess.job")
    require(d["exposed"] or not ({"schedule", "onSuccess"} & d.keys()), "internal job has a schedule or successor")
    outputs = {}
    names = set(d["parameters"])
    paths = set()
    for out in sequence(d.get("outputs", []), 32, "outputs"):
        fields(out, "name label path parameter", "optional", "output")
        name, path, parameter = out["name"], out["path"], out["parameter"]
        require(matches(name, ID, 40) and matches(path, PATH, 240) and matches(parameter, FIELD, 48)
                and parameter not in {"constructor", "prototype"} and string(out["label"]), f"invalid output {name}")
        require(name not in outputs and parameter not in names and not any(
            path == old or path.startswith(old + "/") or old.startswith(path + "/") for old in paths
        ), f"conflicting output {name}")
        out.setdefault("optional", False)
        flag(out, "optional")
        outputs[name] = out
        names.add(parameter)
        paths.add(path)
    d["outputs"] = outputs


def compatible(source, target):
    return (source and source["type"] == target["type"]
            and (not target["required"] or "default" in target or source["required"] or "default" in source)
            and {x.lower() for x in source.get("accept", [])} <= {x.lower() for x in target.get("accept", [])}
            and {x["value"] for x in source.get("options", [])} <= {x["value"] for x in target.get("options", [])})


def validate_flow(flow, jobs):
    previous = {}
    for step in sequence(flow["steps"], 24, "steps", 1):
        if isinstance(step, dict) and "approval" in step:
            fields(step, "id approval", "", "step")
            approval = step["approval"]
            fields(approval, "message", "allowSelfApproval commentRequired fields", "approval")
            require(string(approval["message"]) and len(approval["message"]) <= 2000, "invalid approval message")
            for key in ("allowSelfApproval", "commentRequired"):
                approval.setdefault(key, False)
                flag(approval, key)
            approval["fields"] = parameters(approval.get("fields", []), 16)
            require(all(p["type"] != "file" for p in approval["fields"].values()), "approval cannot upload files")
            require(any(flow["access"].get("approve", {}).values()), "approval requires access.approve")
        else:
            fields(step, "id job", "inputs", "step")
            job = jobs.get(step["job"])
            require(job and "onSuccess" not in job, f"unavailable step job {step['job']}")
            bindings = step.setdefault("inputs", {})
            require(isinstance(bindings, dict) and bindings.keys() <= job["parameters"].keys(),
                    f"{step['id']}: unknown inputs")
            for name, target in job["parameters"].items():
                label = f"{step['id']}.{name}"
                if name not in bindings:
                    require(not target["required"] or "default" in target, f"missing input {label}")
                    continue
                binding = bindings[name]
                fields(binding, "", "value fromInput fromApproval fromArtifact", label)
                require(len(binding) == 1, f"{label}: expected exactly one source")
                key, ref = next(iter(binding.items()))
                if key == "value":
                    check_value(target, ref)
                elif key == "fromInput":
                    require(compatible(flow["parameters"].get(ref), target), f"{label}: incompatible input")
                elif key == "fromApproval":
                    fields(ref, "step field", "", label)
                    source = previous.get(ref["step"], {}).get("approval", {}).get("fields", {}).get(ref["field"])
                    require(compatible(source, target) and (source["required"] or "default" in source),
                            f"{label}: incompatible approval")
                else:
                    fields(ref, "step name", "", label)
                    old_job = jobs.get(previous.get(ref["step"], {}).get("job"), {})
                    output = old_job.get("outputs", {}).get(ref["name"])
                    require(target["type"] == "file" and output and (not target["required"] or not output["optional"])
                            and PurePosixPath(output["path"]).suffix.lower() in
                            {ext.lower() for ext in target["accept"]}, f"{label}: incompatible artifact")
        require(matches(step["id"], ID, 40) and step["id"] not in previous
                and step["id"] not in {"reports", "outputs", "inputs", "source", "work"}, "invalid, reserved or duplicate step id")
        previous[step["id"]] = step


def validate_cron(cron):
    """Check the Jenkins numeric grammar and ranges before loading Job DSL."""
    for field, (low, high) in zip(cron.split(), [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]):
        for item in field.split(","):
            match = re.fullmatch(r"(?:H(?:\(([0-9]+)-([0-9]+)\))?|\*|([0-9]+)(?:-([0-9]+))?)(?:/([0-9]+))?", item)
            require(match, f"invalid schedule.cron field {item}")
            start, end, first, last, step = match.groups()
            bounds = [int(number) for number in (start, end, first, last) if number is not None]
            require(all(low <= number <= high for number in bounds) and bounds == sorted(bounds)
                    and (step is None or 1 <= int(step) <= high - low + 1), f"invalid schedule.cron range {item}")


def git(repository, *args):
    return subprocess.check_output(["git", "-C", str(repository), *args], text=True).rstrip("\n")


def read_catalog(repository, revision):
    files = set(git(repository, "ls-tree", "-r", "--name-only", revision).splitlines())
    catalog, jobs = {}, {}
    for path in sorted(files):
        flow = bool(re.fullmatch(r"flows/[^/]+/flow\.yaml", path))
        if not flow and not re.fullmatch(r"jobs/[^/]+/job\.yaml", path):
            continue
        try:
            require(matches(path, PATH, 240), "unsupported catalog path")
            require(git(repository, "ls-tree", revision, path).startswith(("100644 blob ", "100755 blob ")),
                    "definition must be a regular Git file")
            d = yaml.load(git(repository, "show", f"{revision}:{path}"), Loader=CatalogLoader)
            validate_definition(d, flow, files, path)
            require(d["id"] not in catalog, f"duplicate id {d['id']}")
            d["source"] = path
            if not flow:
                require(git(repository, "ls-tree", revision, str(PurePosixPath(path).parent / d["script"]))
                        .startswith(("100644 blob ", "100755 blob ")), "script must be a regular Git file")
                jobs[d["id"]] = d
            catalog[d["id"]] = d
        except (ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
            raise ValueError(f"{path}: {exc}") from exc
    require(catalog, "no V2 definitions found")
    for d in catalog.values():
        try:
            if "steps" in d:
                validate_flow(d, jobs)
            if "onSuccess" in d:
                target = jobs.get(d["onSuccess"]["job"])
                require(target and not target["exposed"] and all(
                    not p["required"] or "default" in p for p in target["parameters"].values()
                ), "onSuccess requires an internal job with usable defaults")
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(f"{d['source']}: {exc}") from exc
    return catalog


def literal(value):
    # Single-quoted Groovy avoids GString interpolation, including in labels.
    return "'" + json.dumps(value, ensure_ascii=True)[1:-1].replace("'", "\\'").replace('\\"', '"') + "'"


def text_default(p):
    return str(p.get("default", ""))


def choices(p):
    values = [o["value"] for o in p["options"]]
    if "default" in p:
        values.remove(p["default"])
        values.insert(0, p["default"])
    return values


def render_parameter(p):
    name, label = literal(build_parameter(p["name"])), literal(p["label"])
    kind = p["type"]
    if kind == "file":
        return f"stashedFile {{ name({name}); description({label}) }}"
    if kind == "boolean":
        return f"booleanParam({name}, {str(p.get('default', False)).lower()}, {label})"
    if kind == "enum":
        return f"choiceParam({name}, [{', '.join(map(literal, choices(p)))}], {label})"
    method = "textParam" if kind == "multiline" else "stringParam"
    return f"{method}({name}, {literal(text_default(p))}, {label})"


def principals(grant):
    groups = grant.get("groups", [])
    users = set(grant.get("users", []))
    for group in groups:
        users.update(LOCAL_GROUPS.get(group, []))
    return [("USER", user) for user in sorted(users)] + [("GROUP", group) for group in groups]


def render(d, catalog, url, revision, runtime, pipeline):
    steps = d.get("steps") or [{"id": d["id"], "job": d["id"], "inputs": {
        name: {"fromInput": name} for name in d["parameters"]
    }}]
    if "onSuccess" in d:
        successor = d["onSuccess"]["job"]
        steps.append({"id": successor, "job": successor, "inputs": {}})
    grants = {}
    for action in ("view", "run", "approve", "manage"):
        for kind, principal in principals(d["access"].get(action, {})):
            rights = grants.setdefault((kind, principal), {"hudson.model.Item.Read"})
            if action == "run":
                rights.add("hudson.model.Item.Build")
            if action == "manage":
                rights.add("hudson.model.Item.Configure")
    submitters = sorted({name for _, name in principals(d["access"].get("approve", {}))})
    jobs = {s["job"]: catalog[s["job"]] for s in steps if "job" in s}
    model = dict(id=d["id"], url=url, revision=revision, runtime=runtime, jobs=jobs, steps=steps,
                 execution=d["execution"], parameters=d["parameters"], submitters=",".join(submitters))
    require(pipeline.count("'@MODEL@'") == 1, "expected one pipeline model placeholder")
    payload = json.dumps(model, ensure_ascii=True)
    script = pipeline.replace("'@MODEL@'", literal(payload), 1)
    encoded = base64.b64encode(script.encode()).decode()
    lines = [f"pipelineJob({literal('gretl-' + d['id'])}) {{", f"  displayName({literal(d['name'])})",
             f"  description({literal(d['description'])})", "  disabled(false)"]
    if d["parameters"]:
        lines += ["  parameters {", *("    " + render_parameter(p) for p in d["parameters"].values()), "  }"]
    if d["execution"]["serial"] or grants or "schedule" in d:
        lines.append("  properties {")
        if d["execution"]["serial"]:
            lines.append("    disableConcurrentBuilds()")
        if grants:
            lines += ["    authorizationMatrix {", "      entries {"]
            for (kind, name), rights in sorted(grants.items()):
                lines += [f"        {kind.lower()} {{", f"          name({literal(name)})",
                          f"          permissions([{', '.join(map(literal, sorted(rights)))}])", "        }"]
            lines += ["      }", "    }"]
        if "schedule" in d:
            schedule = d["schedule"]
            cron = "TZ=" + schedule["timeZone"] + "\n" + schedule["cron"]
            lines += ["    pipelineTriggers { triggers { cron { spec(" + literal(cron) + ") } } }"]
        lines.append("  }")
    lines += ["  definition { cps {", f"    script(new String('{encoded}'.decodeBase64(), 'UTF-8'))",
              "    sandbox()", "  } }", "}"]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--scripts", action="store_true", help="Print validated job script paths")
    args = parser.parse_args()
    revision = git(args.repository, "rev-parse", "--verify", "--end-of-options", args.ref + "^{commit}")
    url = git(args.repository, "remote", "get-url", "origin")
    require(re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", url),
            "expected a public HTTPS GitHub origin")
    catalog = read_catalog(args.repository, revision)
    if args.scripts:
        for definition in catalog.values():
            if "script" in definition:
                print(PurePosixPath(definition["source"]).parent / definition["script"])
        return
    pipeline = (ROOT / "jenkins/gretl.Jenkinsfile").read_text()
    exposed = [d for d in catalog.values() if d.get("exposed", True)]
    active = ", ".join(literal("gretl-" + d["id"]) for d in exposed)
    scripts = [render(d, catalog, url, revision, args.runtime, pipeline) for d in exposed]
    # Preserve previous run histories while withdrawing jobs removed from the catalog.
    scripts.append(f"""jenkins.model.Jenkins.get().items.findAll {{
    it.class.name == "org.jenkinsci.plugins.workflow.job.WorkflowJob" &&
        it.name.startsWith('gretl-') && !(it.name in [{active}])
}}.each {{ item ->
    item.setDisabled(true)
    item.save()
}}""")
    script = "\n\n".join(scripts)
    print(yaml.safe_dump({"jobs": [{"script": script + "\n"}]}, sort_keys=False), end="")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, subprocess.CalledProcessError, OSError, KeyError) as exc:
        sys.exit(str(exc))
