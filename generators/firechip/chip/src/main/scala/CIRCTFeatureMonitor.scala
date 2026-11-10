// See LICENSE for license details.
package firechip.chip

import chisel3._
import midas.targetutils.{PerfCounter, SynthesizePrintf}

/** Bounded enabled-case observer attached to an actual target clock domain.
  *
  * Counter state lives in the target and advances only on that domain's clock.
  * The explicit annotations select real CSV widgets independently of the cover
  * module list. The four-bit identity exercises rollover without a long run.
  * Periodic Print records allow the output checker to correlate CSV identity,
  * reset release, domain ownership and ordered payloads during a Linux boot.
  */
class CIRCTFeatureMonitor(domain: Int) extends Module {
  require(domain >= 0)
  val cycles = RegInit(0.U(64.W))
  cycles := cycles + 1.U
  val active = WireDefault(!reset.asBool)
  val wrap = Wire(UInt(4.W))
  wrap := cycles(3, 0)
  PerfCounter(active.asUInt, s"feature_domain${domain}_active",
    "Cycles outside local reset.")
  PerfCounter.identity(cycles, s"feature_domain${domain}_cycles",
    "Target domain cycles since local reset release.")
  PerfCounter.identity(wrap, s"feature_domain${domain}_wrap",
    "Low four bits of target domain cycles.")
  when (!reset.asBool && cycles(19, 0) === 0.U) {
    SynthesizePrintf(printf(s"[CIRCT_FEATURE] domain=$domain cycles=%d wrap=%d\n", cycles, wrap))
  }
}

object CIRCTFeatureMonitor {
  def instantiate(clock: Clock, reset: Bool, domain: Int): Unit = {
    withClockAndReset(clock, reset) {
      Module(new CIRCTFeatureMonitor(domain)).suggestName(s"feature_domain_$domain")
    }
  }
}
