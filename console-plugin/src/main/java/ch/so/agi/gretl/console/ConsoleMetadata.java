package ch.so.agi.gretl.console;

import hudson.model.JobProperty;
import org.jenkinsci.plugins.workflow.job.WorkflowJob;
import org.kohsuke.stapler.DataBoundConstructor;

/** Display data published as part of the administrator-managed catalog. */
public final class ConsoleMetadata extends JobProperty<WorkflowJob> {
    private final String encoded;

    @DataBoundConstructor
    public ConsoleMetadata(String encoded) {
        this.encoded = encoded;
    }

    String encoded() {
        return encoded;
    }
}
