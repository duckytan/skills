# -*- coding: utf-8 -*-
"""Unit tests for the filename-ending-bracket password rule (2026-09-13).

Run:  python -m unittest tests.test_passwords -v   (from scripts/)
Pure stdlib; no 7z, no real batches, no network.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline_lib import passwords as pw


class TrailingBracketTests(unittest.TestCase):

    def test_trailing_fullwidth_numeric(self):
        # 用户示例 1
        cands = pw.scrape_trailing_password(
            "【精品洗澡】浴室偷拍两姐妹冲洗逼逼的样子好认真（654321123456）.7z")
        self.assertEqual(cands, [("654321123456", "TRAIL_BRACKET")])

    def test_trailing_fullwidth_alnum(self):
        # 用户示例 2
        cands = pw.scrape_trailing_password(
            "女生宿舍楼连续三位小嫩妹（sX8uRvp4Ld73）.tar")
        self.assertEqual(cands, [("sX8uRvp4Ld73", "TRAIL_BRACKET")])

    def test_trailing_halfwidth(self):
        cands = pw.scrape_trailing_password("file(sX8uRvp4Ld73).tar")
        self.assertEqual(cands, [("sX8uRvp4Ld73", "TRAIL_BRACKET")])

    def test_no_trailing_bracket(self):
        self.assertEqual(pw.scrape_trailing_password("普通文件.7z"), [])
        self.assertEqual(pw.scrape_trailing_password(""), [])
        self.assertEqual(pw.scrape_trailing_password("（中间）名字.7z"), [])

    def test_short_password_fires(self):
        # 普通 RE_BRACKET 要求 >=3 字符，专用规则允许 1+ 字符
        cands = pw.scrape_trailing_password("短片（12）.rar")
        self.assertEqual(cands, [("12", "TRAIL_BRACKET")])

    def test_middle_bracket_not_trailing(self):
        # 中间的括号不是密码；只有末尾的才是
        self.assertEqual(
            pw.scrape_trailing_password("写真（123）合集（456）.7z"),
            [("456", "TRAIL_BRACKET")])

    def test_whitespace_between_bracket_and_ext(self):
        cands = pw.scrape_trailing_password("x（sX8uRvp4Ld73） .7z")
        self.assertEqual(cands, [("sX8uRvp4Ld73", "TRAIL_BRACKET")])

    def test_candidates_for_includes_trailing(self):
        row = {"file_name": "女生宿舍楼连续三位小嫩妹（sX8uRvp4Ld73）.tar",
               "dir_path": "/x", "password": ""}
        cands = pw.candidates_for(row, None, [])
        pairs = [(p, s) for p, s in cands]
        sources = [s for _, s in cands]
        self.assertIn("TRAIL_BRACKET", sources)
        # NONE 第一，TRAIL_BRACKET 紧随其后
        self.assertEqual(sources[0], "NONE")
        self.assertEqual(sources[1], "TRAIL_BRACKET")

    def test_trailing_priority_over_earlier_bracket(self):
        # 名中有两个括号：末尾的走 TRAIL_BRACKET（高优先），
        # 前面的走普通 FILE_NAME 抠码（低优先）
        row = {"file_name": "写真（123）合集（456）.7z",
               "dir_path": "/x", "password": ""}
        cands = pw.candidates_for(row, None, [])
        sources = [s for _, s in cands]
        self.assertIn("TRAIL_BRACKET", sources)
        self.assertIn("FILE_NAME", sources)
        self.assertLess(sources.index("TRAIL_BRACKET"),
                        sources.index("FILE_NAME"))

    # ---- 多括号种类（2026-09-13 扩写）----

    def test_trailing_fullwidth_square(self):
        # 全角方括号 【】
        cands = pw.scrape_trailing_password("合集【abc123】.rar")
        self.assertEqual(cands, [("abc123", "TRAIL_BRACKET")])

    def test_trailing_halfwidth_square(self):
        # 半角方括号 []
        cands = pw.scrape_trailing_password("pack[Ab9x].zip")
        self.assertEqual(cands, [("Ab9x", "TRAIL_BRACKET")])

    def test_trailing_curly(self):
        # 花括号 {}
        cands = pw.scrape_trailing_password("资源{cX3kQ}.7z")
        self.assertEqual(cands, [("cX3kQ", "TRAIL_BRACKET")])

    def test_trailing_fullwidth_round_longname(self):
        # 全角圆括号 （）长密码
        cands = pw.scrape_trailing_password("某视频（sX8uRvp4Ld73Wq2）.7z")
        self.assertEqual(cands, [("sX8uRvp4Ld73Wq2", "TRAIL_BRACKET")])

    def test_trailing_mismatched_not_matched(self):
        # 错配括号（开 （ 闭 】）不误认：没有任何配对括号在末尾
        self.assertEqual(pw.scrape_trailing_password("测试（abc】.7z"), [])
        self.assertEqual(pw.scrape_trailing_password("前【abc）后.7z"), [])

    def test_trailing_only_matching_pair_fires(self):
        # 名中有错配括号 + 末尾正确配对，只认末尾配对的那对
        cands = pw.scrape_trailing_password("乱（x】合集（ok99）.7z")
        self.assertEqual(cands, [("ok99", "TRAIL_BRACKET")])

    def test_dir_name_trailing_bracket(self):
        # 父目录名末尾的配对括号（含 【】）要走 DIR_NAME
        row = {"file_name": "a.7z",
               "dir_path": "/x/合集【abc123】", "password": ""}
        cands = pw.candidates_for(row, None, [])
        self.assertIn(("abc123", "DIR_NAME"), [(p, s) for p, s in cands])

    def test_dir_name_trailing_bracket_halfwidth_user_example(self):
        # 用户例子：父文件夹名末尾半角括号 = 密码（DIR_NAME）
        row = {"file_name": "abc.zip",
               "dir_path": r"D:\Downloads\美丽的姑娘(123)", "password": ""}
        cands = pw.candidates_for(row, None, [])
        self.assertIn(("123", "DIR_NAME"), [(p, s) for p, s in cands])

    def test_no_duplicate_trailing_via_file_name(self):
        # 末尾 （） 被 TRAIL_BRACKET 和 RE_BRACKET 都命中，不应出现两条相同密码
        row = {"file_name": "写真（sX8uRvp4Ld73）.7z",
               "dir_path": "/x", "password": ""}
        cands = pw.candidates_for(row, None, [])
        pwds = [p for p, s in cands]
        self.assertEqual(pwds.count("sX8uRvp4Ld73"), 1)


class KeywordHintTests(unittest.TestCase):
    """密码提示词同义词（口令/解压口令 + '=' 分隔）走 RE_PW_HINT。"""

    def _names(self, n):
        return pw.scrape_from_names([n], "FILE_NAME")

    def test_kouling_fullwidth_colon(self):
        cands = self._names("资料口令：abc123.mp4")
        self.assertIn(("abc123", "FILE_NAME"), cands)

    def test_jieya_kouling(self):
        cands = self._names("解压口令：xyz789.rar")
        self.assertIn(("xyz789", "FILE_NAME"), cands)

    def test_equals_separator(self):
        # 密码=xxx 形式
        cands = self._names("密码=xyz789.7z")
        self.assertIn(("xyz789", "FILE_NAME"), cands)

    def test_kouling_in_candidates(self):
        row = {"file_name": "合集口令：kLm42.mp4",
               "dir_path": "/x", "password": ""}
        cands = pw.candidates_for(row, None, [])
        self.assertIn(("kLm42", "FILE_NAME"), [(p, s) for p, s in cands])

    def test_pw_hint_strips_file_extension(self):
        # 文件名里的扩展名不该被带进密码候选
        cands = self._names("解压码：维生素.rar")
        self.assertIn(("维生素", "FILE_NAME"), cands)

    def test_pw_hint_keeps_dotted_password(self):
        # 真密码含点（abc.def）应保留，不被扩展名白名单误删
        cands = self._names("密码：abc.def.mp4")
        self.assertIn(("abc.def", "FILE_NAME"), cands)


if __name__ == "__main__":
    unittest.main()
