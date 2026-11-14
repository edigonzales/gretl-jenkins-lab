package ch.so.agi.gretl.console;

import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URISyntaxException;
import java.nio.charset.StandardCharsets;
import java.util.regex.Pattern;
import net.sf.json.JSONException;
import net.sf.json.JSONObject;

public final class BranchPreview {
    private static final Pattern SHA = Pattern.compile("[a-f0-9]{40}");
    private static final Pattern SOURCE = Pattern.compile("(?:jobs/[a-z0-9-]+/job|flows/[a-z0-9-]+/flow)\\.yaml");
    private final String repository;
    private final String branch;
    private final String deployed;
    private final String source;
    private final String commit;
    private final String error;

    private BranchPreview(String repository, String branch, String deployed, String source, String commit, String error) {
        this.repository = repository;
        this.branch = branch;
        this.deployed = deployed;
        this.source = source;
        this.commit = commit;
        this.error = error;
    }

    static BranchPreview resolve(String repository, String input, String deployed, String source) {
        String branch = input == null ? "" : input.strip();
        if (!validBranch(branch)) {
            return new BranchPreview(repository, "", deployed, source, null, "Ungültiger Git-Branchname.");
        }
        try {
            String commit = resolveCommit(repository, branch);
            if (commit != null) {
                return new BranchPreview(repository, branch, deployed, source, commit, null);
            }
            return new BranchPreview(repository, branch, deployed, source, null, "Branch im Job-Repository nicht gefunden.");
        } catch (IOException | URISyntaxException | JSONException e) {
            return new BranchPreview(repository, branch, deployed, source, null,
                    "GitHub konnte den Branch gerade nicht auflösen. Versuche es erneut.");
        }
    }

    static boolean validBranch(String name) {
        if (name.isEmpty() || name.length() > 255 || name.startsWith("-") || name.startsWith("refs/")
                || name.contains("..") || name.contains("@{") || name.equals("@") || name.endsWith(".")) {
            return false;
        }
        if (name.chars().anyMatch(c -> c <= 32 || c == 127 || "~^:?*[\\".indexOf(c) >= 0)) {
            return false;
        }
        for (String component : name.split("/", -1)) {
            if (component.isEmpty() || component.startsWith(".") || component.endsWith(".lock")) {
                return false;
            }
        }
        return true;
    }

    private static String resolveCommit(String repository, String branch) throws IOException, URISyntaxException {
        // The validated repository comes from the deployed catalog. Keep the host
        // fixed, encode the branch as URL path data and reject all redirects.
        URI uri = new URI("https", "api.github.com",
                "/repos" + URI.create(repository).getPath() + "/git/ref/heads/" + branch, null);
        HttpURLConnection connection = (HttpURLConnection) uri.toURL().openConnection();
        try {
            connection.setInstanceFollowRedirects(false);
            connection.setConnectTimeout(3000);
            connection.setReadTimeout(3000);
            connection.setRequestProperty("Accept", "application/vnd.github+json");
            connection.setRequestProperty("User-Agent", "GRETL-Console");
            int status = connection.getResponseCode();
            if (status == 404) {
                return null;
            }
            if (status != 200) {
                throw new IOException("GitHub API status " + status);
            }
            try (InputStream stream = connection.getInputStream()) {
                byte[] body = stream.readNBytes(8193);
                if (body.length > 8192) {
                    throw new IOException("Unexpected GitHub response size");
                }
                JSONObject ref = JSONObject.fromObject(new String(body, StandardCharsets.UTF_8));
                JSONObject object = ref.optJSONObject("object");
                String commit = object == null ? "" : object.optString("sha", "");
                if (!("refs/heads/" + branch).equals(ref.optString("ref"))
                        || !SHA.matcher(commit).matches() || !"commit".equals(object.optString("type"))) {
                    throw new IOException("Unexpected GitHub reference");
                }
                return commit;
            }
        } finally {
            connection.disconnect();
        }
    }

    public String getBranch() { return branch; }
    public String getCommit() { return commit; }
    public String getDeployed() { return deployed; }
    public String getError() { return error; }
    public boolean isCurrent() { return deployed.equals(commit); }
    public String getCommitUrl() { return commit == null ? "" : repository + "/commit/" + commit; }
    public String getSourceUrl() {
        return commit == null || !SOURCE.matcher(source).matches() ? "" : repository + "/blob/" + commit + "/" + source;
    }
}
