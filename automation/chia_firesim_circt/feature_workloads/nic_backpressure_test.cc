// Actual production NIC and CPU-managed input stream; physical IO alone is a provider.
#include "bridges/simplenic.h"
#include "bridges/cpu_managed_stream.h"
#include "core/config.h"
#include <algorithm>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <stdexcept>
#include <sys/mman.h>
#include <unistd.h>
simif_t::simif_t(const TargetConfig &c):config(c){}
simif_t::~simif_t()=default;
CPUManagedStreamIO &simif_t::get_cpu_managed_stream_io(){throw std::runtime_error("unexpected IO");}
FPGAManagedStreamIO &simif_t::get_fpga_managed_stream_io(){throw std::runtime_error("unexpected IO");}
int simif_t::run(simulation_t &){throw std::runtime_error("unexpected run");}
struct Physical:simif_t {
  static const TargetConfig target;
  Physical():simif_t(target){}
  void write(size_t,uint32_t)override{}
  uint32_t read(size_t)override{throw std::runtime_error("unexpected read");}
};
const TargetConfig Physical::target{{1,32,32},{1,64,512},1,std::nullopt,std::nullopt,{0,0},"nic-backpressure-test"};
constexpr size_t bytes=58560;
struct IO:CPUManagedStreamIO {
  uint32_t count[2]={0,0};
  std::vector<std::vector<unsigned char>> writes[2];
  uint32_t mmio_read(size_t addr)override{assert(addr==600||addr==604);return count[(addr-600)/4];}
  size_t cpu_managed_axi4_write(size_t addr,const char *data,size_t n)override{
    assert(addr==0||addr==262144);unsigned slot=addr?1:0;
    assert(n==bytes);writes[slot].emplace_back(data,data+n);count[slot]+=n/64;return n;
  }
  size_t cpu_managed_axi4_read(size_t,char *,size_t)override{abort();}
  uint64_t get_beat_bytes()const override{return 64;}
};
struct Out:FPGAToCPUStreamDriver {
  unsigned pulls=0,copies=0;bool short_pull=false;std::vector<unsigned char> pending;
  void init()override{} void flush()override{}
  size_t pull(void *dest,size_t n,size_t minimum)override{
    ++pulls;if(pending.empty())return 0;
    assert(n==bytes&&minimum==bytes);memcpy(dest,pending.data(),n);pending.clear();++copies;return short_pull?n/2:n;
  }
};
struct Engine:StreamEngine {
  Out *out[2];CPUManagedStreams::CPUToFPGADriver *in[2];
  explicit Engine(IO &io){for(unsigned i=0;i<2;++i){
    auto t=std::make_unique<Out>();out[i]=t.get();fpga_to_cpu_streams.push_back(std::move(t));
    auto f=std::make_unique<CPUManagedStreams::CPUToFPGADriver>(CPUManagedStreams::StreamParameters("nic",262144*i,600+4*i,3072,64),io);
    in[i]=f.get();cpu_to_fpga_streams.push_back(std::move(f));
  }}
};
struct Short:CPUToFPGAStreamDriver {
  unsigned calls=0;void init()override{} void flush()override{}
  size_t push(void *,size_t n,size_t)override{return ++calls==1?n:64;}
};
struct ShortEngine:StreamEngine {
  Out *out;ShortEngine(){auto t=std::make_unique<Out>();out=t.get();fpga_to_cpu_streams.push_back(std::move(t));cpu_to_fpga_streams.push_back(std::make_unique<Short>());}
};
int main(int argc,char **argv){
  if(argc==2&&std::string(argv[1])=="--short-pull"){
    Physical sim;ShortEngine engine;
    simplenic_t loop(sim,engine,{0,4,8,12,16,20},0,{"+nic-loopback0","+linklatency0=6405"},0,3072,0,3072);
    loop.init();engine.out->pending.assign(bytes,0);engine.out->short_pull=true;loop.tick();return 2;
  }
  if(argc==2&&std::string(argv[1])=="--short-write"){
    Physical sim;ShortEngine engine;
    simplenic_t loop(sim,engine,{0,4,8,12,16,20},0,{"+nic-loopback0","+linklatency0=6405"},0,3072,0,3072);
    loop.init();engine.out->pending.assign(bytes,0);loop.tick();return 2;
  }
  assert(argc==1);
  IO io;Engine engine(io);Physical sim;
  const auto port="chia_pending_"+std::to_string(getpid());
  std::vector<std::string> names;unsigned char *mapping[2][2];
  for(unsigned dir=0;dir<2;++dir)for(unsigned round=0;round<2;++round){
    auto name="/port_"+std::string(dir?"stn":"nts")+port+"_"+std::to_string(round);names.push_back(name);
    int fd=shm_open(name.c_str(),O_RDWR|O_CREAT|O_EXCL,0600);assert(fd>=0);assert(!ftruncate(fd,bytes+1));
    auto p=mmap(nullptr,bytes+1,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);assert(p!=MAP_FAILED);close(fd);
    mapping[dir][round]=static_cast<unsigned char *>(p);memset(p,0,bytes+1);
  }
  {
    simplenic_t normal(sim,engine,{0,4,8,12,16,20},0,{"+linklatency0=6405","+shmemportname0="+port},0,3072,0,3072);
    simplenic_t loop(sim,engine,{100,104,108,112,116,120},1,{"+nic-loopback1","+linklatency1=6405"},1,3072,1,3072);
    normal.init();loop.init();assert(io.count[0]==915&&io.count[1]==915);
    std::vector<unsigned char> reply(bytes),loop_reply(bytes),outgoing(bytes);
    for(unsigned i=0;i<bytes;++i){reply[i]=(i*7+11)&255;loop_reply[i]=(i*13+7)&255;outgoing[i]=(i*19+3)&255;}
    engine.out[0]->pending=outgoing;engine.out[1]->pending=loop_reply;
    // The normal peer has not responded. Its sink has space, but no bytes
    // may be pushed yet, and the other widget must still receive its tick.
    io.count[0]=2157;io.count[1]=3072;
    normal.tick();loop.tick();
    const auto pulls0=engine.out[0]->pulls,pulls1=engine.out[1]->pulls;
    for(unsigned retry=0;retry<5;++retry){normal.tick();loop.tick();}
    assert(engine.out[0]->pulls==pulls0&&engine.out[1]->pulls==pulls1);
    assert(engine.out[0]->copies==1&&engine.out[1]->copies==1);
    assert(io.writes[0].size()==1&&io.writes[1].size()==1);
    assert(mapping[1][0][bytes]==0&&mapping[0][0][bytes]==1);
    assert(memcmp(mapping[0][0],outgoing.data(),bytes)==0&&mapping[0][1][bytes]==0);
    normal.finish(); // waiting_peer=1, pending_reply=0 must remain visible.
    // One independent widget can resume while the normal peer is delayed.
    io.count[1]=2157;loop.tick();assert(io.writes[1].size()==2&&io.writes[1].back()==loop_reply);
    normal.tick();assert(io.writes[0].size()==1&&mapping[1][0][bytes]==0);
    assert(engine.out[0]->pulls==pulls0&&mapping[0][0][bytes]==1);
    // A real peer consumes the outgoing publication and releases one reply.
    // Capacity now blocks acceptance: preserve that reply across more ticks,
    // without republishing the already consumed outgoing batch.
    __atomic_store_n(mapping[0][0]+bytes,0,__ATOMIC_RELEASE);
    memcpy(mapping[1][0],reply.data(),bytes);
    __atomic_store_n(mapping[1][0]+bytes,1,__ATOMIC_RELEASE);
    io.count[0]=2158;
    for(unsigned retry=0;retry<5;++retry){
      normal.tick();
      assert(io.writes[0].size()==1&&engine.out[0]->pulls==pulls0);
      assert(mapping[1][0][bytes]==1&&mapping[0][0][bytes]==0);
      assert(memcmp(mapping[1][0],reply.data(),bytes)==0);
      assert(memcmp(mapping[0][0],outgoing.data(),bytes)==0);
    }
    normal.finish(); // waiting_peer=0, pending_reply=1 until complete push.
    io.count[0]=2157;normal.tick();assert(io.writes[0].size()==2&&io.writes[0].back()==reply);
    assert(mapping[1][0][bytes]==0&&mapping[0][1][bytes]==0);
    // The next pending batch uses round1 and must not reuse round0.
    memcpy(mapping[1][1],loop_reply.data(),bytes);mapping[1][1][bytes]=1;
    engine.out[0]->pending.assign(bytes,0);io.count[0]=0;normal.tick();
    assert(io.writes[0].size()==3&&io.writes[0].back()==loop_reply&&mapping[1][1][bytes]==0);
    for(unsigned i=0;i<2;++i){
      io.count[i]=3073;bool rejected=false;
      try{engine.in[i]->push(reply.data(),bytes,bytes);}catch(const std::runtime_error &){rejected=true;}
      assert(rejected);
    }
  }
  for(auto &row:mapping)for(auto p:row)assert(!munmap(p,bytes+1));
  for(auto &name:names)assert(!shm_unlink(name.c_str()));
  puts("PASS actual NIC/CPUToFPGADriver: delayed peer yields scheduler without repull/publication, repeated capacity stalls retain same peer reply, exactly-once full batch, independent widgets, loopback/normal, round advancement, invalid occupancy rejection");
}
