from __future__ import annotations

import unittest

from bot.runtime_profile import resolve_runtime_profile


class RuntimeProfileTest(unittest.TestCase):
    def test_missing_profile_keeps_legacy_top_level_values(self) -> None:
        profile = resolve_runtime_profile(
            {
                "loop_interval": 0.9,
                "after_click_delay": 1.1,
                "ocr_scale": 1.8,
            }
        )

        self.assertEqual(profile.name, "stable")
        self.assertEqual(profile.loop_interval, 0.9)
        self.assertEqual(profile.after_click_delay, 1.1)
        self.assertEqual(profile.ocr_scale, 1.8)

    def test_invalid_profile_falls_back_to_stable(self) -> None:
        profile = resolve_runtime_profile({"runtime_profile": "turbo"})

        self.assertEqual(profile.name, "stable")
        self.assertEqual(profile.loop_interval, 0.45)

    def test_fast_profile_uses_profile_values_even_with_legacy_top_level_values(self) -> None:
        profile = resolve_runtime_profile(
            {
                "runtime_profile": "fast",
                "loop_interval": 9.9,
                "runtime_profiles": {
                    "fast": {
                        "loop_interval": 0.2,
                        "after_click_delay": 0.3,
                        "ocr_scale": 1.25,
                    }
                },
            }
        )

        self.assertEqual(profile.name, "fast")
        self.assertEqual(profile.loop_interval, 0.2)
        self.assertEqual(profile.after_click_delay, 0.3)
        self.assertEqual(profile.ocr_scale, 1.25)


if __name__ == "__main__":
    unittest.main()
