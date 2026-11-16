package navi.server.assembly;

import java.util.Optional;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 무드 선행 태그 분리 — 두뇌 응답 맨 앞의 {@code [mood:key]}를 떼어 낸다. 원본은
 * {@code navi/mouth/mood.py}.
 *
 * <p>서버는 태그를 떼어 구조화 필드로 보내고 본문만 흘린다(service.md §8, G2). 태그 문자열이
 * 클라이언트나 저장소로 새면 안 된다 — TTS가 읽으면 "대괄호 무드"가 소리로 나오고, 기억에
 * 남으면 다음 턴에 모델이 태그를 대사로 배운다.
 *
 * <p>스트림 앞머리만 잠깐 모은다. 여는 {@code [mood:}가 아니라고 판정되면 그때까지 모은 것을
 * 한 글자도 빼지 않고 내보낸다. 미지 키·형식 오류·태그 부재는 전부 {@code neutral}이다.
 *
 * <p>한 턴에 하나씩 만들어 쓴다(상태가 있다). 스레드 안전하지 않다.
 */
public final class MoodPeeler {

    public static final Set<String> MOODS = Set.of("neutral", "bright", "calm");
    public static final String DEFAULT_MOOD = "neutral";

    // 선두 태그. 앞뒤 공백 허용, 키는 영문. \s는 Python처럼 유니코드 공백으로 본다.
    private static final Pattern TAG =
            Pattern.compile(
                    "^\\s*\\[mood:\\s*([A-Za-z]+)\\s*\\]\\s*", Pattern.UNICODE_CHARACTER_CLASS);
    private static final String OPEN = "[mood:";
    /** 태그를 기다리며 모을 한도(글자) — 유효 태그(14자)보다 넉넉하다. */
    private static final int SCAN_LIMIT = 32;

    private final StringBuilder buffer = new StringBuilder();
    private boolean decided;
    private String mood = DEFAULT_MOOD;

    /**
     * 토큰 하나를 넣고, 지금 내보낼 본문을 받는다. 판정 전이면 빈 문자열이다.
     *
     * <p>판정이 난 뒤에는 받은 토큰을 그대로 돌려준다.
     */
    public String feed(String token) {
        if (decided) {
            return token;
        }
        buffer.append(token);
        String buf = buffer.toString();
        Matcher m = TAG.matcher(buf);
        if (m.lookingAt()) {
            mood = classify(m.group(1));
            decided = true;
            return buf.substring(m.end());
        }
        String stripped = buf.stripLeading();
        String head = stripped.substring(0, Math.min(stripped.length(), OPEN.length()));
        // 아직 태그가 될 여지가 있나: 앞머리가 "[mood:"의 접두사인 동안만 더 기다린다.
        boolean cannotBeTag = !stripped.isEmpty() && !OPEN.startsWith(head);
        if (cannotBeTag || buf.codePointCount(0, buf.length()) >= SCAN_LIMIT) {
            decided = true;
            return buf;
        }
        return "";
    }

    /** 스트림이 끝났다. 판정 전에 끝났으면 모아 둔 것을 그대로 돌려준다. */
    public String finish() {
        if (decided) {
            return "";
        }
        decided = true;
        return buffer.toString();
    }

    /** 무드가 정해졌으면 그 값. 판정 전이면 비어 있다. */
    public Optional<String> mood() {
        return decided ? Optional.of(mood) : Optional.empty();
    }

    /** 확정 전문에서 선두 태그를 뗀다 — 저장용. 태그가 없으면 그대로다. */
    public static String strip(String text) {
        Matcher m = TAG.matcher(text);
        return m.lookingAt() ? text.substring(m.end()) : text;
    }

    private static String classify(String key) {
        String lower = key.toLowerCase(java.util.Locale.ROOT);
        return MOODS.contains(lower) ? lower : DEFAULT_MOOD;
    }
}
