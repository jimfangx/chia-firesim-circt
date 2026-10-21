#include "fame_test.h"
#include <string.h>

static uint32_t crc32(const uint8_t *p,size_t n)
{
  uint32_t c=UINT32_MAX;
  while(n--) {
    c^=*p++;
    for(unsigned bit=0;bit<8;++bit) c=(c>>1)^(UINT32_C(0xedb88320)&(0u-(c&1)));
  }
  return ~c;
}

static uint32_t rotate(uint32_t x,unsigned n) { return (x>>n)|(x<<(32-n)); }
static const uint32_t constants[64]={
  0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
  0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
  0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
  0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
  0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
  0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
  0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
  0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2
};

/* Two-block implementation, sufficient for the three independent known vectors. */
static void sha256(const char *message,size_t length,uint32_t output[8])
{
  uint8_t padded[128]={0}; uint32_t w[64];
  static const uint32_t initial[]={0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,
                                  0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
  memcpy(output,initial,sizeof(initial)); memcpy(padded,message,length); padded[length]=128;
  size_t blocks=(length+9+63)/64;
  uint64_t bits=length*8;
  for(unsigned i=0;i<8;++i) padded[blocks*64-1-i]=(uint8_t)(bits>>(8*i));
  for(size_t block=0;block<blocks;++block) {
    for(unsigned i=0;i<16;++i) {
      const uint8_t *p=padded+block*64+4*i;
      w[i]=((uint32_t)p[0]<<24)|((uint32_t)p[1]<<16)|((uint32_t)p[2]<<8)|p[3];
    }
    for(unsigned i=16;i<64;++i) {
      uint32_t a=w[i-15],b=w[i-2];
      w[i]=w[i-16]+(rotate(a,7)^rotate(a,18)^(a>>3))+w[i-7]+(rotate(b,17)^rotate(b,19)^(b>>10));
    }
    uint32_t a=output[0],b=output[1],c=output[2],d=output[3];
    uint32_t e=output[4],f=output[5],g=output[6],h=output[7];
    for(unsigned i=0;i<64;++i) {
      uint32_t t1=h+(rotate(e,6)^rotate(e,11)^rotate(e,25))+((e&f)^(~e&g))+constants[i]+w[i];
      uint32_t t2=(rotate(a,2)^rotate(a,13)^rotate(a,22))+((a&b)^(a&c)^(b&c));
      h=g;g=f;f=e;e=d+t1;d=c;c=b;b=a;a=t1+t2;
    }
    output[0]+=a;output[1]+=b;output[2]+=c;output[3]+=d;
    output[4]+=e;output[5]+=f;output[6]+=g;output[7]+=h;
  }
}

static volatile uint32_t next[1024] __attribute__((aligned(4096)));
static volatile int32_t matrix_a[16][16],matrix_b[16][16];

void fame_workloads(void)
{
  FAME_CHECK("CRC32 independent known vector",crc32((const uint8_t *)"123456789",9),0xcbf43926);
  FAME_CHECK("CRC32 empty",crc32((const uint8_t *)"",0),0);
  static const struct { const char *s; uint32_t h[8]; } vectors[]={
    {"",{0xe3b0c442,0x98fc1c14,0x9afbf4c8,0x996fb924,0x27ae41e4,0x649b934c,0xa495991b,0x7852b855}},
    {"abc",{0xba7816bf,0x8f01cfea,0x414140de,0x5dae2223,0xb00361a3,0x96177a9c,0xb410ff61,0xf20015ad}},
    {"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq",{0x248d6a61,0xd20638b8,0xe5c02693,0x0c3e6039,0xa33ce459,0x64ff2167,0xf6ecedd4,0x19db06c1}}
  };
  uint32_t digest[8];
  for(unsigned v=0;v<FAME_ARRAY_SIZE(vectors);++v) {
    sha256(vectors[v].s,strlen(vectors[v].s),digest);
    for(unsigned i=0;i<8;++i) FAME_CHECK("SHA256 known vector",digest[i],vectors[v].h[i]);
  }
  for(unsigned i=0;i<16;++i) for(unsigned j=0;j<16;++j) {
    matrix_a[i][j]=(int32_t)i-(int32_t)j;
    matrix_b[i][j]=(int32_t)i+2*(int32_t)j;
  }
  for(unsigned i=0;i<16;++i) for(unsigned j=0;j<16;++j) {
    int64_t sum=0;
    for(unsigned k=0;k<16;++k) sum+=(int64_t)matrix_a[i][k]*matrix_b[k][j];
    /* Independent closed form: sum(k)=120, sum(k*k)=1240. */
    int64_t expected=(int64_t)i*120+32*(int64_t)i*j-1240-240*(int64_t)j;
    FAME_CHECK("matrix multiply",(uint64_t)sum,(uint64_t)expected);
  }
  for(unsigned i=0;i<1024;++i) next[i]=(i+37)&1023;
  unsigned p=0;
  for(unsigned i=1;i<=8192;++i) {
    p=next[p]; FAME_CHECK("dependent pointer chase",p,(i*37)&1023);
  }
  /* Repeated UART/tohost writes in the runner give observable ordered epochs. */
  uint32_t sorted[256];
  for(unsigned i=0;i<256;++i) sorted[i]=(i*149)&255;
  for(unsigned i=1;i<256;++i) {
    uint32_t x=sorted[i];unsigned j=i;
    while(j && sorted[j-1]>x) { sorted[j]=sorted[j-1];--j; }
    sorted[j]=x;
  }
  for(unsigned i=0;i<256;++i) FAME_CHECK("insertion sort permutation",sorted[i],i);
}
