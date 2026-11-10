"""Exercise the real packet guest against down interfaces and syscall failures.

The shim models Linux's deferred ENETDOWN from binding a down packet device.
An optional private network namespace also checks the actual kernel behavior.
Neither test establishes IceNIC/FPGA packet acceptance.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


WORKLOADS = Path(__file__).resolve().parent / 'feature_workloads'
CALLS = ('if_nametoindex', 'socket', 'bind', 'ioctl', 'close', 'send',
         'poll', 'recvfrom', 'clock_gettime', 'nanosleep')
SHIM = r'''
#define _DEFAULT_SOURCE
#include "nic_protocol.h"
#include <arpa/inet.h>
#include <assert.h>
#include <errno.h>
#include <linux/if_packet.h>
#include <net/if.h>
#include <poll.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <time.h>

int nic_guest_main(int, char **);
static struct { int up, pending_error; unsigned sent, received; } devices[2] = {{1,0,0,0},{0,0,0,0}};
static unsigned widget, direction, packet_sockets, binds, sends;
static const char *failure;
static int fail(const char *operation) {
  if (failure && !strcmp(failure, operation)) { errno = EIO; return 1; }
  return 0;
}
static void report(void) {
  fprintf(stderr, "CALLS sockets=%u binds=%u sends=%u up0=%d up1=%d sent0=%u sent1=%u\n",
          packet_sockets, binds, sends, devices[0].up, devices[1].up,
          devices[0].sent, devices[1].sent);
}
unsigned mock_if_nametoindex(const char *name) {
  assert(!strcmp(name, widget ? "eth1" : "eth0")); return widget + 1;
}
int mock_socket(int domain, int type, int protocol) {
  if (domain == AF_INET) { assert(type == SOCK_DGRAM && protocol == 0); return 100 + widget; }
  assert(domain == AF_PACKET && type == SOCK_RAW && protocol == htons(NIC_ETHERTYPE));
  ++packet_sockets; return 200 + widget;
}
int mock_bind(int fd, const struct sockaddr *raw, socklen_t length) {
  const struct sockaddr_ll *address = (const struct sockaddr_ll *)raw;
  assert(fd == 200 + (int)widget && length == sizeof(*address));
  assert(address->sll_family == AF_PACKET && address->sll_protocol == htons(NIC_ETHERTYPE));
  assert(address->sll_ifindex == (int)widget + 1); ++binds;
  if (!devices[widget].up) devices[widget].pending_error = ENETDOWN;
  return 0; /* Linux bind succeeds while retaining the asynchronous error. */
}
int mock_ioctl(int fd, unsigned long operation, ...) {
  assert(fd == 100 + (int)widget);
  va_list arguments; va_start(arguments, operation);
  struct ifreq *request = va_arg(arguments, struct ifreq *); va_end(arguments);
  assert(!strcmp(request->ifr_name, widget ? "eth1" : "eth0"));
  if (operation == SIOCGIFFLAGS) {
    if (fail("getflags")) return -1;
    request->ifr_flags = IFF_BROADCAST | (devices[widget].up ? IFF_UP : 0);
  } else {
    assert(operation == SIOCSIFFLAGS);
    if (fail("setflags")) return -1;
    assert(request->ifr_flags == (IFF_UP | IFF_BROADCAST)); devices[widget].up = 1;
  }
  return 0;
}
int mock_close(int fd) { assert(fd == 100 + (int)widget || fd == 200 + (int)widget); return 0; }
ssize_t mock_send(int fd, const void *frame, size_t length, int flags) {
  assert(fd == 200 + (int)widget && flags == 0); ++sends;
  if (devices[widget].pending_error) { errno = devices[widget].pending_error; devices[widget].pending_error = 0; return -1; }
  if (fail("send")) return -1;
  if (failure && !strcmp(failure, "shortsend")) return (ssize_t)length - 1;
  unsigned char expected[NIC_FRAME_MAX];
  assert(nic_frame(expected, widget, devices[widget].sent, 0) == length);
  assert(!memcmp(frame, expected, length)); ++devices[widget].sent; return (ssize_t)length;
}
int mock_poll(struct pollfd *fds, nfds_t count, int timeout) {
  assert(count == 1 && timeout == 1000 && fds->fd == 200 + (int)widget && fds->events == POLLIN);
  assert(devices[widget].received < devices[widget].sent); fds->revents = POLLIN; return 1;
}
ssize_t mock_recvfrom(int fd, void *frame, size_t capacity, int flags, struct sockaddr *raw, socklen_t *length) {
  assert(fd == 200 + (int)widget && capacity >= NIC_FRAME_MAX && flags == 0);
  assert(*length == sizeof(struct sockaddr_ll));
  ((struct sockaddr_ll *)raw)->sll_pkttype = PACKET_HOST;
  return nic_frame(frame, widget, devices[widget].received++, direction);
}
int mock_clock_gettime(clockid_t clock, struct timespec *value) {
  assert(clock == CLOCK_MONOTONIC); value->tv_sec = 10; value->tv_nsec = 0; return 0;
}
int mock_nanosleep(const struct timespec *request, struct timespec *remain) {
  assert(request->tv_nsec == 1000000 && !remain); return 0;
}
int main(int argc, char **argv) {
  assert(argc == 3); failure = argv[1]; direction = !strcmp(argv[2], "normal"); atexit(report);
  for (widget = 0; widget < 2; ++widget) {
    char id[2] = {(char)('0' + widget), 0};
    char *arguments[] = {"nic_packets", widget ? "eth1" : "eth0", id, argv[2], "16", NULL};
    assert(nic_guest_main(5, arguments) == 0);
    assert(devices[widget].sent == 16 && devices[widget].received == 16);
  }
  assert(packet_sockets == 2 && binds == 2 && sends == 32);
  return 0;
}
'''


def old_order(source):
    """Negative control restores the exact previous bind-before-UP ordering."""
    begin = source.index('  // Explicitly bring up this real target NIC')
    packet = source.index('  int fd = socket(AF_PACKET', begin)
    after = source.index('  unsigned char tx[', packet)
    return source[:begin] + source[packet:after] + source[begin:packet] + source[after:]


class PacketSetup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='nic-packet-setup-')
        cls.directory = Path(cls.temporary.name)
        cls.compiler = shutil.which('gcc') or shutil.which('cc')
        if not cls.compiler:
            raise unittest.SkipTest('C compiler unavailable')
        cls.source = (WORKLOADS / 'nic_packets.c').read_text()
        shim = cls.directory / 'shim.c'
        shim.write_text(SHIM)
        cls.binaries = {}
        for variant, text in [('current', cls.source), ('old', old_order(cls.source))]:
            source = cls.directory / (variant + '.c')
            source.write_text(text)
            common = [cls.compiler, '-O2', '-Wall', '-Wextra', '-Werror', '-I', str(WORKLOADS)]
            native = cls.directory / variant
            subprocess.run([*common, str(source), '-o', str(native)], check=True, capture_output=True, text=True)
            obj = cls.directory / (variant + '.o')
            # Fortified libc inline wrappers can bypass the syscall-name shim
            # (for example recvfrom becomes __recvfrom_chk). Disable fortify
            # only for this mocked object; the real namespace binary above
            # retains the compiler's normal hardening and calls the real kernel.
            subprocess.run([*common, '-U_FORTIFY_SOURCE', '-D_FORTIFY_SOURCE=0',
                            '-Dmain=nic_guest_main',
                            *['-D' + name + '=mock_' + name for name in CALLS],
                            '-c', str(source), '-o', str(obj)], check=True, capture_output=True, text=True)
            binary = cls.directory / (variant + '-shim')
            subprocess.run([*common, str(shim), str(obj), '-o', str(binary)], check=True, capture_output=True, text=True)
            cls.binaries[variant] = (native, binary)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_shim(self, failure='', mode='normal', variant='current'):
        return subprocess.run([str(self.binaries[variant][1]), failure, mode],
                              capture_output=True, text=True, timeout=5)

    def test_two_independent_interfaces(self):
        result = self.run_shim()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count('CIRCT_NIC_PACKET '), 32)
        self.assertEqual(result.stdout.count('CIRCT_NIC_RESULT '), 2)
        self.assertIn('sockets=2 binds=2 sends=32 up0=1 up1=1 sent0=16 sent1=16', result.stderr)

    def test_loopback_preserves_payload_checks(self):
        result = self.run_shim(mode='loopback')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count('"direction":0'), 32)

    def test_old_order_reproduces_second_interface_failure(self):
        result = self.run_shim(variant='old')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('send: Network is down', result.stderr)
        self.assertEqual(result.stdout.count('CIRCT_NIC_RESULT '), 1)
        self.assertIn('sockets=2 binds=2 sends=17 up0=1 up1=1 sent0=16 sent1=0', result.stderr)

    def test_flag_failures_stop_before_packet_socket(self):
        for operation, diagnostic in [('getflags', 'SIOCGIFFLAGS'), ('setflags', 'SIOCSIFFLAGS')]:
            with self.subTest(operation=operation):
                result = self.run_shim(failure=operation)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(diagnostic + ':', result.stderr)
                self.assertIn('sockets=0 binds=0 sends=0', result.stderr)
                self.assertNotIn('CIRCT_NIC_RESULT', result.stdout)

    def test_send_errors_and_short_writes_are_fatal_without_retry(self):
        for operation in ['send', 'shortsend']:
            with self.subTest(operation=operation):
                result = self.run_shim(failure=operation)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn('send:', result.stderr)
                self.assertIn('sockets=1 binds=1 sends=1', result.stderr)
                self.assertNotIn('CIRCT_NIC_RESULT', result.stdout)

    def test_actual_linux_private_network_namespace(self):
        # Loopback begins down in a fresh namespace. No host interface changes.
        if not shutil.which('unshare'):
            self.skipTest('unshare unavailable')
        prefix = [] if os.geteuid() == 0 else ['sudo', '-n']
        if prefix and not shutil.which('sudo'):
            self.skipTest('private network namespace needs noninteractive sudo')
        probe = subprocess.run([*prefix, 'unshare', '-n', 'true'], capture_output=True, text=True, timeout=5)
        if probe.returncode:
            self.skipTest('private network namespace unavailable: ' + probe.stderr.strip())
        for variant, expected in [('old', 1), ('current', 0)]:
            with self.subTest(variant=variant):
                result = subprocess.run([*prefix, 'unshare', '-n', str(self.binaries[variant][0]),
                                         'lo', '0', 'loopback', '8'], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, expected, result.stderr)
                if variant == 'old':
                    self.assertIn('send: Network is down', result.stderr)
                    self.assertNotIn('CIRCT_NIC_RESULT', result.stdout)
                else:
                    self.assertEqual(result.stdout.count('CIRCT_NIC_PACKET '), 8)
                    self.assertIn('CIRCT_NIC_RESULT ', result.stdout)


if __name__ == '__main__':
    unittest.main()
