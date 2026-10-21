#include "fame_test.h"

#define BINOP(name, insn) \
  static uint64_t name(uint64_t a, uint64_t b) { uint64_t r; \
    __asm__ volatile (insn " %0, %1, %2" : "=r"(r) : "r"(a), "r"(b)); return r; }
BINOP(rv_add, "add") BINOP(rv_sub, "sub")
BINOP(rv_addw, "addw") BINOP(rv_subw, "subw")
BINOP(rv_mul, "mul") BINOP(rv_mulh, "mulh")
BINOP(rv_mulhu, "mulhu") BINOP(rv_mulhsu, "mulhsu") BINOP(rv_mulw, "mulw")
BINOP(rv_div, "div") BINOP(rv_divu, "divu") BINOP(rv_rem, "rem") BINOP(rv_remu, "remu")
BINOP(rv_divw, "divw") BINOP(rv_divuw, "divuw")
BINOP(rv_remw, "remw") BINOP(rv_remuw, "remuw")
BINOP(rv_sll, "sll") BINOP(rv_srl, "srl") BINOP(rv_sra, "sra")
BINOP(rv_sllw, "sllw") BINOP(rv_srlw, "srlw") BINOP(rv_sraw, "sraw")
BINOP(rv_and, "and") BINOP(rv_or, "or") BINOP(rv_xor, "xor")
BINOP(rv_slt, "slt") BINOP(rv_sltu, "sltu")

/* Bit-serial oracles intentionally do not use the instructions under test. */
static FAME_NOINLINE uint64_t multiply(uint64_t a, uint64_t b, uint64_t *hi)
{
  uint64_t lo = 0, high = 0, shifted_hi = 0;
  for (unsigned i = 0; i < 64; ++i) {
    if (b & 1) {
      uint64_t next = lo + a;
      high += shifted_hi + (next < lo);
      lo = next;
    }
    shifted_hi = (shifted_hi << 1) | (a >> 63);
    a <<= 1;
    b >>= 1;
  }
  *hi = high;
  return lo;
}

static FAME_NOINLINE uint64_t divide(uint64_t a, uint64_t b, uint64_t *rem)
{
  if (!b) { *rem = a; return UINT64_MAX; }
  uint64_t q = 0, r = 0;
  for (unsigned i = 64; i-- > 0;) {
    uint64_t carry = r >> 63;
    r = (r << 1) | ((a >> i) & 1);
    if (carry || r >= b) { r -= b; q |= UINT64_C(1) << i; }
  }
  *rem = r;
  return q;
}

static uint64_t signed_divide(uint64_t a, uint64_t b, uint64_t *rem)
{
  uint64_t r, q;
  if (!b) { *rem = a; return UINT64_MAX; }
  q = divide((a >> 63) ? -a : a, (b >> 63) ? -b : b, &r);
  *rem = (a >> 63) ? -r : r;
  return ((a ^ b) >> 63) ? -q : q;
}

static uint64_t arshift(uint64_t x, unsigned n)
{
  return (x >> n) | ((n && (x >> 63)) ? UINT64_MAX << (64 - n) : 0);
}

static void pair(uint64_t a, uint64_t b)
{
  uint64_t hi, rem, q, lo = multiply(a, b, &hi);
  unsigned s = b & 63, w = b & 31;
  FAME_CHECK("add", rv_add(a,b), a+b);
  FAME_CHECK("sub", rv_sub(a,b), a-b);
  FAME_CHECK("addw", rv_addw(a,b), fame_sext32((uint32_t)a+(uint32_t)b));
  FAME_CHECK("subw", rv_subw(a,b), fame_sext32((uint32_t)a-(uint32_t)b));
  FAME_CHECK("mul", rv_mul(a,b), lo);
  FAME_CHECK("mulhu", rv_mulhu(a,b), hi);
  FAME_CHECK("mulh", rv_mulh(a,b), hi-((a>>63)?b:0)-((b>>63)?a:0));
  FAME_CHECK("mulhsu", rv_mulhsu(a,b), hi-((a>>63)?b:0));
  FAME_CHECK("mulw", rv_mulw(a,b), fame_sext32((uint32_t)lo));
  q = divide(a,b,&rem);
  FAME_CHECK("divu", rv_divu(a,b), q); FAME_CHECK("remu", rv_remu(a,b), rem);
  q = signed_divide(a,b,&rem);
  FAME_CHECK("div", rv_div(a,b), q); FAME_CHECK("rem", rv_rem(a,b), rem);
  q = divide((uint32_t)a,(uint32_t)b,&rem);
  FAME_CHECK("divuw", rv_divuw(a,b), fame_sext32((uint32_t)q));
  FAME_CHECK("remuw", rv_remuw(a,b), fame_sext32((uint32_t)rem));
  q = signed_divide(fame_sext32((uint32_t)a),fame_sext32((uint32_t)b),&rem);
  FAME_CHECK("divw", rv_divw(a,b), fame_sext32((uint32_t)q));
  FAME_CHECK("remw", rv_remw(a,b), fame_sext32((uint32_t)rem));
  FAME_CHECK("sll", rv_sll(a,b), a<<s); FAME_CHECK("srl", rv_srl(a,b), a>>s);
  FAME_CHECK("sra", rv_sra(a,b), arshift(a,s));
  FAME_CHECK("sllw", rv_sllw(a,b), fame_sext32((uint32_t)a<<w));
  FAME_CHECK("srlw", rv_srlw(a,b), fame_sext32((uint32_t)a>>w));
  FAME_CHECK("sraw", rv_sraw(a,b), arshift(fame_sext32((uint32_t)a),w));
  FAME_CHECK("and", rv_and(a,b), a&b); FAME_CHECK("or", rv_or(a,b), a|b);
  FAME_CHECK("xor", rv_xor(a,b), a^b);
  FAME_CHECK("sltu", rv_sltu(a,b), a<b);
  FAME_CHECK("slt", rv_slt(a,b), (a^UINT64_C(0x8000000000000000)) <
                                  (b^UINT64_C(0x8000000000000000)));
}

void fame_integer(void)
{
  static const uint64_t edge[] = {
    0,1,2,3,31,32,63,64,127,128,UINT64_C(0x7fffffff),UINT64_C(0x80000000),
    UINT64_C(0xffffffff),UINT64_C(0x100000000),UINT64_C(0x7fffffffffffffff),
    UINT64_C(0x8000000000000000),UINT64_MAX,UINT64_MAX-1,
    UINT64_C(0xaaaaaaaaaaaaaaaa),UINT64_C(0x5555555555555555)
  };
  for (unsigned i=0;i<FAME_ARRAY_SIZE(edge);++i)
    for (unsigned j=0;j<FAME_ARRAY_SIZE(edge);++j) pair(edge[i],edge[j]);
  uint64_t state = UINT64_C(0x3141592653589793);
  for (unsigned i=0;i<128;++i) {
    state = fame_mix(state+i); uint64_t a=state;
    state = fame_mix(state+1); pair(a,state);
  }
  uint64_t zero;
  __asm__ volatile ("addi zero, zero, 7\n\tmv %0, zero" : "=r"(zero));
  FAME_CHECK("x0 remains zero",zero,0);
}
