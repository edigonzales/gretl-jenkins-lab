// The generator embeds the validated catalog for this entry and its pinned revision.
def model = readJSON(text: '@MODEL@', returnPojo: true)

def scalar(p, value) {
    if (value == null || (value == '' && !(p.type in ['text', 'multiline', 'enum']))) {
        if (p.containsKey('default')) return scalar(p, p.default)
        if (!p.required) return null
        error("Missing parameter ${p.name}")
    }
    switch (p.type) {
        case 'text':
        case 'multiline':
            if (value instanceof String && value.length() <= 4096 && !value.contains('\u0000') &&
                    (!p.required || value.trim())) return value
            break
        case 'integer':
            if (value.toString() ==~ /[+-]?[0-9]{1,10}/) {
                long number = value.toString().toLong()
                if (number >= -2147483648L && number <= 2147483647L) return (int) number
            }
            break
        case 'boolean':
            if (value instanceof Boolean) return value
            break
        case 'enum':
            if (p.options.any { it.value == value }) return value
            break
        case 'date':
            if (validDate(value)) return value
    }
    error("Invalid ${p.type} parameter ${p.name}")
}

@NonCPS
boolean validDate(value) {
    if (!(value instanceof String) || !(value ==~ /[0-9]{4}-[0-9]{2}-[0-9]{2}/) ||
            value.startsWith('0000')) return false
    int year = value.substring(0, 4).toInteger()
    int month = value.substring(5, 7).toInteger()
    int day = value.substring(8, 10).toInteger()
    if (month < 1 || month > 12 || day < 1) return false
    boolean leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0)
    return day <= [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
}

def upload(p) {
    String original = env."${p.name}_FILENAME"
    if (!original) {
        if (p.required) error("Missing upload ${p.name}")
        return null
    }
    if (original in ['.', '..'] || original.length() > 255 || original ==~ /(?s).*[\x00-\x1f\x7f\/\\].*/) {
        error("Invalid filename for ${p.name}")
    }
    String extension = p.accept.find { original.toLowerCase().endsWith(it.toLowerCase()) }
    if (!extension) error("Invalid upload extension for ${p.name}")
    return extension
}

def approvalFields(fields) {
    fields.values().collect { p ->
        def args = [name: p.name, description: p.label]
        switch (p.type) {
            case 'boolean': return booleanParam(args + [defaultValue: p.default ?: false])
            case 'enum':
                def choices = p.options.collect { it.value }
                if (p.containsKey('default')) choices = [p.default] + choices.findAll { it != p.default }
                return choice(args + [choices: choices])
            case 'multiline': return text(args + [defaultValue: p.default ?: ''])
            default: return string(args + [defaultValue: p.default?.toString() ?: ''])
        }
    }
}

def approve(model, step, requester) {
    def policy = step.approval
    def fields = approvalFields(policy.fields)
    if (policy.commentRequired) fields.add(text(name: 'GRETL_APPROVAL_COMMENT', description: 'Kommentar'))
    def response = input(id: step.id, message: policy.message, submitter: model.submitters,
                         submitterParameter: 'GRETL_APPROVER', parameters: fields)
    def reviewer = fields ? response.GRETL_APPROVER : response
    if (!reviewer || (!policy.allowSelfApproval && (!requester || reviewer.equalsIgnoreCase(requester)))) {
        error('Approval requires a reviewer other than the authenticated requester')
    }
    if (policy.commentRequired && !response.GRETL_APPROVAL_COMMENT?.trim()) error('Approval comment required')
    echo "Approval by ${reviewer}"
    return policy.fields.collectEntries { name, p -> [(name): scalar(p, response[name])] }
}

def materialize(name, binding, inputs, uploads, approvals, artifacts) {
    if (binding.containsKey('value')) return binding.value
    if (binding.fromApproval) return approvals[binding.fromApproval.step][binding.fromApproval.field]
    if (binding.fromArtifact) {
        def ref = binding.fromArtifact
        String path = artifacts[ref.step][ref.name]
        if (!path) return null
        dir("inputs/${name}") { unstash("output.${ref.step}.${ref.name}") }
        return "${pwd()}/inputs/${name}/${path}"
    }
    if (binding.fromInput in uploads.keySet()) {
        String extension = uploads[binding.fromInput]
        if (!extension) return null
        dir('inputs') {
            dir(name) { unstash(binding.fromInput) }
            withEnv(["UPLOAD_SOURCE=${name}/${binding.fromInput}", "UPLOAD_TARGET=${name}${extension}"]) {
                sh 'mv -- "$UPLOAD_SOURCE" "$UPLOAD_TARGET"'
            }
        }
        return "${pwd()}/inputs/${name}${extension}"
    }
    return inputs[binding.fromInput]
}

