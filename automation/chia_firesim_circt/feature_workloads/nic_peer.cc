// Host-connected FireSim normal NIC peer. Uses the actual simplenic_t double
// buffer shared-memory protocol; it does not replace the simulator or driver.
#include "nic_protocol.h"
#include <atomic>
#include <chrono>
#include <cinttypes>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <deque>
#include <fcntl.h>
#include <stdexcept>
#include <string>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

static volatile sig_atomic_t stopping;
static void stop(int) { stopping = 1; }
static void require(bool value, const std::string &message) {
  if (!value) throw std::runtime_error(message);
}
struct Mapping {
  std::string name;
  unsigned char *data = nullptr;
  size_t length = 0;
  Mapping(const std::string &n, size_t size) : name(n), length(size) {
    int fd = shm_open(name.c_str(), O_RDWR | O_CREAT | O_EXCL, 0600);
    require(fd >= 0, "shared-memory name already used or cannot be created: " + name);
    if (ftruncate(fd, length)) { close(fd); shm_unlink(name.c_str()); throw std::runtime_error("ftruncate failed"); }
    void *p = mmap(nullptr, length, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);
    if (p == MAP_FAILED) { shm_unlink(name.c_str()); throw std::runtime_error("mmap failed"); }
    data = static_cast<unsigned char *>(p);
    memset(data, 0, length);
  }
  Mapping(const Mapping &) = delete;
  Mapping &operator=(const Mapping &) = delete;
  Mapping(Mapping &&other) noexcept : name(std::move(other.name)), data(other.data), length(other.length) { other.data = nullptr; }
  ~Mapping() { if (data) { munmap(data, length); shm_unlink(name.c_str()); } }
};
struct Flit { uint64_t value; bool last; };
struct Peer {
  unsigned widget, next = 0, replies = 0, round = 0;
  size_t bytes, rounds = 0;
  static constexpr uint64_t sequence_mask = (uint64_t(1) << 43) - 1;
  bool sequence_observed = false;
  uint64_t first_sequence = 0, last_sequence = 0, token_count = 0;
  std::vector<Mapping> incoming, outgoing;
  std::vector<unsigned char> packet;
  std::deque<Flit> pending;
  Peer(unsigned id, const std::string &port, size_t size) : widget(id), bytes(size) {
    for (unsigned i = 0; i < 2; i++) {
      outgoing.emplace_back("/port_nts" + port + "_" + std::to_string(i), bytes + 1);
      incoming.emplace_back("/port_stn" + port + "_" + std::to_string(i), bytes + 1);
    }
  }
  Peer(const Peer &) = delete;
  Peer &operator=(const Peer &) = delete;
  Peer(Peer &&) noexcept = default;
  void accept(unsigned expected_packets) {
    if (packet.size() >= 40 && packet[14] == (NIC_ETHERTYPE >> 8) && packet[15] == (NIC_ETHERTYPE & 255) &&
        memcmp(packet.data() + 16, "CIRCTNIC", 8) == 0) {
      unsigned char expected[NIC_FRAME_MAX], reply[NIC_FRAME_MAX + 2] = {0};
      unsigned length = nic_frame(expected, widget, next, 0);
      require(next < expected_packets && packet.size() == length + 2 &&
              memcmp(packet.data() + 2, expected, length) == 0,
              "host request payload/order/widget mismatch for widget " + std::to_string(widget));
      printf("CIRCT_NIC_HOST {\"event\":\"request\",\"widget\":%u,\"sequence\":%u,\"direction\":0,\"bytes\":%u,\"fnv1a\":%u}\n",
             widget, next, length, nic_hash(expected, length));
      nic_frame(reply + 2, widget, next, 1);
      for (unsigned offset = 0; offset < length + 2; offset += 8) {
        uint64_t word;
        memcpy(&word, reply + offset, 8);
        pending.push_back({word, offset + 8 == length + 2});
      }
      printf("CIRCT_NIC_HOST {\"event\":\"reply\",\"widget\":%u,\"sequence\":%u,\"direction\":1,\"bytes\":%u,\"fnv1a\":%u}\n",
             widget, next, length, nic_hash(reply + 2, length));
      ++next;
    }
    packet.clear();
  }
  bool tick(unsigned expected_packets) {
    auto &tx = outgoing[round];
    auto &rx = incoming[round];
    // The producer publishes payload before the status byte. Each side owns
    // its direction's flag and waits for the previous consumption to finish.
    if (!__atomic_load_n(tx.data + bytes, __ATOMIC_ACQUIRE) ||
        __atomic_load_n(rx.data + bytes, __ATOMIC_ACQUIRE)) return false;
    for (size_t offset = 0; offset < bytes; offset += 64) {
      uint64_t control;
      memcpy(&control, tx.data + offset, 8);
      // SFC/native BIGToken.pad counts each packed group of seven target
      // tokens, including idle groups. Check every beat so PCI duplication or
      // loss is observable before Linux has emitted any network packets.
      const uint64_t sequence = control & sequence_mask;
      const uint64_t expected = (last_sequence + 1) & sequence_mask;
      if (sequence_observed && sequence != expected) {
        fprintf(stderr, "CIRCT_NIC_TOKEN_SEQUENCE_FAILURE {\"widget\":%u,\"expected\":%" PRIu64
                ",\"actual\":%" PRIu64 ",\"token_count\":%" PRIu64
                ",\"round\":%zu,\"beat\":%zu,\"first\":%" PRIu64 ",\"last\":%" PRIu64 "}\n",
                widget, expected, sequence, token_count, rounds, offset / 64, first_sequence, last_sequence);
        throw std::runtime_error("NIC token sequence mismatch for widget " + std::to_string(widget) +
                                 ": expected " + std::to_string(expected) + ", actual " + std::to_string(sequence));
      }
      if (!sequence_observed) first_sequence = sequence;
      sequence_observed = true;
      last_sequence = sequence;
      ++token_count;
      for (unsigned slot = 0; slot < 7; slot++) {
        unsigned bits = (control >> (43 + slot * 3)) & 7;
        if (bits & 1) {
          require(packet.size() < 16384, "unterminated/oversized NIC packet");
          auto data = tx.data + offset + (slot + 1) * 8;
          packet.insert(packet.end(), data, data + 8);
          if (bits & 4) accept(expected_packets);
        }
      }
    }
    // ready remains asserted. The target vonly interface has no dynamic
    // incoming ready, so whole bursts are bounded by the real target RX ring.
    memset(rx.data, 0, bytes);
    for (size_t offset = 0; offset < bytes; offset += 64) {
      uint64_t control = 0;
      for (unsigned slot = 0; slot < 7; slot++) {
        unsigned bits = 2;
        if (!pending.empty()) {
          auto flit = pending.front(); pending.pop_front();
          bits |= 1 | (flit.last ? 4 : 0);
          memcpy(rx.data + offset + (slot + 1) * 8, &flit.value, 8);
          if (flit.last) ++replies;
        }
        control |= (uint64_t)bits << (43 + slot * 3);
      }
      memcpy(rx.data + offset, &control, 8);
    }
    __atomic_store_n(tx.data + bytes, 0, __ATOMIC_RELEASE);
    __atomic_store_n(rx.data + bytes, 1, __ATOMIC_RELEASE);
    round ^= 1;
    ++rounds;
    fflush(stdout);
    return true;
  }
};
int main(int argc, char **argv) {
  try {
    require(argc >= 6, "usage: nic_peer LINKLATENCY PACKETS TIMEOUT_SECONDS STOP_FILE WIDGET:SHMEM_PORT...");
    unsigned latency = std::stoul(argv[1]), packets = std::stoul(argv[2]), timeout = std::stoul(argv[3]);
    require(latency && latency % 7 == 0 && latency / 7 <= 3072 && packets && packets % NIC_BATCH == 0 && timeout,
            "invalid latency, packet count, or timeout");
    const std::string stop_file = argv[4];
    std::vector<Peer> peers;
    for (int i = 5; i < argc; i++) {
      std::string text = argv[i];
      auto colon = text.find(':');
      require(colon != std::string::npos && colon + 1 < text.size(), "expected WIDGET:SHMEM_PORT");
      auto port = text.substr(colon + 1);
      require(port.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-") == std::string::npos,
              "unsafe shared-memory port");
      peers.emplace_back(std::stoul(text.substr(0, colon)), port, latency / 7 * 64);
    }
    signal(SIGTERM, stop); signal(SIGINT, stop);
    printf("CIRCT_NIC_PEER_READY {\"widgets\":%zu,\"linklatency\":%u}\n", peers.size(), latency); fflush(stdout);
    auto started = std::chrono::steady_clock::now();
    while (!stopping && access(stop_file.c_str(), F_OK)) {
      bool progress = false;
      for (auto &peer : peers) progress |= peer.tick(packets);
      if (std::chrono::steady_clock::now() - started > std::chrono::seconds(timeout))
        throw std::runtime_error("NIC peer wall-clock timeout");
      if (!progress) usleep(50);
    }
    bool complete = true;
    for (auto &peer : peers) {
      bool passed = peer.next == packets && peer.replies == packets && peer.pending.empty() && peer.packet.empty();
      complete &= passed;
      printf("CIRCT_NIC_PEER_RESULT {\"widget\":%u,\"requests\":%u,\"replies\":%u,\"rounds\":%zu,"
             "\"token_sequence_observed\":%s,\"token_first\":%s,\"token_last\":%s,\"token_count\":%" PRIu64 ",\"passed\":%s}\n",
             peer.widget, peer.next, peer.replies, peer.rounds, peer.sequence_observed ? "true" : "false",
             peer.sequence_observed ? std::to_string(peer.first_sequence).c_str() : "null",
             peer.sequence_observed ? std::to_string(peer.last_sequence).c_str() : "null",
             peer.token_count, passed ? "true" : "false");
    }
    return complete ? 0 : 1;
  } catch (const std::exception &error) {
    fprintf(stderr, "CIRCT_NIC_PEER_FAILURE: %s\n", error.what()); return 1;
  }
}
