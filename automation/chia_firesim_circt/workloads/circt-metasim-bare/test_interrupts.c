#include "fame_test.h"

static volatile unsigned active,software_count,timer_count;

int fame_interrupt_dispatch(uint64_t cause)
{
  if(!active) return 0;
  if(cause==3) {
    *FAME_MSIP=0;
    __asm__ volatile ("fence iorw, iorw" ::: "memory");
    ++software_count;
    __asm__ volatile ("csrc mie, %0" :: "r"(UINT64_C(8)) : "memory");
    return 1;
  }
  if(cause==7) {
    *FAME_MTIMECMP=UINT64_MAX;
    __asm__ volatile ("fence iorw, iorw" ::: "memory");
    ++timer_count;
    __asm__ volatile ("csrc mie, %0" :: "r"(UINT64_C(128)) : "memory");
    return 1;
  }
  return 0;
}

static void wait_for(volatile unsigned *count)
{
  for(unsigned i=0;i<1000000 && !*count;++i)
    __asm__ volatile ("nop" ::: "memory");
  FAME_CHECK("bounded interrupt delivery",*count,1);
}

void fame_interrupts(void)
{
  uint64_t saved_status=FAME_CSR_READ(mstatus),saved_mie=FAME_CSR_READ(mie);
  uint64_t saved_compare=*FAME_MTIMECMP;
  uint32_t saved_msip=*FAME_MSIP;
  FAME_CSR_WRITE(mstatus,saved_status&~UINT64_C(8));
  FAME_CSR_WRITE(mie,0); active=1; software_count=timer_count=0;
  *FAME_MSIP=1;
  __asm__ volatile ("fence iorw, iorw" ::: "memory");
  FAME_CSR_WRITE(mie,8); FAME_CSR_WRITE(mstatus,saved_status|8);
  wait_for(&software_count);
  FAME_CSR_WRITE(mstatus,saved_status&~UINT64_C(8));
  FAME_CSR_WRITE(mie,0); *FAME_MSIP=0;
  uint64_t before=*FAME_MTIME;
  *FAME_MTIMECMP=before+16;
  __asm__ volatile ("fence iorw, iorw" ::: "memory");
  FAME_CSR_WRITE(mie,128);
  /* Global MIE remains clear: WFI wakes on local MTIP before it is consumed.
     This avoids a race where the handler runs before WFI and disarms the timer. */
  __asm__ volatile ("wfi" ::: "memory");
  FAME_CSR_WRITE(mstatus,saved_status|8);
  wait_for(&timer_count);
  FAME_CHECK("CLINT time progresses",*FAME_MTIME>before,1);
  FAME_CSR_WRITE(mstatus,saved_status&~UINT64_C(8));
  FAME_CSR_WRITE(mie,0); active=0;
  *FAME_MSIP=saved_msip; *FAME_MTIMECMP=saved_compare;
  __asm__ volatile ("fence iorw, iorw" ::: "memory");
  FAME_CSR_WRITE(mie,saved_mie); FAME_CSR_WRITE(mstatus,saved_status);
}
