#include "fame_test.h"

static volatile uint32_t code[16] __attribute__((aligned(64)));

void fame_icache(void)
{
  int (*volatile generated)(void)=(int (*)(void))(uintptr_t)code;
  code[1]=UINT32_C(0x00008067); /* jalr x0, 0(ra) */
  for(unsigned i=1;i<=64;++i) {
    code[0]=(i<<20)|UINT32_C(0x00000513); /* addi a0, x0, i */
    __asm__ volatile ("fence rw, rw\n\tfence.i" ::: "memory");
    FAME_CHECK("self modifying code fence.i",generated(),i);
  }
  code[0]=UINT32_C(0x8082451d); /* c.li a0,7 ; c.jr ra */
  __asm__ volatile ("fence rw, rw\n\tfence.i" ::: "memory");
  FAME_CHECK("compressed instruction fetch",generated(),7);
  code[0]=UINT32_C(0x00000513);
  __asm__ volatile ("fence.i" ::: "memory");
  FAME_CHECK("replacement instruction fetch",generated(),0);
}
