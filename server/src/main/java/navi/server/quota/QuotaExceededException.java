package navi.server.quota;

/** 한도를 넘어 턴을 받지 않았다. 턴은 만들어지지 않았고 두뇌도 부르지 않았다. */
public class QuotaExceededException extends RuntimeException {

    public static final String TEXT_TOO_LONG = "text_too_long";
    public static final String RATE_LIMITED = "rate_limited";
    public static final String DAILY_QUOTA_EXCEEDED = "daily_quota_exceeded";

    private final String code;
    private final Integer retryAfterSeconds;

    public QuotaExceededException(String code, String message, Integer retryAfterSeconds) {
        super(message);
        this.code = code;
        this.retryAfterSeconds = retryAfterSeconds;
    }

    public String code() {
        return code;
    }

    /** 몇 초 뒤에 다시 될지. 기다려도 소용없는 거절(발화가 김)이면 null. */
    public Integer retryAfterSeconds() {
        return retryAfterSeconds;
    }
}
