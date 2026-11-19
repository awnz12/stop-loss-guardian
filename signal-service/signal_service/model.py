"""The stop-loss signal model: small, deterministic, explainable.

Plain-language logic (also in the README):

The model watches a rolling window of recent prices and asks two questions:

1. How far has the price fallen from its recent peak?  ("drawdown")
2. How jumpy has the price been lately?                ("volatility" =
   standard deviation of the recent per-step percentage moves)

Risk is HIGH when the drawdown reaches `arm_drawdown_bps` (default 8%) OR
volatility reaches `arm_volatility_bps` (default 3%). Risk is LOW again when
both fall back under the disarm thresholds (2% / 1%).

Decisions:
- Not armed + risk HIGH  -> ARM. The stop trigger is placed a safety buffer
  below the current price; the buffer widens with volatility
  (buffer = clamp(2 x volatility, 5%, 25%), default 10% when volatility
  cannot be measured yet). The guardian fires when price <= trigger.
- Armed + risk HIGH      -> trailing UPDATE: if the freshly computed trigger
  differs from the armed trigger by at least 3%, re-arm with the new trigger
  so the stop follows a continuing slide instead of going stale.
- Armed + risk LOW       -> DISARM (the guardian ignores prices again).
- Anything else          -> HOLD, no transaction.

All thresholds are integers in basis points (1 bp = 0.01%) and every decision
is a pure function of the price series + config, so unit tests can pin the
behaviour exactly. No randomness, no clocks, no network.
"""

import math
import statistics
from collections import deque
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelConfig:
    window: int = 32                 # prices kept in the rolling window
    vol_window: int = 12             # returns used for the volatility estimate
    min_samples: int = 5             # samples required before any decision
    arm_drawdown_bps: int = 800      # 8%
    arm_volatility_bps: int = 300    # 3%
    disarm_drawdown_bps: int = 200   # 2%
    disarm_volatility_bps: int = 100  # 1%
    buffer_vol_mult: float = 2.0     # stop buffer = mult x volatility ...
    buffer_min_bps: int = 500        # ... clamped to [5%, 25%]
    buffer_max_bps: int = 2500
    buffer_default_bps: int = 1000   # 10% when volatility is not measurable
    refresh_threshold_bps: int = 300  # trailing update needs a >=3% trigger move


@dataclass
class Decision:
    action: str  # "hold" | "arm" | "disarm" | "update"
    trigger_price: int = None
    reasons: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


class SignalModel:
    def __init__(self, config: ModelConfig = ModelConfig()):
        self.config = config
        self.prices = deque(maxlen=config.window)
        self.armed = False
        self.current_trigger = 0

    def sync_chain_state(self, armed: bool, trigger_price: int) -> None:
        """Adopt the on-chain signal state (startup reconciliation / after a
        confirmed submission), so restarts never desync from the registry."""
        self.armed = armed
        self.current_trigger = trigger_price

    def seed_history(self, prices) -> None:
        """Preload past prices (oldest first), e.g. from PriceUpdated logs."""
        for p in prices:
            if p > 0:
                self.prices.append(int(p))

    # -- metrics ------------------------------------------------------------
    def _drawdown_bps(self, price: int) -> int:
        peak = max(self.prices)
        if peak <= 0:
            return 0
        return (peak - price) * 10000 // peak

    def _volatility_bps(self):
        """Population stdev of the last `vol_window` simple returns, in bps.
        None when there are not at least 2 usable returns."""
        prices = list(self.prices)
        returns = []
        for prev, cur in zip(prices, prices[1:]):
            if prev > 0:
                returns.append((cur - prev) / prev)
        returns = returns[-self.config.vol_window:]
        if len(returns) < 2:
            return None
        return int(round(statistics.pstdev(returns) * 10000))

    def _buffer_bps(self, vol_bps) -> int:
        cfg = self.config
        if vol_bps is None:
            return cfg.buffer_default_bps
        raw = int(round(cfg.buffer_vol_mult * vol_bps))
        return max(cfg.buffer_min_bps, min(cfg.buffer_max_bps, raw))

    # -- decision -------------------------------------------------------------
    def update(self, price: int) -> Decision:
        cfg = self.config
        if price is None or price <= 0:
            return Decision("hold", reasons=["invalid price (<= 0) — feed not ready"])
        self.prices.append(int(price))

        metrics = {"samples": len(self.prices), "price": int(price)}
        if len(self.prices) < cfg.min_samples:
            return Decision("hold", reasons=[f"warming up ({len(self.prices)}/{cfg.min_samples} samples)"],
                            metrics=metrics)

        drawdown = self._drawdown_bps(int(price))
        vol = self._volatility_bps()
        metrics.update({"peak": max(self.prices), "drawdown_bps": drawdown, "volatility_bps": vol})

        risk_high = drawdown >= cfg.arm_drawdown_bps or (vol is not None and vol >= cfg.arm_volatility_bps)
        risk_low = drawdown <= cfg.disarm_drawdown_bps and (vol is not None and vol <= cfg.disarm_volatility_bps)

        buffer_bps = self._buffer_bps(vol)
        new_trigger = max(1, int(price) * (10000 - buffer_bps) // 10000)
        metrics.update({"buffer_bps": buffer_bps, "candidate_trigger": new_trigger})

        if not self.armed and risk_high:
            why = []
            if drawdown >= cfg.arm_drawdown_bps:
                why.append(f"drawdown {drawdown} bps >= arm threshold {cfg.arm_drawdown_bps} bps")
            if vol is not None and vol >= cfg.arm_volatility_bps:
                why.append(f"volatility {vol} bps >= arm threshold {cfg.arm_volatility_bps} bps")
            return Decision("arm", trigger_price=new_trigger, reasons=why, metrics=metrics)

        if self.armed and risk_low:
            return Decision("disarm", trigger_price=self.current_trigger,
                            reasons=[f"risk subsided (drawdown {drawdown} bps, volatility {vol} bps)"],
                            metrics=metrics)

        if self.armed and risk_high:
            if self.current_trigger > 0:
                diff_bps = abs(new_trigger - self.current_trigger) * 10000 // self.current_trigger
                metrics["trigger_diff_bps"] = diff_bps
                if diff_bps >= cfg.refresh_threshold_bps:
                    return Decision("update", trigger_price=new_trigger,
                                    reasons=[f"trailing stop: trigger {self.current_trigger} -> {new_trigger} "
                                             f"({diff_bps} bps move) while risk stays high"],
                                    metrics=metrics)
            return Decision("hold", reasons=["armed; trigger still fresh"], metrics=metrics)

        return Decision("hold", reasons=["risk below arm thresholds"], metrics=metrics)

    def note_submitted(self, action: str, trigger_price: int) -> None:
        """Record that an arm/disarm/update was submitted (live) or would have
        been (dry-run shadow state), keeping later decisions consistent."""
        if action in ("arm", "update"):
            self.armed = True
            self.current_trigger = trigger_price
        elif action == "disarm":
            self.armed = False
            self.current_trigger = trigger_price
