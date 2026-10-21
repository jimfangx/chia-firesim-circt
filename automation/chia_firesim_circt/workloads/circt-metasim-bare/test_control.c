#include "fame_test.h"

#define BRANCH(name, insn) \
  static uint64_t name(uint64_t a,uint64_t b) { uint64_t r; \
    __asm__ volatile ("li %0, 0\n\t" insn " %1, %2, 1f\n\tj 2f\n1:\n\tli %0, 1\n2:" \
      : "=&r"(r) : "r"(a),"r"(b)); return r; }
BRANCH(beq,"beq") BRANCH(bne,"bne") BRANCH(blt,"blt")
BRANCH(bge,"bge") BRANCH(bltu,"bltu") BRANCH(bgeu,"bgeu")

static FAME_NOINLINE uint64_t f0(uint64_t x) { return x+7; }
static FAME_NOINLINE uint64_t f1(uint64_t x) { return x^UINT64_C(0xa55aa55a); }
static FAME_NOINLINE uint64_t f2(uint64_t x) { return (x<<9)|(x>>55); }
static FAME_NOINLINE uint64_t f3(uint64_t x) { return x*33-17; }
static uint64_t (*volatile functions[])(uint64_t)={f0,f1,f2,f3};

static FAME_NOINLINE uint64_t fibonacci(unsigned n)
{
  volatile uint64_t guard=UINT64_C(0xfeedface01234567)^n;
  if (n<2) return n;
  uint64_t a=fibonacci(n-1),b=fibonacci(n-2);
  FAME_CHECK("recursive stack guard",guard,UINT64_C(0xfeedface01234567)^n);
  return a+b;
}

void fame_control(void)
{
  static const uint64_t v[]={0,1,UINT64_MAX,UINT64_C(0x8000000000000000),
                              UINT64_C(0x7fffffffffffffff),127,128};
  for (unsigned i=0;i<FAME_ARRAY_SIZE(v);++i)
    for (unsigned j=0;j<FAME_ARRAY_SIZE(v);++j) {
      uint64_t a=v[i],b=v[j];
      unsigned less=(a^UINT64_C(0x8000000000000000))<(b^UINT64_C(0x8000000000000000));
      FAME_CHECK("beq",beq(a,b),a==b); FAME_CHECK("bne",bne(a,b),a!=b);
      FAME_CHECK("blt",blt(a,b),less); FAME_CHECK("bge",bge(a,b),!less);
      FAME_CHECK("bltu",bltu(a,b),a<b); FAME_CHECK("bgeu",bgeu(a,b),a>=b);
    }
  uint64_t state=UINT64_C(0x0123456789abcdef);
  for (unsigned i=0;i<2048;++i) {
    unsigned op=(unsigned)(state>>7)&3;
    uint64_t expected;
    switch(op) {
      case 0: expected=state+7; break;
      case 1: expected=state^UINT64_C(0xa55aa55a); break;
      case 2: expected=(state<<9)|(state>>55); break;
      default: expected=state*33-17; break;
    }
    state=functions[op](state);
    FAME_CHECK("indirect call and return",state,expected);
  }
  uint64_t a=0,b=1;
  for (unsigned n=0;n<13;++n) {
    FAME_CHECK("recursive fibonacci",fibonacci(n),a);
    uint64_t next=a+b; a=b; b=next;
  }
  /* Explicit RV64C instructions, without depending on GCC compression. */
  uint64_t compressed;
  __asm__ volatile (".option push\n.option rvc\n\tc.li %0, 7\n\tc.addi %0, -3\n"
                    "\tc.slli %0, 2\n\tc.nop\n.option pop" : "=r"(compressed));
  FAME_CHECK("compressed integer stream",compressed,16);
}
