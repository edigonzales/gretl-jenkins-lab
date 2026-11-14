package ch.so.agi.gretl.console;

import hudson.Extension;
import hudson.model.Job;
import hudson.model.JobProperty;
import hudson.model.JobPropertyDescriptor;
import org.jenkinsci.plugins.workflow.job.WorkflowJob;
import org.kohsuke.stapler.DataBoundConstructor;

/** Display data published as part of the administrator-managed catalog. */
public final class ConsoleMetadata extends JobProperty<WorkflowJob> {
    private final String encoded;

    @DataBoundConstructor
    public ConsoleMetadata(String encoded) {
        this.encoded = encoded;
    }

    @Extension
    public static final class DescriptorImpl extends JobPropertyDescriptor {
        @Override
        public boolean isApplicable(Class<? extends Job> jobType) {
            return WorkflowJob.class.isAssignableFrom(jobType);
        }

        @Override
        public String getDisplayName() {
            return "GRETL Console metadata";
        }
    }

    String encoded() {
        return encoded;
    }
}
