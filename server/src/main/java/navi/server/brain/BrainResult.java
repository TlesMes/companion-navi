package navi.server.brain;

/**
 * 스트림이 끝났을 때의 결과.
 *
 * @param fullText 두뇌가 낸 전문 — 무드 태그가 그대로 붙어 있다. 취소됐으면 그때까지 받은 만큼
 * @param cancelled 호출한 쪽이 중단시켰다. usage는 그 시점까지 벤더가 알려 준 값이다
 */
public record BrainResult(String fullText, Usage usage, boolean cancelled) {}
