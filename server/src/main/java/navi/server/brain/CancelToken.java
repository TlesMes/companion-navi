package navi.server.brain;

import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

/**
 * 진행 중인 두뇌 호출을 멈추는 손잡이. 턴마다 하나.
 *
 * <p>끼어들기({@code turn.cancel})나 연결 끊김(D22)이 다른 스레드에서 {@link #cancel()}을 부른다.
 * 두뇌 어댑터는 {@link #onCancel}로 "스트림을 닫는 일"을 걸어 둔다 — 다음 토큰이 올 때까지
 * 기다리지 않고 그 자리에서 벤더 연결을 끊어야 쓰지 않을 출력 토큰 값을 덜 낸다.
 */
public final class CancelToken {

    private final AtomicBoolean cancelled = new AtomicBoolean();
    private final AtomicReference<Runnable> hook = new AtomicReference<>();

    public boolean isCancelled() {
        return cancelled.get();
    }

    public void cancel() {
        if (cancelled.compareAndSet(false, true)) {
            Runnable h = hook.getAndSet(null);
            if (h != null) {
                h.run();
            }
        }
    }

    /** 취소될 때 한 번 부를 일을 건다. 이미 취소됐으면 바로 부른다. */
    public void onCancel(Runnable action) {
        hook.set(action);
        if (cancelled.get()) {
            Runnable h = hook.getAndSet(null);
            if (h != null) {
                h.run();
            }
        }
    }
}
