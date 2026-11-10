#!/usr/bin/env python3
"""Compile the real generic host driver against adversarial physical IO only."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SOURCE = r'''
#include "bridges/cpu_managed_stream.h"
#include <array>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <deque>
#include <map>
#include <vector>

using namespace CPUManagedStreams;
using Beat=std::array<unsigned char,64>;
static Beat payload(unsigned stream,unsigned head){
  Beat data{};
  for(unsigned word=0;word<16;word++){
    const uint32_t value=0xCAFE0000u|(stream<<12)|(head<<5)|word;
    memcpy(data.data()+word*4,&value,4);
  }
  return data;
}
struct FakeIO final:CPUManagedStreamIO{
  struct Queue{std::deque<Beat> heads;bool ack_pending=false;};
  std::map<size_t,Queue> queues;
  std::map<size_t,size_t> counts;
  struct Read{size_t address,bytes;};
  std::vector<Read> reads;
  std::vector<std::pair<size_t,uint32_t>> acks;
  std::vector<size_t> count_reads;
  bool legacy=false,short_read=false,bad_count=false;
  unsigned extra_reads=3;
  FakeIO(){counts[0x100]=0;counts[0x104]=0x80000;}
  bool supports_explicit_ack()const override{return true;}
  uint32_t mmio_read(size_t address)override{
    count_reads.push_back(address);auto &queue=queues.at(counts.at(address));
    // A posted OCL ACK does not become visible until the same-interface
    // readback flushes it. Omitting that readback replays the previous head.
    if(queue.ack_pending){assert(!queue.heads.empty());queue.heads.pop_front();queue.ack_pending=false;}
    return bad_count?100:queue.heads.size();
  }
  void mmio_write(size_t address,uint32_t data)override{
    auto &queue=queues.at(counts.at(address));assert(data==1&&!queue.heads.empty()&&!queue.ack_pending);
    queue.ack_pending=true;acks.emplace_back(address,data);
  }
  size_t cpu_managed_axi4_read(size_t address,char *data,size_t bytes)override{
    reads.push_back({address,bytes});auto &queue=queues.at(address);
    assert(!queue.heads.empty());
    if(legacy){
      assert(bytes%64==0&&queue.heads.size()>=bytes/64);
      for(size_t offset=0;offset<bytes;offset+=64){memcpy(data+offset,queue.heads.front().data(),64);queue.heads.pop_front();}
      return bytes;
    }
    assert(bytes==64&&!queue.ack_pending);
    const auto head=queue.heads.front();
    // Physical WC reads can be duplicated or grouped, and their bus addresses
    // can be aligned to the same line. None of these observations consumes it.
    for(unsigned repeat=0;repeat<extra_reads;repeat++)
      for(unsigned lane=0;lane<16;lane++)assert(queue.heads.front()[lane]==head[lane]);
    memcpy(data,head.data(),short_read?32:64);
    return short_read?32:64;
  }
  size_t cpu_managed_axi4_write(size_t,const char *,size_t)override{assert(false);return 0;}
  uint64_t get_beat_bytes()const override{return 64;}
};
static StreamParameters params(unsigned index,bool ack=true){
  return StreamParameters("actual-host-test",index?0x80000:0,0x100+4*index,8,64,ack);
}
int main(){
  {
    FakeIO io;for(unsigned i=0;i<3;i++)io.queues[0].heads.push_back(payload(0,i));
    for(unsigned i=0;i<2;i++)io.queues[0x80000].heads.push_back(payload(1,i));
    FPGAToCPUDriver first(params(0),io),second(params(1),io);
    std::array<unsigned char,192> data{};
    assert(first.pull(data.data(),192,64)==192);
    for(unsigned i=0;i<3;i++)assert(!memcmp(data.data()+64*i,payload(0,i).data(),64));
    assert(io.acks.size()==3&&io.count_reads.size()==4&&io.queues[0].heads.empty());
    assert(io.queues[0x80000].heads.size()==2);
    assert(second.pull(data.data(),128,64)==128);
    for(unsigned i=0;i<2;i++)assert(!memcmp(data.data()+64*i,payload(1,i).data(),64));
    assert(io.acks.size()==5&&io.count_reads.size()==7);
    assert(first.pull(data.data(),64,0)==0&&io.acks.size()==5);
  }
  {
    FakeIO io;io.queues[0].heads.push_back(payload(0,0));
    FPGAToCPUDriver driver(params(0),io);unsigned char data[128]{};
    assert(driver.pull(data,128,128)==0&&io.reads.empty()&&io.acks.empty());
    io.short_read=true;
    try{driver.pull(data,64,64);assert(false);}catch(const std::runtime_error&){}
    assert(io.acks.empty()&&io.queues[0].heads.size()==1);
  }
  {
    FakeIO io;io.queues[0].heads.push_back(payload(0,0));io.bad_count=true;
    FPGAToCPUDriver driver(params(0),io);unsigned char data[128]{};
    try{driver.pull(data,64,0);assert(false);}catch(const std::runtime_error&){}
    assert(io.reads.empty()&&io.acks.empty());
    try{driver.pull(data,4,0);assert(false);}catch(const std::invalid_argument&){}
    auto bad=params(0);bad.fpga_buffer_width_bytes=32;
    FPGAToCPUDriver unsupported(std::move(bad),io);
    try{unsupported.pull(data,64,0);assert(false);}catch(const std::invalid_argument&){}
  }
  {
    FakeIO io;io.legacy=true;for(unsigned i=0;i<3;i++)io.queues[0].heads.push_back(payload(0,i));
    auto legacy_params=StreamParameters("legacy",0,0x100,8,64);assert(!legacy_params.explicit_ack);
    FPGAToCPUDriver driver(std::move(legacy_params),io);unsigned char data[192]{};
    assert(driver.pull(data,192,64)==192&&io.reads.size()==1&&io.reads[0].bytes==192);
    assert(io.acks.empty()&&io.count_reads.size()==1);
    for(unsigned i=0;i<3;i++)assert(!memcmp(data+64*i,payload(0,i).data(),64));
  }
  struct OldBackend final:CPUManagedStreamIO{
    unsigned mmio_reads=0,dma_reads=0,writes=0;
    uint32_t mmio_read(size_t)override{++mmio_reads;return 1;}
    void mmio_write(size_t,uint32_t)override{++writes;}
    size_t cpu_managed_axi4_read(size_t,char *data,size_t size)override{++dma_reads;memset(data,0,size);return size;}
    size_t cpu_managed_axi4_write(size_t,const char *,size_t)override{return 0;}
    uint64_t get_beat_bytes()const override{return 64;}
  }old;
  FPGAToCPUDriver unavailable(params(0),old);unsigned char bytes[64]{};
  try{unavailable.pull(bytes,64,64);assert(false);}catch(const std::runtime_error&){}
  assert(old.mmio_reads==0&&old.dma_reads==0&&old.writes==0);
  FPGAToCPUDriver old_protocol(params(0,false),old);
  assert(old_protocol.pull(bytes,64,64)==64);
  assert(old.mmio_reads==1&&old.dma_reads==1&&old.writes==0);
  puts("PASS actual CPUManagedStreamDriver: explicit ACK after complete head, posted-write readback, duplicated physical reads harmless, independent streams, upper payload bits, short reads no ACK, occupancy/geometry rejection, legacy bulk API preserved, unavailable backend rejects before any IO");
}
'''


class CPUStreamAckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.tmp.cleanup)
        cls.root=Path(cls.tmp.name);repo=Path(__file__).resolve().parents[2]
        source=cls.root/'control.cc';source.write_text(SOURCE)
        cls.binary=cls.root/'control'
        compiler=repo/'.conda-env/bin/c++'
        if not compiler.exists():compiler=Path(shutil.which('c++'))
        core=repo/'sims/firesim/sim/midas/src/main/cc'
        result=subprocess.run([str(compiler),'-O2','-std=c++17','-ffunction-sections','-fdata-sections',
            '-I',str(core),str(source),str(core/'bridges/cpu_managed_stream.cc'),'-Wl,--gc-sections',
            '-o',str(cls.binary)],capture_output=True,text=True)
        if result.returncode:raise AssertionError(result.stderr)

    def test_real_host_driver_with_adversarial_physical_reads(self):
        result=subprocess.run([str(self.binary)],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('PASS actual CPUManagedStreamDriver',result.stdout)

    def test_x86_toolchain_retains_full_io_fences(self):
        import platform
        if platform.machine() not in ('x86_64','i386','i686'):self.skipTest('x86 machine-code control')
        result=subprocess.run(['objdump','-d',str(self.binary)],capture_output=True,text=True,check=True)
        self.assertGreaterEqual(result.stdout.count('mfence'),2)


if __name__=='__main__':unittest.main()
