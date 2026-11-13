package ch.so.agi.gretl.console;

import hudson.Functions;
import hudson.model.BooleanParameterDefinition;
import hudson.model.ChoiceParameterDefinition;
import hudson.model.Item;
import hudson.model.ParameterDefinition;
import hudson.model.ParametersDefinitionProperty;
import hudson.model.Result;
import hudson.model.Run;
import hudson.model.StringParameterDefinition;
import hudson.model.StringParameterValue;
import hudson.model.TextParameterDefinition;
import java.util.ArrayList;
import java.util.List;
import net.sf.json.JSONArray;
import net.sf.json.JSONObject;
import org.jenkinsci.plugins.workflow.job.WorkflowJob;
import org.jenkinsci.plugins.workflow.job.WorkflowRun;
import org.jenkinsci.plugins.workflow.support.steps.input.InputAction;

public final class ConsoleJob {
    private final WorkflowJob job;
    private final JSONObject metadata;

    ConsoleJob(WorkflowJob job, JSONObject metadata) {
        this.job = job;
        this.metadata = metadata;
    }

    public String getId() {
        return metadata.getString("id");
    }

    public String getTitle() {
        return job.getDisplayName();
    }

    public String getDescription() {
        return job.getDescription();
    }

    public String getJobUrl() {
        return job.getUrl();
    }

    public String getRevision() {
        return metadata.getString("revision");
    }

    public String getRevisionUrl() {
        return metadata.getString("url").replaceFirst("\\.git$", "") + "/commit/" + getRevision();
    }

    public String getRuntime() {
        return metadata.optString("runtime");
    }

    public boolean isFlow() {
        return metadata.optBoolean("flow");
    }

    public boolean isSerial() {
        return metadata.optBoolean("serial");
    }

    public String getSchedule() {
        JSONObject schedule = metadata.optJSONObject("schedule");
        return schedule == null ? "" : schedule.optString("cron") + " · " + schedule.optString("timeZone");
    }

    public boolean isCanBuild() {
        return job.isBuildable() && job.hasPermission(Item.BUILD);
    }

    public boolean isQueued() {
        return job.isInQueue();
    }

    public List<Field> getFields() {
        ParametersDefinitionProperty property = job.getProperty(ParametersDefinitionProperty.class);
        if (property == null) {
            return List.of();
        }
        JSONArray parameters = metadata.optJSONArray("parameters");
        List<Field> fields = new ArrayList<>();
        for (ParameterDefinition definition : property.getParameterDefinitions()) {
            JSONObject parameter = new JSONObject();
            if (parameters != null) {
                for (Object candidate : parameters) {
                    if (candidate instanceof JSONObject object
                            && definition.getName().equals("J2_" + object.optString("name"))) {
                        parameter = object;
                        break;
                    }
                }
            }
            fields.add(new Field(definition, parameter));
        }
        return List.copyOf(fields);
    }

    public List<Build> getBuilds() {
        job.checkPermission(Item.READ);
        return job.getBuilds().stream().limit(20).map(ConsoleJob::build).toList();
    }

    private static Build build(WorkflowRun run) {
        Result result = run.getResult();
        boolean running = run.isBuilding();
        boolean waiting = running && run.getActions(InputAction.class).stream()
                .anyMatch(input -> input.getIconFileName() != null);
        String status = waiting ? "Wartet auf Freigabe" : running ? "Läuft" : resultLabel(result);
        boolean artifacts = (!Functions.isArtifactsPermissionEnabled() || run.hasPermission(Run.ARTIFACTS))
                && !run.getArtifactsUpTo(1).isEmpty();
        return new Build(run.getNumber(), run.getUrl(), status, waiting,
                artifacts, run.getTimestampString2());
    }

    private static String resultLabel(Result result) {
        if (result == null) {
            return "Unbekannt";
        }
        return switch (result.toString()) {
            case "SUCCESS" -> "Erfolgreich";
            case "FAILURE" -> "Fehlgeschlagen";
            case "ABORTED" -> "Abgebrochen";
            case "UNSTABLE" -> "Instabil";
            default -> result.toString();
        };
    }

    public Build getLatest() {
        job.checkPermission(Item.READ);
        var run = job.getLastBuild();
        return run == null ? null : build(run);
    }

    public record Build(int number, String url, String status, boolean waiting, boolean artifacts, String age) {
        public int getNumber() { return number; }
        public String getUrl() { return url; }
        public String getStatus() { return status; }
        public boolean isWaiting() { return waiting; }
        public boolean isArtifacts() { return artifacts; }
        public String getAge() { return age; }
    }

    public record Choice(String value, String label, boolean selected) {
        public String getValue() { return value; }
        public String getLabel() { return label; }
        public boolean isSelected() { return selected; }
    }

    public record Field(ParameterDefinition definition, JSONObject metadata) {
        public ParameterDefinition getDefinition() {
            return definition;
        }

        public String getType() {
            String type = metadata.optString("type");
            boolean compatible = switch (type) {
                case "date", "integer", "text" -> definition.getClass() == StringParameterDefinition.class;
                case "multiline" -> definition.getClass() == TextParameterDefinition.class;
                case "boolean" -> definition.getClass() == BooleanParameterDefinition.class;
                case "enum" -> definition.getClass() == ChoiceParameterDefinition.class;
                case "file" -> definition.getClass().getName()
                        .equals("io.jenkins.plugins.file_parameters.StashedFileParameterDefinition");
                default -> false;
            };
            return compatible ? type : "";
        }

        public String getLabel() {
            return metadata.optString("label", definition.getName());
        }

        public String getDefaultValue() {
            return definition.getDefaultParameterValue() instanceof StringParameterValue value
                    ? value.getValue() : "";
        }

        public List<Choice> getChoices() {
            if (!(definition instanceof ChoiceParameterDefinition choice)) {
                return List.of();
            }
            JSONArray options = metadata.optJSONArray("options");
            return choice.getChoices().stream().map(value -> {
                String label = value;
                if (options != null) {
                    for (Object candidate : options) {
                        if (candidate instanceof JSONObject option && value.equals(option.optString("value"))) {
                            label = option.optString("label", value);
                            break;
                        }
                    }
                }
                return new Choice(value, label, value.equals(getDefaultValue()));
            }).toList();
        }

        public boolean isRequired() {
            return metadata.optBoolean("required");
        }

        public String getAccept() {
            JSONArray extensions = metadata.optJSONArray("accept");
            return extensions == null ? "" : String.join(",", extensions.stream().map(Object::toString).toList());
        }
    }
}
