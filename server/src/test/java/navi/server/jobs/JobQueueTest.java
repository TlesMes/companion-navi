package navi.server.jobs;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Optional;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import navi.server.PostgresTestConfig;
import navi.server.identity.DeviceService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Import;
import org.springframework.jdbc.core.simple.JdbcClient;

/** 작업 큐(D25) — 한 작업은 한 워커에게, 죽은 워커의 작업은 다시, 늦은 보고는 거절. */
@SpringBootTest(properties = {"navi.worker.max-attempts=2", "navi.worker.retry-backoff-seconds=0"})
@Import(PostgresTestConfig.class)
class JobQueueTest {

    private static final List<String> KINDS = List.of("extract_facts");

    @Autowired JobQueue queue;
    @Autowired DeviceService devices;
    @Autowired JdbcClient jdbc;

    @BeforeEach
    void clean() {
        jdbc.sql("DELETE FROM job").update();
    }

    private long user() {
        return devices.register().identity().userId();
    }

    private void expireLeases() {
        jdbc.sql("UPDATE job SET lease_until = now() - interval '1 second' WHERE status = 'claimed'")
                .update();
    }

    private String status(long jobId) {
        return jdbc.sql("SELECT status FROM job WHERE job_id = ?").param(jobId)
                .query(String.class).single();
    }

    @Test
    void onePendingJobPerUserAndKind() {
        long a = user();
        long b = user();

        assertThat(queue.enqueue("extract_facts", a)).isTrue();
        assertThat(queue.enqueue("extract_facts", a)).isFalse();
        assertThat(queue.enqueue("extract_facts", b)).isTrue();
        // 가져가 있는 동안에도 같은 사용자의 작업은 하나다 — 같은 대화를 두 워커가 동시에 읽지 않게.
        queue.claim(KINDS);
        queue.claim(KINDS);
        assertThat(queue.enqueue("extract_facts", a)).isFalse();
    }

    @Test
    void claimedJobIsNotHandedOutAgainUntilTheLeaseEnds() {
        long u = user();
        queue.enqueue("extract_facts", u);

        JobQueue.Claimed first = queue.claim(KINDS).orElseThrow();
        assertThat(first.userId()).isEqualTo(u);
        assertThat(first.attempt()).isEqualTo(1);
        assertThat(queue.claim(KINDS)).isEmpty();

        expireLeases(); // 첫 워커가 죽었다
        JobQueue.Claimed second = queue.claim(KINDS).orElseThrow();

        assertThat(second.jobId()).isEqualTo(first.jobId());
        assertThat(second.attempt()).isEqualTo(2);
        // 늦게 깨어난 첫 워커의 보고는 받지 않는다(펜싱).
        assertThat(queue.lockForCompletion(first.jobId(), first.attempt())).isEmpty();
        assertThat(queue.fail(first.jobId(), first.attempt(), "늦음")).isFalse();
        assertThat(queue.lockForCompletion(second.jobId(), second.attempt())).isPresent();
    }

    @Test
    void concurrentWorkersNeverGetTheSameJob() throws Exception {
        int jobs = 5;
        for (int i = 0; i < jobs; i++) {
            queue.enqueue("extract_facts", user());
        }
        int workers = 12;
        CountDownLatch go = new CountDownLatch(1);
        List<Future<Optional<JobQueue.Claimed>>> claims = new ArrayList<>();
        try (ExecutorService pool = Executors.newFixedThreadPool(workers)) {
            for (int i = 0; i < workers; i++) {
                claims.add(pool.submit(() -> {
                    go.await();
                    return queue.claim(KINDS);
                }));
            }
            go.countDown();
            Set<Long> got = new HashSet<>();
            int empty = 0;
            for (Future<Optional<JobQueue.Claimed>> c : claims) {
                Optional<JobQueue.Claimed> job = c.get();
                if (job.isPresent()) {
                    assertThat(got.add(job.get().jobId())).as("같은 작업을 두 번 내줬다").isTrue();
                } else {
                    empty++;
                }
            }
            assertThat(got).hasSize(jobs);
            assertThat(empty).isEqualTo(workers - jobs);
        }
    }

    @Test
    void failedJobIsRetriedUntilAttemptsRunOut() {
        long u = user();
        queue.enqueue("extract_facts", u);

        JobQueue.Claimed first = queue.claim(KINDS).orElseThrow();
        assertThat(queue.fail(first.jobId(), 1, "벤더 장애")).isTrue();
        assertThat(status(first.jobId())).isEqualTo("queued");

        JobQueue.Claimed second = queue.claim(KINDS).orElseThrow();
        assertThat(queue.fail(second.jobId(), 2, "또 장애")).isTrue();

        assertThat(status(first.jobId())).isEqualTo("failed");
        assertThat(queue.claim(KINDS)).isEmpty();
        // 버린 작업은 대기 자리를 차지하지 않는다 — 다음 대화가 쌓이면 새 작업이 들어간다.
        assertThat(queue.enqueue("extract_facts", u)).isTrue();
    }

    @Test
    void jobWhoseLastAttemptDiedIsClearedOnNextEnqueue() {
        long u = user();
        queue.enqueue("extract_facts", u);
        queue.claim(KINDS);
        expireLeases();
        queue.claim(KINDS); // 둘째(마지막) 시도
        expireLeases(); // 그 워커도 죽었다 — 더 시도할 수 없다

        assertThat(queue.claim(KINDS)).isEmpty();
        assertThat(queue.enqueue("extract_facts", u)).isTrue();
    }

    @Test
    void unknownKindsAreNotHandedOut() {
        queue.enqueue("extract_facts", user());

        assertThat(queue.claim(List.of("summarize_feed"))).isEmpty();
    }
}
