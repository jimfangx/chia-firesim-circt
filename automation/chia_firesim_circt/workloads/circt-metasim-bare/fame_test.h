#ifndef CIRCT_FAME_TEST_H
#define CIRCT_FAME_TEST_H

#include <stddef.h>
#include <stdint.h>

#define FAME_NOINLINE __attribute__((noinline))
#define FAME_ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define FAME_CHECK(label, actual, expected) \
  fame_check((label), (uint64_t)(actual), (uint64_t)(expected))
#define FAME_CSR_READ(csr) ({ uint64_t v; \
  __asm__ volatile ("csrr %0, " #csr : "=r"(v)); v; })
#define FAME_CSR_WRITE(csr, v) \
  __asm__ volatile ("csrw " #csr ", %0" :: "r"((uint64_t)(v)) : "memory")

void fame_check(const char *label, uint64_t actual, uint64_t expected);
uint64_t fame_mix(uint64_t x);
uint64_t fame_sext32(uint32_t x);
void fame_integer(void);
void fame_memory(void);
void fame_control(void);
void fame_atomics(void);
void fame_csr(void);
void fame_traps(void);
void fame_vm(void);
void fame_interrupts(void);
void fame_fp(void);
void fame_icache(void);
void fame_workloads(void);
void fame_uart(void);

/* Instructions used for synchronous trap checks must have this exact length. */
void fame_arm_trap(uint64_t cause, uint64_t tval, int check_tval, unsigned bytes);
void fame_finish_trap(void);
int fame_interrupt_dispatch(uint64_t cause);

#ifndef FAME_CLINT_BASE
#define FAME_CLINT_BASE UINT64_C(0x02000000)
#endif
#define FAME_MSIP ((volatile uint32_t *)(uintptr_t)FAME_CLINT_BASE)
#define FAME_MTIMECMP ((volatile uint64_t *)(uintptr_t)(FAME_CLINT_BASE + 0x4000))
#define FAME_MTIME ((volatile uint64_t *)(uintptr_t)(FAME_CLINT_BASE + 0xbff8))

#endif
