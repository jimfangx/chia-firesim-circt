"""Execute F2 stable-head reads and the real explicit-ACK driver dispatch.

Mapped host RAM supplies changing device heads; this checks bytes, instruction
selection and ownership, not WC/PCI performance or FPGA acceptance.
"""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest


SOURCE = r'''
#include "core/f2_stream_io.h"
#include "bridges/cpu_managed_stream.h"
#include <array>
#include <cassert>
#include <cstdio>
#include <deque>
#include <map>
#include <vector>
using Beat=std::array<char,64>;
static Beat payload(unsigned stream,unsigned head) {
  Beat value{};
  for(unsigned i=0;i<64;i++) value[i]=char(i*37+head*13+stream*101);
  return value;
}
struct IO final:CPUManagedStreamIO {
  struct Queue {alignas(64) Beat mapped{};std::deque<Beat> heads;bool posted=false;};
  std::map<size_t,Queue> queues;
  unsigned legacy_reads=0,stable_reads=0,word_reads=0,acks=0,flushes=0;
  bool streaming=false,short_read=false;
  std::vector<unsigned> word_offsets;
  bool supports_explicit_ack()const override{return true;}
  uint32_t mmio_read(size_t addr)override {
    auto &q=queues.at((addr-0x100)*0x10000);
    if(q.posted){assert(!q.heads.empty());q.heads.pop_front();q.posted=false;++flushes;
      if(!q.heads.empty())q.mapped=q.heads.front();}
    return q.heads.size();
  }
  void mmio_write(size_t addr,uint32_t value)override {
    auto &q=queues.at((addr-0x100)*0x10000);
    assert(value==1&&!q.posted&&!q.heads.empty());q.posted=true;++acks;
  }
  size_t cpu_managed_axi4_read(size_t addr,char *data,size_t bytes)override {
    ++legacy_reads;auto &q=queues.at(addr);assert(!q.posted&&bytes%64==0);
    for(size_t offset=0;offset<bytes;offset+=64){
      memcpy(data+offset,q.heads.front().data(),64);q.heads.pop_front();}
    return bytes;
  }
  size_t cpu_managed_axi4_read_acked(size_t addr,char *data,size_t bytes)override {
    ++stable_reads;auto &q=queues.at(addr);assert(bytes==64&&!q.posted&&!q.heads.empty());
    return f2_stream_io::read_stable_line(q.mapped.data(),data,streaming,
      [&](size_t offset,uint64_t *word){
        assert(!q.posted&&offset%8==0);word_offsets.push_back(offset);++word_reads;
        memcpy(word,q.mapped.data()+offset,8);return short_read?size_t(4):size_t(8);
      });
  }
  size_t cpu_managed_axi4_write(size_t,const char *,size_t)override{assert(false);return 0;}
  uint64_t get_beat_bytes()const override{return 64;}
};
static CPUManagedStreams::StreamParameters params(unsigned stream,bool ack=true) {
  return {"stable",stream*0x40000,0x100+stream*4,128,64,ack};
}
static void transfer(bool streaming) {
  IO io;io.streaming=streaming;
  for(unsigned stream=0;stream<2;stream++){
    auto &q=io.queues[stream*0x40000];
    for(unsigned head=0;head<128;head++)q.heads.push_back(payload(stream,head));
    q.mapped=q.heads.front();
  }
  CPUManagedStreams::FPGAToCPUDriver first(params(0),io),second(params(1),io);
  std::array<char,131> data{};
  for(unsigned head=0;head<128;head++)for(unsigned stream=0;stream<2;stream++){
    auto &driver=stream?second:first;
    data.fill(char(0x5a));
    assert(driver.pull(data.data()+3,64,64)==64);
    assert(!memcmp(data.data()+3,payload(stream,head).data(),64));
    assert(data[2]==char(0x5a)&&data[67]==char(0x5a));
    assert(io.acks==head*2+stream+1&&io.flushes==io.acks);
  }
  assert(io.legacy_reads==0&&io.stable_reads==256&&io.acks==256);
  assert(io.word_reads==(streaming?0:256*8));
  for(unsigned i=0;i<io.word_offsets.size();i++)assert(io.word_offsets[i]==(i%8)*8);
  assert(io.queues[0].heads.empty()&&io.queues[0x40000].heads.empty());
}
int main() {
  transfer(false);
  if(f2_stream_io::streaming_load_supported())transfer(true);
  {
    IO io;io.short_read=true;auto &q=io.queues[0];q.heads.push_back(payload(0,1));q.mapped=q.heads.front();
    CPUManagedStreams::FPGAToCPUDriver driver(params(0),io);char data[64];
    try{driver.pull(data,64,64);assert(false);}catch(const std::runtime_error&){}
    assert(io.acks==0&&io.flushes==0&&q.heads.size()==1&&io.word_reads==1);
  }
  {
    IO io;auto &q=io.queues[0];for(unsigned i=0;i<2;i++)q.heads.push_back(payload(0,i));
    CPUManagedStreams::FPGAToCPUDriver driver(params(0,false),io);char data[128];
    assert(driver.pull(data,128,128)==128);
    assert(io.legacy_reads==1&&io.stable_reads==0&&io.acks==0);
    for(unsigned i=0;i<2;i++)assert(!memcmp(data+64*i,payload(0,i).data(),64));
  }
  alignas(64) std::array<char,128> source{};char data[64];unsigned reads=0;
  auto unexpected=[&](size_t,uint64_t *){++reads;return size_t(8);};
  for(unsigned offset: {1u,16u,32u,48u}) {
    try{f2_stream_io::read_stable_line(source.data()+offset,data,true,unexpected);assert(false);}
    catch(const std::invalid_argument&){}
  }
  try{f2_stream_io::read_stable_line(nullptr,data,true,unexpected);assert(false);}
  catch(const std::invalid_argument&){}
  try{f2_stream_io::read_stable_line(source.data(),nullptr,false,unexpected);assert(false);}
  catch(const std::invalid_argument&){}
  assert(reads==0);
  if(!f2_stream_io::streaming_load_supported()) {
    try{f2_stream_io::read_stable_line(source.data(),data,true,unexpected);assert(false);}
    catch(const std::runtime_error&){}
    assert(reads==0);
  }
  for(size_t incomplete: {size_t(0),size_t(4)}) {
    reads=0;
    try{f2_stream_io::read_stable_line(nullptr,data,false,[&](size_t,uint64_t *){++reads;return incomplete;});assert(false);}
    catch(const std::runtime_error&){}
    assert(reads==1);
  }
  puts("PASS F2 stable-head payload bytes, unaligned destination, changing heads, two streams, ACK/flush order, SDK64 fallback, short reads retain head, legacy dispatch, alignment rejection");
  printf("streaming_sse41_exercised=%u\n",unsigned(f2_stream_io::streaming_load_supported()));
}
'''


