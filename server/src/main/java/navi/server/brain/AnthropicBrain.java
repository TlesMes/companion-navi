package navi.server.brain;

import com.anthropic.client.AnthropicClient;
import com.anthropic.client.okhttp.AnthropicOkHttpClient;
import com.anthropic.core.http.StreamResponse;
import com.anthropic.errors.AnthropicException;
import com.anthropic.errors.PermissionDeniedException;
import com.anthropic.errors.UnauthorizedException;
import com.anthropic.models.messages.CacheControlEphemeral;
import com.anthropic.models.messages.MessageCreateParams;
import com.anthropic.models.messages.RawMessageStreamEvent;
import com.anthropic.models.messages.TextBlockParam;
import java.util.ArrayList;
import java.util.List;
import java.util.function.Consumer;
import navi.server.assembly.LlmRequest;
import navi.server.assembly.Message;

/**
 * Anthropic(Claude) 어댑터. 원본은 {@code navi/brain/anthropic.py}.
 *
 * <p>system을 블록 둘로 보낸다: 카드, 그리고 사실이 있으면 사실. 각각 캐시 경계를 둔다 — 사실이
 * 바뀌어도 카드 경계는 그대로 맞는다. 캐시는 모델별 최소 길이를 넘어야 걸리고 못 넘으면 조용히
 * 안 걸린다(오류 없음). 걸렸는지는 {@link Usage#cacheReadTokens()}로 본다(service.md §10, G6).
 *
 * <p>출력 상한은 고정이다(service.md §3, G7) — 클라이언트가 바꿀 수 없다.
 *
 * <p>인스턴스에 요청별 상태가 없다. 하나를 모든 사용자가 같이 쓴다.
 */
public final class AnthropicBrain implements BrainPort {

    private final AnthropicClient client;
    private final long maxTokens;

    public AnthropicBrain(AnthropicClient client, long maxTokens) {
        this.client = client;
        this.maxTokens = maxTokens;
    }

    public static AnthropicBrain create(String apiKey, String baseUrl, long maxTokens) {
        AnthropicOkHttpClient.Builder builder = AnthropicOkHttpClient.builder().apiKey(apiKey);
        if (baseUrl != null && !baseUrl.isBlank()) {
            builder.baseUrl(baseUrl);
        }
        return new AnthropicBrain(builder.build(), maxTokens);
    }

    @Override
    public BrainResult stream(LlmRequest request, Consumer<String> onToken, CancelToken cancel) {
        MessageCreateParams params = toParams(request);
        StringBuilder full = new StringBuilder();
        long[] usage = new long[4]; // input, output, cacheRead, cacheWrite

        try (StreamResponse<RawMessageStreamEvent> response =
                client.messages().createStreaming(params)) {
            // 취소되면 그 자리에서 연결을 닫는다 — 다음 토큰을 기다리지 않는다.
            cancel.onCancel(response::close);
            var events = response.stream().iterator();
            while (!cancel.isCancelled() && events.hasNext()) {
                RawMessageStreamEvent event = events.next();
                event.messageStart()
                        .ifPresent(
                                start -> {
                                    var u = start.message().usage();
                                    usage[0] = u.inputTokens();
                                    usage[1] = u.outputTokens();
                                    usage[2] = u.cacheReadInputTokens().orElse(0L);
                                    usage[3] = u.cacheCreationInputTokens().orElse(0L);
                                });
                event.messageDelta().ifPresent(delta -> usage[1] = delta.usage().outputTokens());
                event.contentBlockDelta()
                        .flatMap(delta -> delta.delta().text())
                        .ifPresent(
                                text -> {
                                    full.append(text.text());
                                    onToken.accept(text.text());
                                });
            }
        } catch (UnauthorizedException | PermissionDeniedException e) {
            throw new BrainException.Auth("Anthropic이 서버 키를 거부했습니다", e);
        } catch (AnthropicException e) {
            // 취소가 연결을 닫으면 읽던 쪽이 예외로 깨어난다 — 그건 실패가 아니다.
            if (!cancel.isCancelled()) {
                throw new BrainException.Unavailable(
                        "Anthropic 호출 실패: " + e.getClass().getSimpleName(), e);
            }
        } catch (RuntimeException e) {
            if (!cancel.isCancelled()) {
                throw e;
            }
        }
        return new BrainResult(
                full.toString(),
                new Usage(usage[0], usage[1], usage[2], usage[3]),
                cancel.isCancelled());
    }

    MessageCreateParams toParams(LlmRequest request) {
        CacheControlEphemeral cache = CacheControlEphemeral.builder().build();
        List<TextBlockParam> system = new ArrayList<>();
        system.add(TextBlockParam.builder().text(request.system()).cacheControl(cache).build());
        if (!request.context().isEmpty()) {
            system.add(TextBlockParam.builder().text(request.context()).cacheControl(cache).build());
        }
        MessageCreateParams.Builder params =
                MessageCreateParams.builder()
                        .model(request.model())
                        .maxTokens(maxTokens)
                        .systemOfTextBlockParams(system);
        for (Message m : AnthropicMessages.normalize(request.messages())) {
            if (Message.ASSISTANT.equals(m.role())) {
                params.addAssistantMessage(m.text());
            } else {
                params.addUserMessage(m.text());
            }
        }
        return params.build();
    }
}
