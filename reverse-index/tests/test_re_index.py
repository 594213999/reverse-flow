import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import re_index  # noqa: E402

ALIASES = json.loads((ROOT / "data" / "aliases.json").read_text(encoding="utf-8"))


def make_entry(name, desc, **overrides):
    base = {
        "name": name,
        "url": "https://github.com/example/" + name.lower().replace(" ", "-"),
        "desc": desc,
        "category": "Devices, IoT & Hardware",
        "subcategory": "Misc",
        "stars": 10,
        "archived": False,
        "pushed": "2026-10-01",
        "lang": "Python",
        "license": "MIT",
        "topics": [],
        "platform": "",
    }
    base.update(overrides)
    return base


class TranslateTests(unittest.TestCase):
    def test_chinese_phrase(self):
        out = re_index.translate_query("微信 协议", ALIASES)
        self.assertIn("wechat", out["terms"])
        self.assertIn("protocol", out["terms"])
        self.assertEqual(out["unknown"], [])

    def test_mixed_bilibili(self):
        out = re_index.translate_query("B站 抓包", ALIASES)
        self.assertIn("bilibili", out["terms"])
        self.assertIn("packet capture", out["terms"])

    def test_synonyms_are_alternatives(self):
        out = re_index.translate_query("蓝牙", ALIASES)
        self.assertIn("bluetooth", out["terms"])
        self.assertIn("ble", out["alternatives"]["bluetooth"])

    def test_unknown_chinese(self):
        out = re_index.translate_query("量子纠缠", ALIASES)
        self.assertEqual(out["terms"], [])
        self.assertTrue(out["unknown"])

    def test_english_passthrough(self):
        out = re_index.translate_query("n64 decomp", ALIASES)
        self.assertEqual(out["terms"], ["n64", "decomp"])


class FacetTests(unittest.TestCase):
    def setUp(self):
        self.rules = ALIASES["facets"]

    def test_windows(self):
        e = make_entry("RegTool", "Windows registry driver for win32")
        self.assertIn("windows", re_index.facet_matches(e, self.rules))

    def test_android(self):
        e = make_entry("DexTool", "APK and DEX decompiler for Android")
        self.assertIn("android", re_index.facet_matches(e, self.rules))

    def test_protocol_category(self):
        e = make_entry("Proto", "custom wire protocol", category="Network & Wire Protocols")
        self.assertIn("protocol", re_index.facet_matches(e, self.rules))

    def test_firmware(self):
        e = make_entry("EspTool", "ESP32 firmware dump utility")
        self.assertIn("firmware", re_index.facet_matches(e, self.rules))

    def test_unrelated(self):
        e = make_entry("MusicPlayer", "A desktop music player")
        self.assertEqual(re_index.facet_matches(e, self.rules), {})


class RankingTests(unittest.TestCase):
    def test_decomp_prefix_still_matches(self):
        e = make_entry("Super Mario 64 decomp", "Matching decompilation")
        scored = re_index.score_entry(e, ["decomp"], {})
        self.assertEqual(scored["matched"], 1)

    def test_hyphenated_router_is_not_network_router(self):
        e = make_entry("YesPlayMusic", "music player", topics=["vue-router"])
        scored = re_index.score_entry(e, ["router"], {"router": ["openwrt", "gateway"]})
        self.assertEqual(scored["matched"], 0)

    def test_search_excludes_zero_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir(parents=True)
            (root / "data" / "aliases.json").write_text(
                json.dumps(ALIASES, ensure_ascii=False), encoding="utf-8")
            entries = [
                make_entry("Music", "a music player", stars=99999, topics=["vue-router"]),
                make_entry("OpenWrt", "router firmware", stars=100),
            ]
            (root / "data" / "entries.json").write_text(
                json.dumps(entries, ensure_ascii=False), encoding="utf-8")
            out = re_index.search(root, "路由器", limit=5)
            names = [e["name"] for e in out["results"]]
            self.assertNotIn("Music", names)
            self.assertIn("OpenWrt", names)

    def test_popularity_beats_tiny_exact_name(self):
        tiny = make_entry("Gex (N64)", "Decompilation of Gex", stars=29)
        big = make_entry("Super Mario 64 decomp", "Matching decompilation", stars=8950)
        tiny_score = re_index.score_entry(tiny, ["n64", "decomp"], {})
        big_score = re_index.score_entry(big, ["n64", "decomp"], {})
        self.assertGreater(big_score["score"], tiny_score["score"])

    def test_search_filters_and_translates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir(parents=True)
            (root / "data" / "aliases.json").write_text(
                json.dumps(ALIASES, ensure_ascii=False), encoding="utf-8")
            entries = [
                make_entry("WeChatMsg", "Export local WeChat chat history", stars=42000),
                make_entry("Unrelated", "A music player", stars=99),
            ]
            (root / "data" / "entries.json").write_text(
                json.dumps(entries, ensure_ascii=False), encoding="utf-8")
            out = re_index.search(root, "微信", limit=5)
            self.assertEqual(out["results"][0]["name"], "WeChatMsg")
            self.assertEqual(out["match_mode"], "all")


