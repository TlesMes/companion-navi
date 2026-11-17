package navi.server.brain;

/**
 * 두뇌 호출 실패 — 벤더 예외를 이 둘로 옮긴다. 벤더 타입은 어댑터 밖으로 나가지 않는다.
 *
 * <p>메시지에 요청 본문을 넣지 않는다. 서버 로그에 대화가 남으면 안 된다(service.md §10, G8 ③).
 */
public abstract sealed class BrainException extends RuntimeException {

    protected BrainException(String message, Throwable cause) {
        super(message, cause);
    }

    /** 서버 키가 거부됐다 — 운영자가 고쳐야 하고, 다시 시도해도 같다. */
    public static final class Auth extends BrainException {
        public Auth(String message, Throwable cause) {
            super(message, cause);
        }
    }

    /** 벤더에 닿지 못했거나 벤더가 거절했다(네트워크·장애·한도) — 나중에 되는 종류다. */
    public static final class Unavailable extends BrainException {
        public Unavailable(String message, Throwable cause) {
            super(message, cause);
        }
    }
}