def runJob(model, step, inputs, uploads, approvals, artifacts) {
    def job = model.jobs[step.job]
    def produced = [:]
    node('gretl-j2') {
        try {
            deleteDir()
            echo "GRETL checkout-start ${System.currentTimeMillis()}"
            dir('source') {
                checkout(scmGit(branches: [[name: model.revision]], userRemoteConfigs: [[url: model.url]]))
                if (sh(script: 'git rev-parse HEAD', returnStdout: true).trim() != model.revision) {
                    error("Checkout did not resolve ${model.revision}")
                }
            }
            echo "GRETL checkout-end ${System.currentTimeMillis()} ${model.revision}"
            def values = [:]
            job.parameters.each { name, p ->
                def binding = step.inputs[name]
                def value = binding ? materialize(name, binding, inputs, uploads, approvals, artifacts) : null
                if (p.type != 'file') value = scalar(p, value)
                if (p.required && value == null) error("Missing parameter ${name}")
                if (value != null) values[name] = value
            }
            if (writeJSON(returnText: true, json: values).getBytes('UTF-8').length > 131072) {
                error('Total parameter size exceeds 128 KiB')
            }
            def workspace = pwd()
            job.outputs.values().each { out -> values[out.parameter] = "${workspace}/outputs/${out.path}" }
            writeJSON(file: 'parameters.json', json: values.collectEntries { name, value -> [(name): value.toString()] })
            writeJSON(file: 'runtime.json', json: [schemaVersion: 1, runtime: [
                workRoot: "${workspace}/work", reportRoot: "${workspace}/reports"
            ], secrets: [:], taskConfig: [:]])
            String scriptPath = job.source.substring(0, job.source.lastIndexOf('/')) + '/' + job.script
            sh 'mkdir -p work reports outputs'
            job.outputs.values().each { out ->
                withEnv(["OUTPUT_FILE=outputs/${out.path}"]) { sh 'mkdir -p -- "$(dirname -- "$OUTPUT_FILE")"' }
            }
            try {
                container('gretl') {
                    withEnv(["GRETL_SCRIPT=source/${scriptPath}", "HOME=${workspace}/work",
                             "JAVA_TOOL_OPTIONS=-Duser.home=${workspace}/work -Djava.io.tmpdir=/tmp"]) {
                        echo "GRETL runtime-start ${System.currentTimeMillis()}"
                        timeout(time: job.execution.timeoutSeconds, unit: 'SECONDS') {
                            sh '''
                                set -eu
                                java -version
                                exec /opt/gretl/bin/gretl run "$GRETL_SCRIPT" --params-file "$PWD/parameters.json" \
                                  --config "$PWD/runtime.json" --work-dir "$PWD/work" --report "$PWD/reports/run.json"
                            '''
                        }
                    }
                }
            } finally {
                if (fileExists('reports/run.json')) {
                    dir("reports") {
                        withEnv(["GRETL_STEP=${step.id}"]) {
                            sh 'mv -- run.json "$GRETL_STEP.json"'
                        }
                    }
                    archiveArtifacts(artifacts: "reports/${step.id}.json", followSymlinks: false)
                }
                job.outputs.values().each { out ->
                    String path = "outputs/${out.path}"
                    int status
                    withEnv(["OUTPUT_FILE=${path}"]) {
                        status = sh(returnStatus: true, script: '''
                            test -f "$OUTPUT_FILE" && test "$(realpath -- "$OUTPUT_FILE")" = "$PWD/$OUTPUT_FILE"
                        ''')
                    }
                    if (status == 0) {
                        withEnv(["OUTPUT_FILE=${path}", "STAGE_ARTIFACT=${step.id}/${out.path}"]) {
                            sh 'mkdir -p -- "$(dirname -- "$STAGE_ARTIFACT")" && cp -- "$OUTPUT_FILE" "$STAGE_ARTIFACT"'
                        }
                        archiveArtifacts(artifacts: "${step.id}/${out.path}", followSymlinks: false)
                        if (model.steps.any { later ->
                            later.inputs?.values()?.any { it.fromArtifact?.step == step.id && it.fromArtifact?.name == out.name }
                        }) {
                            dir('outputs') { stash(name: "output.${step.id}.${out.name}", includes: out.path) }
                        }
                        produced[out.name] = out.path
                    }
                }
            }
            job.outputs.values().each { out ->
                if (!out.optional && !produced.containsKey(out.name)) error("Missing required output ${out.name}")
            }
        } finally {
            deleteDir()
        }
    }
    return produced
}

timestamps {
    currentBuild.description = "${model.id} @ ${model.revision.take(12)}"
    echo "GRETL ${model.id} build ${env.BUILD_NUMBER} source ${model.url}@${model.revision} runtime ${model.runtime}"
    def requester = currentBuild.getBuildCauses('hudson.model.Cause$UserIdCause').find { it.userId }?.userId
    def unexpected = params.keySet() - model.parameters.keySet()
    if (unexpected) error("Unexpected parameters: ${unexpected}")
    def inputs = [:]
    def uploads = [:]
    model.parameters.each { name, p ->
        if (p.type == 'file') uploads[name] = upload(p)
        else inputs[name] = scalar(p, params[name])
    }
    def approvals = [:]
    def artifacts = [:]
    // Jenkins exports build parameters to subprocesses by default. Keep them in JSON.
    def cleared = model.parameters.keySet().collect { "${it}=" }
    cleared.addAll(uploads.keySet().collect { "${it}_FILENAME=" })
    withEnv(cleared) {
        timeout(time: model.execution.timeoutSeconds, unit: 'SECONDS') {
            model.steps.each { step ->
                stage(step.id) {
                    if (step.approval) approvals[step.id] = approve(model, step, requester)
                    else artifacts[step.id] = runJob(model, step, inputs, uploads, approvals, artifacts)
                }
            }
        }
    }
}
