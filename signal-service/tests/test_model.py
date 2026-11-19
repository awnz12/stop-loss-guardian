import unittest

from signal_service.model import ModelConfig, SignalModel


def feed(model, prices):
    decision = None
    for p in prices:
        decision = model.update(p)
    return decision


class TestSignalModel(unittest.TestCase):
    def setUp(self):
        self.model = SignalModel(ModelConfig())

    def test_warmup_holds(self):
        decision = feed(self.model, [100, 100, 100])
        self.assertEqual(decision.action, "hold")
        self.assertIn("warming up", decision.reasons[0])

    def test_invalid_price_holds(self):
        self.assertEqual(self.model.update(0).action, "hold")

    def test_calm_market_holds(self):
        decision = feed(self.model, [100] * 10)
        self.assertEqual(decision.action, "hold")

    def test_drawdown_arms_with_computed_trigger(self):
        # Peak 100 -> 92 is exactly the 8% arm drawdown. Volatility of the
        # series is 3.2% -> buffer = 2 * 3.2% = 6.4% -> trigger = 92 * 0.936 = 86.
        decision = feed(self.model, [100, 100, 100, 100, 100, 92])
        self.assertEqual(decision.action, "arm")
        self.assertEqual(decision.trigger_price, 86)
        self.assertEqual(decision.metrics["drawdown_bps"], 800)

    def test_volatility_alone_arms(self):
        # No drawdown at the end (price at its peak) but +-4% whipsaw returns
        # push volatility well past the 3% arm threshold.
        decision = feed(self.model, [100, 104, 100, 104, 100, 104])
        self.assertEqual(decision.action, "arm")
        self.assertGreaterEqual(decision.metrics["volatility_bps"], 300)
        self.assertLess(decision.trigger_price, 104)

    def test_disarm_when_risk_subsides(self):
        self.model.seed_history([100] * 8)
        self.model.sync_chain_state(armed=True, trigger_price=90)
        decision = self.model.update(100)
        self.assertEqual(decision.action, "disarm")
        self.assertEqual(decision.trigger_price, 90)  # keeps the last trigger

    def test_trailing_update_when_slide_continues(self):
        # Armed at trigger 90; price slides to 85 (15% drawdown, 6% vol ->
        # 12% buffer) -> candidate trigger 74, a >3% move from 90 -> update.
        self.model.sync_chain_state(armed=True, trigger_price=90)
        decision = feed(self.model, [100, 100, 100, 100, 100, 85])
        self.assertEqual(decision.action, "update")
        self.assertEqual(decision.trigger_price, 74)

    def test_armed_small_move_holds(self):
        self.model.seed_history([100, 100, 100, 100, 100, 85])
        self.model.sync_chain_state(armed=True, trigger_price=74)
        decision = self.model.update(85)
        self.assertEqual(decision.action, "hold")

    def test_note_submitted_tracks_state(self):
        self.model.note_submitted("arm", 86)
        self.assertTrue(self.model.armed)
        self.assertEqual(self.model.current_trigger, 86)
        self.model.note_submitted("disarm", 86)
        self.assertFalse(self.model.armed)

    def test_deterministic(self):
        series = [100, 100, 100, 100, 100, 92, 90, 88]
        d1 = feed(SignalModel(ModelConfig()), series)
        d2 = feed(SignalModel(ModelConfig()), series)
        self.assertEqual((d1.action, d1.trigger_price), (d2.action, d2.trigger_price))


if __name__ == "__main__":
    unittest.main()
