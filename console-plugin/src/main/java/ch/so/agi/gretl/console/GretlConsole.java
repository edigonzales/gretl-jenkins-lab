package ch.so.agi.gretl.console;

import hudson.Extension;
import hudson.model.Item;
import hudson.model.RootAction;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.Comparator;
import java.util.List;
import java.util.Optional;
import java.util.regex.Pattern;
import jenkins.model.Jenkins;
import jenkins.security.stapler.StaplerDispatchable;
import net.sf.json.JSONException;
import net.sf.json.JSONObject;
import org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition;
import org.jenkinsci.plugins.workflow.job.WorkflowJob;

@Extension
public final class GretlConsole implements RootAction {
    private static final String MARKER = "// GRETL-CONSOLE-V2 ";
    private static final Pattern ID = Pattern.compile("[a-z0-9]+(?:-[a-z0-9]+)*");
    private static final Pattern REPOSITORY =
            Pattern.compile("https://github\\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+");

    @Override
    public String getIconFileName() {
        return Jenkins.get().hasPermission(Jenkins.READ) ? "symbol-terminal" : null;
    }

    @Override
    public String getDisplayName() {
        return "GRETL Console";
    }

    @Override
    public String getUrlName() {
        return "gretl-console";
    }

    public List<ConsoleJob> getJobs() {
        Jenkins jenkins = Jenkins.get();
        jenkins.checkPermission(Jenkins.READ);
        return jenkins.getItems(WorkflowJob.class).stream()
                .filter(job -> !job.isDisabled())
                .map(GretlConsole::entry)
                .flatMap(Optional::stream)
                .sorted(Comparator.comparing(ConsoleJob::getTitle))
                .toList();
    }

    @StaplerDispatchable
    public ConsoleJob getDynamic(String id) {
        Jenkins jenkins = Jenkins.get();
        jenkins.checkPermission(Jenkins.READ);
        if (id.length() > 40 || !ID.matcher(id).matches()) {
            return null;
        }
        Item item = jenkins.getItem("gretl-" + id);
        return item instanceof WorkflowJob job ? entry(job).orElse(null) : null;
    }

    private static Optional<ConsoleJob> entry(WorkflowJob job) {
        if (!job.hasPermission(Item.READ) || !(job.getDefinition() instanceof CpsFlowDefinition definition)) {
            return Optional.empty();
        }
        String script = definition.getScript();
        int newline = script.indexOf('\n');
        if (!script.startsWith(MARKER) || newline < MARKER.length() || newline > 128 * 1024) {
            return Optional.empty();
        }
        try {
            String json = new String(Base64.getDecoder().decode(script.substring(MARKER.length(), newline)),
                    StandardCharsets.UTF_8);
            JSONObject metadata = JSONObject.fromObject(json);
            if (metadata.optInt("schemaVersion") == 2
                    && job.getName().equals("gretl-" + metadata.getString("id"))
                    && metadata.getString("revision").matches("[a-f0-9]{40}")
                    && REPOSITORY.matcher(metadata.getString("url")).matches()) {
                return Optional.of(new ConsoleJob(job, metadata));
            }
        } catch (IllegalArgumentException | JSONException ignored) {
            // Invalid display metadata does not affect the underlying Jenkins job.
        }
        return Optional.empty();
    }
}
