package benchmark;

import java.lang.instrument.Instrumentation;

public final class SizeAgent {
    private static volatile Instrumentation instrumentation;

    private SizeAgent() {
    }

    public static void premain(String arguments, Instrumentation inst) {
        instrumentation = inst;
    }

    public static Instrumentation instrumentation() {
        if (instrumentation == null) {
            throw new IllegalStateException("SizeAgent was not loaded; start Java with -javaagent:<agent.jar>");
        }
        return instrumentation;
    }
}
