#include "fame_test.h"
#include <stdio.h>

static volatile unsigned armed,trap_bytes,check_value,check_registers;
static volatile uint64_t expected_cause,expected_tval;
extern void tohost_exit(uintptr_t code) __attribute__((noreturn));

void fame_arm_trap(uint64_t cause,uint64_t tval,int check_tval,unsigned bytes)
{
  expected_cause=cause; expected_tval=tval;
  trap_bytes=bytes; check_value=(unsigned)check_tval; armed=1;
  __asm__ volatile ("" ::: "memory");
}

void fame_finish_trap(void)
{
  FAME_CHECK("expected trap delivered",armed,0);
  armed=0;
}

uintptr_t handle_trap(uintptr_t cause,uintptr_t epc,uintptr_t regs[32])
{
  if(cause>>63) {
    if(fame_interrupt_dispatch(cause&UINT64_C(0x7fffffffffffffff))) return epc;
  } else if(armed) {
    FAME_CHECK("synchronous trap cause",cause,expected_cause);
    if(check_value) FAME_CHECK("synchronous trap address",FAME_CSR_READ(mtval),expected_tval);
    if(check_registers) {
      FAME_CHECK("trap saved t0",regs[5],123);
      FAME_CHECK("trap saved t1",regs[6],456);
    }
    armed=0;
    return epc+trap_bytes;
  }
  printf("CIRCT_FAME: FATAL unexpected trap cause=0x%016lx pc=0x%016lx tval=0x%016lx\n",
         (unsigned long)cause,(unsigned long)epc,(unsigned long)FAME_CSR_READ(mtval));
  tohost_exit(1);
}

void fame_traps(void)
{
  uint64_t a,b;
  check_registers=1;
  fame_arm_trap(11,0,0,4);
  __asm__ volatile (".option push\n.option norvc\n\tli t0, 123\n\tli t1, 456\n"
                    "\tecall\n\tmv %0, t0\n\tmv %1, t1\n.option pop"
                    : "=r"(a),"=r"(b) :: "t0","t1","memory");
  fame_finish_trap(); check_registers=0;
  FAME_CHECK("mret restores t0",a,123); FAME_CHECK("mret restores t1",b,456);
  fame_arm_trap(3,0,0,4);
  __asm__ volatile (".option push\n.option norvc\n\tebreak\n.option pop" ::: "memory");
  fame_finish_trap();
  fame_arm_trap(2,0,0,4);
  __asm__ volatile ("csrw cycle, zero" ::: "memory");
  fame_finish_trap();
  fame_arm_trap(3,0,0,2);
  __asm__ volatile (".2byte 0x9002" ::: "memory"); /* c.ebreak */
  fame_finish_trap();
  fame_arm_trap(2,0,0,2);
  __asm__ volatile (".2byte 0x0000" ::: "memory"); /* reserved compressed encoding */
  fame_finish_trap();
}
