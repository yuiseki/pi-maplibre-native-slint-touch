"""A place name with a feature word on the end names one thing.

「京都駅を表示して」flew to the city of Kyoto: the nine-city table found 京都
inside 京都駅 and the verb was 表示 rather than ズーム, so the zoom rule never
looked at it. But the verb was never what made 広島駅 a named thing. The 駅 was.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "pi-hear"))

import intent


class NamedFeatureTest(unittest.TestCase):
    def plan(self, said):
        return intent.for_voice(said, lang="ja")

    def test_a_named_feature_goes_to_overpass_whatever_the_verb(self):
        for said, want in (
            ("京都駅を表示して", "京都駅"),
            ("広島駅を表示して", "広島駅"),
            ("京都駅にズームして", "京都駅"),
            ("広島国際会議場を表示して", "広島国際会議場"),
            ("関西空港を表示して", "関西空港"),
            ("広島城を表示して", "広島城"),
            ("上野公園を表示して", "上野公園"),
        ):
            plan = self.plan(said)
            self.assertIsNotNone(plan, said)
            self.assertEqual(plan["tool"], "pi-zoom", said)
            self.assertEqual(plan["args"], [want], said)

    def test_the_bare_feature_word_is_still_a_category(self):
        # With nothing in front of it, "station" is what was asked for.
        self.assertEqual(self.plan("駅を表示して")["args"], ["station"])

    def test_the_name_does_not_run_across_a_particle(self):
        # 「地図に駅」and 「東京の駅」are category searches. Without the particle
        # boundary the first became a search for a place called 地図に駅.
        self.assertEqual(self.plan("地図に駅を表示して")["args"], ["station"])
        self.assertEqual(self.plan("東京の駅を表示して")["args"],
                         ["station", "東京"])
        self.assertEqual(self.plan("新宿の駅を表示して")["args"],
                         ["station", "新宿"])

    def test_a_plain_city_is_still_a_city(self):
        for said in ("京都を表示して", "広島を表示して"):
            self.assertEqual(self.plan(said)["tool"], "pi-geocode", said)

    def test_the_place_table_is_told_to_stay_out_of_these_too(self):
        self.assertTrue(intent.is_zoom_request("京都駅を表示して"))
        self.assertFalse(intent.is_zoom_request("京都を表示して"))


class ModelGuessTest(unittest.TestCase):
    def test_show_here_is_rules_only(self):
        # 「広島を表示して」came back once as 「フィルスの表示して」and the model
        # answered show_here, jumping the map to the GPS position. The real
        # phrasings are a fixed handful and the rules have all of them; what
        # the model adds here is only guesses, and a guess that moves the map
        # is worse than a shrug.
        self.assertNotIn("show_here", intent.MODEL_INTENTS)
        self.assertNotIn("show_here", intent.grammar())
        self.assertIsNone(intent.for_voice("フィルスの表示して", lang="ja"))

    def test_the_real_phrasings_still_work(self):
        for said in ("現在地", "いまどこ", "ここはどこ"):
            self.assertEqual(intent.for_voice(said, lang="ja")["tool"],
                             "pi-here", said)
        for said in ("where am I", "show current location"):
            self.assertEqual(intent.for_voice(said, lang="en")["tool"],
                             "pi-here", said)


if __name__ == "__main__":
    unittest.main()
