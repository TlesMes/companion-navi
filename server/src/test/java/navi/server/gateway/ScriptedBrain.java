package navi.server.gateway;

import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;
import navi.server.assembly.LlmRequest;
import navi.server.brain.BrainPort;
import navi.server.brain.BrainResult;
import navi.server.brain.CancelToken;
import navi.server.brain.Usage;

/** 대본대로 말하는 두뇌 — 멈춰 서기·실패를 테스트가 정한다. 받은 요청을 남겨 둔다. */
class ScriptedBrain implements BrainPort {

    static final Usage USAGE = new Usage(50, 10, 0, 0);

    volatile List<String> tokens;
    volatile int stallAfter;
    volatile RuntimeException failure;
    volatile LlmRequest lastRequest;
    volatile CountDownLatch stalled;
    volatile CountDownLatch cancelSeen;

    ScriptedBrain() {
        reset();
    }

    void reset() {
        tokens = List.of("[mood:bright]", " 안녕", ", 왔어?");
        stallAfter = -1;
        failure = null;
        lastRequest = null;
        stalled = new CountDownLatch(1);
        cancelSeen = new CountDownLatch(1);
    }

    boolean awaitStalled() throws InterruptedException {
        return stalled.await(5, TimeUnit.SECONDS);
    }

    boolean awaitCancelSeen() throws InterruptedException {
        return cancelSeen.await(5, TimeUnit.SECONDS);
    }

    @Override
    public BrainResult stream(LlmRequest request, Consumer<String> onToken, CancelToken cancel) {
        lastRequest = request;
        if (failure != null) {
            throw failure;
        }
        StringBuilder full = new StringBuilder();
        for (int i = 0; i < tokens.size() && !cancel.isCancelled(); i++) {
            if (i == stallAfter) {
                stalled.countDown();
                while (!cancel.isCancelled()) {
                    try {
                        Thread.sleep(10);
                    } catch (InterruptedException e) {
                        Thread.currentThread().interrupt();
                        break;
                    }
                }
                break;
            }
            full.append(tokens.get(i));
            onToken.accept(tokens.get(i));
        }
        if (cancel.isCancelled()) {
            cancelSeen.countDown();
        }
        return new BrainResult(full.toString(), USAGE, cancel.isCancelled());
    }
}
