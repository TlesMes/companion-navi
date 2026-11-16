package navi.server.assembly;

/** LLM 요청 안의 대화 한 줄 (벤더 중립). role은 {@code user} 또는 {@code assistant}. */
public record Message(String role, String text) {

    public static final String USER = "user";
    public static final String ASSISTANT = "assistant";

    public static Message user(String text) {
        return new Message(USER, text);
    }

    public static Message assistant(String text) {
        return new Message(ASSISTANT, text);
    }
}
