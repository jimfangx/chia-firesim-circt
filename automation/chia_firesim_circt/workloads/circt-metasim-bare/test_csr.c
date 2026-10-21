#include "fame_test.h"

void fame_csr(void)
{
  uint64_t saved=FAME_CSR_READ(mscratch),old;
  static const uint64_t values[]={0,UINT64_MAX,UINT64_C(0x0123456789abcdef),
                                 UINT64_C(0x8000000000000000)};
  for(unsigned i=0;i<FAME_ARRAY_SIZE(values);++i) {
    uint64_t v=values[i]; FAME_CSR_WRITE(mscratch,v);
    FAME_CHECK("CSR read/write",FAME_CSR_READ(mscratch),v);
    __asm__ volatile ("csrrw %0, mscratch, %1" : "=r"(old) : "r"(~v) : "memory");
    FAME_CHECK("CSRRW old",old,v); FAME_CHECK("CSRRW new",FAME_CSR_READ(mscratch),~v);
    FAME_CSR_WRITE(mscratch,v);
    __asm__ volatile ("csrrs %0, mscratch, %1" : "=r"(old) : "r"(UINT64_C(0x55aa55aa)) : "memory");
    FAME_CHECK("CSRRS old",old,v);
    FAME_CHECK("CSRRS new",FAME_CSR_READ(mscratch),v|UINT64_C(0x55aa55aa));
    __asm__ volatile ("csrrc %0, mscratch, %1" : "=r"(old) : "r"(UINT64_C(0x55aa55aa)) : "memory");
    FAME_CHECK("CSRRC old",old,v|UINT64_C(0x55aa55aa));
    FAME_CHECK("CSRRC new",FAME_CSR_READ(mscratch),v&~UINT64_C(0x55aa55aa));
  }
  FAME_CSR_WRITE(mscratch,0);
  __asm__ volatile ("csrrwi %0, mscratch, 21" : "=r"(old) :: "memory");
  FAME_CHECK("CSRRWI old",old,0); FAME_CHECK("CSRRWI new",FAME_CSR_READ(mscratch),21);
  __asm__ volatile ("csrrsi %0, mscratch, 10" : "=r"(old) :: "memory");
  FAME_CHECK("CSRRSI old",old,21); FAME_CHECK("CSRRSI new",FAME_CSR_READ(mscratch),31);
  __asm__ volatile ("csrrci %0, mscratch, 3" : "=r"(old) :: "memory");
  FAME_CHECK("CSRRCI old",old,31); FAME_CHECK("CSRRCI new",FAME_CSR_READ(mscratch),28);
  FAME_CSR_WRITE(mscratch,saved);
  FAME_CHECK("single Rocket hart",FAME_CSR_READ(mhartid),0);
  uint64_t misa=FAME_CSR_READ(misa);
  FAME_CHECK("RV64 XLEN",misa>>62,2);
  FAME_CHECK("RV64IMAFDC extensions",misa&UINT64_C(0x112d),UINT64_C(0x112d));
  uint64_t c0=FAME_CSR_READ(mcycle),i0=FAME_CSR_READ(minstret);
  for(unsigned i=0;i<256;++i) __asm__ volatile ("addi zero, zero, 0" ::: "memory");
  uint64_t i1=FAME_CSR_READ(minstret),c1=FAME_CSR_READ(mcycle);
  FAME_CHECK("retired instructions advance",i1-i0>=256,1);
  FAME_CHECK("target cycle counter advances",c1>c0,1);
  FAME_CHECK("user cycle alias available",FAME_CSR_READ(cycle)>=c1,1);
  FAME_CHECK("user instret alias available",FAME_CSR_READ(instret)>=i1,1);
}
