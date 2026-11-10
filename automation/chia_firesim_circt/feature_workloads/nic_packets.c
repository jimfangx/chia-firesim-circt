#define _DEFAULT_SOURCE
#include "nic_protocol.h"
#include <arpa/inet.h>
#include <errno.h>
#include <linux/if_packet.h>
#include <net/ethernet.h>
#include <net/if.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

static void die(const char *message) { perror(message); exit(1); }
static unsigned number(const char *text) {
  char *end;
  errno = 0;
  unsigned long value = strtoul(text, &end, 10);
  if (errno || !*text || *end || value > 65535) { fprintf(stderr, "invalid number: %s\n", text); exit(2); }
  return value;
}
static double now(void) {
  struct timespec ts;
  if (clock_gettime(CLOCK_MONOTONIC, &ts)) die("clock_gettime");
  return ts.tv_sec + ts.tv_nsec * 1e-9;
}
int main(int argc, char **argv) {
  if (argc != 5) {
    fprintf(stderr, "usage: nic_packets INTERFACE WIDGET normal|loopback PACKETS\n"); return 2;
  }
  const char *interface = argv[1];
  unsigned widget = number(argv[2]), packets = number(argv[4]);
  unsigned direction = strcmp(argv[3], "normal") == 0;
  if ((!direction && strcmp(argv[3], "loopback")) || !packets || packets % NIC_BATCH) return 2;
  unsigned index = if_nametoindex(interface);
  if (!index) die("if_nametoindex");
  // Explicitly bring up this real target NIC, without needing DHCP or an IP stack.
  // AF_PACKET bind on a down device latches ENETDOWN in the socket; raising the
  // interface later does not clear that error before the first send.
  int control = socket(AF_INET, SOCK_DGRAM, 0);
  if (control < 0) die("socket control");
  struct ifreq request = {0};
  if (strlen(interface) >= IFNAMSIZ) return 2;
  strcpy(request.ifr_name, interface);
  if (ioctl(control, SIOCGIFFLAGS, &request)) die("SIOCGIFFLAGS");
  request.ifr_flags |= IFF_UP;
  if (ioctl(control, SIOCSIFFLAGS, &request)) die("SIOCSIFFLAGS");
  close(control);
  int fd = socket(AF_PACKET, SOCK_RAW, htons(NIC_ETHERTYPE));
  if (fd < 0) die("socket AF_PACKET (root is required)");
  struct sockaddr_ll address = {.sll_family = AF_PACKET, .sll_protocol = htons(NIC_ETHERTYPE), .sll_ifindex = index};
  if (bind(fd, (struct sockaddr *)&address, sizeof(address))) die("bind");
  unsigned char tx[NIC_FRAME_MAX], rx[2048], expected[NIC_FRAME_MAX];
  unsigned received = 0;
  size_t bytes = 0;
  double deadline = now() + 120;
  for (unsigned base = 0; base < packets; base += NIC_BATCH) {
    // Burst into the real DMA ring, then leave receive work pending briefly.
    // This stresses buffering; RTL supporting tests establish precise stalls.
    for (unsigned sequence = base; sequence < base + NIC_BATCH; sequence++) {
      unsigned length = nic_frame(tx, widget, sequence, 0);
      if (send(fd, tx, length, 0) != (ssize_t)length) die("send");
    }
    struct timespec delay = {.tv_nsec = 1000000};
    nanosleep(&delay, NULL);
    while (received < base + NIC_BATCH) {
      if (now() > deadline) { fprintf(stderr, "NIC timeout widget=%u received=%u\n", widget, received); return 1; }
      struct pollfd poller = {.fd = fd, .events = POLLIN};
      int status = poll(&poller, 1, 1000);
      if (status < 0 && errno == EINTR) continue;
      if (status < 0) die("poll");
      if (!status) continue;
      struct sockaddr_ll origin;
      socklen_t length = sizeof(origin);
      ssize_t count = recvfrom(fd, rx, sizeof(rx), 0, (struct sockaddr *)&origin, &length);
      if (count < 0) die("recvfrom");
      if (origin.sll_pkttype == PACKET_OUTGOING) continue;
      // An experimental frame for another widget must fail, not be ignored.
      if (count < 38 || memcmp(rx + 14, "CIRCTNIC", 8)) continue;
      unsigned expected_length = nic_frame(expected, widget, received, direction);
      if (count != (ssize_t)expected_length || memcmp(rx, expected, expected_length)) {
        fprintf(stderr, "NIC frame mismatch widget=%u sequence=%u received_bytes=%zd\n", widget, received, count); return 1;
      }
      printf("CIRCT_NIC_PACKET {\"widget\":%u,\"sequence\":%u,\"direction\":%u,\"bytes\":%u,\"fnv1a\":%u}\n",
             widget, received, direction, expected_length, nic_hash(rx, expected_length));
      received++;
      bytes += expected_length;
    }
  }
  printf("CIRCT_NIC_RESULT {\"widget\":%u,\"mode\":\"%s\",\"interface\":\"%s\",\"packets\":%u,\"bytes\":%zu,\"passed\":true}\n",
         widget, argv[3], interface, received, bytes);
  close(fd);
  return 0;
}
