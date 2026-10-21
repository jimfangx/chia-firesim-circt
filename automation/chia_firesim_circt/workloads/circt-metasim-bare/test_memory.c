#include "fame_test.h"
#include <string.h>

#define WORDS 16384u
static volatile uint64_t arena[WORDS] __attribute__((aligned(4096)));
static volatile uint64_t lane __attribute__((aligned(64)));
static uint8_t copied[1024], source[1024];

#define LOAD(name, insn) \
  static uint64_t name(const volatile void *p) { uint64_t v; \
    __asm__ volatile (insn " %0, 0(%1)" : "=r"(v) : "r"(p) : "memory"); return v; }
LOAD(load_b,"lb") LOAD(load_bu,"lbu") LOAD(load_h,"lh") LOAD(load_hu,"lhu")
LOAD(load_w,"lw") LOAD(load_wu,"lwu") LOAD(load_d,"ld")
#define STORE(name, insn) \
  static void name(volatile void *p, uint64_t v) { \
    __asm__ volatile (insn " %0, 0(%1)" :: "r"(v), "r"(p) : "memory"); }
STORE(store_b,"sb") STORE(store_h,"sh") STORE(store_w,"sw")

void fame_memory(void)
{
  /* Walking address/data patterns, beyond the Rocket L1 working set. */
  for (unsigned pass=0;pass<3;++pass) {
    for (unsigned i=0;i<WORDS;++i) {
      unsigned p=(i*4051u)&(WORDS-1);
      arena[p]=fame_mix(p+UINT64_C(0x1020304050607080)+pass);
    }
    __asm__ volatile ("fence rw, rw" ::: "memory");
    for (unsigned i=0;i<WORDS;++i) {
      unsigned p=(i*8191u)&(WORDS-1);
      FAME_CHECK("cacheline/permuted memory",arena[p],
                 fame_mix(p+UINT64_C(0x1020304050607080)+pass));
    }
  }
  /* Alternating lines and 4-KiB aliases stress cache refills and writebacks. */
  for (unsigned offset=0;offset<8;++offset) {
    for (unsigned page=0;page<32;++page) arena[page*512+offset]=page*17+offset;
    for (unsigned page=32;page-- >0;)
      FAME_CHECK("page stride",arena[page*512+offset],page*17+offset);
  }
  volatile uint8_t *bytes=(volatile uint8_t *)&lane;
  for (unsigned bit=0;bit<64;++bit) {
    lane=UINT64_C(1)<<bit;
    FAME_CHECK("walking one",load_d(&lane),UINT64_C(1)<<bit);
    lane=~(UINT64_C(1)<<bit);
    FAME_CHECK("walking zero",load_d(&lane),~(UINT64_C(1)<<bit));
  }
  lane=UINT64_C(0x8877665544332211);
  for (unsigned i=0;i<8;++i)
    FAME_CHECK("little endian bytes",load_bu(bytes+i),(UINT64_C(0x8877665544332211)>>(8*i))&255);
  for (unsigned i=0;i<8;++i) {
    lane=UINT64_MAX;
    store_b(bytes+i,0x80+i);
    uint64_t expected=(UINT64_MAX&~(UINT64_C(255)<<(8*i)))|((uint64_t)(0x80+i)<<(8*i));
    FAME_CHECK("byte write mask",lane,expected);
    FAME_CHECK("signed byte",load_b(bytes+i),UINT64_C(0xffffffffffffff80)+i);
    FAME_CHECK("unsigned byte",load_bu(bytes+i),0x80+i);
  }
  for (unsigned i=0;i<8;i+=2) {
    lane=UINT64_MAX; store_h(bytes+i,0x8001);
    FAME_CHECK("halfword mask",lane,(UINT64_MAX&~(UINT64_C(0xffff)<<(8*i)))|
                                    (UINT64_C(0x8001)<<(8*i)));
    FAME_CHECK("signed halfword",load_h(bytes+i),UINT64_C(0xffffffffffff8001));
    FAME_CHECK("unsigned halfword",load_hu(bytes+i),0x8001);
  }
  for (unsigned i=0;i<8;i+=4) {
    lane=UINT64_MAX; store_w(bytes+i,UINT64_C(0x80000001));
    FAME_CHECK("word mask",lane,(UINT64_MAX&~(UINT64_C(0xffffffff)<<(8*i)))|
                                (UINT64_C(0x80000001)<<(8*i)));
    FAME_CHECK("signed word",load_w(bytes+i),UINT64_C(0xffffffff80000001));
    FAME_CHECK("unsigned word",load_wu(bytes+i),UINT64_C(0x80000001));
  }
  /* Load-use, store-forwarding, and read-after-write dependencies. */
  uint64_t expected=0;
  arena[0]=0;
  for (unsigned i=0;i<1024;++i) {
    expected=expected*3+i;
    arena[0]=arena[0]*3+i;
    FAME_CHECK("dependent load/store",arena[0],expected);
  }
  for (unsigned i=0;i<sizeof(source);++i) source[i]=(uint8_t)(i*37+19);
  for (unsigned offset=0;offset<8;++offset) {
    memset(copied,0xa5,sizeof(copied));
    memcpy(copied+offset,source+7-offset,997);
    for (unsigned i=0;i<sizeof(copied);++i)
      FAME_CHECK("unaligned memcpy guards",copied[i],
                 i>=offset && i<offset+997 ? source[7-offset+i-offset] : 0xa5);
  }
}
