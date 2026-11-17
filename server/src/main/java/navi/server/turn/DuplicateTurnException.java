package navi.server.turn;

import java.util.UUID;

/** 같은 {@code turn_id}가 이미 있다 — 클라이언트가 같은 턴을 두 번 보냈다. */
public class DuplicateTurnException extends RuntimeException {
    public DuplicateTurnException(UUID clientTurnId) {
        super("이미 받은 turn_id: " + clientTurnId);
    }
}
