import java.nio.file.Files
import java.nio.file.Path
import java.time.Instant

action('fixture') {
    Path work = Path.of(System.getenv('HOME'))
    Files.writeString(work.resolve('gretl.pid'), "${ProcessHandle.current().pid()}\n")
    Runtime.runtime.addShutdownHook(new Thread({
        Files.writeString(work.resolve('term-seen'), "GRETL JVM received TERM ${Instant.now()}")
        Thread.sleep(60_000L)
    } as Runnable))

    // Both descendants stay in the GRETL process group and inherit ignored TERM.
    def child = new ProcessBuilder('bash', '-c', '''
        set -eu
        trap '' TERM
        sleep 60 &
        printf '%s\n' "$!" > "$1/grandchild.pid"
        wait "$!"
    ''', 'timeout-fixture', work.toString()).start()
    Files.writeString(work.resolve('child.pid'), "${child.pid()}\n")

    def topology = ['gretl', 'child', 'grandchild'].collect { role ->
        Path pidFile = work.resolve("${role}.pid")
        long deadline = System.nanoTime() + 2_000_000_000L
        while ((!Files.exists(pidFile) || Files.size(pidFile) == 0) && System.nanoTime() < deadline) {
            Thread.sleep(10L)
        }
        String pid = Files.readString(pidFile).trim()
        String stat = Files.readString(Path.of("/proc/${pid}/stat"))
        def fields = stat.substring(stat.lastIndexOf(')') + 2).split(' ')
        "${role} ${pid} ${fields[1]} ${fields[2]} ${fields[19]}"
    }
    Files.writeString(work.resolve('timeout-tree'), topology.join('\n') + '\n')
    println 'ACCEPTANCE_PROCESS_STARTED'
    child.waitFor()
    println 'ACCEPTANCE_PROCESS_FINISHED'
}
defaults 'fixture'
