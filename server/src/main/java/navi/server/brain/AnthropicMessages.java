package navi.server.brain;

import java.util.ArrayList;
import java.util.List;
import navi.server.assembly.Message;

/**
 * Anthropic API의 메시지 제약(user로 시작, user/assistant 교대)을 맞춘다. 원본은
 * {@code navi/brain/anthropic.py}의 {@code _normalize}.
 *
 * <p>저장된 대화는 이 모양이 아닐 수 있다 — 답 없이 끝난 턴 뒤의 새 발화(user 연속), 선제 발화가
 * 첫 행인 경우(assistant 시작). 벤더 제약이라 조립이 아니라 어댑터 경계에서 흡수한다.
 */
final class AnthropicMessages {

    private AnthropicMessages() {}

    static List<Message> normalize(List<Message> messages) {
        List<Message> merged = new ArrayList<>();
        for (Message m : messages) {
            if (!merged.isEmpty() && merged.getLast().role().equals(m.role())) {
                Message prev = merged.removeLast();
                merged.add(new Message(m.role(), prev.text() + "\n" + m.text()));
            } else {
                merged.add(m);
            }
        }
        if (!merged.isEmpty() && Message.ASSISTANT.equals(merged.getFirst().role())) {
            merged.addFirst(Message.user("(대화 시작)"));
        }
        return merged;
    }
}
