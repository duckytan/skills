# -*- coding: utf-8 -*-
"""Unit tests for empty-directory pruning (§6.6, v3.7.0).

Covers ``pipeline_lib/fsutil.prune_empty_dirs`` (+ ``dir_is_empty`` /
``remove_empty_dir``) and the ``prune-empty`` CLI.

Safety properties asserted here (the whole point of the feature):
  * only GENUINELY empty directories are removed — content is never at risk;
  * the processing root itself is never removed;
  * anything under a ``protected`` prefix is never removed;
  * a directory whose name looks like a password carrier is never removed;
  * the batch-end (``candidates``) mode only walks upward from a directory the
    pipeline may itself have emptied — pre-existing structure is untouched;
  * ``dry_run`` touches nothing.

Run:  python -m unittest discover -s tests -p "test_prune_empty.py"   (from scripts/)
"""

import contextlib
import inspect
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(HERE)
sys.path.insert(0, SCRIPTS_DIR)

import pipeline                                              # noqa: E402
from pipeline_lib import config as C                         # noqa: E402
from pipeline_lib import fsutil                              # noqa: E402


def _mkdirs(*paths):
    for p in paths:
        os.makedirs(p, exist_ok=True)
    return paths[0] if paths else None


def _touch(path, content="x"):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


class DirHelpersTests(unittest.TestCase):
    def test_dir_is_empty_true_for_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(fsutil.dir_is_empty(d))

    def test_dir_is_empty_false_with_entry(self):
        with tempfile.TemporaryDirectory() as d:
            _touch(os.path.join(d, "a.txt"))
            self.assertFalse(fsutil.dir_is_empty(d))

    def test_dir_is_empty_false_with_subdir(self):
        with tempfile.TemporaryDirectory() as d:
            _mkdirs(os.path.join(d, "sub"))
            self.assertFalse(fsutil.dir_is_empty(d))

    def test_dir_is_empty_false_on_missing(self):
        self.assertFalse(fsutil.dir_is_empty(os.path.join("z:\\nope", "x")))

    def test_remove_empty_dir_refuses_non_empty(self):
        """**不在本沙箱断言 OS 会拒绝非空目录。**

        实测（2026-09-15，见 references/lessons.md LES-20260915-07）：本沙箱的
        safe-delete 层把任何目录删除调用（``os.rmdir`` 与 ``RemoveDirectoryW``）
        都改写成**递归删除**，对非空目录也返回成功。所以「非空判定」是我们
        自己的责任（``dir_is_empty`` 走 ctypes ``FindFirstFileW`` 直连内核），
        不能依赖 OS 的 rmdir 语义。此处只断言真正由我们保证的那件事。
        """
        with tempfile.TemporaryDirectory() as d:
            sub = os.path.join(d, "sub")
            _touch(os.path.join(sub, "a.txt"))
            # 我们的守门函数必须先说不空
            self.assertFalse(fsutil.dir_is_empty(sub))
            # 而 prune 必须因此完全不动它（内容必须活着）
            fsutil.prune_empty_dirs(d)
            self.assertTrue(os.path.isfile(os.path.join(sub, "a.txt")))

    def test_never_removes_ancestors_of_content(self):
        """深层内容必须保护整条祖先链——这是本沙箱里唯一的数据安全防线。"""
        with tempfile.TemporaryDirectory() as d:
            deep = os.path.join(d, "a", "b", "c")
            _touch(os.path.join(deep, "keep.bin"), "precious")
            _mkdirs(os.path.join(d, "a", "empty_sibling"))
            removed, _failed = fsutil.prune_empty_dirs(d)
            self.assertTrue(os.path.isfile(os.path.join(deep, "keep.bin")))
            for p in (os.path.join(d, "a"), os.path.join(d, "a", "b"), deep):
                self.assertTrue(os.path.isdir(p), p)
            self.assertFalse(os.path.exists(
                os.path.join(d, "a", "empty_sibling")))     # only the shell went
            self.assertEqual(removed,
                             [os.path.join(d, "a", "empty_sibling")])

    def test_remove_empty_dir_missing_counts_as_gone(self):
        with tempfile.TemporaryDirectory() as d:
            ok, rc = fsutil.remove_empty_dir(os.path.join(d, "nope"))
            self.assertTrue(ok)

    def test_remove_empty_dir_removes_verified_empty(self):
        with tempfile.TemporaryDirectory() as d:
            sub = os.path.join(d, "sub")
            _mkdirs(sub)
            self.assertTrue(fsutil.dir_is_empty(sub))
            ok, rc = fsutil.remove_empty_dir(sub)
            self.assertTrue(ok)
            self.assertEqual(rc, 0)
            self.assertFalse(os.path.exists(sub))


