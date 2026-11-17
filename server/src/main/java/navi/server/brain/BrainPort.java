package navi.server.brain;

import java.util.function.Consumer;
import navi.server.assembly.LlmRequest;

/**
 * 두뇌 — 벤더 중립 요청을 받아 토큰을 흘리고 결과를 돌려준다.
 *
 * <p>Python 원본({@code navi/brain/base.py})과 다른 점: <b>결과를 인스턴스에 두지 않고 돌려준다.</b>
 * 원본은 {@code last_result}가 인스턴스 상태라 동시에 한 요청만 받는다(service.md §1). 서버는
 * 한 인스턴스를 모든 사용자가 같이 쓰므로 구현은 호출 간에 상태를 나누면 안 된다.
 *
 * <p>호출은 스트림이 끝날 때까지 막힌다. 요청마다 가상 스레드 하나가 이 호출을 붙들고 있는 것이
 * 이 서버의 동시성 모델이다(service.md §11).
 */
public interface BrainPort {

    /**
     * @param onToken 텍스트 조각이 올 때마다 부른다. 호출한 스레드에서 차례대로 불린다
     * @param cancel 다른 스레드가 중단시킬 수 있다. 중단되면 예외 없이 그때까지의 결과를 돌려준다
     * @throws BrainException 벤더가 거절했거나 닿지 못했다
     */
    BrainResult stream(LlmRequest request, Consumer<String> onToken, CancelToken cancel);
}
