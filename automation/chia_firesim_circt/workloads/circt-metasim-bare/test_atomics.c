#include "fame_test.h"

static volatile uint64_t atomic64 __attribute__((aligned(64)));
static volatile uint32_t atomic32 __attribute__((aligned(64)));
#define AMO(name, opcode) \
  static uint64_t name##64(uint64_t v) { uint64_t old; \
    __asm__ volatile (opcode ".d.aqrl %0, %2, (%1)" : "=r"(old) : "r"(&atomic64),"r"(v) : "memory"); return old; } \
  static uint64_t name##32(uint32_t v) { uint64_t old; \
    __asm__ volatile (opcode ".w.aqrl %0, %2, (%1)" : "=r"(old) : "r"(&atomic32),"r"((uint64_t)v) : "memory"); return old; }
AMO(swap,"amoswap") AMO(add,"amoadd") AMO(bitxor,"amoxor")
AMO(bitand,"amoand") AMO(bitor,"amoor") AMO(mins,"amomin")
AMO(maxs,"amomax") AMO(minu,"amominu") AMO(maxu,"amomaxu")

static uint64_t expected(unsigned op,uint64_t a,uint64_t b,unsigned width)
{
  uint64_t sign=UINT64_C(1)<<(width-1);
  switch(op) {
    case 0:return b; case 1:return a+b; case 2:return a^b;
    case 3:return a&b; case 4:return a|b;
    case 5:return (a^sign)<(b^sign)?a:b;
    case 6:return (a^sign)>(b^sign)?a:b;
    case 7:return a<b?a:b; default:return a>b?a:b;
  }
}

void fame_atomics(void)
{
  static uint64_t (*const ops64[])(uint64_t)={swap64,add64,bitxor64,bitand64,bitor64,mins64,maxs64,minu64,maxu64};
  static uint64_t (*const ops32[])(uint32_t)={swap32,add32,bitxor32,bitand32,bitor32,mins32,maxs32,minu32,maxu32};
  static const uint64_t values[]={0,1,UINT64_MAX,UINT64_C(0x8000000000000000),
    UINT64_C(0x7fffffffffffffff),UINT64_C(0x80000001),UINT64_C(0xaaaaaaaa55555555)};
  for(unsigned op=0;op<FAME_ARRAY_SIZE(ops64);++op)
    for(unsigned i=0;i<FAME_ARRAY_SIZE(values);++i)
      for(unsigned j=0;j<FAME_ARRAY_SIZE(values);++j) {
        uint64_t a=values[i],b=values[j]; atomic64=a;
        FAME_CHECK("AMO.d old value",ops64[op](b),a);
        FAME_CHECK("AMO.d new value",atomic64,expected(op,a,b,64));
        atomic32=(uint32_t)a;
        FAME_CHECK("AMO.w sign extension",ops32[op]((uint32_t)b),fame_sext32((uint32_t)a));
        FAME_CHECK("AMO.w new value",atomic32,(uint32_t)expected(op,(uint32_t)a,(uint32_t)b,32));
      }
  for(unsigned i=0;i<256;++i) {
    atomic64=i; uint64_t old=0,status=1,second=0;
    for(unsigned retry=0;retry<128 && status;++retry)
      __asm__ volatile ("lr.d.aq %0, (%3)\n\tsc.d.rl %1, %4, (%3)\n\tsc.d %2, %4, (%3)"
        : "=&r"(old),"=&r"(status),"=&r"(second) : "r"(&atomic64),"r"((uint64_t)i+1) : "memory");
    FAME_CHECK("LR/SC.d bounded success",status,0);
    FAME_CHECK("LR.d old",old,i); FAME_CHECK("SC.d store",atomic64,i+1);
    FAME_CHECK("SC without reservation fails",second!=0,1);
    atomic32=UINT32_C(0x80000000)+i; status=1;
    for(unsigned retry=0;retry<128 && status;++retry)
      __asm__ volatile ("lr.w.aq %0, (%2)\n\tsc.w.rl %1, %3, (%2)"
        : "=&r"(old),"=&r"(status) : "r"(&atomic32),"r"((uint64_t)i+1) : "memory");
    FAME_CHECK("LR/SC.w bounded success",status,0);
    FAME_CHECK("LR.w sign extension",old,fame_sext32(UINT32_C(0x80000000)+i));
    FAME_CHECK("SC.w store",atomic32,i+1);
  }
}