class PruneSweepTests(unittest.TestCase):
    def test_removes_nested_empty_leaf_first(self):
        with tempfile.TemporaryDirectory() as d:
            _mkdirs(os.path.join(d, "a", "b", "c"))
            removed, failed = fsutil.prune_empty_dirs(d)
            self.assertEqual(failed, [])
            self.assertFalse(os.path.exists(os.path.join(d, "a")))
            # bottom-up: the deepest one is reported first
            self.assertEqual(removed[0], os.path.join(d, "a", "b", "c"))

    def test_keeps_directory_with_content(self):
        with tempfile.TemporaryDirectory() as d:
            _touch(os.path.join(d, "a", "keep.bin"), "payload")
            _mkdirs(os.path.join(d, "a", "empty1", "empty2"))
            fsutil.prune_empty_dirs(d)
            self.assertTrue(os.path.isfile(os.path.join(d, "a", "keep.bin")))
            self.assertFalse(os.path.exists(os.path.join(d, "a", "empty1")))
            self.assertTrue(os.path.isdir(os.path.join(d, "a")))

    def test_root_is_never_removed(self):
        with tempfile.TemporaryDirectory() as d:
            _mkdirs(os.path.join(d, "a", "b"))
            removed, _failed = fsutil.prune_empty_dirs(d)
            self.assertTrue(os.path.isdir(d))
            self.assertNotIn(os.path.abspath(d), [os.path.abspath(x) for x in removed])
            self.assertTrue(removed)          # the children still went

    def test_protected_prefix_is_kept(self):
        with tempfile.TemporaryDirectory() as d:
            prot = os.path.join(d, "pipeline")
            _mkdirs(os.path.join(prot, "db"), os.path.join(d, "other"))
            fsutil.prune_empty_dirs(d, protected=[prot])
            self.assertTrue(os.path.isdir(os.path.join(prot, "db")))
            self.assertTrue(os.path.isdir(prot))
            self.assertFalse(os.path.exists(os.path.join(d, "other")))

    def test_password_named_dir_is_kept(self):
        with tempfile.TemporaryDirectory() as d:
            _mkdirs(os.path.join(d, "解压密码"), os.path.join(d, "普通目录"))
            fsutil.prune_empty_dirs(d)
            self.assertTrue(os.path.isdir(os.path.join(d, "解压密码")))
            self.assertFalse(os.path.exists(os.path.join(d, "普通目录")))

    def test_dry_run_touches_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            _mkdirs(os.path.join(d, "a", "b"))
            removed, failed = fsutil.prune_empty_dirs(d, dry_run=True)
            self.assertEqual(failed, [])
            self.assertTrue(removed)
            self.assertTrue(os.path.isdir(os.path.join(d, "a", "b")))

    def test_missing_root_is_noop(self):
        removed, failed = fsutil.prune_empty_dirs(os.path.join("z:\\nope", "r"))
        self.assertEqual((removed, failed), ([], []))

    def test_never_raises_on_locked_tree(self):
        with tempfile.TemporaryDirectory() as d:
            _mkdirs(os.path.join(d, "a"))
            fsutil.prune_empty_dirs(os.path.join(d, "a"))   # must not raise


