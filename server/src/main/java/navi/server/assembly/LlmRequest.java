package navi.server.assembly;

import java.util.List;

/**
 * 조립이 만들고 두뇌 어댑터가 벤더 형식으로 바꾸는 요청.
 *
 * <p>순서는 덜 변하는 것이 앞이다: {@code system}(카드, 고정) → {@code context}(기억한 사실,
 * 가끔 변함) → {@code messages}(최근 대화 + 이번 트리거, 매번 변함). 앞부분이 그대로여야
 * 프롬프트 캐시가 유지된다(service.md §3, G6).
 *
 * <p>{@code context}를 system에 이어 붙이지 않고 따로 둔 이유: 어댑터가 둘 사이에 캐시 경계를
 * 놓을 수 있어야 한다. 빈 문자열이면 넣을 사실이 없다는 뜻이고 어댑터는 빈 블록을 보내지 않는다.
 */
public record LlmRequest(String system, String context, List<Message> messages, String model) {

    public LlmRequest {
        messages = List.copyOf(messages);
    }
}
