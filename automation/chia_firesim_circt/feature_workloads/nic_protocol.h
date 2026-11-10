#ifndef CHIA_NIC_PROTOCOL_H
#define CHIA_NIC_PROTOCOL_H
#include <stdint.h>
#include <stddef.h>
#include <string.h>

// IceNIC prepends NET_IP_ALIGN=2 bytes on its wire. Ethernet lengths below
// therefore give whole 64-bit flits including that padding; CPU token keep
// masks are always full. EtherType 0x88b5 is local experimental use.
#define NIC_ETHERTYPE 0x88b5
#define NIC_FRAME_MAX 1024
#define NIC_DEFAULT_PACKETS 128
#define NIC_BATCH 8
static inline unsigned nic_length(unsigned sequence) {
  static const unsigned sizes[] = {126, 254, 510, 1022};
  return sizes[sequence % 4];
}
static inline void nic_put32(unsigned char *p, uint32_t value) {
  for (unsigned i = 0; i < 4; i++) p[i] = (value >> (8 * i)) & 255;
}
static inline uint32_t nic_get32(const unsigned char *p) {
  return (uint32_t)p[0] | (uint32_t)p[1] << 8 |
         (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}
static inline uint32_t nic_hash(const unsigned char *p, size_t length) {
  uint32_t hash = 2166136261u;
  for (size_t i = 0; i < length; i++) hash = (hash ^ p[i]) * 16777619u;
  return hash;
}
static inline unsigned nic_frame(unsigned char *frame, unsigned widget,
                                 unsigned sequence, unsigned direction) {
  unsigned length = nic_length(sequence);
  memset(frame, 0, length);
  memset(frame, 255, 6); // broadcast also works with compile-time macAddr = 0
  frame[6] = 2;
  frame[9] = direction;
  frame[10] = (widget >> 8) & 255;
  frame[11] = widget & 255;
  frame[12] = NIC_ETHERTYPE >> 8;
  frame[13] = NIC_ETHERTYPE & 255;
  memcpy(frame + 14, "CIRCTNIC", 8);
  nic_put32(frame + 22, widget);
  nic_put32(frame + 26, sequence);
  nic_put32(frame + 30, direction);
  nic_put32(frame + 34, length);
  for (unsigned i = 38; i < length; i++)
    frame[i] = (unsigned char)((i * 29 + sequence * 17 + widget * 43 + direction * 101) & 255);
  return length;
}
#endif
