"""Exercise the production F2 ordered-line writer with a fragmented transport."""
from pathlib import Path
import subprocess
import tempfile
import unittest


SOURCE = r'''
#include "core/f2_stream_io.h"
#include <array>
#include <cassert>
#include <cstring>
#include <vector>
int main() {
  std::array<char,58560> batch{};
  for (size_t i=0;i<batch.size();++i) batch[i]=char(i*37+11);
  const auto original=batch;
  std::vector<char> delivered;
  unsigned calls=0;
  auto transport=[&](size_t address,const char *data,size_t n) {
    assert(address==0x40000+calls*64 && n==64);
    // A line may arrive as arbitrary fragments, with intact payload lanes.
    for (size_t offset=0;offset<n;offset+=4)
      delivered.insert(delivered.end(),data+offset,data+offset+4);
    ++calls;return n;
  };
  assert(f2_stream_io::write_lines(0x40000,batch.data(),batch.size(),transport)==batch.size());
  assert(calls==915 && delivered.size()==batch.size());
  assert(!memcmp(batch.data(),delivered.data(),batch.size()) && batch==original);
  assert(f2_stream_io::write_lines(0x40000,batch.data(),0,transport)==0 && calls==915);
  for (const auto bad: {size_t(1),size_t(63),size_t(65)}) {
    try {f2_stream_io::write_lines(0,batch.data(),bad,transport);assert(false);}
    catch(const std::invalid_argument&) {}
    try {f2_stream_io::write_lines(bad,batch.data(),64,transport);assert(false);}
    catch(const std::invalid_argument&) {}
  }
  assert(calls==915);
  for (size_t short_size: {size_t(0),size_t(32)}) {
    unsigned attempted=0;
    try {
      f2_stream_io::write_lines(0,batch.data(),128,[&](size_t,const char*,size_t){++attempted;return short_size;});
      assert(false);
    } catch(const std::runtime_error&) {}
    assert(attempted==1 && batch==original);
  }
}
'''


class F2StreamWriteTest(unittest.TestCase):
    def test_production_writer_order_geometry_and_incomplete_transfers(self):
        repo = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as tmp:
            source, binary = Path(tmp)/'test.cc', Path(tmp)/'test'
            source.write_text(SOURCE)
            subprocess.run([str(repo/'.conda-env/bin/c++'), '-O2', '-std=c++17',
                            '-I', str(repo/'sims/firesim/sim/midas/src/main/cc'),
                            str(source), '-o', str(binary)], check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True, timeout=10)
            code = subprocess.run(['objdump', '-d', str(binary)], check=True,
                                  capture_output=True, text=True).stdout
            self.assertIn('mfence', code)


if __name__ == '__main__':
    unittest.main()