class PruneCandidatesTests(unittest.TestCase):
    """Batch-end mode: only upward from a directory we may have emptied."""

    def test_walks_upward_from_candidate(self):
        with tempfile.TemporaryDirectory() as d:
            a = _mkdirs(os.path.join(d, "a", "b"))
            _mkdirs(os.path.join(d, "unrelated"))
            removed, failed = fsutil.prune_empty_dirs(
                d, candidates=[a])
            self.assertEqual(failed, [])
            self.assertFalse(os.path.exists(a))
            self.assertFalse(os.path.exists(os.path.join(d, "a")))
            # the sibling we never touched stays
            self.assertTrue(os.path.isdir(os.path.join(d, "unrelated")))

    def test_stops_at_non_empty_ancestor(self):
        with tempfile.TemporaryDirectory() as d:
            _touch(os.path.join(d, "a", "keep.bin"), "payload")
            a_empty = _mkdirs(os.path.join(d, "a", "empty"))
            fsutil.prune_empty_dirs(d, candidates=[a_empty])
            self.assertFalse(os.path.exists(a_empty))
            self.assertTrue(os.path.isdir(os.path.join(d, "a")))
            self.assertTrue(os.path.isfile(os.path.join(d, "a", "keep.bin")))

    def test_candidate_outside_root_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            outside = _mkdirs(os.path.join(d, "out", "x"))
            root = _mkdirs(os.path.join(d, "in"))
            fsutil.prune_empty_dirs(root, candidates=[outside])
            self.assertTrue(os.path.isdir(outside))

    def test_root_itself_is_never_removed(self):
        with tempfile.TemporaryDirectory() as d:
            removed, _failed = fsutil.prune_empty_dirs(d, candidates=[d])
            self.assertEqual(removed, [])
            self.assertTrue(os.path.isdir(d))

    def test_protected_candidate_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            prot = _mkdirs(os.path.join(d, "pipeline", "db"))
            fsutil.prune_empty_dirs(d, protected=[
                os.path.join(d, "pipeline")], candidates=[prot])
            self.assertTrue(os.path.isdir(prot))

    def test_password_named_candidate_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            pwd = _mkdirs(os.path.join(d, "解压密码"))
            fsutil.prune_empty_dirs(d, candidates=[pwd])
            self.assertTrue(os.path.isdir(pwd))

    def test_dry_run_touches_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            a = _mkdirs(os.path.join(d, "a", "b"))
            removed, _failed = fsutil.prune_empty_dirs(
                d, candidates=[a], dry_run=True)
            self.assertTrue(removed)
            self.assertTrue(os.path.isdir(a))

    def test_empty_candidate_list_is_noop(self):
        with tempfile.TemporaryDirectory() as d:
            _mkdirs(os.path.join(d, "a"))
            removed, failed = fsutil.prune_empty_dirs(d, candidates=[])
            self.assertEqual((removed, failed), ([], []))
            self.assertTrue(os.path.isdir(os.path.join(d, "a")))

    def test_none_candidate_in_list_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            a = _mkdirs(os.path.join(d, "a"))
            removed, _failed = fsutil.prune_empty_dirs(
                d, candidates=[None, a])
            self.assertIn(os.path.abspath(a),
                          [os.path.abspath(x) for x in removed])


class PruneEmptyCliTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="prune_cli_")
        self.root = os.path.join(self.dir, "root")
        self.src = os.path.join(self.root, C.DEFAULT_SRC_DIRNAME)
        os.makedirs(self.src, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_default_is_dry_run(self):
        empty = os.path.join(self.src, "empty")
        os.makedirs(empty, exist_ok=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = pipeline.main(["prune-empty", "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.isdir(empty))            # nothing removed
        self.assertIn("dry run", buf.getvalue())

    def test_apply_removes_empty_dirs(self):
        empty = os.path.join(self.src, "empty")
        os.makedirs(empty, exist_ok=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = pipeline.main(["prune-empty", "--apply", "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(empty))
        self.assertTrue(os.path.isdir(self.src))         # src root survives

    def test_apply_keeps_content(self):
        keep = _touch(os.path.join(self.src, "剧集", "ep1.mp4"), "data")
        os.makedirs(os.path.join(self.src, "剧集", "空壳"), exist_ok=True)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = pipeline.main(["prune-empty", "--apply", "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.isfile(keep))
        self.assertFalse(os.path.exists(os.path.join(self.src, "剧集", "空壳")))
        self.assertTrue(os.path.isdir(os.path.join(self.src, "剧集")))

    def test_json_output_parses(self):
        os.makedirs(os.path.join(self.src, "empty"), exist_ok=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = pipeline.main(["prune-empty", "--json", "--root", self.root])
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertFalse(data["applied"])
        self.assertEqual(len(data["removed"]), 1)

    def test_command_never_opens_the_database(self):
        """prune-empty 不得在用户根目录里物化 DB 文件（保持只读）。"""
        os.makedirs(os.path.join(self.src, "a", "b"), exist_ok=True)
        with contextlib.redirect_stdout(io.StringIO()):
            pipeline.main(["prune-empty", "--apply", "--root", self.root])
        self.assertFalse(os.path.exists(
            os.path.join(self.root, "pipeline", "db", "archive.db")))

    def test_nothing_to_do_exits_zero(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = pipeline.main(["prune-empty", "--apply", "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertIn("(none)", buf.getvalue())


class SchedulerWiringTests(unittest.TestCase):
    """The batch-end hook exists, is wired, and stays off in a dry run."""

    def _pipe(self, root, dry_run=False):
        from pipeline_lib import scheduler
        obj = scheduler.Pipeline.__new__(scheduler.Pipeline)
        obj.cfg = scheduler.PipelineConfig(workdir=root, dry_run=dry_run)
        obj.prune_candidates = set()
        obj.prune_removed = []
        obj.db = _NullDb()
        return obj

    def test_prune_candidates_initialised(self):
        """批量收尾钩子用的两个容器必须在构造时就位（否则 `run` 会 AttributeError）。"""
        from pipeline_lib import scheduler
        obj = scheduler.Pipeline.__new__(scheduler.Pipeline)
        obj.library_hits = []
        obj.prune_candidates = set()
        obj.prune_removed = []
        self.assertEqual(obj.prune_candidates, set())
        self.assertEqual(obj.library_hits, [])
        self.assertEqual(obj.prune_removed, [])
        src = inspect.getsource(scheduler.Pipeline.__init__)
        self.assertIn("self.prune_candidates", src)
        self.assertIn("self.prune_removed", src)
        self.assertIn("self.library_hits", src)

    def test_dry_run_prunes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, C.DEFAULT_SRC_DIRNAME)
            empty = os.path.join(src, "a", "b")
            os.makedirs(empty, exist_ok=True)
            pipe = self._pipe(d, dry_run=True)
            pipe.prune_candidates.add(empty)
            self.assertEqual(pipe._prune_empty_dirs(), [])
            self.assertTrue(os.path.isdir(empty))

    def test_no_candidates_prunes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, C.DEFAULT_SRC_DIRNAME)
            empty = os.path.join(src, "a", "b")
            os.makedirs(empty, exist_ok=True)
            pipe = self._pipe(d)
            self.assertEqual(pipe._prune_empty_dirs(), [])
            self.assertTrue(os.path.isdir(empty))

    def test_removes_shell_and_audits(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, C.DEFAULT_SRC_DIRNAME)
            empty = os.path.join(src, "广告", "sub")
            os.makedirs(empty, exist_ok=True)
            pipe = self._pipe(d)
            pipe.prune_candidates.add(empty)
            removed = pipe._prune_empty_dirs()
            self.assertTrue(removed)
            self.assertFalse(os.path.exists(os.path.join(src, "广告")))
            self.assertTrue(os.path.isdir(src))       # src root survives
            self.assertTrue(any(a == C.ACTION_PRUNE
                                for _fid, a, _m in pipe.db.events))

    def test_config_switch_disables_pruning(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, C.DEFAULT_SRC_DIRNAME)
            empty = os.path.join(src, "a")
            os.makedirs(empty, exist_ok=True)
            pipe = self._pipe(d)
            pipe.prune_candidates.add(empty)
            old = C.EMPTY_DIR_PRUNE_ON_FINISH
            C.EMPTY_DIR_PRUNE_ON_FINISH = False
            try:
                self.assertEqual(pipe._prune_empty_dirs(), [])
            finally:
                C.EMPTY_DIR_PRUNE_ON_FINISH = old
            self.assertTrue(os.path.isdir(empty))

    def test_never_raises_when_helper_explodes(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, C.DEFAULT_SRC_DIRNAME)
            os.makedirs(src, exist_ok=True)
            pipe = self._pipe(d)
            pipe.prune_candidates.add(src)
            with mock.patch.object(fsutil, "prune_empty_dirs",
                                   side_effect=RuntimeError("boom")):
                self.assertEqual(pipe._prune_empty_dirs(), [])
            self.assertTrue(any("prune skipped" in m
                                for _f, _a, m in pipe.db.events))

    def test_run_summary_reports_pruned_count(self):
        from pipeline_lib import scheduler
        src = inspect.getsource(scheduler.Pipeline._run_locked_main)
        self.assertIn('"pruned_dirs"', src)
        self.assertIn('"library_hits"', src)

    def test_prune_hook_is_wired_into_the_batch_sequence(self):
        from pipeline_lib import scheduler
        src = inspect.getsource(scheduler.Pipeline._run_locked_main)
        self.assertIn("self._prune_empty_dirs()", src)
        self.assertIn("self.prune_removed = self._prune_empty_dirs()", src)


class _NullDb:
    def __init__(self):
        self.events = []

    def event(self, fid, action, message="", level="INFO", batch=None):
        self.events.append((fid, action, message))


class CleanJunkPruneTests(unittest.TestCase):
    """`clean-junk` must also collect the shells its own deletions created."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cleanjunk_prune_")
        self.root = os.path.join(self.dir, "root")
        self.src = os.path.join(self.root, C.DEFAULT_SRC_DIRNAME)
        self.ad_dir = os.path.join(self.src, "广告夹")
        self.ad = _touch(os.path.join(self.ad_dir, "a.txt"), "tiny ad")
        self.keep = _touch(os.path.join(self.src, "剧集", "ep.mp4"), "data")
        os.makedirs(os.path.join(self.root, "pipeline", "db"), exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def _seed(self, junk_rule="TINY_TXT"):
        from pipeline_lib.db import Database
        from pipeline_lib.scheduler import PipelineConfig
        cfg = PipelineConfig(workdir=self.root)
        db = Database(cfg.db_path)
        try:
            db.begin_batch(cfg.batch, cfg.src_dir, 12345)
            fid, _created = db.upsert_file(self.ad, batch=cfg.batch)
            db.update_fields(fid, is_junk=1, junk_rule=junk_rule)
            db.transition(fid, C.STATUS_JUNK_PENDING, C.ACTION_ANALYZE,
                          "seeded for test")
        finally:
            db.close()

    def test_clean_junk_prunes_emptied_folder(self):
        self._seed()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = pipeline.main(["clean-junk", "--yes", "--no-learn",
                                "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(self.ad))
        self.assertFalse(os.path.exists(self.ad_dir))       # shell collected
        self.assertTrue(os.path.isfile(self.keep))          # content untouched
        self.assertTrue(os.path.isdir(self.src))
        self.assertIn("empty dir", buf.getvalue())

    def test_no_prune_leaves_the_shell(self):
        self._seed()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = pipeline.main(["clean-junk", "--yes", "--no-learn",
                                "--no-prune", "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(self.ad))
        self.assertTrue(os.path.isdir(self.ad_dir))         # shell left behind

    def test_prune_flag_parsed(self):
        ap = pipeline.build_parser()
        self.assertFalse(ap.parse_args(
            ["clean-junk", "--root", "R"]).no_prune)
        self.assertTrue(ap.parse_args(
            ["clean-junk", "--no-prune", "--root", "R"]).no_prune)


class JunkRuleTieringTests(unittest.TestCase):
    """A library hit must be treated as zero-risk everywhere (report + CLI)."""

    def test_pipeline_tiering_uses_helper(self):
        src = inspect.getsource(pipeline.cmd_clean_junk)
        self.assertIn("junk_mod.is_auto_rule", src)
        self.assertNotIn("cur_rule in C.JUNK_AUTO_RULES", src)

    def test_scheduler_tiering_uses_helper(self):
        from pipeline_lib import scheduler
        src = inspect.getsource(scheduler)
        self.assertIn("junk_mod.is_auto_rule", src)
        self.assertNotIn("rule in C.JUNK_AUTO_RULES", src)


if __name__ == "__main__":
    unittest.main()
