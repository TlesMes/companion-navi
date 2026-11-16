package navi.server.gateway;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import navi.server.assembly.CharacterCard;
import navi.server.assembly.LlmRequest;
import navi.server.assembly.Message;
import navi.server.assembly.MoodPeeler;
import navi.server.assembly.RequestAssembler;
import navi.server.assembly.TurnKind;
import navi.server.brain.BrainException;
import navi.server.brain.BrainPort;
import navi.server.brain.BrainProperties;
import navi.server.brain.BrainResult;
import navi.server.brain.Usage;
import navi.server.gateway.ClientConnection.ActiveTurn;
import navi.server.turn.DuplicateTurnException;
import navi.server.turn.TurnStore;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * 턴 하나를 처음부터 끝까지 돌린다: 이력 인출 → 조립 → 두뇌 스트림 → 저장.
 *
 * <p>가상 스레드 하나가 이 메서드를 붙들고 두뇌 스트림이 끝날 때까지 막힌다(service.md §11).
 *
 * <p>로그에 발화도 응답도 남기지 않는다 — 수치만 남긴다(service.md §10, G8 ③).
 */
@Component
class TurnRunner {

    private static final Logger log = LoggerFactory.getLogger(TurnRunner.class);

    private final TurnStore store;
    private final BrainPort brain;
    private final String model;
    private final int recentRows;

    TurnRunner(TurnStore store, BrainPort brain, BrainProperties brainProps, TurnProperties props) {
        this.store = store;
        this.brain = brain;
        this.model = brainProps.model();
        this.recentRows = props.recentRows();
    }

    void run(
            ClientConnection conn, ActiveTurn turn, String personaKey, CharacterCard card,
            TurnKind kind, String text) {
        long userId = conn.identity().userId();
        String id = turn.clientTurnId().toString();
        long started = System.nanoTime();

        long turnId;
        List<Message> history;
        try {
            // 이력을 먼저 읽는다 — 이번 발화는 이력이 아니라 트리거다.
            history = store.recallRecent(userId, personaKey, recentRows);
            turnId = store.begin(userId, turn.clientTurnId(), personaKey, kind, text, model);
        } catch (DuplicateTurnException e) {
            conn.end(turn);
            conn.send(Protocol.error(id, Protocol.DUPLICATE_TURN, "이미 받은 turn_id입니다"));
            return;
        } catch (RuntimeException e) {
            log.error("턴을 열지 못했습니다 user={}", userId, e);
            conn.end(turn);
            conn.send(Protocol.error(id, Protocol.INTERNAL, "서버 오류"));
            return;
        }
        conn.send(Protocol.of("turn.accepted", id));

        // 서버에 사실 추출이 아직 없다 — 사실 블록은 비어 간다(조립 코드는 받을 준비가 돼 있다).
        LlmRequest request =
                RequestAssembler.assemble(card, history, List.of(), kind, text, model);
        Stream stream = new Stream(conn, turn, id, started);

        BrainResult result;
        try {
            result = brain.stream(request, stream::onToken, turn.cancel());
            stream.finish();
        } catch (RuntimeException e) {
            int ms = elapsedMs(started);
            store.discard(turnId, Usage.ZERO, ms);
            conn.end(turn);
            if (e instanceof BrainException.Auth) {
                // 서버 키 문제다 — 운영자가 봐야 한다. 클라이언트에는 구분해 주지 않는다.
                log.error("두뇌가 서버 키를 거부했습니다", e);
            } else if (e instanceof BrainException) {
                log.warn("두뇌 호출 실패 user={} turn={}: {}", userId, turnId, e.getMessage());
            } else {
                log.error("턴 실행 중 오류 user={} turn={}", userId, turnId, e);
            }
            conn.send(Protocol.error(
                    id,
                    e instanceof BrainException ? Protocol.BRAIN_UNAVAILABLE : Protocol.INTERNAL,
                    "지금은 답할 수 없습니다"));
            return;
        }

        int totalMs = elapsedMs(started);
        Usage usage = result.usage();
        if (result.cancelled() || conn.isClosed()) {
            // 취소됐거나 그사이 연결이 끊겼다 — 이 턴은 없던 일이다(D22). 쓴 토큰은 적는다.
            store.discard(turnId, usage, totalMs);
            conn.end(turn);
            conn.send(Protocol.of("turn.cancelled", id));
            log.info("턴 폐기 user={} turn={} persona={} in={} out={} ms={}",
                    userId, turnId, personaKey, usage.totalInputTokens(), usage.outputTokens(), totalMs);
            return;
        }

        store.finishProvisional(turnId, stream.body(), stream.mood(), usage, stream.ttftMs(), totalMs);
        // 끝 메시지를 보내기 전에 자리를 비운다 — 클라이언트가 turn.end를 받자마자 다음 턴을
        // 보내도 busy로 튕기지 않게.
        conn.end(turn);
        Map<String, Object> end = Protocol.of("turn.end", id);
        end.put("last_seq", stream.lastSeq());
        end.put("chars", stream.body().codePointCount(0, stream.body().length()));
        end.put("mood", stream.mood());
        end.put("usage", Protocol.usage(usage));
        Map<String, Object> serverMs = new LinkedHashMap<>();
        serverMs.put("ttft", stream.ttftMs());
        serverMs.put("total", totalMs);
        end.put("server_ms", serverMs);
        boolean delivered = conn.send(end);
        if (!delivered || conn.isClosed()) {
            // 끝까지 생성했는데 재생 보고를 받을 연결이 없다 — 전문으로 확정한다(TurnStore 참고).
            store.confirmUnreported(userId);
        }
        log.info("턴 완료 user={} turn={} persona={} kind={} in={} out={} cache_read={} ttft={} ms={}",
                userId, turnId, personaKey, kind.wire(), usage.totalInputTokens(),
                usage.outputTokens(), usage.cacheReadTokens(), stream.ttftMs(), totalMs);
    }

