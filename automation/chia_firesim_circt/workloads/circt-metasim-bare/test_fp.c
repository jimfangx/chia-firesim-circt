#include "fame_test.h"

#define FP_BINARY(name, op, size, inmove, outmove) \
  static uint64_t name(uint64_t a,uint64_t b) { uint64_t result; \
    __asm__ volatile (inmove " ft0, %1\n\t" inmove " ft1, %2\n\t" \
       op "." size " ft2, ft0, ft1, rne\n\t" outmove " %0, ft2" \
       : "=r"(result) : "r"(a),"r"(b) : "ft0","ft1","ft2"); return result; }
FP_BINARY(add_s,"fadd","s","fmv.w.x","fmv.x.w")
FP_BINARY(mul_s,"fmul","s","fmv.w.x","fmv.x.w")
FP_BINARY(div_s,"fdiv","s","fmv.w.x","fmv.x.w")
FP_BINARY(add_d,"fadd","d","fmv.d.x","fmv.x.d")
FP_BINARY(mul_d,"fmul","d","fmv.d.x","fmv.x.d")
FP_BINARY(div_d,"fdiv","d","fmv.d.x","fmv.x.d")

void fame_fp(void)
{
  uint64_t saved=FAME_CSR_READ(fcsr),r;
  FAME_CSR_WRITE(fcsr,0);
  FAME_CHECK("fadd.s",(uint32_t)add_s(0x3fc00000,0x40100000),0x40700000);
  FAME_CHECK("fmul.s",(uint32_t)mul_s(0x3fc00000,0x40000000),0x40400000);
  FAME_CHECK("fdiv.s",(uint32_t)div_s(0x40c00000,0x40000000),0x40400000);
  FAME_CHECK("fadd.d",add_d(UINT64_C(0x3ff8000000000000),UINT64_C(0x4002000000000000)),UINT64_C(0x400e000000000000));
  FAME_CHECK("fmul.d",mul_d(UINT64_C(0x3ff8000000000000),UINT64_C(0x4000000000000000)),UINT64_C(0x4008000000000000));
  FAME_CHECK("fdiv.d",div_d(UINT64_C(0x4018000000000000),UINT64_C(0x4000000000000000)),UINT64_C(0x4008000000000000));
  FAME_CHECK("exact FP flags",FAME_CSR_READ(fflags),0);
  FAME_CHECK("negative zero sum",(uint32_t)add_s(0x80000000,0x80000000),0x80000000);
  FAME_CHECK("subnormal add",(uint32_t)add_s(1,1),2);
  FAME_CSR_WRITE(fflags,0);
  FAME_CHECK("FP divide zero",(uint32_t)div_s(0x3f800000,0),0x7f800000);
  FAME_CHECK("divide zero flag",FAME_CSR_READ(fflags),8);
  FAME_CSR_WRITE(fflags,0);
  FAME_CHECK("FP invalid canonical NaN",(uint32_t)div_s(0,0),0x7fc00000);
  FAME_CHECK("invalid flag",FAME_CSR_READ(fflags),16);
  FAME_CSR_WRITE(fflags,0);
  FAME_CHECK("FP overflow",(uint32_t)mul_s(0x7f7fffff,0x40000000),0x7f800000);
  FAME_CHECK("overflow and inexact flags",FAME_CSR_READ(fflags),5);
  FAME_CSR_WRITE(fflags,0);
  FAME_CHECK("FP inexact one third",(uint32_t)div_s(0x3f800000,0x40400000),0x3eaaaaab);
  FAME_CHECK("inexact flag",FAME_CSR_READ(fflags),1);
  FAME_CSR_WRITE(fflags,0);
  FAME_CHECK("double one third",div_d(UINT64_C(0x3ff0000000000000),UINT64_C(0x4008000000000000)),UINT64_C(0x3fd5555555555555));
  FAME_CHECK("double inexact flag",FAME_CSR_READ(fflags),1);
  __asm__ volatile ("fmv.d.x ft0, %1\n\tfsqrt.d ft1, ft0, rne\n\tfmv.x.d %0, ft1"
     : "=r"(r) : "r"(UINT64_C(0x4010000000000000)) : "ft0","ft1");
  FAME_CHECK("sqrt.d",r,UINT64_C(0x4000000000000000));
  __asm__ volatile ("fmv.w.x ft0, %1\n\tfsqrt.s ft1, ft0, rne\n\tfmv.x.w %0, ft1"
     : "=r"(r) : "r"(UINT64_C(0x40800000)) : "ft0","ft1");
  FAME_CHECK("sqrt.s",(uint32_t)r,0x40000000);
  __asm__ volatile ("fmv.d.x ft0, %1\n\tfmv.d.x ft1, %2\n\tfmv.d.x ft2, %3\n"
                    "\tfmadd.d ft3, ft0, ft1, ft2, rne\n\tfmv.x.d %0, ft3"
     : "=r"(r) : "r"(UINT64_C(0x3ff8000000000000)),"r"(UINT64_C(0x4000000000000000)),
     "r"(UINT64_C(0x3fe0000000000000)) : "ft0","ft1","ft2","ft3");
  FAME_CHECK("fused multiply add",r,UINT64_C(0x400c000000000000));
#define CONVERT(label, bits, mode, result) do { \
  __asm__ volatile ("fmv.d.x ft0, %1\n\tfcvt.l.d %0, ft0, " mode \
     : "=r"(r) : "r"(UINT64_C(bits)) : "ft0"); \
  FAME_CHECK(label,r,result); } while(0)
  CONVERT("convert ties even",0x4004000000000000,"rne",2);
  CONVERT("convert ties max magnitude",0x4004000000000000,"rmm",3);
  CONVERT("convert negative floor",0xc004000000000000,"rdn",UINT64_MAX-2);
  CONVERT("convert negative truncation",0xc004000000000000,"rtz",UINT64_MAX-1);
  __asm__ volatile ("fmv.w.x ft0, %1\n\tfclass.s %0, ft0" : "=r"(r) : "r"(UINT64_C(0x7fc00000)) : "ft0");
  FAME_CHECK("classify quiet NaN",r,512);
  __asm__ volatile ("fmv.w.x ft0, %1\n\tfclass.s %0, ft0" : "=r"(r) : "r"(UINT64_C(0x80000000)) : "ft0");
  FAME_CHECK("classify negative zero",r,8);
  __asm__ volatile ("fmv.d.x ft0, %1\n\tfmv.d.x ft1, %2\n\tflt.d %0, ft0, ft1"
     : "=r"(r) : "r"(UINT64_C(0x3ff0000000000000)),"r"(UINT64_C(0x4000000000000000)) : "ft0","ft1");
  FAME_CHECK("FP compare",r,1);
  /* Force FP register spill/fill traffic, including mixed integer dependencies. */
  volatile double values[128];
  for(unsigned i=0;i<128;++i) values[i]=(double)i*0.5;
  for(unsigned i=0;i<128;++i) {
    union { double d; uint64_t u; } actual,expected;
    actual.d=values[(i*37)&127]*2.0+1.0;
    expected.d=(double)((i*37)&127)+1.0;
    FAME_CHECK("FP load/store stream",actual.u,expected.u);
  }
  FAME_CSR_WRITE(fcsr,saved);
}
