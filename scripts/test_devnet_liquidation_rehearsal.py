"""Offline checks; these never instantiate the live rehearsal client."""

import unittest
from decimal import Decimal as D

from scripts.devnet_liquidation_rehearsal import EXECUTION_TOPICS, target_price


class TargetPriceTests(unittest.TestCase):
    def test_both_sides_reach_requested_health(self):
        for base in (D("3.8"), D("-3.8")):
            for ratio in (D("0.85"), D("0.35")):
                with self.subTest(base=base, ratio=ratio):
                    mark, margin, lmr = D(2500), D(400), D(292)
                    price = target_price(base, margin, lmr, mark, ratio)
                    resulting_margin = margin + base * (price - mark)
                    resulting_lmr = lmr * price / mark
                    self.assertLess(abs(resulting_margin / resulting_lmr - ratio), D("0.00001"))
                    self.assertGreater(resulting_margin, 0)
                    self.assertLess(base * (price - mark), 0)

    def test_backstop_requires_a_larger_adverse_move(self):
        for base in (D("3.8"), D("-3.8")):
            dutch = target_price(base, D(400), D(292), D(2500), D("0.85"))
            backstop = target_price(base, D(400), D(292), D(2500), D("0.35"))
            self.assertLess(base * (backstop - dutch), 0)

    def test_flat_account_is_rejected(self):
        with self.assertRaises(ValueError):
            target_price(D(0), D(400), D(0), D(2500), D("0.85"))

    def test_receipt_topic_matches_verified_devnet_liquidations(self):
        self.assertIn("3241b27e0f195bcdee369c35b4949b4dc7357380ca69e20b51e4b17d00b6fabe", EXECUTION_TOPICS)


if __name__ == "__main__":
    unittest.main()
