// See LICENSE for license details

#ifndef __SIMPLENIC_H
#define __SIMPLENIC_H

#include <vector>

#include "core/bridge_driver.h"
#include "core/stream_engine.h"

// TODO this should not be hardcoded here.
#define MAX_BANDWIDTH 200

struct SIMPLENICBRIDGEMODULE_struct {
  uint64_t macaddr_upper;
  uint64_t macaddr_lower;
  uint64_t rlimit_settings;
  uint64_t pause_threshold;
  uint64_t pause_times;
  uint64_t done;
};

class simplenic_t final : public streaming_bridge_driver_t {
public:
  /// The identifier for the bridge type used for casts.
  static char KIND;

  simplenic_t(simif_t &sim,
              StreamEngine &stream,
              const SIMPLENICBRIDGEMODULE_struct &addrs,
              int simplenicno,
              const std::vector<std::string> &args,
              int stream_to_cpu_idx,
              int stream_to_cpu_depth,
              int stream_from_cpu_idx,
              int stream_from_cpu_depth);
  ~simplenic_t() override;

  void init() override;
  void tick() override;
  void finish() override;

private:
  const SIMPLENICBRIDGEMODULE_struct mmio_addrs;
  uint64_t mac_lendian;
  char *pcis_read_bufs[2];
  char *pcis_write_bufs[2];
  int rlimit_inc, rlimit_period, rlimit_size;
  int pause_threshold, pause_quanta, pause_refresh;

  // link latency in cycles
  // assuming 3.2 GHz, this number / 3.2 = link latency in ns
  // e.g. setting this to 6405 gives you 6405/3.2 = 2001.5625 ns latency
  // IMPORTANT: this must be a multiple of 7
  int LINKLATENCY;
  FILE *niclog;
  bool loopback;

  // checking for token loss
  int currentround = 0;

  // A published outgoing batch remains owned while the peer responds. Poll
  // once per tick so another NIC, TSI and the heartbeat can still run.
  bool waiting_for_peer = false;
  uint64_t pending_peer_wait_ticks = 0;
  // A completed peer reply remains owned until its entire stream batch is
  // accepted. push() is nonblocking and may return zero under backpressure.
  bool pending_to_fpga = false;
  uint64_t pending_push_retries = 0;
  uint64_t completed_rounds = 0;
  unsigned backpressure_logs = 0;
  bool pending_backpressure_logged = false;

  // only for TOKENVERIFY
  const int stream_to_cpu_idx;
  const int stream_from_cpu_idx;
};

#endif // __SIMPLENIC_H
