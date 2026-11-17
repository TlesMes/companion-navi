package navi.server.brain;

/**
 * 한 번의 두뇌 호출이 쓴 토큰 — 쿼터 차감과 원가 기록의 단위.
 *
 * <p>캐시에서 읽은 토큰과 캐시에 쓴 토큰을 따로 둔다. 벤더는 이 셋을 서로 다른 값으로 청구하고,
 * 사용자 간 캐시 적중률은 따로 봐야 하는 수치다(service.md §10, G6).
 *
 * @param inputTokens 캐시와 무관하게 정가로 청구된 입력
 * @param cacheReadTokens 캐시에서 읽은 입력
 * @param cacheWriteTokens 캐시에 새로 쓴 입력
 */
public record Usage(long inputTokens, long outputTokens, long cacheReadTokens, long cacheWriteTokens) {

    public static final Usage ZERO = new Usage(0, 0, 0, 0);

    /** 요청에 들어간 입력 전체 — 쿼터는 이 값으로 센다(캐시로 싸졌어도 쓴 양은 같다). */
    public long totalInputTokens() {
        return inputTokens + cacheReadTokens + cacheWriteTokens;
    }

    public long totalTokens() {
        return totalInputTokens() + outputTokens;
    }
}