class F2StreamReadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        root = Path(cls.tmp.name)
        repo = Path(__file__).resolve().parents[2]
        core = repo / 'sims/firesim/sim/midas/src/main/cc'
        source = root / 'test.cc'
        source.write_text(SOURCE)
        cls.binary = root / 'test'
        command = [str(repo / '.conda-env/bin/c++'), '-O2', '-std=c++17',
                   '-ffunction-sections', '-fdata-sections', '-I', str(core),
                   str(source), str(core / 'bridges/cpu_managed_stream.cc'),
                   '-Wl,--gc-sections']
        subprocess.run(command + ['-o', str(cls.binary)], check=True, capture_output=True)
        cls.fallback_binary = root / 'no-sse41'
        # Override CPUID only in this test executable. It must never execute
        # the available SIMD function when capability detection says absent.
        subprocess.run(command + ['-D__builtin_cpu_supports(x)=0', '-o',
                                  str(cls.fallback_binary)], check=True, capture_output=True)

    def test_actual_stable_read_and_explicit_ack_dispatch(self):
        result = subprocess.run([str(self.binary)], check=True,
                                capture_output=True, text=True, timeout=10)
        self.assertIn('PASS F2 stable-head payload bytes', result.stdout)

    def test_absent_sse41_uses_scalar_reads_and_rejects_forced_simd(self):
        result = subprocess.run([str(self.fallback_binary)], check=True,
                                capture_output=True, text=True, timeout=10)
        self.assertIn('streaming_sse41_exercised=0', result.stdout)

    def test_sse41_local_function_keeps_four_streaming_loads_and_fences(self):
        if platform.machine() not in ('x86_64', 'i386', 'i686'):
            self.skipTest('x86 opcode check')
        code = subprocess.run(['objdump', '-Cd', str(self.binary)], check=True,
                              capture_output=True, text=True).stdout
        start = code.index('<f2_stream_io::read_stable_line_sse41(')
        # Find the definition rather than an earlier call-site reference.
        while not code[start:code.index('\n', start)].endswith('>:'):
            start = code.index('<f2_stream_io::read_stable_line_sse41(', start + 1)
        function = code[start:code.index('\n\n', start)]
        self.assertEqual(function.count('movntdqa'), 4)
        self.assertGreaterEqual(function.count('mfence'), 2)


if __name__ == '__main__':
    unittest.main()