    private static int elapsedMs(long startedNanos) {
        return (int) ((System.nanoTime() - startedNanos) / 1_000_000);
    }

    /**
     * 두뇌 토큰 → 클라이언트 메시지. 무드 태그를 떼어 {@code turn.mood}로 먼저 보내고 본문만
     * {@code turn.delta}로 흘린다(service.md §8, G2).
     *
     * <p>본문 앞 공백은 버린다. 태그 뒤 공백이 다음 토큰으로 오면 본문이 공백으로 시작하는데
     * (실호출에서 관측), 저장하는 본문과 보낸 본문이 같아야 재생 보고의 글자 위치가 맞는다.
     * Python 원본은 흘리는 쪽에 공백을 남긴다 — 여기서 갈린다.
     */
    private static final class Stream {
        private final ClientConnection conn;
        private final ActiveTurn turn;
        private final String id;
        private final long started;
        private final MoodPeeler peeler = new MoodPeeler();
        private final StringBuilder body = new StringBuilder();
        private boolean moodSent;
        private int nextSeq;
        private Integer ttftMs;

        Stream(ClientConnection conn, ActiveTurn turn, String id, long started) {
            this.conn = conn;
            this.turn = turn;
            this.id = id;
            this.started = started;
        }

        void onToken(String token) {
            if (ttftMs == null) {
                ttftMs = elapsedMs(started);
            }
            emit(peeler.feed(token));
        }

        void finish() {
            emit(peeler.finish());
            sendMoodOnce(); // 본문이 한 글자도 없어도 무드는 알려 준다
        }

        private void emit(String chunk) {
            if (body.isEmpty()) {
                chunk = chunk.stripLeading();
            }
            if (chunk.isEmpty() || turn.cancel().isCancelled()) {
                return;
            }
            sendMoodOnce();
            body.append(chunk);
            Map<String, Object> delta = Protocol.of("turn.delta", id);
            delta.put("seq", nextSeq++);
            delta.put("text", chunk);
            send(delta);
        }

        private void sendMoodOnce() {
            if (moodSent || peeler.mood().isEmpty() || turn.cancel().isCancelled()) {
                return;
            }
            moodSent = true;
            Map<String, Object> mood = Protocol.of("turn.mood", id);
            mood.put("mood", peeler.mood().get());
            send(mood);
        }

        private void send(Map<String, Object> message) {
            if (!conn.send(message)) {
                // 받을 사람이 없다 — 두뇌를 멈춘다. 계속 생성하면 버릴 출력에 돈을 낸다.
                turn.cancel().cancel();
            }
        }

        String body() {
            return body.toString();
        }

        String mood() {
            return peeler.mood().orElse(MoodPeeler.DEFAULT_MOOD);
        }

        int lastSeq() {
            return nextSeq - 1;
        }

        Integer ttftMs() {
            return ttftMs;
        }
    }
}
