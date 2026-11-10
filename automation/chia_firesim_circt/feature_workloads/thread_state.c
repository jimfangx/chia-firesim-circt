#define _GNU_SOURCE
#include <errno.h>
#include <inttypes.h>
#include <pthread.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* Each pinned Linux task exercises the corresponding logical tile, with private
 * written memory, independently delayed execution and repeated software epochs.
 * Hardware target-reset and unwritten RAM semantics require separate RTL tests. */
enum { WORDS = 4093, EPOCHS = 5, ROUNDS = 1024, MAX_THREADS = 64 };
struct task { unsigned id; int cpu, failed; uint64_t digest; };
static uint64_t step(uint64_t state) {
    return state * UINT64_C(6364136223846793005) + UINT64_C(1442695040888963407);
}
static void *worker(void *opaque) {
    struct task *task = opaque;
    cpu_set_t affinity;
    CPU_ZERO(&affinity); CPU_SET(task->cpu, &affinity);
    if (pthread_setaffinity_np(pthread_self(), sizeof affinity, &affinity)) {
        task->failed = 1; return NULL;
    }
    uint64_t *memory = malloc(WORDS * sizeof *memory);
    if (!memory) { task->failed = 1; return NULL; }
    uint64_t result = 0;
    for (unsigned epoch = 0; epoch < EPOCHS; ++epoch) {
        uint64_t state = UINT64_C(0x123456789abcdef) ^ ((uint64_t)task->id << 32) ^ epoch;
        for (unsigned i = 0; i < WORDS; ++i) memory[i] = step(state ^ i);
        for (unsigned round = 0; round < ROUNDS; ++round) {
            unsigned index = (round * 97u + task->id * 31u) % WORDS;
            memory[index] = step(memory[index]);
            state = step(state ^ memory[index]);
            if ((round + task->id * 13u) % (17u + task->id) == 0) sched_yield();
        }
        for (unsigned i = 0; i < WORDS; ++i) result = step(result ^ memory[i]);
        result = step(result ^ state);
        if (sched_getcpu() != task->cpu) task->failed = 1;
    }
    free(memory); task->digest = result; return NULL;
}
int main(int argc, char **argv) {
    char *end;
    unsigned long count = argc == 2 ? strtoul(argv[1], &end, 10) : 0;
    if (argc != 2 || !*argv[1] || *end || !count || count > MAX_THREADS) return 2;
    cpu_set_t available;
    if (sched_getaffinity(0, sizeof available, &available)) return 2;
    struct task tasks[MAX_THREADS] = {{0}};
    pthread_t threads[MAX_THREADS];
    unsigned launched = 0;
    for (unsigned cpu = 0; cpu < CPU_SETSIZE && launched < count; ++cpu) {
        if (!CPU_ISSET(cpu, &available)) continue;
        tasks[launched].id = launched; tasks[launched].cpu = cpu;
        if (pthread_create(&threads[launched], NULL, worker, &tasks[launched])) break;
        ++launched;
    }
    int failed = launched != count;
    for (unsigned i = 0; i < launched; ++i) {
        if (pthread_join(threads[i], NULL) || tasks[i].failed) failed = 1;
        printf("CIRCT_THREAD_STATE {\"thread\":%u,\"cpu\":%d,\"epochs\":%u,\"words\":%u,\"rounds\":%u,\"digest\":\"%016" PRIx64 "\"}\n",
               i, tasks[i].cpu, EPOCHS, WORDS, ROUNDS, tasks[i].digest);
    }
    printf("CIRCT_THREAD_RESULT: %s threads=%u\n", failed ? "FAIL" : "PASS", launched);
    return failed;
}
