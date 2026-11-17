package navi.server.assembly;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * 요청 조립 — 카드·기억·트리거를 한 요청으로 만든다. 원본은 {@code navi/conductor.py}.
 *
 * <p>조립이 서버에 있는 이유(service.md §2.2): 클라이언트가 조립한 프롬프트를 중계하면 서버가
 * 누구나 쓰는 무료 LLM 프록시가 된다. 클라이언트는 발화 텍스트와 턴 종류만 보낸다.
 *
 * <p>기억을 읽지 않는다 — 최근 대화와 사실은 인자로 받는다. 무엇을 얼마나 넣을지는 부르는 쪽이
 * 정하고 여기는 문자열만 만든다. 그래서 상태가 없고 사용자 간에 공유해도 된다.
 *
 * <p>아래 문구는 실측으로 여러 번 고친 자산이고 그 경위는 원본 주석에 있다. 여기서는 한 가지만
 * 되새긴다: <b>프레임과 사실 머리말은 어느 카드에도 성립해야 한다.</b> 모든 페르소나가 공유하는
 * 코드라 특정 카드의 세계관을 넣으면 다른 카드가 남의 설정을 뒤집어쓴다.
 */
public final class RequestAssembler {

    /** 재료(소재·사실)는 한국어 고정이다 — 수집이 배치라 어느 카드가 말할지 모른다. */
    private static final String MATERIAL_LANGUAGE = "ko";

    private static final String LANGUAGE_RULE = "소재는 한국어로 적혀 있지만 답은 {language}로 해.";

    /** 프롬프트에 쓸 언어 이름 — 코드(ko)를 그대로 넣으면 모델이 덜 확실하게 받는다. */
    private static final Map<String, String> LANGUAGE_NAMES =
            Map.of("ko", "한국어", "ja", "일본어", "zh", "중국어", "en", "영어");

    private static final String PROACTIVE_FRAME =
            "(사용자는 지금 아무 말도 안 했어. 아래 소재로 네가 먼저 말을 꺼내줘 — "
                    + "사용자가 알려준 게 아니야. 어떻게 알게 됐는지는 굳이 말하지 말고, "
                    + "어디서 봤다거나 들었다고 출처를 지어내지도 마. "
                    + "소재를 다 옮기려 하지 말고 네가 가장 흥미로운 것 하나만 골라서, "
                    + "거기 나온 작품·제품 이름은 그대로 말해. 질문으로 끝내지 마. "
                    + "{language_rule}: {trigger})";

    private static final String CALLBACK_FRAME =
            "(사용자는 지금 아무 말도 안 했어. 아래는 사용자가 전에 한 말에서 나온 거야 — "
                    + "그 뒤가 어떻게 됐는지 궁금해서 네가 먼저 물어보는 거고, "
                    + "새로 알게 된 소식이 아니야. "
                    + "짧게 한두 문장으로. "
                    + "{language_rule}: {trigger})";

    private static final String FACTS_HEADER =
            "[기억]\n"
                    + "아래는 지난 대화들에서 네가 알게 된 것들이야. 지금 보이는 대화 기록에는 없어도 "
                    + "실제로 있었던 일이야. '사용자'는 지금 너와 말하는 상대야. "
                    + "이미 알고 있는 걸 전제로 말하되, 지금 이야기와 상관없으면 꺼내지 말고 "
                    + "목록처럼 늘어놓지도 마.";

    private static final String FACTS_LANGUAGE_RULE = " 아래는 한국어로 적혀 있지만 답은 {language}로 해.";

    // Python의 \s는 유니코드 공백이다. 같은 범위를 잡으려고 UNICODE_CHARACTER_CLASS를 켠다.
    private static final Pattern WHITESPACE_RUN =
            Pattern.compile("\\s+", Pattern.UNICODE_CHARACTER_CLASS);

    private RequestAssembler() {}

    /**
     * @param history 최근 대화, 시간순. 그대로 messages 앞에 놓인다
     * @param facts 넣을 사실 문장들. 순서는 호출마다 같아야 한다(프롬프트 캐시)
     * @param trigger REACTIVE면 사용자 발화, 선제 종류면 소재
     */
    public static LlmRequest assemble(
            CharacterCard card, List<Message> history, List<String> facts, TurnKind kind,
            String trigger, String model) {
        List<Message> messages = new ArrayList<>(history);
        messages.add(frame(kind, trigger, card.language()));
        return new LlmRequest(
                card.systemPrompt(), factsBlock(facts, card.language()), messages, model);
    }

    /** 사실 목록 → 프롬프트 블록. 없으면 빈 문자열(머리말만 보내지 않는다). */
    static String factsBlock(List<String> facts, String language) {
        if (facts.isEmpty()) {
            return "";
        }
        StringBuilder block = new StringBuilder(FACTS_HEADER);
        if (!MATERIAL_LANGUAGE.equals(language)) {
            block.append(FACTS_LANGUAGE_RULE.replace("{language}", languageName(language)));
        }
        for (String fact : facts) {
            block.append("\n- ").append(fact);
        }
        return block.toString();
    }

    /** 트리거를 종류에 맞는 마지막 메시지로 만든다. */
    static Message frame(TurnKind kind, String trigger, String language) {
        String frame =
                switch (kind) {
                    case REACTIVE -> null; // 진짜 사용자 발화라 감싸지 않는다
                    case PROACTIVE -> PROACTIVE_FRAME;
                    case PROACTIVE_CALLBACK -> CALLBACK_FRAME;
                };
        if (frame == null) {
            return Message.user(trigger);
        }
        // 카드 언어가 재료 언어와 같으면 지시가 자기를 부정한다("한국어로 적혀 있지만 답은
        // 한국어로") — 아예 뺀다.
        String rule =
                MATERIAL_LANGUAGE.equals(language)
                        ? ""
                        : LANGUAGE_RULE.replace("{language}", languageName(language));
        // 트리거를 마지막에 넣는다 — 트리거 안의 "{language_rule}" 같은 글자가 다시 치환되면
        // 안 된다(원본의 str.format은 한 번에 치환해 같은 성질을 갖는다).
        String text = frame.replace("{language_rule}", rule).replace("{trigger}", trigger);
        // 공백 정리와 ".:" 제거도 원본대로 **트리거를 넣은 뒤 전체에** 건다.
        return Message.user(WHITESPACE_RUN.matcher(text).replaceAll(" ").replace(".:", ":"));
    }

    private static String languageName(String language) {
        // 이름이 없으면 코드를 그대로 쓴다 — 프레임 한 줄 때문에 턴이 죽는 게 더 나쁘다.
        return LANGUAGE_NAMES.getOrDefault(language, language);
    }
}