class SubsetTests(unittest.TestCase):
    def test_build_subset_and_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir(parents=True)
            (root / "data" / "aliases.json").write_text(
                json.dumps(ALIASES, ensure_ascii=False), encoding="utf-8")
            entries = [
                make_entry("WinTool", "Windows registry driver"),
                make_entry("ApkTool", "APK decompiler"),
                make_entry("ProtoTool", "custom protocol", category="Network & Wire Protocols"),
                make_entry("FwTool", "ESP32 firmware dump"),
                make_entry("Other", "music player"),
            ]
            out = re_index.build_subset(root, entries)
            self.assertEqual(out["total_subset"], 4)
            self.assertEqual(out["counts"]["windows"], 1)
            self.assertEqual(out["counts"]["android"], 1)
            self.assertEqual(out["counts"]["protocol"], 1)
            self.assertEqual(out["counts"]["firmware"], 1)
            payload = json.loads((root / "data" / "subset.json").read_text(encoding="utf-8"))
            self.assertEqual(len(payload["entries"]), 4)
            self.assertTrue((root / "INDEX.md").exists())


class SyncTests(unittest.TestCase):
    def _root(self, tmp):
        root = Path(tmp)
        (root / "data").mkdir(parents=True)
        (root / "data" / "aliases.json").write_text(
            json.dumps(ALIASES, ensure_ascii=False), encoding="utf-8")
        return root

    def test_sync_local_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            entries = [
                make_entry(f"proj{i}", "Windows registry driver" if i == 0 else "misc")
                for i in range(1000)
            ]
            entries[1]["desc"] = "APK decompiler"
            entries[2]["desc"] = "custom protocol"
            entries[2]["category"] = "Network & Wire Protocols"
            entries[3]["desc"] = "ESP32 firmware dump"
            fixture = Path(tmp) / "fixture.json"
            fixture.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
            out = re_index.sync(root, source=str(fixture), force=True, quiet=True)
            self.assertEqual(out["status"], "updated")
            self.assertEqual(out["total"], 1000)
            self.assertTrue((root / "data" / "subset.json").exists())
            self.assertTrue((root / "INDEX.md").exists())
            state = json.loads((root / "data" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["total"], 1000)

    def test_sync_rejects_bad_payload_and_keeps_old(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            old = [make_entry("old", "old data")]
            (root / "data" / "entries.json").write_text(
                json.dumps(old, ensure_ascii=False), encoding="utf-8")
            fixture = Path(tmp) / "bad.json"
            fixture.write_text(json.dumps([make_entry("x", "y")]), encoding="utf-8")
            out = re_index.sync(root, source=str(fixture), force=True, quiet=True)
            self.assertEqual(out["status"], "error")
            kept = json.loads((root / "data" / "entries.json").read_text(encoding="utf-8"))
            self.assertEqual(kept[0]["name"], "old")


class McpTests(unittest.TestCase):
    def test_translate_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir(parents=True)
            (root / "data" / "aliases.json").write_text(
                json.dumps(ALIASES, ensure_ascii=False), encoding="utf-8")
            text, data = re_index.mcp_call(root, "translate_reverse_query", {"query": "安卓 固件"})
            self.assertIn("android", data["terms"])
            self.assertIn("firmware", data["terms"])

    def test_hook_entry(self):
        entry = re_index.hook_entry(ROOT)
        self.assertIn("re_index.py", entry["command"])
        self.assertIn("sync --hook", entry["command"])


class FindEntryTests(unittest.TestCase):
    def _root(self, tmp):
        root = Path(tmp)
        (root / "data").mkdir(parents=True)
        entries = [make_entry("Tuyapi", "tuya local api",
                              url="https://github.com/codetheweb/tuyapi")]
        (root / "data" / "entries.json").write_text(
            json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        return root

    def test_owner_repo_shorthand_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            for ref in ("codetheweb/tuyapi", "https://github.com/codetheweb/tuyapi",
                        "github.com/codetheweb/tuyapi", "Tuyapi"):
                self.assertIsNotNone(re_index.find_entry(root, ref), ref)

    def test_unknown_ref_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            self.assertIsNone(re_index.find_entry(root, "no/such-repo"))
            self.assertIsNone(re_index.find_entry(root, ""))


RESURRECTED = [
    {"product": "Spotify Car Thing", "maker": "Spotify", "killed": 2024,
     "what": "Spotify ended support and the device stopped working",
     "revived_by": ["https://github.com/ItsRiprod/DeskThing"],
     "source": "https://en.wikipedia.org/wiki/Car_Thing", "kind": "device"},
    {"product": "Nintendo Wi-Fi Connection", "maker": "Nintendo", "killed": 2014,
     "what": "Nintendo shut down online play for DS and Wii games",
     "revived_by": ["https://github.com/example/wfc-server"],
     "source": "https://en.wikipedia.org/wiki/Nintendo_Wi-Fi_Connection", "kind": "service"},
]


class ResurrectedTests(unittest.TestCase):
    def _root(self, tmp):
        root = Path(tmp)
        (root / "data").mkdir(parents=True)
        (root / "data" / "resurrected.json").write_text(
            json.dumps(RESURRECTED, ensure_ascii=False), encoding="utf-8")
        return root

    def test_matches_maker_and_product(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            rows = re_index.search_resurrected(root, "spotify")
            self.assertEqual([r["product"] for r in rows], ["Spotify Car Thing"])

    def test_matches_reviving_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            rows = re_index.search_resurrected(root, "deskthing")
            self.assertEqual(len(rows), 1)

    def test_kind_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            rows = re_index.search_resurrected(root, "", kind="service")
            self.assertEqual([r["product"] for r in rows], ["Nintendo Wi-Fi Connection"])

    def test_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            rows = re_index.search_resurrected(root, "")
            self.assertEqual(rows[0]["killed"], 2024)

    def test_missing_dataset_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir(parents=True)
            self.assertEqual(re_index.search_resurrected(root, "anything"), [])

    def test_mcp_tool_returns_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            text, data = re_index.mcp_call(
                root, "search_resurrected_products", {"query": "nintendo"})
            self.assertIn("Nintendo Wi-Fi Connection", text)
            self.assertEqual(len(data["results"]), 1)


class CloneTests(unittest.TestCase):
    def test_refuses_unindexed_repo(self):
        # The guard is what keeps this a research shortcut, not a general cloner.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir(parents=True)
            (root / "data" / "entries.json").write_text(
                json.dumps([make_entry("A", "a")], ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(KeyError):
                re_index.clone_project(root, "not/in-index")

    def test_refuses_non_github_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir(parents=True)
            entry = make_entry("Weird", "not on github", url="https://gitlab.com/a/b")
            (root / "data" / "entries.json").write_text(
                json.dumps([entry], ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ValueError):
                re_index.clone_project(root, "https://gitlab.com/a/b")

    def test_existing_target_is_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir(parents=True)
            (root / "data" / "entries.json").write_text(
                json.dumps([make_entry("Tuyapi", "d",
                                       url="https://github.com/codetheweb/tuyapi")],
                           ensure_ascii=False), encoding="utf-8")
            target = root / "vendor" / "codetheweb__tuyapi"
            target.mkdir(parents=True)
            (target / "README.md").write_text("already here", encoding="utf-8")
            out = re_index.clone_project(root, "codetheweb/tuyapi")
            self.assertEqual(out["status"], "exists")
            self.assertEqual((target / "README.md").read_text(encoding="utf-8"), "already here")


if __name__ == "__main__":
    unittest.main()
