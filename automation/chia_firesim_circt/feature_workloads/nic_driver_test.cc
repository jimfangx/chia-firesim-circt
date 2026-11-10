// Execute the production shared NIC driver with real core/stream implementations.
// Only physical MMIO and stream data providers are replaced for this host test.
#include "bridges/simplenic.h"
#include "core/config.h"
#include <algorithm>
#include <cassert>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <dirent.h>
#include <fcntl.h>
#include <stdexcept>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

simif_t::simif_t(const TargetConfig &c):config(c){}
simif_t::~simif_t()=default;
CPUManagedStreamIO &simif_t::get_cpu_managed_stream_io(){throw std::runtime_error("unexpected CPU MMIO");}
FPGAManagedStreamIO &simif_t::get_fpga_managed_stream_io(){throw std::runtime_error("unexpected FPGA MMIO");}
int simif_t::run(simulation_t &){throw std::runtime_error("unexpected simulator run");}
struct Physical final:simif_t {
  static const TargetConfig target;
  std::vector<std::pair<size_t,uint32_t>> writes;
  Physical():simif_t(target){}
  void write(size_t address,uint32_t value)override {writes.emplace_back(address,value);}
  uint32_t read(size_t)override {throw std::runtime_error("unexpected MMIO read");}
};
const TargetConfig Physical::target{{1,32,32},{1,64,512},1,std::nullopt,std::nullopt,{0,0},"nic-driver-test"};
struct ToCPU final:FPGAToCPUStreamDriver {
  std::vector<unsigned char> pending;
  void init()override{}
  void flush()override{}
  size_t pull(void *dest,size_t bytes,size_t minimum)override {
    if(pending.empty())return 0;
    assert(bytes==pending.size()&&minimum==pending.size());
    memcpy(dest,pending.data(),bytes);pending.clear();return bytes;
  }
};
struct FromCPU final:CPUToFPGAStreamDriver {
  std::vector<std::vector<unsigned char>> transfers;
  void init()override{}
  void flush()override{}
  size_t push(void *source,size_t bytes,size_t)override {
    auto p=static_cast<unsigned char *>(source);
    transfers.emplace_back(p,p+bytes);return bytes;
  }
};
struct Engine final:StreamEngine {
  ToCPU *to[2]; FromCPU *from[2];
  Engine(){for(unsigned i=0;i<2;i++){
    auto t=std::make_unique<ToCPU>();to[i]=t.get();fpga_to_cpu_streams.push_back(std::move(t));
    auto f=std::make_unique<FromCPU>();from[i]=f.get();cpu_to_fpga_streams.push_back(std::move(f));}}
};
static unsigned fds(){unsigned count=0;DIR *dir=opendir("/proc/self/fd");assert(dir);while(readdir(dir))++count;closedir(dir);return count;}
static int ownership_probe(const std::string &mode,const std::string &port){
  assert(port.rfind("chia_ownership_",0)==0);
  assert(port.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")==std::string::npos);
  const bool standalone=mode=="--standalone";
  assert(standalone||mode=="--existing-peer");
  Physical sim;Engine streams;
  std::vector<std::string> args{"+linklatency0=6405","+netbw0=200","+shmemportname0="+port,
                               "+macaddr0=02:00:00:00:00:00"};
  std::vector<std::string> names;
  for(const char *direction:{"nts","stn"})for(unsigned round=0;round<2;round++)
    names.push_back("/port_"+std::string(direction)+port+"_"+std::to_string(round));
  const auto before=fds();
  {
    simplenic_t normal(sim,streams,{0,4,8,12,16,20},0,args,0,3072,0,3072);
    assert(fds()==before);
    normal.init();
    assert(sim.writes.size()==5);
    assert(streams.from[0]->transfers.size()==1);
    assert(streams.from[0]->transfers[0].size()==58560);
    assert(std::all_of(streams.from[0]->transfers[0].begin(),streams.from[0]->transfers[0].end(),[](auto b){return b==0;}));
    if(!standalone){
      // Actual root driver exchanges one complete idle burst with the real
      // unprivileged peer. Physical FPGA stream IO alone is a test provider.
      streams.to[0]->pending.resize(58560,0);
      for(unsigned offset=0;offset<58560;offset+=64){
        uint64_t sequence=offset/64;
        memcpy(streams.to[0]->pending.data()+offset,&sequence,8);
      }
      // The real peer responds asynchronously; a driver tick now yields
      // while it waits so the simulator can service its other bridges.
      const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(5);
      while(streams.from[0]->transfers.size()==1){
        normal.tick();
        assert(std::chrono::steady_clock::now()<deadline);
        usleep(50);
      }
      assert(streams.from[0]->transfers.size()==2);
      auto &reply=streams.from[0]->transfers.back();
      assert(reply.size()==58560);
      uint64_t expected=0;
      for(unsigned slot=0;slot<7;slot++)expected|=uint64_t(2)<<(43+slot*3);
      for(unsigned offset=0;offset<reply.size();offset+=64){
        uint64_t control;memcpy(&control,reply.data()+offset,8);assert(control==expected);
      }
    }
  }
  assert(fds()==before);
  for(auto &name:names){
    struct stat info{};assert(!stat(("/dev/shm"+name).c_str(),&info));assert(info.st_size==58561);
    if(standalone){assert(info.st_uid==geteuid());assert(!shm_unlink(name.c_str()));}
    else assert(info.st_uid!=geteuid()); // root opens peer-owned existing objects
  }
  printf("PASS production NIC ownership probe: mode=%s uid=%u bytes=58561 descriptors_closed=true\n",mode.c_str(),getuid());
  return 0;
}
int main(int argc,char **argv){
  if(argc==3)return ownership_probe(argv[1],argv[2]);
  assert(argc==1);
  Physical sim;Engine streams;
  const auto port="chia_driver_test_"+std::to_string(getpid());
  std::vector<std::string> names;
  for(const char *direction:{"nts","stn"})for(unsigned round=0;round<2;round++){
    auto name="/port_"+std::string(direction)+port+"_"+std::to_string(round);names.push_back(name);
    int fd=shm_open(name.c_str(),O_RDWR|O_CREAT|O_EXCL,0600);assert(fd>=0);assert(!ftruncate(fd,129));
    unsigned char poison[129];memset(poison,255,sizeof(poison));assert(write(fd,poison,sizeof(poison))==129);close(fd);
  }
  const auto before=fds();
  std::vector<std::string> args{"+nic-loopback10","+linklatency1=14","+linklatency10=14","+shmemportname1="+port,
                                "+macaddr1=02:12:34:56:78:9a"};
  {
    simplenic_t normal(sim,streams,{0,4,8,12,16,20},1,args,0,3072,0,3072);
    assert(fds()==before); // all four shm_open descriptors were closed
    normal.init();
    assert(sim.writes.size()==5);
    assert(sim.writes[0]==std::make_pair(size_t(0),uint32_t(0x9a78)));
    assert(sim.writes[1]==std::make_pair(size_t(4),uint32_t(0x56341202)));
    assert(streams.from[0]->transfers.size()==1&&streams.from[1]->transfers.empty());
    assert(std::all_of(streams.from[0]->transfers[0].begin(),streams.from[0]->transfers[0].end(),[](auto b){return b==0;}));
    // +nic-loopback10 must NOT select widget1. Its incoming mapping is live;
    // the former prefix match used malloc and left our poisoned shm unchanged.
    int fd=shm_open(names[3].c_str(),O_RDONLY,0);assert(fd>=0);
    unsigned char seed[128];assert(pread(fd,seed,sizeof(seed),0)==128);close(fd);
    assert(std::all_of(std::begin(seed),std::end(seed),[](auto b){return b==0;}));
    simplenic_t loop(sim,streams,{100,104,108,112,116,120},10,args,1,3072,1,3072);
    loop.init();assert(sim.writes.size()==10);
    assert(streams.from[1]->transfers.size()==1);
    assert(std::all_of(streams.from[1]->transfers[0].begin(),streams.from[1]->transfers[0].end(),[](auto b){return b==0;}));
    std::vector<unsigned char> packet(128);for(unsigned i=0;i<128;i++)packet[i]=i*7+11;
    streams.to[1]->pending=packet;
    loop.tick();
    assert(streams.from[1]->transfers.size()==2&&streams.from[1]->transfers[1]==packet);
    assert(streams.from[0]->transfers.size()==1);
  }
  assert(fds()==before);
  for(auto &name:names)assert(!shm_unlink(name.c_str()));
  puts("PASS production NIC driver: exact runtime widget selection, strict MMIO, seeded idle tokens, stream isolation, loopback payload, descriptor cleanup");
}
