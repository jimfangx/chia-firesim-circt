#include "fame_test.h"
#include <stdio.h>

#ifndef FAME_TEST_MASK
#define FAME_TEST_MASK 2047
#endif

static uint64_t checks, failures, signature;

uint64_t fame_mix(uint64_t x)
{
  x ^= x >> 30;
  x *= UINT64_C(0xbf58476d1ce4e5b9);
  x ^= x >> 27;
  x *= UINT64_C(0x94d049bb133111eb);
  return x ^ (x >> 31);
}

uint64_t fame_sext32(uint32_t x)
{
  return (uint64_t)x | ((x & UINT32_C(0x80000000)) ? UINT64_C(0xffffffff00000000) : 0);
}

void fame_check(const char *label, uint64_t actual, uint64_t expected)
{
  ++checks;
  signature ^= actual + UINT64_C(0x9e3779b97f4a7c15) + checks;
  signature = ((signature << 13) | (signature >> 51)) * UINT64_C(0x100000001b3);
  if (actual != expected) {
    if (failures < 12)
      printf("FAME_FAIL: %s actual=0x%016lx expected=0x%016lx\n",
             label, (unsigned long)actual, (unsigned long)expected);
    ++failures;
  }
}

int main(void)
{
  static const struct { const char *name; void (*run)(void); } tests[] = {
#if FAME_TEST_MASK & 1
    {"integer", fame_integer},
#endif
#if FAME_TEST_MASK & 2
    {"memory", fame_memory},
#endif
#if FAME_TEST_MASK & 4
    {"control", fame_control},
#endif
#if FAME_TEST_MASK & 8
    {"atomics", fame_atomics},
#endif
#if FAME_TEST_MASK & 16
    {"csr", fame_csr},
#endif
#if FAME_TEST_MASK & 32
    {"traps", fame_traps},
#endif
#if FAME_TEST_MASK & 64
    {"vm", fame_vm},
#endif
#if FAME_TEST_MASK & 128
    {"interrupts", fame_interrupts},
#endif
#if FAME_TEST_MASK & 256
    {"fp", fame_fp},
#endif
#if FAME_TEST_MASK & 512
    {"icache", fame_icache},
#endif
#if FAME_TEST_MASK & 1024
    {"workloads", fame_workloads},
#endif
#if FAME_TEST_MASK & 2048
    {"uart", fame_uart},
#endif
  };
  uint64_t aggregate = UINT64_C(0xcbf29ce484222325), total = 0, failed = 0;
  printf("CIRCT_FAME: BEGIN version=1 mask=%u\n", (unsigned)FAME_TEST_MASK);
  for (unsigned i = 0; i < FAME_ARRAY_SIZE(tests); ++i) {
    checks = failures = 0;
    signature = UINT64_C(0xcbf29ce484222325);
    printf("FAME_TEST: %s BEGIN\n", tests[i].name);
    tests[i].run();
    printf("FAME_TEST: %s %s checks=%lu signature=0x%016lx\n", tests[i].name,
           failures ? "FAIL" : "PASS", (unsigned long)checks, (unsigned long)signature);
    aggregate = fame_mix(aggregate ^ signature ^ checks);
    total += checks;
    failed += failures;
  }
  printf("CIRCT_FAME: %s tests=%u checks=%lu failures=%lu signature=0x%016lx\n",
         failed ? "FAIL" : "PASS", (unsigned)FAME_ARRAY_SIZE(tests),
         (unsigned long)total, (unsigned long)failed, (unsigned long)aggregate);
  return failed ? 1 : 0;
}
