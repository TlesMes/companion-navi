package navi.server.brain;

import java.util.function.Consumer;
import navi.server.assembly.LlmRequest;
import navi.server.assembly.Message;

/**
 * 키 없이 도는 두뇌 — 마지막 메시지를 되돌려 준다. 테스트와 로컬 배선 확인용.
 *
 * <p>실제 두뇌처럼 무드 태그를 앞에 붙이고 여러 조각으로 나눠 흘린다. 그래야 태그 분리와
 * 스트리밍 경로가 키 없이도 끝까지 돈다. 토큰 수는 글자 수로 흉내 낸다.
 */
public final class EchoBrain implements BrainPort {

    private static final String PREFIX = "[mood:neutral] ";
    private static final int CHUNK = 4;

    @Override
    public BrainResult stream(LlmRequest request, Consumer<String> onToken, CancelToken cancel) {
        Message last = request.messages().getLast();
        String full = PREFIX + "(echo) " + last.text();
        StringBuilder sent = new StringBuilder();
        for (int i = 0; i < full.length() && !cancel.isCancelled(); i += CHUNK) {
            String chunk = full.substring(i, Math.min(full.length(), i + CHUNK));
            sent.append(chunk);
            onToken.accept(chunk);
        }
        long input = request.system().length() + request.context().length()
                + request.messages().stream().mapToLong(m -> m.text().length()).sum();
        return new BrainResult(
                sent.toString(), new Usage(input, sent.length(), 0, 0), cancel.isCancelled());
    }
}
